"""
Sub-agent — the executor that carries out individual steps.

Responsibilities:
  - Receives a task description + workflow spec from the Chief
  - Pulls specific data from the Chief via ask_chief tool
  - Executes workflows or raw API calls
  - Reports results back to the orchestrator
"""

import json
import logging

from ..llm import tool_use_loop
from ..tripletex import TripletexClient
from ..workflows import WORKFLOWS
from ..workflows.schemas import TASK_SCHEMAS
from .chief import chief_answer

logger = logging.getLogger("agent.sub_agent")


# ---------------------------------------------------------------------------
# Workflow spec builder (for sub-agent's system prompt)
# ---------------------------------------------------------------------------

def build_workflow_spec(workflow_name: str) -> str:
    """Build the exact field spec for a single workflow."""
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
# Tool definitions (Anthropic format)
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
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
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
# Run a sub-agent for a single step
# ---------------------------------------------------------------------------

async def run_sub_agent(
    task_description: str,
    suggested_workflow: str,
    prior_context: str,
    prompt: str,
    files: list,
    client: TripletexClient,
    chief_memory: str,
) -> dict:
    """Run a sub-agent to execute a single step. Returns tool_use_loop result."""

    if suggested_workflow == "fallback":
        workflow_spec = (
            "## No single pre-built workflow covers this entire task\n"
            "Use raw Tripletex API tools (tripletex_get/post/put/delete) for the parts "
            "that don't match a workflow.\n"
            "However, you CAN still use execute_workflow for known sub-tasks like "
            "creating employees, customers, products, etc. — check if a workflow exists "
            "before resorting to raw API calls.\n"
            "Ask the Chief for guidance on which approach to use."
        )
    else:
        workflow_spec = build_workflow_spec(suggested_workflow)
    system = SYSTEM_PROMPT.format(
        task_description=task_description,
        workflow_spec=workflow_spec,
        prior_context=prior_context,
    )

    content = [{"type": "text", "text": f"Execute this task: {task_description}"}]

    # Conversation memory for this step's Chief ↔ sub-agent pair
    conversation_log: list[dict] = []
    # Tool call trace for post-mortem analysis
    tool_trace: list[dict] = []
    # Last successful workflow result (for passing IDs to next step)
    last_workflow_result: dict = {}

    async def execute_tool(name: str, input_data: dict) -> dict:
        if name == "ask_chief":
            question = input_data.get("question", "")
            logger.info("Sub-agent asks Chief: %s", question)
            answer = await chief_answer(question, prompt, files, conversation_log, chief_memory)
            conversation_log.append({"question": question, "answer": answer})
            tool_trace.append({"tool": "ask_chief", "ok": True})
            return {"answer": answer}

        if name == "execute_workflow":
            wf_name = input_data.get("workflow_name", "")
            data = input_data.get("data", {})
            if wf_name not in WORKFLOWS:
                tool_trace.append({"tool": "execute_workflow", "workflow": wf_name, "ok": False, "error": "unknown workflow"})
                return {"error": f"Unknown workflow '{wf_name}'. Available: {list(WORKFLOWS.keys())}"}
            logger.info("Sub-agent → execute_workflow('%s', %s)", wf_name, json.dumps(data))
            try:
                result = await WORKFLOWS[wf_name](data, client)
                has_error = "error" in result
                logger.info("Workflow '%s' %s: %s", wf_name, "FAILED" if has_error else "OK", json.dumps(result))
                tool_trace.append({"tool": "execute_workflow", "workflow": wf_name, "ok": not has_error})
                if not has_error:
                    last_workflow_result.clear()
                    last_workflow_result.update(result)
                return result
            except Exception as e:
                logger.exception("Workflow '%s' raised exception", wf_name)
                tool_trace.append({"tool": "execute_workflow", "workflow": wf_name, "ok": False, "error": str(e)})
                return {"error": str(e)}

        # Raw API tools (fallback)
        endpoint = input_data.get("endpoint", "")
        params = input_data.get("params")
        payload = input_data.get("payload")
        logger.info("Sub-agent → raw %s %s (params=%s)", name, endpoint, params)
        tool_trace.append({"tool": name, "endpoint": endpoint})

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

    result = await tool_use_loop(
        system=system,
        user_content=content,
        tools=SUB_AGENT_TOOLS,
        execute_tool=execute_tool,
        max_iterations=15,
    )

    # Performance summary
    workflow_attempts = [t for t in tool_trace if t["tool"] == "execute_workflow"]
    workflow_failures = [t for t in workflow_attempts if not t["ok"]]
    raw_api_calls = [t for t in tool_trace if t["tool"].startswith("tripletex_")]
    logger.info(
        "Sub-agent summary: %d tool calls [%d ask_chief, %d workflow (%d failed), %d raw API]",
        len(tool_trace), len(conversation_log), len(workflow_attempts),
        len(workflow_failures), len(raw_api_calls),
    )
    if workflow_failures:
        logger.warning("Sub-agent workflow failures: %s", workflow_failures)

    result["chief_qas"] = len(conversation_log)
    result["tool_trace"] = tool_trace
    result["workflow_result"] = _extract_key_fields(last_workflow_result)
    return result


def _extract_key_fields(result: dict) -> dict:
    """Extract IDs and key fields from workflow result for passing to next step."""
    value = result.get("value", {})
    if not value:
        return {}
    keys = {}
    for field in ["id", "invoiceNumber", "customerNumber", "supplierNumber",
                   "name", "displayName", "employeeNumber", "number"]:
        if field in value and value[field]:
            keys[field] = value[field]
    return keys
