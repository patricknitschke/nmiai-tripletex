"""Chief routing tests — verify Chief picks the right workflow for each task.

Requires real LLM API key (ANTHROPIC_API_KEY or Vertex AI creds).
Each test makes 1 LLM call (~3-5s).

Run with: pytest tests/test_chief_routing.py -v
Skip with: pytest -m "not slow"
"""

import os

import pytest

from .conftest import load_tasks, parse_expected_workflows

# Skip entire module if no LLM key available
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("GCP_PROJECT_ID"),
        reason="No LLM API key set (need ANTHROPIC_API_KEY or GCP_PROJECT_ID)",
    ),
]


def _build_test_cases() -> list[tuple[str, str, list[str]]]:
    """Build (prompt, task_type, expected_workflows) tuples from tasks.csv."""
    cases = []
    seen_prompts = set()
    for row in load_tasks():
        prompt = row["prompt"].strip()
        task_type = row["task_type"].strip()

        # Skip duplicates (same prompt tested twice)
        if prompt in seen_prompts:
            continue
        seen_prompts.add(prompt)

        expected = parse_expected_workflows(task_type)
        if not expected:
            continue  # pure fallback tasks, nothing to assert

        cases.append((prompt, task_type, expected))
    return cases


TEST_CASES = _build_test_cases()


@pytest.mark.parametrize(
    "prompt,task_type,expected_workflows",
    TEST_CASES,
    ids=[c[1] for c in TEST_CASES],
)
async def test_chief_routes_correctly(prompt, task_type, expected_workflows):
    """Chief should include the expected workflow(s) in its plan."""
    from src.agent.agents.chief import chief_plan

    thinking, steps = await chief_plan(prompt, [])

    returned_workflows = [s.get("suggested_workflow", "") for s in steps]

    for expected in expected_workflows:
        assert expected in returned_workflows, (
            f"Chief missed workflow '{expected}' for task_type '{task_type}'. "
            f"Got: {returned_workflows}. "
            f"Thinking: {thinking[:200]}"
        )
