import base64
import json
import logging

from .models import FileAttachment
from .llm import complete

logger = logging.getLogger("agent.interpreter")

SYSTEM_PROMPT = """\
You are a task interpreter for Tripletex accounting software. Your job is to:
1. Read the accounting task prompt (which may be in Norwegian, English, Spanish, Portuguese, Nynorsk, German, or French)
2. Identify the task type
3. Extract all structured data needed to execute the task

Return a JSON object with:
- "task_type": one of the known task types below
- "data": an object with all extracted fields relevant to the task

Known task types:
- "create_employee" — Create a new employee. Extract: firstName, lastName, email, role (if mentioned, e.g. "administrator", "accountant")
- "create_customer" — Create a customer. Extract: name, email, phone, isCustomer (true), isSupplier, address fields
- "create_product" — Create a product. Extract: name, number, cost, price, vatType
- "create_invoice" — Create an invoice. Extract: customer info, invoice lines (product, quantity, unitPrice), invoiceDate, dueDate
- "register_payment" — Register a payment on an invoice. Extract: invoice reference, amount, date, paymentType
- "create_credit_note" — Issue a credit note. Extract: original invoice reference, reason, lines
- "create_travel_expense" — Register a travel expense. Extract: employee info, title, amount, costCategory, description
- "delete_travel_expense" — Delete a travel expense. Extract: identifying info for the expense
- "create_project" — Create a project. Extract: name, number, customer info, projectManager
- "create_department" — Create a department. Extract: name, departmentNumber
- "delete_entry" — Delete or reverse an incorrect entry. Extract: entry type, identifying info
- "create_order" — Create an order. Extract: customer info, order lines, orderDate, deliveryDate
- "unknown" — If the task doesn't clearly match any known type

If the prompt mentions creating multiple resources (e.g., create customer AND then invoice), set task_type to the PRIMARY action and include dependent resources in the data.

For file attachments (PDFs, images), extract any relevant data you can see (invoice numbers, amounts, customer names, line items, etc.) and include it in the data object.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, just the JSON object.
"""


async def interpret_task(
    prompt: str, files: list[FileAttachment]
) -> dict:
    """Use LLM to interpret the task prompt and extract structured data."""

    # Build content blocks
    content: list[dict] = [{"type": "text", "text": prompt}]

    # Add file attachments
    for f in files:
        if f.mime_type.startswith("image/"):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": f.mime_type,
                    "data": f.content_base64,
                },
            })
            logger.info("Attached image: %s", f.filename)
        elif f.mime_type == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": f.content_base64,
                },
            })
            logger.info("Attached PDF: %s", f.filename)
        else:
            try:
                text_content = base64.b64decode(f.content_base64).decode("utf-8")
                content.append(
                    {"type": "text", "text": f"--- File: {f.filename} ---\n{text_content}"}
                )
                logger.info("Attached text file: %s", f.filename)
            except Exception:
                logger.warning("Could not decode file: %s", f.filename)

    raw = await complete(SYSTEM_PROMPT, content)
    logger.info("LLM raw response: %s", raw[:500])

    # Parse JSON response
    try:
        result = json.loads(raw)
        logger.info("Parsed task_type: %s", result.get("task_type"))
        return result
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code blocks
        if "```" in raw:
            json_str = raw.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            result = json.loads(json_str.strip())
            logger.info("Parsed task_type (from code block): %s", result.get("task_type"))
            return result
        logger.error("Failed to parse LLM response as JSON: %s", raw[:200])
        return {"task_type": "unknown", "data": {}, "raw_response": raw}
