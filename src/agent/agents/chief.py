"""
Chief Accountant agent — the solution architect and planner.

Reads the original prompt (multilingual) and produces a step-by-step plan
for specialist agents to execute in hybrid mode.
"""

import json
import logging
import re
from datetime import date

import os

from ..llm import complete
from ..utils import build_content, parse_json
from ..workflows.schemas import TASK_SCHEMAS

# Fast model for planning — no need for the heavy model, Chief just outputs small JSON
CHIEF_MODEL = os.environ.get("CHIEF_MODEL", "gemini-2.5-flash")

logger = logging.getLogger("agent.chief")


def _extract_thinking(raw: str) -> str:
    """Best-effort extract of the thinking field from truncated/broken JSON."""
    m = re.search(r'"thinking"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    return m.group(1) if m else ""


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
- **"Overdue invoice" / "Mahngebühr" / "late fee" / "reminder fee"** → MUST use find_overdue_invoices \
  as the FIRST step. This finds the real customer and invoice. NEVER invent a customer name like \
  "Musterkunde GmbH" — the customer ALREADY EXISTS in Tripletex with a real overdue invoice. \
  Pass the customerName and invoiceId from the find result to ALL subsequent steps.

NEVER say "I can't proceed because X doesn't exist." If a search finds nothing, CREATE what's needed.

## Available Workflows
{workflow_catalog}

## Your Process
1. **DESIGN** a task list in 20s.
   - Which resources (invoices, customers, projects, employees) need to exist for this task?
   - What is the correct order of operations?
   - Which workflows handle prerequisite creation automatically vs which need explicit steps?

## Output Format
Return ONLY valid JSON:
{{
  "thinking": "Your reasoning about dependencies, order of operations, and approach",
  "steps": [
    {{
      "task": "Short description of the task to perform",
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
- **Overdue invoice / reminder fee tasks** require this EXACT order: \
  (1) find_overdue_invoices to discover the real invoice + customer, \
  (2) send_reminder with includeCharge=true (pass invoiceId from step 1). This uses \
  PUT /invoice/:createReminder which handles the reminder charge, accounting entries \
  (debit 1500/credit 3400), and sending to the customer ALL in one call. \
  (3) register_payment partial payment on the overdue invoice (pass invoiceId from step 1). \
  DO NOT use create_voucher + create_invoice for reminders — that's 3 extra writes and risks \
  double-posting. The send_reminder workflow is purpose-built for this. \
  Norwegian reminder fees (purregebyr) are VAT-exempt (0%). If the task specifies a charge \
  amount (e.g., 65 NOK), pass it as chargeAmount.
- **KEEP PLANS SHORT: maximum 5 steps.** For CSV/bulk tasks (bank reconciliation, batch processing), \
  create ONE high-level step per category (e.g., "process all customer payments", "process all supplier payments"), \
  NOT one step per row. The sub-agent will loop through the data within each step.
- For employment contracts (arbeidskontrakt/tilbudsbrev), use the register_employment workflow — \
  it handles employee creation, department, employment details, salary, and working hours in ONE call.
- If the prompt mentions a START DATE for an employee (tiltredelse/startdato/fecha de inicio/date de début/ \
  data de início/Startdatum/Anfangsdatum/start date), ALWAYS use register_employment instead of create_employee.
- **Time registration dates:** Newly created projects default to startDate=today. Time entries CANNOT be \
  registered before the project start date. Tell the sub-agent to register hours on today's date (or split \
  across today and future dates if needed). NEVER tell them to use past dates for a new project.
- **Project manager:** When the prompt specifies who the project manager is, pass their email as \
  projectManagerEmail to the create_project workflow so the correct person is set as manager.
- **Month-end/year-end closing plans:** For tasks with multiple journal entries, your plan MUST enforce this order: \
  (1) gather all accounts and run ONE GET /ledger/account with comma-separated numbers, \
  (2) create any missing accounts with POST /ledger/account, \
  (3) post one voucher per journal entry, \
  (4) if asked to verify trial balance ("saldobalanse går i null"), run GET /balanceSheet for the target month \
  and confirm total debits equal total credits. \
  Never invent missing amounts; instruct the sub-agent to derive from payroll/ledger GET data or report missing data. \
  For linear depreciation, use depreciation expense debit (e.g. 6030) and accumulated depreciation credit (e.g. 1209), \
  not the gross asset account (1200).
"""


async def chief_plan(prompt: str, files: list) -> tuple[str, list[dict]]:
    """Chief produces a plan with thinking. Returns (thinking, steps)."""
    today = date.today().isoformat()
    catalog = build_workflow_catalog()
    system = PLAN_PROMPT.format(today=today, workflow_catalog=catalog)

    content = build_content(prompt, files)
    raw = await complete(system, content, max_tokens=4096, model=CHIEF_MODEL)

    # Retry once if Chief returned empty (gemini sometimes returns blank)
    if not raw or not raw.strip():
        logger.warning("Chief returned empty response, retrying once...")
        raw = await complete(system, content, max_tokens=4096, model=CHIEF_MODEL)

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
        # Retry once on parse failure (truncated JSON, etc.)
        logger.warning("Failed to parse Chief plan, retrying once...")
        raw = await complete(system, content, max_tokens=4096, model=CHIEF_MODEL)
        logger.info("Chief plan retry raw: %s", raw)
        try:
            plan = parse_json(raw)
            thinking = plan.get("thinking", "")
            steps = plan.get("steps", [])
            logger.info("Chief retry succeeded with %d step(s)", len(steps))
            return thinking, steps
        except Exception:
            # Salvage thinking from truncated JSON if possible
            thinking = _extract_thinking(raw)
            logger.error("Failed to parse Chief plan after retry, returning empty steps (thinking=%s)", bool(thinking))
            return thinking, []
