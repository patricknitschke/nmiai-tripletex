"""
Chief Accountant agent — the solution architect and planner.

Responsibilities:
  - Reads the original prompt (multilingual) and produces a step-by-step plan
  - Answers sub-agent questions by re-reading the original prompt
  - Reviews progress between steps and adapts the plan
  - Maintains persistent memory (thinking + plan) across all interactions
"""

import json
import logging
from datetime import date

from ..api_spec import lookup as api_lookup
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


# ---------------------------------------------------------------------------
# Stage 2: Answer sub-agent questions
# ---------------------------------------------------------------------------

ANSWER_PROMPT = """\
You are a Chief Accountant AI. A sub-agent working on a Tripletex accounting task \
is asking you a question. You have access to the original task prompt and attachments.

Today's date: {today}

## Your Plan and Reasoning
{chief_memory}

## IMPORTANT: Fresh Environment
The Tripletex account starts EMPTY. If a resource (customer, product, employee) \
doesn't exist, it must be CREATED — this is expected, not an error. Guide your \
sub-agent to create what's needed. The workflows handle this automatically when \
given the right data.

## Rules
- Answer precisely and concisely. Extract exact values from the prompt.
- Do NOT guess — if the prompt doesn't contain the info, say so clearly.
- When providing field values, use the exact workflow field names.
- If the sub-agent reports an error, help them fix it — suggest the right field \
  names, data format, or alternative approach.
- Stay consistent with your original plan and reasoning above.

## Prior conversation with this sub-agent
{conversation_history}
"""


async def chief_answer(
    question: str,
    prompt: str,
    files: list,
    conversation_log: list[dict],
    chief_memory: str,
) -> str:
    """Chief answers a sub-agent's question with full plan + conversation memory."""
    today = date.today().isoformat()

    if conversation_log:
        history_lines = []
        for entry in conversation_log:
            history_lines.append(f"Sub-agent asked: {entry['question']}")
            history_lines.append(f"You answered: {entry['answer']}")
            history_lines.append("")
        conversation_history = "\n".join(history_lines)
    else:
        conversation_history = "No prior conversation yet."

    system = ANSWER_PROMPT.format(
        today=today,
        chief_memory=chief_memory,
        conversation_history=conversation_history,
    )

    content = build_content(prompt, files)

    # Auto-include relevant API spec info if question mentions field names or errors
    api_context = ""
    question_lower = question.lower()
    if any(kw in question_lower for kw in ["field", "error", "failed", "422", "400", "404", "parameter", "schema", "endpoint"]):
        # Try to extract an endpoint or keyword from the question
        for endpoint_keyword in ["/employee", "/customer", "/invoice", "/order", "/product",
                                  "/department", "/project", "/travelExpense", "/ledger"]:
            if endpoint_keyword.lower().lstrip("/") in question_lower:
                api_context = f"\n\n## Verified API spec for reference:\n{api_lookup('POST ' + endpoint_keyword)}"
                break

    content.append({
        "type": "text",
        "text": f"{api_context}\n\n---\nSub-agent's new question:\n{question}",
    })

    answer = await complete(system, content)
    logger.info("Chief Q&A: Q='%s' → A='%s'", question, answer)
    return answer


# ---------------------------------------------------------------------------
# Stage 3: Review between steps
# ---------------------------------------------------------------------------

REVIEW_PROMPT = """\
You are a Chief Accountant AI reviewing progress on an accounting task.

The Tripletex account started EMPTY. Resources are created as needed — this is normal.

Original task prompt is provided below along with the plan and results so far.
Determine if the remaining steps need adjustment based on what happened.

Reply with ONLY valid JSON:
{{
  "continue": true,
  "adjusted_steps": []
}}

If steps need adjustment, set continue to true and provide the adjusted remaining steps \
(each with "task" and "suggested_workflow" fields). \
If everything looks good, return continue: true with empty adjusted_steps. \
If the task is already complete, return continue: false.
"""


async def chief_review(
    prompt: str,
    files: list,
    plan: list[dict],
    completed_steps: list[dict],
    remaining_steps: list[dict],
) -> tuple[bool, list[dict]]:
    """Chief reviews progress and optionally adjusts remaining steps."""
    content = build_content(prompt, files)
    context_text = (
        f"\n---\nOriginal plan: {json.dumps(plan)}"
        f"\n\nCompleted steps and results:\n{json.dumps(completed_steps, indent=2)}"
        f"\n\nRemaining steps:\n{json.dumps(remaining_steps, indent=2)}"
        f"\n\nShould we continue with the remaining steps as-is, adjust them, or stop?"
    )
    content.append({"type": "text", "text": context_text})

    raw = await complete(REVIEW_PROMPT, content)
    logger.info("Chief review raw: %s", raw)

    try:
        review = parse_json(raw)
        should_continue = review.get("continue", True)
        adjusted = review.get("adjusted_steps", [])
        if not should_continue:
            logger.info("Chief review decision: STOP (task complete)")
        elif adjusted:
            logger.info("Chief review decision: CONTINUE with %d adjusted steps", len(adjusted))
        else:
            logger.info("Chief review decision: CONTINUE as planned")
        return should_continue, adjusted if adjusted else remaining_steps
    except Exception:
        logger.warning("Failed to parse Chief review (raw: %s), continuing with original plan", raw[:200])
        return True, remaining_steps
