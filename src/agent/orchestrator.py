"""
Orchestrator — routes tasks to the right execution mode.

Modes:
  - "senior": Single Senior Accountant agent (fast path, default)
  - "hybrid": Chief plans (1 LLM call) → Senior executes with plan as context
  - "chief": Multi-agent Chief + sub-agents (complex tasks, multi-step coordination)

Set via AGENT_MODE env var. Defaults to "senior".
"""

import json
import logging
import os

from .agents import chief_plan, chief_review
from .agents.senior import run_senior_accountant
from .agents.sub_agent import run_sub_agent
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

    if AGENT_MODE == "chief":
        return await _run_chief_mode(prompt, files, client)
    elif AGENT_MODE == "hybrid":
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

    # Stage 1: Chief produces a strategic plan
    logger.info("=" * 40)
    logger.info("HYBRID MODE: Chief planning...")
    thinking, steps = await chief_plan(prompt, files)

    if not steps:
        logger.warning("Chief produced empty plan, Senior will proceed without plan")
        return await run_senior_accountant(prompt, files, client, deadline=deadline)

    # Log the plan
    for i, step in enumerate(steps, 1):
        logger.info("  PLAN STEP %d: [%s] %s", i, step.get("suggested_workflow", "?"), step.get("task", ""))

    # Build a readable preamble for the Senior
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
    plan_lines.append("Follow this plan but adapt if you encounter errors. "
                      "Pass IDs from earlier steps to later ones.")
    preamble = "\n".join(plan_lines)

    # Stage 2: Senior executes with the plan injected
    logger.info("HYBRID MODE: Senior executing with plan...")
    return await run_senior_accountant(prompt, files, client, deadline=deadline, plan_preamble=preamble)


async def _run_chief_mode(
    prompt: str,
    files: list,
    client: TripletexClient,
) -> dict:
    """Multi-agent Chief Accountant orchestrator."""

    # Stage 1: Chief plans (with thinking)
    logger.info("=" * 40)
    logger.info("CHIEF ACCOUNTANT: Planning...")
    thinking, steps = await chief_plan(prompt, files)

    if not steps:
        logger.warning("Chief produced empty plan, falling back")
        steps = [{"task": "Complete the accounting task", "suggested_workflow": "fallback"}]

    # Log the full plan
    for i, step in enumerate(steps, 1):
        logger.info("  PLAN STEP %d: [%s] %s", i, step.get("suggested_workflow", "?"), step.get("task", ""))

    # Build persistent Chief memory
    chief_memory = f"Thinking: {thinking}\n\nPlan: {json.dumps(steps, indent=2)}" if thinking else f"Plan: {json.dumps(steps, indent=2)}"

    # Execute steps one by one
    completed_steps = []
    remaining_steps = list(steps)

    while remaining_steps:
        step = remaining_steps.pop(0)
        step_num = len(completed_steps) + 1
        task_desc = step.get("task", "")
        suggested_wf = step.get("suggested_workflow", "fallback")

        logger.info("-" * 40)
        logger.info("STEP %d: %s (workflow: %s)", step_num, task_desc, suggested_wf)

        # Build prior context with actual results (IDs, numbers) from prior steps
        prior_context = "None yet." if not completed_steps else json.dumps(
            [{"step": s["task"], "result": s.get("key_results", {})}
             for s in completed_steps],
            indent=2,
        )

        # Run sub-agent for this step
        sub_result = await run_sub_agent(
            task_description=task_desc,
            suggested_workflow=suggested_wf,
            prior_context=prior_context,
            prompt=prompt,
            files=files,
            client=client,
            chief_memory=chief_memory,
        )

        # Record completed step with actual results
        key_results = sub_result.get("workflow_result", {})
        completed_steps.append({
            "task": task_desc,
            "suggested_workflow": suggested_wf,
            "result_summary": sub_result.get("status", "unknown"),
            "iterations": sub_result.get("iterations", 0),
            "key_results": key_results,
        })

        # Update Chief memory with step results so it knows what was created
        if key_results:
            chief_memory += f"\n\nStep {step_num} result: {json.dumps(key_results)}"

        logger.info("Step %d completed: %s (%d iterations, %d chief Q&As, results: %s)",
                     step_num, sub_result.get("status"), sub_result.get("iterations", 0),
                     sub_result.get("chief_qas", 0), key_results)

        # Stage 3: Chief reviews (only if there are remaining steps)
        if remaining_steps:
            logger.info("CHIEF ACCOUNTANT: Reviewing progress...")
            should_continue, adjusted_remaining = await chief_review(
                prompt, files, steps, completed_steps, remaining_steps,
            )
            if not should_continue:
                logger.info("Chief says task is complete, stopping early")
                break
            remaining_steps = adjusted_remaining

    logger.info("=" * 40)
    logger.info("CHIEF ACCOUNTANT: All steps complete (%d steps executed)", len(completed_steps))
    return {"status": "completed", "steps_executed": len(completed_steps)}
