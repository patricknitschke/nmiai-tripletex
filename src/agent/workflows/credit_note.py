import logging
from datetime import date

from ..tripletex import TripletexClient
from .invoice import create_invoice
from .payment import _rank_invoice_match

logger = logging.getLogger("agent.workflows.credit_note")


async def _find_invoice_id(data: dict, client: TripletexClient) -> int | None:
    """Find an invoice by ID, number, or ranked multi-pass search."""
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

    # Fetch all non-credit-note invoices for ranked matching
    all_inv = await client.get("/invoice", params={
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": "2099-12-31",
        "count": "1000",
    })
    candidates = [
        inv for inv in all_inv.get("values", [])
        if not inv.get("isCreditNote") and not inv.get("isCredited")
    ]
    if not candidates:
        return None

    best = _rank_invoice_match(data, candidates)
    return best["id"] if best else None


async def create_credit_note(data: dict, client: TripletexClient) -> dict:
    """Create a credit note for an existing invoice. PUT /invoice/{id}/:createCreditNote."""

    invoice_id = await _find_invoice_id(data, client)
    if not invoice_id:
        # No existing invoice found — create one so we can credit it
        logger.info("No existing invoice found, creating one to credit")
        invoice_data = {}
        if data.get("customerName") or data.get("customerOrgNumber") or data.get("organizationNumber"):
            invoice_data["customer"] = {}
            if data.get("customerName"):
                invoice_data["customer"]["name"] = data["customerName"]
            if data.get("customerOrgNumber") or data.get("organizationNumber"):
                invoice_data["customer"]["organizationNumber"] = data.get("customerOrgNumber") or data["organizationNumber"]
        lines = data.get("orderLines") or data.get("lines") or []
        if not lines and data.get("description") and data.get("amount"):
            lines = [{"description": data["description"], "unitPriceExcludingVatCurrency": data["amount"], "count": 1}]
        if lines:
            invoice_data["orderLines"] = lines
        invoice_result = await create_invoice(invoice_data, client)
        invoice_id = invoice_result.get("value", {}).get("id")
        if not invoice_id:
            logger.error("Failed to create invoice for credit note: %s", invoice_result)
            return {"error": "Could not find or create invoice for credit note"}

    params = {
        "id": str(invoice_id),
        "date": data.get("date", date.today().isoformat()),
    }

    if data.get("comment"):
        params["comment"] = data["comment"]
    if data.get("sendToCustomer") is not None:
        params["sendToCustomer"] = str(data["sendToCustomer"]).lower()

    logger.info("Creating credit note for invoice %d (date=%s)", invoice_id, params["date"])
    result = await client.put(f"/invoice/{invoice_id}/:createCreditNote", params=params)

    credit_id = result.get("value", {}).get("id")
    if credit_id:
        logger.info("Credit note created with ID: %d", credit_id)
    else:
        logger.error("Failed to create credit note: %s", result)

    return result
