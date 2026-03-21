"""
Senior Accountant — single-agent fast path.

A single agent loop that has everything it needs: the full prompt,
workflow tools, API lookup, and raw API tools. No planning layer,
no ask_chief round-trips. Just reads the prompt and does the work.

Handles ~80% of competition tasks in 2-3 iterations.
"""

import json
import logging
from datetime import date

from ..api_spec import lookup as api_lookup
from ..llm import tool_use_loop
from ..tripletex import TripletexClient
from ..utils import build_content
from ..workflows import WORKFLOWS
from ..workflows.schemas import TASK_SCHEMAS

logger = logging.getLogger("agent.senior")


# ---------------------------------------------------------------------------
# Workflow catalog (same as chief.py)
# ---------------------------------------------------------------------------

def _build_workflow_catalog() -> str:
    """Generate workflow reference for the system prompt."""
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
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a Senior Accountant AI that solves Tripletex accounting tasks directly.
The task prompt may be in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French.

Today's date: {today}

## CRITICAL: Environment Rules
The Tripletex account may be EMPTY or may have PRE-EXISTING data depending on the task.

**ALWAYS search before creating:** Before creating an invoice, customer, or other resource, \
check if it already exists using tripletex_get. For example:
- "Register payment on invoice" → FIRST search for the existing invoice (GET /invoice with customer name/number). \
  Only create a new invoice if none is found.
- "Issue credit note for invoice" → FIRST search for the existing invoice. Only create if not found.
- "Create customer X" → The customer might already exist. The workflow handles search-before-create.

**If resources don't exist, create them.** Many tasks require creating prerequisites from scratch \
(customers, employees, products, bank accounts). Workflows handle this automatically.

## Your Workflows (PREFER these over raw API calls)
{workflow_catalog}

## Your Tools
1. **execute_workflow** — Run a pre-built workflow. PREFERRED. Use EXACT field names from \
the specs above. Workflows handle dependency lookups (departments, VAT, bank accounts) automatically.

2. **lookup_api** — Look up API endpoint schemas, field names, and enum values from the \
OpenAPI spec. Use this BEFORE guessing field names and AFTER any 4xx error. \
Examples: "POST /employee", "invoice", "template enum".

3. **tripletex_get/post/put/delete** — Raw API access. Use only when no workflow fits. \
Always use lookup_api first to get the correct endpoint schema.

## Rules
- Read the prompt carefully. Extract ALL data you need, then execute.
- Use EXACT field names from the workflow specs — do not rename them.
- Chain workflows when needed: create customer first, then invoice, then payment. \
  Pass IDs from one result to the next.
- Creating a SUPPLIER (leverandør/Lieferant/fournisseur) = create_customer with isSupplier: true.
- **VAT:** Standard Norwegian VAT is 25%. Always include vatRatePercent: 25 on order lines \
  unless the prompt explicitly states a different rate (15%, 12%, 0%) or says "exempt"/"fritatt"/"exonéré". \
  Prices stated as "excl MVA/sin IVA/ohne MwSt/hors TVA" are excluding VAT — the 25% will be added on top.
- If a call returns a 4xx error, use lookup_api to check correct fields, then retry ONCE.
- Do NOT guess field names. Use lookup_api or the workflow specs above.
- Be EFFICIENT and DECISIVE. Aim to complete the task in 3-5 tool calls.
- When done, stop calling tools and briefly confirm what you created.
"""


# ---------------------------------------------------------------------------
# Tool definitions
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
# Entry point
# ---------------------------------------------------------------------------

async def run_senior_accountant(
    prompt: str,
    files: list,
    client: TripletexClient,
    deadline: float | None = None,
    plan_preamble: str | None = None,
) -> dict:
    """Run the Senior Accountant — single agent, fast path.

    If plan_preamble is provided (hybrid mode), it's injected into the system prompt
    so the Senior can follow the Chief's strategic plan while executing directly.
    """

    today = date.today().isoformat()
    catalog = _build_workflow_catalog()
    system = SYSTEM_PROMPT.format(today=today, workflow_catalog=catalog)

    if plan_preamble:
        system += f"\n\n## Chief Accountant's Plan (follow this strategy)\n{plan_preamble}\n"

    content = build_content(prompt, files)

    async def execute_tool(name: str, input_data: dict) -> dict:
        if name == "lookup_api":
            query = input_data.get("query", "")
            logger.info("Senior → lookup_api('%s')", query)
            return {"result": api_lookup(query)}

        if name == "execute_workflow":
            wf_name = input_data.get("workflow_name", "")
            data = input_data.get("data", {})
            if wf_name not in WORKFLOWS:
                return {"error": f"Unknown workflow '{wf_name}'. Available: {list(WORKFLOWS.keys())}"}
            logger.info("Senior → execute_workflow('%s', %s)", wf_name, json.dumps(data))
            try:
                result = await WORKFLOWS[wf_name](data, client)
                has_error = "error" in result or (isinstance(result.get("status"), int) and result["status"] >= 400)
                logger.info("Workflow '%s' %s: %s", wf_name, "FAILED" if has_error else "OK", json.dumps(result))
                return result
            except Exception as e:
                logger.exception("Workflow '%s' raised exception", wf_name)
                return {"error": str(e)}

        # Raw API tools
        endpoint = input_data.get("endpoint", "")
        params = input_data.get("params")
        payload = input_data.get("payload")
        logger.info("Senior → raw %s %s", name, endpoint)

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

    logger.info("Starting Senior Accountant (single-agent fast path)")
    return await tool_use_loop(
        system=system,
        user_content=content,
        tools=TOOLS,
        execute_tool=execute_tool,
        max_iterations=15,
        deadline=deadline,
    )
