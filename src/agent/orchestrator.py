"""
Orchestrator — routes tasks to the right execution mode.

Modes:
  - "senior": Single Senior Accountant agent (fast path, default)
  - "hybrid": Chief plans (1 LLM call) → specialists execute each step

Set via AGENT_MODE env var. Defaults to "hybrid".
"""

import json
import logging
import os

from .agents import chief_plan
from .agents.senior import run_senior_accountant
from .agents.specialists import get_specialist
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
    """Hybrid mode: Chief plans (1 LLM call), specialists execute each step."""

    # Stage 1: Chief produces a strategic plan
    logger.info("=" * 40)
    logger.info("HYBRID MODE: Chief planning...")
    thinking, steps = await chief_plan(prompt, files)

    if not steps:
        logger.warning("Chief produced empty plan, falling back to Senior")
        return await run_senior_accountant(prompt, files, client, deadline=deadline)

    # Safety net: detect if Chief "gave up" instead of planning
    # Signs: single fallback step with language like "missing", "can't", "provide", "not available"
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

    # Stage 2: Route each step to the right specialist
    completed_steps = []

    for i, step in enumerate(steps, 1):
        task_desc = step.get("task", "")
        suggested_wf = step.get("suggested_workflow", "fallback")

        # Get the right specialist for this step
        specialist_fn = get_specialist(suggested_wf)
        specialist_name = specialist_fn.__module__.rsplit(".", 1)[-1]

        logger.info("-" * 40)
        logger.info("STEP %d/%d: [%s specialist] %s", i, len(steps), specialist_name, task_desc)

        # Build prior results context from completed steps
        prior_results = None
        if completed_steps:
            prior_results = json.dumps(
                [{"step": s["task"], "result": s.get("key_results", {})}
                 for s in completed_steps],
                indent=2,
            )

        # Run specialist
        result = await specialist_fn(
            task_description=task_desc,
            prompt=prompt,
            files=files,
            client=client,
            deadline=deadline,
            prior_results=prior_results,
        )

        # Extract key results for passing to next step
        key_results = result.get("workflow_result", {})
        completed_steps.append({
            "task": task_desc,
            "specialist": specialist_name,
            "iterations": result.get("iterations", 0),
            "key_results": key_results,
        })

        logger.info("Step %d completed: %s specialist, %d iterations, results: %s",
                     i, specialist_name, result.get("iterations", 0), json.dumps(key_results)[:200])

    logger.info("=" * 40)
    logger.info("HYBRID MODE: All %d steps complete", len(completed_steps))
    return {"status": "completed", "steps_executed": len(completed_steps)}


