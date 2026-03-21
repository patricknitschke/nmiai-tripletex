"""
Orchestrator — routes tasks to the right execution mode.

Modes:
  - "senior": Single Senior Accountant agent (no planning, direct execution)
  - "hybrid": Chief plans (1 LLM call) → Senior executes with plan as context (default)

Set via AGENT_MODE env var. Defaults to "hybrid".
"""

import asyncio
import logging
import os

from .agents import chief_plan
from .agents.senior import run_senior_accountant
from .tripletex import TripletexClient

logger = logging.getLogger("agent.orchestrator")

AGENT_MODE = os.environ.get("AGENT_MODE", "hybrid").lower()


async def solve_task(
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
) -> dict:
    """Route to the right execution mode."""
    logger.info("Agent mode: %s", AGENT_MODE)

    if AGENT_MODE == "hybrid":
        return await _run_hybrid_mode(prompt, files, client, deadline=deadline)
    else:
        return await run_senior_accountant(prompt, files, client, deadline=deadline)


async def _run_hybrid_mode(
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
) -> dict:
    """Hybrid mode: Chief plans (1 LLM call), Senior executes with plan as context."""

    # Fast-path: skip Chief for multi-voucher/closing tasks where Chief always times out
    # generating 800+ token plans. Senior handles these better without planning overhead.
    _CLOSING_KEYWORDS = [
        "closing", "lukking", "avslutning", "årsoppgjør", "årsavslutning",
        "depreciation", "avskrivning", "avskriving",
        "periodisering", "accrual", "provision",
        "monthly close", "cierre mensual", "clôture", "encerramento",
        "year-end", "yearend", "balance de saldos",
        # Dimension tasks — Chief always times out, Senior handles raw API better
        "dimensjon", "dimension", "dimensão", "dimensión",
    ]
    prompt_lower = prompt.lower()
    if any(kw in prompt_lower for kw in _CLOSING_KEYWORDS):
        logger.info("Multi-voucher/closing task detected — skipping Chief, Senior handles directly")
        return await run_senior_accountant(prompt, files, client, deadline=deadline)

    # Stage 1: Chief produces a strategic plan (30s max — PDF inputs can be slow)
    logger.info("=" * 40)
    logger.info("HYBRID MODE: Chief planning...")
    try:
        # Don't pass files (PDFs/CSVs) to Chief — it only needs the text prompt to plan.
        # Senior gets the full files for data extraction.
        thinking, steps = await asyncio.wait_for(chief_plan(prompt, []), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("Chief planning timed out after 30s — skipping to Senior")
        return await run_senior_accountant(prompt, files, client, deadline=deadline)

    if not steps:
        logger.warning("Chief produced empty plan, falling back to Senior")
        return await run_senior_accountant(prompt, files, client, deadline=deadline)

    # Safety net: detect if Chief "gave up" instead of planning
    if len(steps) == 1 and steps[0].get("suggested_workflow") == "fallback":
        task_text = steps[0].get("task", "").lower()
        give_up_signals = ["missing", "can't proceed", "cannot proceed", "not available",
                           "please provide", "invoice number is required", "not possible"]
        if any(signal in task_text for signal in give_up_signals):
            logger.warning("Chief plan looks like giving up: '%s'. Ignoring plan, Senior will handle directly.",
                           steps[0].get("task", "")[:200])
            return await run_senior_accountant(prompt, files, client, deadline=deadline)

    # Log the plan
    for i, step in enumerate(steps, 1):
        logger.info("  PLAN STEP %d: [%s] %s", i, step.get("suggested_workflow", "?"), step.get("task", ""))
    if thinking:
        logger.info("Chief thinking: %s", thinking[:300])

    # Stage 2: Build plan preamble and let Senior execute in one loop
    plan_lines = []
    if thinking:
        plan_lines.append(f"**Reasoning:** {thinking}")
        plan_lines.append("")
    plan_lines.append("**Steps to execute (in order):**")
    for i, step in enumerate(steps, 1):
        wf = step.get("suggested_workflow", "fallback")
        task = step.get("task", "")
        plan_lines.append(f"{i}. [{wf}] {task}")
    plan_lines.append("")
    plan_lines.append("Follow this plan. Pass IDs from earlier steps to later ones. "
                      "Do NOT repeat steps that were already completed by a previous workflow call.")
    preamble = "\n".join(plan_lines)

    logger.info("HYBRID MODE: Senior executing with plan...")
    return await run_senior_accountant(prompt, files, client, deadline=deadline, plan_preamble=preamble)


