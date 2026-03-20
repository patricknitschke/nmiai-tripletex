"""
Chief Accountant multi-agent orchestrator.

Architecture:
  1. Chief (planner) — reads the prompt, produces a high-level step-by-step plan
  2. Sub-agent (executor) — executes each step with its own reasoning loop
     - Has `ask_chief` tool to pull details from the original prompt on demand
     - Has `execute_workflow` tool to call pre-built workflows
     - Has raw API tools (get/post/put/delete) as fallback
  3. Chief (reviewer) — reviews sub-agent results between steps, adapts the plan

Conversation memory: each Chief ↔ sub-agent pair maintains a conversation log.
The Chief sees all prior Q&A when answering new questions, so it never forgets
what it already told the sub-agent.
"""

import json
import logging
from datetime import date

from .interpreter import _build_content, _parse_json
from .llm import complete, tool_use_loop
from .router import WORKFLOWS
from .schemas import TASK_SCHEMAS
from .tripletex import TripletexClient

logger = logging.getLogger("agent.orchestrator")


# ---------------------------------------------------------------------------
# Workflow catalog (auto-generated from TASK_SCHEMAS for Chief's context)
# ---------------------------------------------------------------------------

def _build_workflow_catalog() -> str:
    """Generate a concise workflow reference from TASK_SCHEMAS."""
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


def _build_workflow_spec(workflow_name: str) -> str:
    """Build the exact field spec for a single workflow (for the sub-agent prompt)."""
    schema = TASK_SCHEMAS.get(workflow_name)
    if not schema:
        return f"No field spec available for '{workflow_name}'. Use raw API tools."

    lines = [
        f"## Workflow: {workflow_name}",
        f"Endpoint: {schema['api_endpoint']}",
        f"Notes: {schema['notes']}",
        "",
        "Use these EXACT field names in the data object for execute_workflow:",
    ]
    for field in schema["fields"]:
        req = "REQUIRED" if field.get("required") else "optional"
        lines.append(f"  - {field['name']} ({field['type']}, {req}): {field['description']}")
        if "items" in field:
            lines.append(f"    Each item has:")
            for item in field["items"]:
                lines.append(f"      - {item['name']} ({item['type']}): {item['description']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stage 1: Chief plans
# ---------------------------------------------------------------------------

CHIEF_PLAN_PROMPT = """\
You are a Chief Accountant AI and solution architect. You receive accounting task \
prompts (in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French) \
and design a concrete solution plan for your sub-agents to execute.

Today's date: {today}

## CRITICAL: Fresh Empty Environment
The Tripletex account starts COMPLETELY EMPTY every time — no customers, no products, \
no employees, no bank accounts. Everything must be created from scratch. \
This is by design. When the task says "create an invoice for customer X", that means \
you need to ensure customer X is created as part of the solution.

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


async def _chief_plan(prompt: str, files: list) -> tuple[str, list[dict]]:
    """Ask the Chief to produce a plan. Returns (thinking, steps)."""
    today = date.today().isoformat()
    catalog = _build_workflow_catalog()
    system = CHIEF_PLAN_PROMPT.format(today=today, workflow_catalog=catalog)

    content = _build_content(prompt, files)
    raw = await complete(system, content)
    logger.info("Chief plan raw: %s", raw[:500])

    try:
        plan = _parse_json(raw)
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
# Chief answering sub-agent questions (ask_chief with conversation memory)
# ---------------------------------------------------------------------------

CHIEF_ANSWER_PROMPT = """\
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


async def _chief_answer(
    question: str,
    prompt: str,
    files: list,
    conversation_log: list[dict],
    chief_memory: str,
) -> str:
    """Chief answers a sub-agent's question, with full plan memory + conversation memory."""
    today = date.today().isoformat()

    # Build conversation history string
    if conversation_log:
        history_lines = []
        for entry in conversation_log:
            history_lines.append(f"Sub-agent asked: {entry['question']}")
            history_lines.append(f"You answered: {entry['answer']}")
            history_lines.append("")
        conversation_history = "\n".join(history_lines)
    else:
        conversation_history = "No prior conversation yet."

    system = CHIEF_ANSWER_PROMPT.format(
        today=today,
        chief_memory=chief_memory,
        conversation_history=conversation_history,
    )

    content = _build_content(prompt, files)
    content.append({
        "type": "text",
        "text": f"\n---\nSub-agent's new question:\n{question}",
    })

    answer = await complete(system, content)
    logger.info("Chief Q&A: Q='%s' → A='%s'", question, answer)
    return answer


# ---------------------------------------------------------------------------
# Stage 3: Chief reviews between steps
# ---------------------------------------------------------------------------

CHIEF_REVIEW_PROMPT = """\
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


async def _chief_review(
    prompt: str,
    files: list,
    plan: list[dict],
    completed_steps: list[dict],
    remaining_steps: list[dict],
) -> tuple[bool, list[dict]]:
    """Chief reviews progress and optionally adjusts remaining steps."""
    content = _build_content(prompt, files)
    context_text = (
        f"\n---\nOriginal plan: {json.dumps(plan)}"
        f"\n\nCompleted steps and results:\n{json.dumps(completed_steps, indent=2)}"
        f"\n\nRemaining steps:\n{json.dumps(remaining_steps, indent=2)}"
        f"\n\nShould we continue with the remaining steps as-is, adjust them, or stop?"
    )
    content.append({"type": "text", "text": context_text})

    raw = await complete(CHIEF_REVIEW_PROMPT, content)
    logger.info("Chief review: %s", raw[:300])

    try:
        review = _parse_json(raw)
        should_continue = review.get("continue", True)
        adjusted = review.get("adjusted_steps", [])
        return should_continue, adjusted if adjusted else remaining_steps
    except Exception:
        logger.warning("Failed to parse Chief review, continuing with original plan")
        return True, remaining_steps


# ---------------------------------------------------------------------------
# Sub-agent tool definitions
# ---------------------------------------------------------------------------

SUB_AGENT_TOOLS = [
    {
        "name": "ask_chief",
        "description": (
            "Ask the Chief Accountant a question about the original task prompt. "
            "The Chief has the full prompt text (possibly in Norwegian/German/French/etc.) "
            "and any file attachments (PDFs, images). The Chief remembers all prior Q&A "
            "in this conversation. Use this to get specific data values you need: "
            "names, amounts, dates, addresses, product details, org numbers, VAT rates, etc. "
            "Be specific in your question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Your question for the Chief, e.g. 'What are ALL the details I need to create this invoice?'",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "execute_workflow",
        "description": (
            "Execute a pre-built Tripletex workflow. PREFERRED over raw API calls. "
            "Workflows handle dependency lookups, resource creation, and error handling automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_name": {
                    "type": "string",
                    "enum": list(WORKFLOWS.keys()),
                    "description": "Which workflow to run",
                },
                "data": {
                    "type": "object",
                    "description": "Data fields for the workflow — use EXACT field names from the workflow spec",
                },
            },
            "required": ["workflow_name", "data"],
        },
    },
    {
        "name": "tripletex_get",
        "description": "GET request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path, e.g. /employee"},
                "params": {
                    "type": "object",
                    "description": "Query parameters",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["endpoint"],
        },
    },
    {
        "name": "tripletex_post",
        "description": "POST request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "payload": {"type": "object", "description": "JSON body"},
            },
            "required": ["endpoint", "payload"],
        },
    },
    {
        "name": "tripletex_put",
        "description": "PUT request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "payload": {"type": "object", "description": "JSON body"},
            },
            "required": ["endpoint"],
        },
    },
    {
        "name": "tripletex_delete",
        "description": "DELETE request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
            },
            "required": ["endpoint"],
        },
    },
]


# ---------------------------------------------------------------------------
# Sub-agent system prompt
# ---------------------------------------------------------------------------

SUB_AGENT_PROMPT = """\
You are an accounting sub-agent executing a specific task in Tripletex.

## IMPORTANT: The Tripletex account is EMPTY
No customers, products, or employees exist yet. This is normal — the workflows will \
create them when you provide the right data. Do NOT search for existing resources \
unless you specifically need an ID from a prior step.

## Your Task
{task_description}

{workflow_spec}

## Context from Prior Steps
{prior_context}

## Your Tools
1. **ask_chief** — Ask the Chief Accountant for details from the original task prompt. \
The Chief has the full prompt (possibly in Norwegian, German, etc.) and any file attachments. \
The Chief remembers all prior Q&A. Ask for specific data values you need.

2. **execute_workflow** — Run the workflow above. Use the EXACT field names from the spec. \
Workflows handle dependency lookups (departments, VAT types, etc.) automatically.

3. **tripletex_get/post/put/delete** — Raw Tripletex API access. Use only when no \
workflow fits or when you need to do something workflows don't support.

## Rules
- FIRST ask the Chief for ALL the data values you need in ONE comprehensive question, \
  THEN call the workflow with that data.
- Use the EXACT field names from the workflow spec above — do not rename or reformat them.
- Do not guess or make up values — ask the Chief.
- If a workflow returns an error, read it carefully. Ask the Chief for guidance if needed.
- Be EFFICIENT: ask one comprehensive question, then execute.
- When done, stop calling tools and briefly confirm what you created/did.
"""


# ---------------------------------------------------------------------------
# Entry point: orchestrate the full plan → execute → review loop
# ---------------------------------------------------------------------------

async def solve_task(
    prompt: str,
    files: list,
    client: TripletexClient,
) -> dict:
    """Run the multi-agent Chief Accountant orchestrator."""

    # Stage 1: Chief plans (with thinking)
    logger.info("=" * 40)
    logger.info("CHIEF ACCOUNTANT: Planning...")
    thinking, steps = await _chief_plan(prompt, files)

    if not steps:
        logger.warning("Chief produced empty plan, falling back")
        steps = [{"task": "Complete the accounting task", "suggested_workflow": "fallback"}]

    # Build persistent Chief memory — stays consistent across all sub-agent interactions
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

        # Build prior context string for the sub-agent
        prior_context = "None yet." if not completed_steps else json.dumps(
            [{"step": s["task"], "result_summary": s.get("result_summary", "completed")}
             for s in completed_steps],
            indent=2,
        )

        # Build sub-agent system prompt with workflow field spec
        workflow_spec = _build_workflow_spec(suggested_wf)
        sub_system = SUB_AGENT_PROMPT.format(
            task_description=task_desc,
            workflow_spec=workflow_spec,
            prior_context=prior_context,
        )

        # Sub-agent initial content (just the task — no raw prompt)
        sub_content = [{"type": "text", "text": f"Execute this task: {task_desc}"}]

        # Conversation memory for this step's Chief ↔ sub-agent pair
        conversation_log: list[dict] = []

        async def execute_tool(name: str, input_data: dict) -> dict:
            if name == "ask_chief":
                question = input_data.get("question", "")
                logger.info("Sub-agent asks Chief: %s", question)
                answer = await _chief_answer(question, prompt, files, conversation_log, chief_memory)
                # Persist to conversation memory
                conversation_log.append({"question": question, "answer": answer})
                return {"answer": answer}

            if name == "execute_workflow":
                wf_name = input_data.get("workflow_name", "")
                data = input_data.get("data", {})
                if wf_name not in WORKFLOWS:
                    return {"error": f"Unknown workflow '{wf_name}'. Available: {list(WORKFLOWS.keys())}"}
                logger.info("Sub-agent → execute_workflow('%s', %s)", wf_name, json.dumps(data))
                try:
                    result = await WORKFLOWS[wf_name](data, client)
                    logger.info("Workflow '%s' result: %s", wf_name, json.dumps(result))
                    return result
                except Exception as e:
                    logger.exception("Workflow '%s' raised exception", wf_name)
                    return {"error": str(e)}

            # Raw API tools
            endpoint = input_data.get("endpoint", "")
            params = input_data.get("params")
            payload = input_data.get("payload")

            if name == "tripletex_get":
                return await client.get(endpoint, params=params)
            elif name == "tripletex_post":
                return await client.post(endpoint, payload=payload)
            elif name == "tripletex_put":
                return await client.put(endpoint, payload=payload)
            elif name == "tripletex_delete":
                return await client.delete(endpoint)
            else:
                return {"error": f"Unknown tool: {name}"}

        # Run sub-agent
        sub_result = await tool_use_loop(
            system=sub_system,
            user_content=sub_content,
            tools=SUB_AGENT_TOOLS,
            execute_tool=execute_tool,
            max_iterations=15,
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
                     len(conversation_log))

        # Stage 3: Chief reviews (only if there are remaining steps)
        if remaining_steps:
            logger.info("CHIEF ACCOUNTANT: Reviewing progress...")
            should_continue, adjusted_remaining = await _chief_review(
                prompt, files, steps, completed_steps, remaining_steps,
            )
            if not should_continue:
                logger.info("Chief says task is complete, stopping early")
                break
            remaining_steps = adjusted_remaining

    logger.info("=" * 40)
    logger.info("CHIEF ACCOUNTANT: All steps complete (%d steps executed)", len(completed_steps))
    return {"status": "completed", "steps_executed": len(completed_steps)}
