import logging
from datetime import date

from ..tripletex import TripletexClient
from .invoice import create_invoice

logger = logging.getLogger("agent.workflows.payment")


def _normalize(s: str) -> str:
    return s.strip().lower() if s else ""


def _is_foreign_currency_invoice(invoice: dict) -> bool:
    currency = invoice.get("currency") or {}
    currency_code = (invoice.get("currencyCode") or currency.get("code") or "").upper()
    return bool(currency_code and currency_code != "NOK")


def _derive_paid_amount_currency(data: dict, invoice: dict, paid_amount: float | int | None) -> float | int | None:
    explicit_amount = data.get("paidAmountCurrency")
    if explicit_amount is not None:
        return explicit_amount

    if not _is_foreign_currency_invoice(invoice):
        return None

    amount_pairs = [
        (invoice.get("amountOutstanding"), invoice.get("amountCurrencyOutstanding")),
        (invoice.get("amountOutstandingTotal"), invoice.get("amountCurrencyOutstandingTotal")),
        (invoice.get("amount"), invoice.get("amountCurrency")),
    ]

    if data.get("fullPayment"):
        for _, currency_amount in amount_pairs:
            if currency_amount is not None:
                return currency_amount
        return paid_amount

    if paid_amount is None:
        return None

    for invoice_amount, currency_amount in amount_pairs:
        if invoice_amount is None or currency_amount is None:
            continue
        if abs(float(invoice_amount) - float(paid_amount)) < 0.01:
            return currency_amount

    return None


def _rank_invoice_match(data: dict, candidates: list[dict]) -> dict | None:
    """Rank invoice candidates by: customer org → customer name → amount/outstanding → description → recency."""
    org_number = data.get("customerOrgNumber") or data.get("organizationNumber") or ""
    customer_name = _normalize(data.get("customerName", ""))
    description = _normalize(data.get("description", ""))
    target_amount = data.get("amountExclVat") or data.get("amount") or data.get("paidAmount")

    def _score(inv: dict) -> tuple:
        cust = inv.get("customer") or {}
        if isinstance(cust, dict):
            inv_org = cust.get("organizationNumber", "") or ""
            inv_cust_name = _normalize(cust.get("name", ""))
        else:
            inv_org = ""
            inv_cust_name = ""

        # Score components (higher = better match)
        s_org = 1 if org_number and inv_org == org_number else 0
        s_name = 1 if customer_name and customer_name in inv_cust_name else 0
        s_amount = 0
        if target_amount:
            outstanding = inv.get("amountOutstanding", 0)
            total = inv.get("amount", 0)
            total_excl = inv.get("amountExcludingVat", inv.get("amountExcludingVatCurrency", 0))
            if outstanding > 0 and abs(outstanding - float(target_amount)) < 0.01:
                s_amount = 2
            elif total > 0 and abs(total - float(target_amount)) < 0.01:
                s_amount = 1
            elif total_excl > 0 and abs(total_excl - float(target_amount)) < 0.01:
                s_amount = 1
        s_desc = 0
        if description:
            for line in inv.get("orderLines", []):
                if description in _normalize(line.get("description", "")):
                    s_desc = 1
                    break
        # Recency: higher invoice ID = more recent
        s_recency = inv.get("id", 0)

        return (s_org, s_name, s_amount, s_desc, s_recency)

    ranked = sorted(candidates, key=_score, reverse=True)
    best = ranked[0]
    score = _score(best)
    # Require at least one signal beyond recency
    if score[0] == 0 and score[1] == 0 and score[2] == 0 and score[3] == 0:
        return None
    logger.info("Ranked invoice match: id=%d score=%s", best["id"], score)
    return best


async def _find_payment_type_id(client: TripletexClient) -> int | None:
    """Get the first available payment type ID."""
    result = await client.get("/invoice/paymentType", params={"count": "1"})
    types = result.get("values", [])
    if types:
        return types[0]["id"]
    return None


async def _resolve_customer_id(data: dict, client: TripletexClient) -> int | None:
    customer_id = data.get("customerId")
    if customer_id:
        return customer_id

    org_number = data.get("customerOrgNumber") or data.get("organizationNumber")
    if org_number:
        cust_result = await client.get("/customer", params={"organizationNumber": org_number, "count": "1"})
        custs = cust_result.get("values", [])
        if custs:
            return custs[0]["id"]

    customer_name = data.get("customerName")
    if customer_name:
        cust_result = await client.get("/customer", params={"customerName": customer_name, "count": "5"})
        for customer in cust_result.get("values", []):
            if _normalize(customer.get("name", "")) == _normalize(customer_name):
                return customer["id"]

    return None


async def _list_invoice_candidates(client: TripletexClient, search_params: dict) -> list[dict]:
    page_size = int(search_params.get("count", "1000"))
    offset = 0
    invoices: list[dict] = []

    while True:
        page_params = {**search_params, "count": str(page_size), "from": str(offset)}
        page = await client.get("/invoice", params=page_params)
        values = page.get("values", [])
        if not values:
            break

        invoices.extend(
            inv for inv in values
            if not inv.get("isCreditNote") and not inv.get("isCredited")
        )

        if len(values) < page_size:
            break

        offset += len(values)

    return invoices


async def _find_invoice(data: dict, client: TripletexClient) -> dict | None:
    """Find an invoice by ID, number, or ranked multi-pass search. Returns full invoice dict."""
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

    search_params = {
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": "2099-12-31",
        "count": "1000",
    }
    customer_id = await _resolve_customer_id(data, client)
    if customer_id:
        scoped_candidates = await _list_invoice_candidates(client, {
            **search_params,
            "customerId": str(customer_id),
        })
        if scoped_candidates:
            scoped_match = _rank_invoice_match(data, scoped_candidates)
            if scoped_match:
                logger.info("Found invoice %d using customer-scoped search", scoped_match["id"])
                return scoped_match
        logger.info("No ranked invoice match in customer-scoped search, falling back to broad search")

    candidates = await _list_invoice_candidates(client, search_params)

    if not candidates:
        return None

    return _rank_invoice_match(data, candidates)


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
    paid_amount = data.get("paidAmount")
    if paid_amount is None:
        paid_amount = data.get("amount")
    if paid_amount is None or data.get("fullPayment"):
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
        "paymentDate": data.get("paymentDate", data.get("date", date.today().isoformat())),
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(paid_amount),
    }

    paid_amount_currency = _derive_paid_amount_currency(data, invoice, paid_amount)
    if paid_amount_currency is not None:
        params["paidAmountCurrency"] = str(paid_amount_currency)
    elif _is_foreign_currency_invoice(invoice):
        logger.warning("Invoice %d uses foreign currency but paidAmountCurrency was not provided or inferred", invoice_id)

    logger.info("Registering payment on invoice %d: amount=%s, date=%s",
                invoice_id, params["paidAmount"], params["paymentDate"])
    result = await client.put(f"/invoice/{invoice_id}/:payment", params=params)

    if result.get("value", {}).get("id"):
        logger.info("Payment registered successfully on invoice %d", invoice_id)

        # Free GET: verify amountOutstanding is correct after payment
        verify = await client.get(f"/invoice/{invoice_id}")
        actual = verify.get("value", {})
        outstanding = actual.get("amountOutstanding", -1)
        if data.get("fullPayment") and outstanding > 0.01:
            result["warnings"] = [f"Payment registered but amountOutstanding is {outstanding} (expected 0). Invoice may not be fully paid."]
            logger.warning("Invoice %d amountOutstanding=%.2f after full payment — expected 0", invoice_id, outstanding)
        elif outstanding >= 0:
            result.setdefault("value", {})["amountOutstandingAfterPayment"] = outstanding
            logger.info("Invoice %d verified: amountOutstanding=%.2f", invoice_id, outstanding)
    else:
        logger.error("Failed to register payment: %s", result)

    return result
