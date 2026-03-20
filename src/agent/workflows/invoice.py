import logging
from datetime import date

from ..tripletex import TripletexClient
from .customer import create_customer

logger = logging.getLogger("agent.workflows.invoice")


def _today() -> str:
    return date.today().isoformat()


async def _ensure_customer(data: dict, client: TripletexClient) -> int | None:
    """Get or create a customer, return its ID."""
    customer_id = data.get("customerId")
    if customer_id:
        return customer_id

    if data.get("customer"):
        result = await create_customer(data["customer"], client)
        return result.get("value", {}).get("id")

    # Try to find by name
    customer_name = data.get("customerName")
    if customer_name:
        search = await client.get("/customer", params={"name": customer_name, "count": "1"})
        customers = search.get("values", [])
        if customers:
            return customers[0]["id"]
        # Create minimal customer
        result = await create_customer({"name": customer_name}, client)
        return result.get("value", {}).get("id")

    return None


def _build_order_lines(lines: list[dict]) -> list[dict]:
    """Build order lines from extracted line data."""
    order_lines = []
    for line in lines:
        ol = {}

        product = line.get("product")
        product_id = line.get("productId")
        if product_id:
            ol["product"] = {"id": product_id}
        elif isinstance(product, dict) and "id" in product:
            ol["product"] = product
        elif isinstance(product, int):
            ol["product"] = {"id": product}
        elif isinstance(product, str):
            # LLM returned product name as string — use as description
            if not line.get("description"):
                ol["description"] = product

        if line.get("description"):
            ol["description"] = line["description"]
        if line.get("quantity") is not None or line.get("count") is not None:
            ol["count"] = line.get("count", line.get("quantity"))
        if line.get("unitPrice") is not None or line.get("unitPriceExcludingVatCurrency") is not None:
            ol["unitPriceExcludingVatCurrency"] = line.get("unitPriceExcludingVatCurrency", line.get("unitPrice"))

        vat = line.get("vatType") or line.get("vatTypeId")
        if vat is not None:
            ol["vatType"] = {"id": vat} if isinstance(vat, int) else vat

        order_lines.append(ol)
    return order_lines


async def create_order(data: dict, client: TripletexClient) -> dict:
    """Create an order in Tripletex."""
    customer_id = await _ensure_customer(data, client)
    if not customer_id:
        logger.error("No customer ID available for order creation")
        return {"error": "No customer for order"}

    order_lines = _build_order_lines(data.get("lines", data.get("orderLines", [])))

    payload = {
        "customer": {"id": customer_id},
        "orderDate": data.get("orderDate", _today()),
        "deliveryDate": data.get("deliveryDate", data.get("orderDate", _today())),
    }

    if order_lines:
        payload["orderLines"] = order_lines
    if data.get("invoiceComment"):
        payload["invoiceComment"] = data["invoiceComment"]
    if data.get("reference"):
        payload["reference"] = data["reference"]
    if data.get("receiverEmail"):
        payload["receiverEmail"] = data["receiverEmail"]

    logger.info("Creating order (customer_id=%d, %d lines)", customer_id, len(order_lines))
    result = await client.post("/order", payload)

    order_id = result.get("value", {}).get("id")
    if order_id:
        logger.info("Order created with ID: %d", order_id)
    else:
        logger.error("Failed to create order: %s", result)

    return result


async def create_invoice(data: dict, client: TripletexClient) -> dict:
    """Create an invoice. Flow: create order → invoice from order."""

    customer_id = await _ensure_customer(data, client)
    if not customer_id:
        logger.error("No customer ID available for invoice creation")
        return {"error": "No customer for invoice"}

    # Build order lines from invoice line data
    order_lines = _build_order_lines(data.get("lines", data.get("orderLines", [])))

    invoice_date = data.get("invoiceDate", _today())
    due_date = data.get("dueDate", data.get("invoiceDueDate", ""))

    # Step 1: Create order
    order_payload = {
        "customer": {"id": customer_id},
        "orderDate": invoice_date,
        "deliveryDate": due_date or invoice_date,
        "orderLines": order_lines,
    }
    if data.get("invoiceComment"):
        order_payload["invoiceComment"] = data["invoiceComment"]

    logger.info("Creating order for invoice (customer_id=%d, %d lines)", customer_id, len(order_lines))
    order_result = await client.post("/order", order_payload)
    order_id = order_result.get("value", {}).get("id")

    if not order_id:
        logger.error("Failed to create order: %s", order_result)
        return order_result

    # Step 2: Invoice from order — PUT /order/{id}/:invoice (query params)
    invoice_params = {"id": str(order_id), "invoiceDate": invoice_date}
    if data.get("sendToCustomer"):
        invoice_params["sendToCustomer"] = "true"

    logger.info("Creating invoice from order %d", order_id)
    result = await client.put(f"/order/{order_id}/:invoice", params=invoice_params)

    invoice_id = result.get("value", {}).get("id")
    if invoice_id:
        logger.info("Invoice created with ID: %d", invoice_id)
    else:
        logger.error("Failed to create invoice: %s", result)

    return result
