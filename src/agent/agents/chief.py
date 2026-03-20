"""
Chief Accountant agent — the solution architect and planner.

Reads the original prompt (multilingual) and produces a step-by-step plan
for specialist agents to execute in hybrid mode.
"""

import json
import logging
from datetime import date

from ..llm import complete
from ..utils import build_content, parse_json
from ..workflows.schemas import TASK_SCHEMAS

logger = logging.getLogger("agent.chief")


# ---------------------------------------------------------------------------
# Workflow catalog (auto-generated from TASK_SCHEMAS)
# ---------------------------------------------------------------------------

def build_workflow_catalog() -> str:
    """Generate a concise workflow reference for the Chief's system prompt."""
    lines = []
    for task_type, schema in TASK_SCHEMAS.items():
        lines.append(f"### {task_type}")
        lines.append(f"  Endpoint: {schema['api_endpoint']}")
        lines.append(f"  Notes: {schema['notes']}")
        for field in schema["fields"]:
            req = "REQUIRED" if field.get("required") else "optional"
            lines.append(f"  - {field['name']} ({field['type']}, {req}): {field['description']}")
            if "items" in field:
                for item in field["items"]:
                    lines.append(f"      - {item['name']} ({item['type']}): {item['description']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 1: Plan
# ---------------------------------------------------------------------------

PLAN_PROMPT = """\
You are a Chief Accountant AI and solution architect. You receive accounting task \
prompts (in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French) \
and design a concrete solution plan for your sub-agents to execute.

Today's date: {today}

## CRITICAL: Fresh Empty Environment
The Tripletex account starts COMPLETELY EMPTY every time — no customers, no products, \
no invoices, no employees, no bank accounts. NOTHING exists. Everything must be created from scratch.

This means prompts describe a DESIRED END STATE, not an existing state. Examples:
- "Customer X has an outstanding invoice for Y kr — register full payment" \
  → You must CREATE the customer, CREATE the invoice, THEN register payment.
- "Register payment on invoice for consulting hours" \
  → The invoice does NOT exist yet. Create it first, then register payment.
- "Create a credit note for invoice #1" \
  → The invoice must be created first if it doesn't exist.

NEVER say "I can't proceed because X doesn't exist." Instead, CREATE what's needed.
NEVER ask for missing information like invoice numbers — the account is empty, so create everything.

## Available Workflows
{workflow_catalog}

## Your Process
1. **THINK** about what the task requires and what dependencies exist:
   - What resources need to exist before the main task can succeed?
   - What is the correct order of operations?
   - Which workflows handle prerequisite creation automatically vs which need explicit steps?
2. **DESIGN** a concrete step-by-step solution, not just a task list.

## Output Format
Return ONLY valid JSON:
{{
  "thinking": "Your reasoning about dependencies, order of operations, and approach",
  "steps": [
    {{
      "task": "Clear English description including HOW to accomplish this step",
      "suggested_workflow": "workflow_name or 'fallback' if no workflow fits"
    }}
  ]
}}

## Rules
- In your thinking, reason about: What needs to exist first? What does each workflow \
  handle automatically? What might go wrong?
- Keep task descriptions clear and in English, even if the prompt is in another language.
- In task descriptions, tell the sub-agent the APPROACH, not just the goal. \
  Example: "Create an invoice for customer Fjelltopp AS (org.nr 862382900). \
  The create_invoice workflow will create the customer automatically if you pass \
  a 'customer' object with name and organizationNumber. Include 3 order lines \
  with product numbers and VAT rates from the prompt."
- Creating a SUPPLIER (leverandør/Lieferant/fournisseur) = create_customer with isSupplier: true.
- For tasks that don't match any workflow, use suggested_workflow: "fallback".
- Most tasks need only 1 step — many workflows handle prerequisites internally.
"""


async def chief_plan(prompt: str, files: list) -> tuple[str, list[dict]]:
    """Chief produces a plan with thinking. Returns (thinking, steps)."""
    today = date.today().isoformat()
    catalog = build_workflow_catalog()
    system = PLAN_PROMPT.format(today=today, workflow_catalog=catalog)

    content = build_content(prompt, files)
    raw = await complete(system, content, max_tokens=4096)
    logger.info("Chief plan raw: %s", raw)

    try:
        plan = parse_json(raw)
        thinking = plan.get("thinking", "")
        if thinking:
            logger.info("Chief thinking: %s", thinking)
        steps = plan.get("steps", [])
        logger.info("Chief produced %d step(s)", len(steps))
        return thinking, steps
    except Exception:
        logger.error("Failed to parse Chief plan, using single fallback step")
        return "", [{"task": "Complete the accounting task described in the prompt", "suggested_workflow": "fallback"}]
