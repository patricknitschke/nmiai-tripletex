"""
Multi-agent orchestrator — wires together the Chief and sub-agents.

Flow:
  1. Chief plans (with thinking step)
  2. For each step: sub-agent executes (with ask_chief for data)
  3. Between steps: Chief reviews and adapts
"""

import json
import logging

from .agents import chief_plan, chief_review
from .agents.sub_agent import run_sub_agent
from .tripletex import TripletexClient

logger = logging.getLogger("agent.orchestrator")


async def solve_task(
    prompt: str,
    files: list,
    client: TripletexClient,
) -> dict:
    """Run the multi-agent Chief Accountant orchestrator."""

    # Stage 1: Chief plans (with thinking)
    logger.info("=" * 40)
    logger.info("CHIEF ACCOUNTANT: Planning...")
    thinking, steps = await chief_plan(prompt, files)

    if not steps:
        logger.warning("Chief produced empty plan, falling back")
        steps = [{"task": "Complete the accounting task", "suggested_workflow": "fallback"}]

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

        # Build prior context
        prior_context = "None yet." if not completed_steps else json.dumps(
            [{"step": s["task"], "result_summary": s.get("result_summary", "completed")}
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

        # Record completed step
        completed_steps.append({
            "task": task_desc,
            "suggested_workflow": suggested_wf,
            "result_summary": sub_result.get("status", "unknown"),
            "iterations": sub_result.get("iterations", 0),
        })

        logger.info("Step %d completed: %s (%d iterations, %d chief Q&As)",
                     step_num, sub_result.get("status"), sub_result.get("iterations", 0),
                     sub_result.get("chief_qas", 0))

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
