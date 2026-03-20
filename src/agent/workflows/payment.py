import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.payment")


async def _find_payment_type_id(client: TripletexClient) -> int | None:
    """Get the first available payment type ID."""
    result = await client.get("/invoice/paymentType", params={"count": "1"})
    types = result.get("values", [])
    if types:
        return types[0]["id"]
    return None


async def _find_invoice_id(data: dict, client: TripletexClient) -> int | None:
    """Find an invoice by number or other reference."""
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


async def register_payment(data: dict, client: TripletexClient) -> dict:
    """Register a payment on an existing invoice. PUT /invoice/{id}/:payment."""

    invoice_id = await _find_invoice_id(data, client)
    if not invoice_id:
        logger.error("No invoice found for payment registration: %s", data)
        return {"error": "Invoice not found"}

    # Get payment type ID
    payment_type_id = data.get("paymentTypeId")
    if not payment_type_id:
        payment_type_id = await _find_payment_type_id(client)
    if not payment_type_id:
        logger.error("No payment type found")
        return {"error": "No payment type available"}

    params = {
        "id": str(invoice_id),
        "paymentDate": data.get("paymentDate", data.get("date", "")),
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(data.get("amount", data.get("paidAmount", 0))),
    }

    if data.get("paidAmountCurrency"):
        params["paidAmountCurrency"] = str(data["paidAmountCurrency"])

    logger.info("Registering payment on invoice %d: amount=%s, date=%s",
                invoice_id, params["paidAmount"], params["paymentDate"])
    result = await client.put(f"/invoice/{invoice_id}/:payment", params=params)

    if result.get("value", {}).get("id"):
        logger.info("Payment registered successfully on invoice %d", invoice_id)
    else:
        logger.error("Failed to register payment: %s", result)

    return result
