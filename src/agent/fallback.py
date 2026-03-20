import base64
import json
import logging

from .llm import tool_use_loop
from .tripletex import TripletexClient

logger = logging.getLogger("agent.fallback")

SYSTEM_PROMPT = """\
You are an accounting agent with access to the Tripletex REST API v2. Complete the task described by the user.

You have these tools available:
- tripletex_get: Make a GET request to a Tripletex API endpoint
- tripletex_post: Make a POST request to create a resource
- tripletex_put: Make a PUT request to update a resource
- tripletex_delete: Make a DELETE request to delete a resource

Key Tripletex API endpoints:
- /employee — employees (firstName, lastName, email)
- /customer — customers (name, email, isCustomer)
- /product — products (name, priceExcludingVatCurrency)
- /order — orders (customer.id, orderDate, orderLines)
- /order/{id}/:invoice — create invoice from order
- /invoice — invoices
- /invoice/{id}/:payment — register payment
- /invoice/{id}/:createCreditNote — create credit note
- /department — departments (name, departmentNumber)
- /project — projects (name, projectManager.id, customer.id)
- /travelExpense — travel expenses (employee.id)
- /travelExpense/cost — travel expense cost lines
- /ledger/voucher — accounting vouchers

Auth is handled automatically. Just specify the endpoint path (e.g., "/employee").

Be EFFICIENT: make the minimum number of API calls needed. Avoid unnecessary GET calls.
Do NOT make exploratory calls — if you know the endpoint, call it directly.
"""

TOOLS = [
    {
        "name": "tripletex_get",
        "description": "GET request to Tripletex API. Use for fetching/listing resources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path, e.g. /employee"},
                "params": {
                    "type": "object",
                    "description": "Query parameters (optional)",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["endpoint"],
        },
    },
    {
        "name": "tripletex_post",
        "description": "POST request to Tripletex API. Use for creating resources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path, e.g. /employee"},
                "payload": {"type": "object", "description": "JSON body to send"},
            },
            "required": ["endpoint", "payload"],
        },
    },
    {
        "name": "tripletex_put",
        "description": "PUT request to Tripletex API. Use for updating resources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path, e.g. /employee/123"},
                "payload": {"type": "object", "description": "JSON body to send"},
            },
            "required": ["endpoint", "payload"],
        },
    },
    {
        "name": "tripletex_delete",
        "description": "DELETE request to Tripletex API. Use for deleting resources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API endpoint path, e.g. /employee/123"},
            },
            "required": ["endpoint"],
        },
    },
]


async def run_fallback_agent(
    prompt: str,
    files: list,
    client: TripletexClient,
) -> dict:
    """Run an LLM agent loop for tasks without a pre-built workflow."""
    logger.warning("=" * 40)
    logger.warning("FALLBACK AGENT ACTIVATED")
    logger.warning("This task has no pre-built workflow. Using LLM agent loop.")
    logger.warning("Expect higher API call count and possible 4xx errors.")
    logger.warning("=" * 40)

    # Build initial content
    content: list[dict] = [{"type": "text", "text": prompt}]
    for f in files:
        if f.mime_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": f.mime_type, "data": f.content_base64},
            })
        elif f.mime_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": f.content_base64},
            })
        else:
            try:
                text = base64.b64decode(f.content_base64).decode("utf-8")
                content.append({"type": "text", "text": f"--- File: {f.filename} ---\n{text}"})
            except Exception:
                pass

    async def execute_tool(name: str, input_data: dict) -> dict:
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
            logger.error("Unknown tool: %s", name)
            return {"error": f"Unknown tool: {name}"}

    return await tool_use_loop(
        system=SYSTEM_PROMPT,
        user_content=content,
        tools=TOOLS,
        execute_tool=execute_tool,
        max_iterations=20,
    )
