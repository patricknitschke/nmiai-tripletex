"""Find overdue invoices in Tripletex.

B41 fix: Provides a search-first approach for tasks that reference existing overdue
invoices. Instead of fabricating customers, the agent finds real overdue invoices
and returns customer info for downstream workflows (voucher, invoice, payment).
"""

import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.overdue")


async def find_overdue_invoices(data: dict, client: TripletexClient) -> dict:
    """Find overdue invoices (dueDate < today, amountOutstanding > 0).

    Returns the overdue invoice(s) with customer details so downstream
    workflows (create_voucher, create_invoice, register_payment) can
    use the correct customer and invoice IDs.

    Input data fields:
    - customerName: optional filter by customer name
    - customerOrgNumber: optional filter by org number
    - minAmount: optional minimum outstanding amount filter

    Returns:
    - value: first (most overdue) invoice with full details
    - overdueInvoices: list of all overdue invoices found
    - customer: resolved customer info from the overdue invoice
    """
    today = date.today().isoformat()

    # Fetch all invoices up to today with customer details expanded
    # invoiceDateFrom/invoiceDateTo are required by the API
    params = {
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": today,
        "count": "1000",
        "fields": "id,invoiceNumber,invoiceDate,invoiceDueDate,amount,amountOutstanding,amountExcludingVat,isCreditNote,isCredited,customer(*)",
    }

    result = await client.get("/invoice", params)
    all_invoices = result.get("values", [])

    # Filter: overdue (dueDate < today) and still outstanding
    overdue = []
    for inv in all_invoices:
        if inv.get("isCreditNote") or inv.get("isCredited"):
            continue
        due_date = inv.get("invoiceDueDate", "")
        outstanding = inv.get("amountOutstanding", 0)
        if due_date and due_date < today and outstanding > 0:
            overdue.append(inv)

    if not overdue:
        # Fallback: any invoice with outstanding balance (might not be technically overdue yet)
        for inv in all_invoices:
            if inv.get("isCreditNote") or inv.get("isCredited"):
                continue
            outstanding = inv.get("amountOutstanding", 0)
            if outstanding > 0:
                overdue.append(inv)
        if overdue:
            logger.info("No strictly overdue invoices found, but found %d with outstanding balance", len(overdue))

    if not overdue:
        logger.warning("No overdue or outstanding invoices found")
        return {"error": "No overdue invoices found", "overdueInvoices": []}

    # Optional filters
    customer_name = data.get("customerName", "").lower()
    customer_org = data.get("customerOrgNumber") or data.get("organizationNumber") or ""
    min_amount = data.get("minAmount", 0)

    if customer_name or customer_org:
        filtered = []
        for inv in overdue:
            cust = inv.get("customer") or {}
            if isinstance(cust, dict):
                inv_name = (cust.get("name") or "").lower()
                inv_org = cust.get("organizationNumber") or ""
            else:
                inv_name = ""
                inv_org = ""
            if customer_name and customer_name in inv_name:
                filtered.append(inv)
            elif customer_org and inv_org == customer_org:
                filtered.append(inv)
        if filtered:
            overdue = filtered

    if min_amount:
        overdue = [inv for inv in overdue if inv.get("amountOutstanding", 0) >= min_amount]

    # Sort by due date (most overdue first)
    overdue.sort(key=lambda inv: inv.get("invoiceDueDate", "9999-12-31"))

    logger.info("Found %d overdue invoice(s)", len(overdue))

    # Return the most overdue invoice as primary result
    best = overdue[0]

    # Fetch full customer details for the overdue invoice
    customer_info = best.get("customer") or {}
    customer_id = customer_info.get("id") if isinstance(customer_info, dict) else None
    if customer_id:
        try:
            cust_result = await client.get(f"/customer/{customer_id}")
            customer_info = cust_result.get("value", customer_info)
        except Exception:
            pass

    return {
        "value": best,
        "overdueInvoices": overdue[:10],  # Limit to top 10
        "customer": customer_info,
        "customerId": customer_id,
        "customerName": customer_info.get("name") if isinstance(customer_info, dict) else None,
        "invoiceId": best.get("id"),
        "amountOutstanding": best.get("amountOutstanding"),
        "invoiceDueDate": best.get("invoiceDueDate"),
    }
