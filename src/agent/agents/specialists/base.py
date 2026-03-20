"""
Shared infrastructure for all specialist agents.

All specialists share the same tools and execute_tool function.
Only the system prompt differs per domain.
"""

import json
import logging
from datetime import date

from ...api_spec import lookup as api_lookup
from ...llm import tool_use_loop
from ...tripletex import TripletexClient
from ...utils import build_content
from ...workflows import WORKFLOWS
from ...workflows.schemas import TASK_SCHEMAS

logger = logging.getLogger("agent.specialist")


# ---------------------------------------------------------------------------
# Shared tool definitions (same as Senior)
# ---------------------------------------------------------------------------

TOOLS = [
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
                    "description": "Data fields for the workflow — use EXACT field names from the spec",
                },
            },
            "required": ["workflow_name", "data"],
        },
    },
    {
        "name": "lookup_api",
        "description": (
            "Look up Tripletex API endpoint details from the OpenAPI spec. "
            "Use BEFORE guessing field names and AFTER any 4xx error. "
            "Examples: 'POST /employee', 'invoice', 'template enum'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Endpoint path, keyword, or enum query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "tripletex_get",
        "description": "GET request to Tripletex API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
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
        "description": "PUT request to Tripletex API. Some endpoints use query params instead of body (e.g. /order/{id}/:invoice).",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path"},
                "payload": {"type": "object", "description": "JSON body"},
                "params": {
                    "type": "object",
                    "description": "Query parameters (used by some action endpoints like /:invoice, /:payment)",
                    "additionalProperties": {"type": "string"},
                },
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


def build_workflow_catalog(workflow_names: list[str] | None = None) -> str:
    """Generate workflow reference, optionally filtered to specific workflows."""
    lines = []
    for task_type, schema in TASK_SCHEMAS.items():
        if workflow_names and task_type not in workflow_names:
            continue
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


def make_execute_tool(client: TripletexClient, specialist_name: str, state: dict):
    """Create an execute_tool function for a specialist.

    state dict is used to track last_workflow_result across tool calls.
    """
    spec_logger = logging.getLogger(f"agent.specialist.{specialist_name}")

    async def execute_tool(name: str, input_data: dict) -> dict:
        if name == "lookup_api":
            query = input_data.get("query", "")
            spec_logger.info("%s → lookup_api('%s')", specialist_name, query)
            return {"result": api_lookup(query)}

        if name == "execute_workflow":
            wf_name = input_data.get("workflow_name", "")
            data = input_data.get("data", {})
            if wf_name not in WORKFLOWS:
                return {"error": f"Unknown workflow '{wf_name}'. Available: {list(WORKFLOWS.keys())}"}
            spec_logger.info("%s → execute_workflow('%s', %s)", specialist_name, wf_name, json.dumps(data))
            try:
                result = await WORKFLOWS[wf_name](data, client)
                has_error = "error" in result or (isinstance(result.get("status"), int) and result["status"] >= 400)
                spec_logger.info("Workflow '%s' %s: %s", wf_name, "FAILED" if has_error else "OK", json.dumps(result))
                if not has_error:
                    state["last_workflow_result"] = result
                return result
            except Exception as e:
                spec_logger.exception("Workflow '%s' raised exception", wf_name)
                return {"error": str(e)}

        # Raw API tools
        endpoint = input_data.get("endpoint", "")
        params = input_data.get("params")
        payload = input_data.get("payload")
        spec_logger.info("%s → raw %s %s", specialist_name, name, endpoint)

        if name == "tripletex_get":
            return await client.get(endpoint, params=params)
        elif name == "tripletex_post":
            result = await client.post(endpoint, payload=payload)
            state["last_workflow_result"] = result
            return result
        elif name == "tripletex_put":
            result = await client.put(endpoint, payload=payload, params=params)
            state["last_workflow_result"] = result
            return result
        elif name == "tripletex_delete":
            return await client.delete(endpoint)
        else:
            return {"error": f"Unknown tool: {name}"}

    return execute_tool


async def run_specialist_loop(
    specialist_name: str,
    system_prompt: str,
    task_description: str,
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
    prior_results: str | None = None,
) -> dict:
    """Run a specialist agent loop with the given system prompt."""
    spec_logger = logging.getLogger(f"agent.specialist.{specialist_name}")

    content = build_content(prompt, files)

    # Add task description and prior results as user message context
    task_context = f"## Your Task\n{task_description}"
    if prior_results:
        task_context += f"\n\n## Results from Prior Steps\n{prior_results}"
    task_context += "\n\nExecute this task now. Be efficient and decisive."
    content.append({"type": "text", "text": task_context})

    state = {"last_workflow_result": {}}
    execute_tool = make_execute_tool(client, specialist_name, state)

    spec_logger.info("Starting %s specialist", specialist_name)
    result = await tool_use_loop(
        system=system_prompt,
        user_content=content,
        tools=TOOLS,
        execute_tool=execute_tool,
        max_iterations=12,
        deadline=deadline,
    )

    # Attach last workflow result so orchestrator can pass it to next step
    result["workflow_result"] = state["last_workflow_result"]
    return result
