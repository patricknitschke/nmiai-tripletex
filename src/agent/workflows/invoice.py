import logging
from datetime import date, timedelta

from ..tripletex import TripletexClient
from .customer import create_customer

logger = logging.getLogger("agent.workflows.invoice")


def _today() -> str:
    return date.today().isoformat()


async def _ensure_bank_account(client: TripletexClient) -> None:
    """Ensure the company has a bank account registered (required for invoicing)."""
    if getattr(client, "_bank_account_ready", False):
        return

    # Check if account 1920 (standard Norwegian bank account) exists
    result = await client.get("/ledger/account", params={
        "number": "1920",
        "count": "1",
        "fields": "id,version,name,bankAccountNumber,isBankAccount",
    })
    accounts = result.get("values", [])
    desired_bank_number = "86011117947"

    if accounts:
        bank_number = str(accounts[0].get("bankAccountNumber") or "").strip()
        is_bank_account = accounts[0].get("isBankAccount")

        # Write-efficiency: if 1920 is already configured with a bank account number,
        # do not force an update to a hardcoded number.
        if bank_number and is_bank_account is not False:
            logger.info("Bank account 1920 already usable → skipping PUT")
            client._bank_account_ready = True
            return

    # Create or update account 1920 with the bank account number
    if accounts:
        # Account exists but lacks usable bank setup — patch it once.
        account_id = accounts[0]["id"]
        existing_bank_number = str(accounts[0].get("bankAccountNumber") or "").strip()
        bank_number_to_use = existing_bank_number or desired_bank_number
        logger.info("Updating account 1920 (id=%d) with bank account details", account_id)
        write_result = await client.put(f"/ledger/account/{account_id}", {
            "id": account_id,
            "version": accounts[0]["version"],
            "name": accounts[0].get("name", "Bank"),
            "number": 1920,
            "bankAccountNumber": bank_number_to_use,
            "isBankAccount": True,
        })
    else:
        # Create account 1920
        logger.info("Creating bank account (ledger account 1920)")
        write_result = await client.post("/ledger/account", {
            "name": "Bank",
            "number": 1920,
            "bankAccountNumber": desired_bank_number,
            "isBankAccount": True,
        })

    if not write_result.get("error"):
        client._bank_account_ready = True
    logger.info("Bank account registered")


async def _ensure_customer(data: dict, client: TripletexClient) -> int | None:
    """Get or create a customer, return its ID."""
    customer_id = data.get("customerId")
    if customer_id:
        return customer_id

    if data.get("customer"):
        customer_data = dict(data["customer"])  # copy to avoid mutating input
        # Merge customerName if customer object is missing name
        if not customer_data.get("name") and data.get("customerName"):
            customer_data["name"] = data["customerName"]
        result = await create_customer(customer_data, client)
        return result.get("value", {}).get("id")

    # Find or create by name — create_customer already searches first, so go directly
    customer_name = data.get("customerName")
    if customer_name:
        result = await create_customer({"name": customer_name}, client)
        return result.get("value", {}).get("id")

    return None


# Per-request VAT cache — reset for each new TripletexClient instance
_vat_cache: dict = {}
_vat_cache_client_id: int | None = None


async def _lookup_vat_type_by_rate(rate: float, client: TripletexClient) -> int | None:
    """Find output (Utgående) VAT type ID by percentage rate (e.g. 25, 15, 0). Cached per client."""
    global _vat_cache, _vat_cache_client_id

    # Reset cache if client changed (new competition submission = new client)
    if id(client) != _vat_cache_client_id:
        _vat_cache = {}
        _vat_cache_client_id = id(client)

    if not _vat_cache:
        result = await client.get("/ledger/vatType", params={"typeOfVat": "OUTGOING", "count": "100"})
        for vt in result.get("values", []):
            pct = vt.get("percentage")
            if pct is not None:
                # Prefer simple numbered codes (3, 31, 33) over special ones (UTTAK, TAP, etc.)
                number = vt.get("number", "")
                if pct not in _vat_cache or number.isdigit():
                    _vat_cache[pct] = vt["id"]
        logger.info("Cached %d output VAT types: %s", len(_vat_cache), _vat_cache)
    return _vat_cache.get(rate)


async def _build_order_lines(lines: list[dict], client: TripletexClient) -> list[dict]:
    """Build order lines from extracted line data. Creates products and resolves VAT types as needed."""
    order_lines = []
    for line in lines:
        ol = {}

        # Resolve product: by ID, by number (create if needed), or use description
        product = line.get("product")
        product_id = line.get("productId")
        product_number = line.get("productNumber")

        if product_id:
            ol["product"] = {"id": product_id}
        elif isinstance(product, dict) and "id" in product:
            ol["product"] = product
        elif isinstance(product, int):
            ol["product"] = {"id": product}
        elif product_number:
            # Try to find existing product by number first, create if not found
            prod_name = line.get("description", f"Product {product_number}")
            price = line.get("unitPriceExcludingVatCurrency", line.get("unitPrice", 0))

            search = await client.get("/product", params={"productNumber": str(product_number), "count": "1"})
            existing = search.get("values", [])
            if existing:
                pid = existing[0]["id"]
                ol["product"] = {"id": pid}
                logger.info("Found existing product '%s' (number=%s, id=%d)", prod_name, product_number, pid)
            else:
                prod_payload = {"name": prod_name, "number": str(product_number)}
                if price:
                    prod_payload["priceExcludingVatCurrency"] = price
                prod_result = await client.post("/product", prod_payload)
                pid = prod_result.get("value", {}).get("id")
                if pid:
                    ol["product"] = {"id": pid}
                    logger.info("Created product '%s' (number=%s, id=%d)", prod_name, product_number, pid)
        elif isinstance(product, str):
            if not line.get("description"):
                ol["description"] = product

        if line.get("description"):
            ol["description"] = line["description"]
        if line.get("quantity") is not None or line.get("count") is not None:
            ol["count"] = line.get("count", line.get("quantity"))
        if line.get("unitPrice") is not None or line.get("unitPriceExcludingVatCurrency") is not None:
            ol["unitPriceExcludingVatCurrency"] = line.get("unitPriceExcludingVatCurrency", line.get("unitPrice"))

        # Resolve VAT type: by ID, by rate percentage, or default to 25% standard Norwegian VAT
        vat = line.get("vatType") or line.get("vatTypeId")
        vat_rate = line.get("vatRatePercent")
        if isinstance(vat, int):
            ol["vatType"] = {"id": vat}
        elif isinstance(vat, dict) and "id" in vat:
            ol["vatType"] = vat
        elif vat_rate is not None:
            vat_id = await _lookup_vat_type_by_rate(float(vat_rate), client)
            if vat_id:
                ol["vatType"] = {"id": vat_id}
                logger.info("Resolved VAT type: %s%% → id=%d", vat_rate, vat_id)
        else:
            # Default to 25% standard Norwegian VAT when no VAT specified
            vat_id = await _lookup_vat_type_by_rate(25, client)
            if vat_id:
                ol["vatType"] = {"id": vat_id}
                logger.info("No VAT specified, defaulting to 25%% → id=%d", vat_id)

        order_lines.append(ol)
    return order_lines


async def create_order(data: dict, client: TripletexClient) -> dict:
    """Create an order in Tripletex."""
    customer_id = await _ensure_customer(data, client)
    if not customer_id:
        logger.error("No customer ID available for order creation")
        return {"error": "No customer for order"}

    await _ensure_bank_account(client)

    order_lines = await _build_order_lines(data.get("lines", data.get("orderLines", [])), client)

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
    """Create an invoice in one write via POST /invoice with embedded order lines."""

    customer_id = await _ensure_customer(data, client)
    if not customer_id:
        logger.error("No customer ID available for invoice creation")
        return {"error": "No customer for invoice"}

    await _ensure_bank_account(client)

    # Build order lines from invoice line data
    order_lines = await _build_order_lines(data.get("lines", data.get("orderLines", [])), client)

    invoice_date = data.get("invoiceDate", _today())
    due_date = data.get("dueDate") or data.get("invoiceDueDate") or (date.fromisoformat(invoice_date) + timedelta(days=14)).isoformat()

    order_payload = {
        "customer": {"id": customer_id},
        "orderDate": invoice_date,
        "deliveryDate": data.get("deliveryDate", invoice_date),
        "orderLines": order_lines,
    }
    if data.get("currencyId"):
        order_payload["currency"] = {"id": data["currencyId"]}
    if data.get("invoiceComment"):
        order_payload["invoiceComment"] = data["invoiceComment"]

    invoice_payload = {
        "customer": {"id": customer_id},
        "invoiceDate": invoice_date,
        "orders": [order_payload],
    }
    invoice_payload["invoiceDueDate"] = due_date

    invoice_params = {
        "sendToCustomer": "true" if data.get("sendToCustomer") else "false",
    }

    logger.info("Creating direct invoice (customer_id=%d, %d lines)", customer_id, len(order_lines))
    result = await client.post("/invoice", invoice_payload, params=invoice_params)

    invoice_id = result.get("value", {}).get("id")
    if invoice_id:
        logger.info("Invoice created with ID: %d", invoice_id)
    else:
        logger.error("Failed to create invoice: %s", result)

    return result
