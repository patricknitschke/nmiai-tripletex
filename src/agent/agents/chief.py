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
    """Generate a slim workflow reference for the Chief. Names + notes only, no field specs."""
    lines = []
    for task_type, schema in TASK_SCHEMAS.items():
        lines.append(f"- **{task_type}**: {schema['notes']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 1: Plan
# ---------------------------------------------------------------------------

PLAN_PROMPT = """\
You are a Chief Accountant AI and solution architect. You receive accounting task \
prompts (in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French) \
and design a concrete solution plan for your sub-agents to execute.

Today's date: {today}

## CRITICAL: Environment Rules
The Tripletex account may have PRE-EXISTING data or may be empty depending on the task.

**ALWAYS plan to search before creating.** Some tasks (credit notes, payments) have pre-existing \
invoices/customers that must be FOUND, not recreated. Other tasks start empty and need everything \
created from scratch.

Your plan should ALWAYS include a search step first for tasks that reference existing resources:
- "Register payment on invoice" → First step: search for the existing invoice. If not found, create it.
- "Issue credit note for invoice" → First step: search for the existing invoice. If not found, create it.
- "Create employee/customer/product" → These are usually new, but the workflows handle search-before-create.

NEVER say "I can't proceed because X doesn't exist." If a search finds nothing, CREATE what's needed.

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
- **VAT:** Standard Norwegian VAT is 25%. ALWAYS use vatRatePercent: 25 unless the prompt \
  explicitly says "exempt"/"fritatt"/"exonéré"/"0% MVA"/"sin impuestos". \
  IMPORTANT: "sin IVA"/"ohne MwSt"/"excl MVA"/"hors TVA"/"eksklusiv MVA" means the PRICE \
  is stated excluding VAT — it does NOT mean 0% VAT. The 25% rate still applies. \
  Only use vatRatePercent: 0 when the prompt explicitly says the service is TAX EXEMPT.
- For tasks that don't match any workflow, use suggested_workflow: "fallback".
- Most tasks need only 1 step — many workflows handle prerequisites internally.
- **KEEP PLANS SHORT: maximum 5 steps.** For CSV/bulk tasks (bank reconciliation, batch processing), \
  create ONE high-level step per category (e.g., "process all customer payments", "process all supplier payments"), \
  NOT one step per row. The sub-agent will loop through the data within each step.
- For employment contracts (arbeidskontrakt/tilbudsbrev), use the register_employment workflow — \
  it handles employee creation, department, employment details, salary, and working hours in ONE call.
"""


async def chief_plan(prompt: str, files: list) -> tuple[str, list[dict]]:
    """Chief produces a plan with thinking. Returns (thinking, steps)."""
    today = date.today().isoformat()
    catalog = build_workflow_catalog()
    system = PLAN_PROMPT.format(today=today, workflow_catalog=catalog)

    content = build_content(prompt, files)
    raw = await complete(system, content, max_tokens=8192)
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
