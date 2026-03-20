import logging

from ..tripletex import TripletexClient
from .customer import create_customer
from .product import create_product

logger = logging.getLogger("agent.workflows.invoice")


async def create_invoice(data: dict, client: TripletexClient) -> dict:
    """Create an invoice, creating customer/products first if needed."""

    # Step 1: Ensure customer exists
    customer_id = data.get("customerId")
    if not customer_id and data.get("customer"):
        logger.info("Customer not found by ID — creating from extracted data...")
        customer_result = await create_customer(data["customer"], client)
        customer_id = customer_result.get("value", {}).get("id")

    if not customer_id:
        logger.error("No customer ID available for invoice creation")
        return {"error": "No customer for invoice"}

    # Step 2: Build invoice lines
    order_lines = []
    for line in data.get("lines", []):
        order_line = {}

        # If product needs to be created
        product_id = line.get("productId")
        if not product_id and line.get("product"):
            product_result = await create_product(line["product"], client)
            product_id = product_result.get("value", {}).get("id")

        if product_id:
            order_line["product"] = {"id": product_id}

        if line.get("description"):
            order_line["description"] = line["description"]
        if line.get("quantity") is not None:
            order_line["count"] = line["quantity"]
        if line.get("unitPrice") is not None:
            order_line["unitCostCurrency"] = line["unitPrice"]

        order_lines.append(order_line)

    # Step 3: Create the invoice
    payload = {
        "customer": {"id": customer_id},
        "invoiceDate": data.get("invoiceDate", ""),
        "invoiceDueDate": data.get("dueDate", ""),
        "orders": [],
    }

    # Create an order first (Tripletex invoices are created from orders)
    order_payload = {
        "customer": {"id": customer_id},
        "orderDate": data.get("invoiceDate", ""),
        "deliveryDate": data.get("dueDate", ""),
        "orderLines": order_lines,
    }

    logger.info("Creating order for invoice (customer_id=%d, %d lines)", customer_id, len(order_lines))
    order_result = await client.post("/order", order_payload)
    order_id = order_result.get("value", {}).get("id")

    if not order_id:
        logger.error("Failed to create order: %s", order_result)
        return order_result

    logger.info("Order created with ID: %d — now creating invoice...", order_id)

    # Create invoice from order
    invoice_payload = {
        "invoiceDate": data.get("invoiceDate", ""),
        "invoiceDueDate": data.get("dueDate", ""),
    }
    result = await client.post(f"/order/{order_id}/:invoice", invoice_payload)

    invoice_id = result.get("value", {}).get("id")
    if invoice_id:
        logger.info("Invoice created with ID: %d", invoice_id)
    else:
        logger.error("Failed to create invoice: %s", result)

    return result
