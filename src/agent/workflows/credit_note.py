import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.credit_note")


def _normalize(s: str) -> str:
    return s.strip().lower() if s else ""


async def _resolve_customer_id(data: dict, client: TripletexClient) -> int | None:
    """Resolve customer ID from org number or name."""
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


def _rank_invoice_match(data: dict, candidates: list[dict]) -> dict | None:
    """Rank invoice candidates — same logic as payment.py."""
    org_number = data.get("customerOrgNumber") or data.get("organizationNumber") or ""
    customer_name = _normalize(data.get("customerName", ""))
    description = _normalize(data.get("description", ""))
    target_amount = data.get("amountExclVat") or data.get("amount")

    def _score(inv: dict) -> tuple:
        cust = inv.get("customer") or {}
        if isinstance(cust, dict):
            inv_org = cust.get("organizationNumber", "") or ""
            inv_cust_name = _normalize(cust.get("name", ""))
        else:
            inv_org = ""
            inv_cust_name = ""

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
        s_recency = inv.get("id", 0)
        return (s_org, s_name, s_amount, s_desc, s_recency)

    ranked = sorted(candidates, key=_score, reverse=True)
    best = ranked[0]
    score = _score(best)
    if score[0] == 0 and score[1] == 0 and score[2] == 0 and score[3] == 0:
        return None
    logger.info("Ranked invoice match: id=%d score=%s", best["id"], score)
    return best


async def _find_invoice(data: dict, client: TripletexClient) -> dict | None:
    """Find an invoice by ID, number, or ranked customer+description search."""
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

    # Search by customer — request orderLines so description ranking works
    search_params = {
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": "2099-12-31",
        "count": "1000",
        "fields": "id,invoiceNumber,amount,amountExcludingVat,amountOutstanding,customer(*),isCreditNote,isCredited,orderLines(*)",
    }
    customer_id = await _resolve_customer_id(data, client)
    if customer_id:
        page = await client.get("/invoice", params={
            **search_params,
            "customerId": str(customer_id),
        })
        candidates = [
            inv for inv in page.get("values", [])
            if not inv.get("isCreditNote") and not inv.get("isCredited")
        ]
        if candidates:
            match = _rank_invoice_match(data, candidates)
            if match:
                logger.info("Found invoice %d using customer-scoped search", match["id"])
                return match
        logger.info("No ranked match in customer-scoped search, trying broad search")

    # Broad search fallback
    page = await client.get("/invoice", params=search_params)
    candidates = [
        inv for inv in page.get("values", [])
        if not inv.get("isCreditNote") and not inv.get("isCredited")
    ]
    if candidates:
        return _rank_invoice_match(data, candidates)

    return None


async def create_credit_note(data: dict, client: TripletexClient) -> dict:
    """Create a credit note for an existing invoice. PUT /invoice/{id}/:createCreditNote.

    Self-contained: searches for the invoice by customer org/name + description if
    invoiceId/invoiceNumber not provided.
    """

    invoice = await _find_invoice(data, client)
    if not invoice:
        logger.error("No invoice found for credit note — searched by customer/description")
        return {"error": "Cannot create credit note: no matching invoice found. Provide invoiceId, invoiceNumber, or customer details + description."}

    invoice_id = invoice["id"]

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
