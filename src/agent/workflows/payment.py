import logging
from datetime import date

from ..tripletex import TripletexClient
from .invoice import create_invoice

logger = logging.getLogger("agent.workflows.payment")


async def _find_payment_type_id(client: TripletexClient) -> int | None:
    """Get the first available payment type ID."""
    result = await client.get("/invoice/paymentType", params={"count": "1"})
    types = result.get("values", [])
    if types:
        return types[0]["id"]
    return None


async def _find_invoice(data: dict, client: TripletexClient) -> dict | None:
    """Find an invoice by ID, number, or by searching the customer's invoices. Returns full invoice dict."""
    invoice_id = data.get("invoiceId")
    if invoice_id:
        result = await client.get(f"/invoice/{invoice_id}")
        return result.get("value")

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
            return invoices[0]

    # Search by customer name or org number
    customer_name = data.get("customerName")
    org_number = data.get("customerOrgNumber") or data.get("organizationNumber")

    if customer_name or org_number:
        params = {"count": "1"}
        if org_number:
            params["organizationNumber"] = org_number
        elif customer_name:
            params["name"] = customer_name
        customers = await client.get("/customer", params=params)
        customer_list = customers.get("values", [])
        if customer_list:
            customer_id = customer_list[0]["id"]
            logger.info("Found customer %s (id=%d), searching their invoices", customer_name or org_number, customer_id)

            invoices = await client.get("/invoice", params={
                "customerId": str(customer_id),
                "invoiceDateFrom": "2000-01-01",
                "invoiceDateTo": "2099-12-31",
                "count": "100",
            })
            for inv in invoices.get("values", []):
                if not inv.get("isCreditNote") and not inv.get("isCredited"):
                    logger.info("Found non-credited invoice %d (amount=%s) for customer %d",
                                inv["id"], inv.get("amount"), customer_id)
                    return inv

    return None


async def register_payment(data: dict, client: TripletexClient) -> dict:
    """Register a payment on an invoice. Self-contained: searches for existing invoice, creates if needed."""

    invoice = await _find_invoice(data, client)

    # If no invoice found, create one
    if not invoice:
        logger.info("No existing invoice found, creating one for payment")
        invoice_data = {}
        if data.get("customerName") or data.get("customerOrgNumber") or data.get("organizationNumber"):
            invoice_data["customer"] = {}
            if data.get("customerName"):
                invoice_data["customer"]["name"] = data["customerName"]
            if data.get("customerOrgNumber") or data.get("organizationNumber"):
                invoice_data["customer"]["organizationNumber"] = data.get("customerOrgNumber") or data["organizationNumber"]
        lines = data.get("orderLines") or data.get("lines") or []
        if not lines and data.get("description") and data.get("amountExclVat"):
            lines = [{"description": data["description"], "unitPriceExcludingVatCurrency": data["amountExclVat"], "count": 1}]
        if lines:
            invoice_data["orderLines"] = lines
        invoice_result = await create_invoice(invoice_data, client)
        invoice = invoice_result.get("value")
        if not invoice:
            logger.error("Failed to create invoice for payment: %s", invoice_result)
            return {"error": "Could not find or create invoice for payment"}

    invoice_id = invoice["id"]

    # For "full payment", use the invoice's actual total amount (including VAT)
    paid_amount = data.get("paidAmount") or data.get("amount")
    if not paid_amount or data.get("fullPayment"):
        # Use the invoice's outstanding amount (includes VAT)
        paid_amount = invoice.get("amountOutstanding") or invoice.get("amount", 0)
        logger.info("Full payment: using invoice outstanding amount %s", paid_amount)

    # Get payment type ID
    payment_type_id = data.get("paymentTypeId")
    if not payment_type_id:
        payment_type_id = await _find_payment_type_id(client)
    if not payment_type_id:
        logger.error("No payment type found")
        return {"error": "No payment type available"}

    params = {
        "id": str(invoice_id),
        "paymentDate": data.get("paymentDate", data.get("date", date.today().isoformat())),
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(paid_amount),
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
