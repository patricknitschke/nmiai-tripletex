import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.credit_note")


async def _find_invoice_id(data: dict, client: TripletexClient) -> int | None:
    """Find an invoice by explicit ID or invoice number. No fuzzy matching."""
    invoice_id = data.get("invoiceId")
    if invoice_id:
        return invoice_id

    invoice_number = data.get("invoiceNumber")
    if invoice_number:
        result = await client.get("/invoice", params={
            "invoiceNumber": str(invoice_number),
            "invoiceDateFrom": "2000-01-01",
            "invoiceDateTo": "2099-12-31",
            "count": "1",
        })
        invoices = result.get("values", [])
        if invoices:
            return invoices[0]["id"]

    return None


async def create_credit_note(data: dict, client: TripletexClient) -> dict:
    """Create a credit note for an existing invoice. PUT /invoice/{id}/:createCreditNote."""

    invoice_id = await _find_invoice_id(data, client)
    if not invoice_id:
        logger.error("No invoice found — invoiceId or invoiceNumber is required")
        return {"error": "Cannot create credit note: provide a valid invoiceId or invoiceNumber for the invoice to credit."}

    params = {
        "date": data.get("date", date.today().isoformat()),
    }

    if data.get("comment"):
        params["comment"] = data["comment"]
    if data.get("sendToCustomer") is not None:
        params["sendToCustomer"] = str(data["sendToCustomer"]).lower()
    if data.get("creditNoteEmail"):
        params["creditNoteEmail"] = data["creditNoteEmail"]
    if data.get("sendType"):
        params["sendType"] = data["sendType"]

    logger.info("Creating credit note for invoice %d (date=%s)", invoice_id, params["date"])
    result = await client.put(f"/invoice/{invoice_id}/:createCreditNote", params=params)

    credit_id = result.get("value", {}).get("id")
    if credit_id:
        logger.info("Credit note created with ID: %d", credit_id)
    else:
        logger.error("Failed to create credit note: %s", result)

    return result
