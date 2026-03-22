"""Find overdue invoices and send reminders in Tripletex.

B41 fix: Provides a search-first approach for tasks that reference existing overdue
invoices. Instead of fabricating customers, the agent finds real overdue invoices
and returns customer info for downstream workflows (voucher, invoice, payment).

B52 fix: send_reminder uses PUT /invoice/{id}/:createReminder with includeCharge=true
to handle reminder creation + charge + accounting entry + sending in ONE write call.
Replaces the old 3-write path (manual voucher + order + invoice).
"""

import logging
from datetime import date, timedelta

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
    # invoiceDateTo is exclusive ("to and excluding"), so use tomorrow to include today
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    # Fetch all invoices up to today with customer details expanded
    # invoiceDateFrom/invoiceDateTo are required by the API
    params = {
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": tomorrow,
        "count": "1000",
        "fields": "id,invoiceNumber,invoiceDate,invoiceDueDate,amount,amountOutstanding,amountOutstandingTotal,amountExcludingVat,isCreditNote,isCredited,customer(*)",
    }

    # Paginate to avoid missing invoices beyond the 1000 limit
    all_invoices = []
    page_from = 0
    while True:
        params["from"] = str(page_from)
        result = await client.get("/invoice", params)
        batch = result.get("values", [])
        all_invoices.extend(batch)
        if len(batch) < 1000:
            break
        page_from += len(batch)

    # Filter: overdue (dueDate < today) and still outstanding
    # Use amountOutstandingTotal which includes reminder fees and partial remittances
    overdue = []
    for inv in all_invoices:
        if inv.get("isCreditNote") or inv.get("isCredited"):
            continue
        due_date = inv.get("invoiceDueDate", "")
        outstanding = inv.get("amountOutstandingTotal") or inv.get("amountOutstanding", 0)
        if due_date and due_date < today and outstanding > 0:
            inv["_isOverdue"] = True
            overdue.append(inv)

    if not overdue:
        # Fallback: any invoice with outstanding balance (might not be technically overdue yet)
        for inv in all_invoices:
            if inv.get("isCreditNote") or inv.get("isCredited"):
                continue
            outstanding = inv.get("amountOutstandingTotal") or inv.get("amountOutstanding", 0)
            if outstanding > 0:
                inv["_isOverdue"] = False
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
            # AND logic when both filters are provided
            if customer_name and customer_org:
                if customer_name in inv_name and inv_org == customer_org:
                    filtered.append(inv)
            elif customer_name and customer_name in inv_name:
                filtered.append(inv)
            elif customer_org and inv_org == customer_org:
                filtered.append(inv)
        if filtered:
            overdue = filtered

    if min_amount:
        overdue = [inv for inv in overdue if (inv.get("amountOutstandingTotal") or inv.get("amountOutstanding", 0)) >= min_amount]

    # Sort by due date (most overdue first)
    overdue.sort(key=lambda inv: inv.get("invoiceDueDate", "9999-12-31"))

    logger.info("Found %d overdue invoice(s)", len(overdue))

    # Return the most overdue invoice as primary result
    best = overdue[0]

    # customer(*) expansion already gives full customer details — no extra call needed
    customer_info = best.get("customer") or {}
    customer_id = customer_info.get("id") if isinstance(customer_info, dict) else None

    best_outstanding = best.get("amountOutstandingTotal") or best.get("amountOutstanding")

    return {
        "value": best,
        "overdueInvoices": overdue[:10],  # Limit to top 10
        "customer": customer_info,
        "customerId": customer_id,
        "customerName": customer_info.get("name") if isinstance(customer_info, dict) else None,
        "invoiceId": best.get("id"),
        "amountOutstanding": best_outstanding,
        "invoiceDueDate": best.get("invoiceDueDate"),
    }


async def send_reminder(data: dict, client: TripletexClient) -> dict:
    """Send a reminder for an overdue invoice using PUT /invoice/{id}/:createReminder.

    This is the optimal path for reminder fee tasks — handles everything in ONE write call:
    - Creates the reminder document
    - Adds the charge (includeCharge=true) — standard Norwegian purregebyr
    - Auto-generates the accounting entries (debit 1500-AR, credit 3400-reminder income)
    - Sends to the customer via email

    Input data fields:
    - invoiceId: required — the overdue invoice ID (from find_overdue_invoices)
    - reminderType: SOFT_REMINDER | REMINDER | NOTICE_OF_DEBT_COLLECTION (default: REMINDER)
    - includeCharge: whether to include the reminder fee (default: true)
    - includeInterest: whether to include interest (default: false)
    - date: reminder date (default: today)
    - dispatchType: EMAIL | OWN_PRINTER | etc (default: EMAIL)

    Fallback: if :createReminder fails, falls back to POST /invoice with 0% VAT for the
    reminder fee (reminder fees / purregebyr are VAT-exempt in Norway).
    """
    invoice_id = data.get("invoiceId")
    if not invoice_id:
        return {"error": "invoiceId is required — run find_overdue_invoices first"}

    today = date.today().isoformat()
    reminder_type = data.get("reminderType", "REMINDER")
    include_charge = data.get("includeCharge", True)
    include_interest = data.get("includeInterest", False)
    reminder_date = data.get("date", today)
    dispatch_type = data.get("dispatchType", "EMAIL")

    # Primary path: PUT /invoice/{id}/:createReminder
    params = {
        "type": reminder_type,
        "date": reminder_date,
        "includeCharge": str(include_charge).lower(),
        "includeInterest": str(include_interest).lower(),
        "dispatchType": dispatch_type,
    }

    logger.info(
        "Creating reminder for invoice %d (type=%s, includeCharge=%s, dispatch=%s)",
        invoice_id, reminder_type, include_charge, dispatch_type,
    )

    result = await client.put(f"/invoice/{invoice_id}/:createReminder", params=params)

    if not result.get("error"):
        reminder_id = result.get("value")
        logger.info("Reminder created successfully (reminder_id=%s) for invoice %d", reminder_id, invoice_id)
        return {
            "value": result.get("value"),
            "reminderId": reminder_id,
            "invoiceId": invoice_id,
            "method": "createReminder",
            "includeCharge": include_charge,
        }

    # Fallback: create a separate invoice for the reminder fee with 0% VAT
    logger.warning(
        "createReminder failed for invoice %d: %s — falling back to manual invoice",
        invoice_id, result.get("error"),
    )
    return await _fallback_reminder_invoice(data, client)


async def _fallback_reminder_invoice(data: dict, client: TripletexClient) -> dict:
    """Fallback: create a direct invoice for the reminder fee with 0% VAT.

    Norwegian reminder fees (purregebyr) are VAT-exempt = 0%.
    Uses POST /invoice directly to avoid the order→invoice 2-call path.
    The invoice auto-generates AR posting (debit 1500), so no separate voucher needed.
    """
    from .invoice import _ensure_bank_account, _ensure_customer, _lookup_vat_type_by_rate

    customer_id = data.get("customerId")
    charge_amount = data.get("chargeAmount", 65)  # Standard Norwegian reminder fee

    if not customer_id:
        customer_id = await _ensure_customer(data, client)
    if not customer_id:
        return {"error": "No customer for fallback reminder invoice"}

    await _ensure_bank_account(client)

    # Resolve 0% VAT type (reminder fees are VAT-exempt)
    vat_id = await _lookup_vat_type_by_rate(0, client)

    today = date.today().isoformat()

    # Build order line for the reminder fee
    order_line = {
        "description": "Purregebyr / Reminder fee",
        "count": 1,
        "unitPriceExcludingVatCurrency": charge_amount,
    }
    if vat_id:
        order_line["vatType"] = {"id": vat_id}

    # Try POST /invoice with embedded order (direct invoice creation)
    invoice_payload = {
        "invoiceDate": today,
        "invoiceDueDate": data.get("dueDate", today),
        "order": {
            "customer": {"id": customer_id},
            "orderDate": today,
            "deliveryDate": today,
            "orderLines": [order_line],
        },
    }

    logger.info("Fallback: creating direct reminder invoice for customer %s, amount=%s, VAT=0%%", customer_id, charge_amount)
    result = await client.post("/invoice", invoice_payload, params={"sendToCustomer": "true"})

    if result.get("error"):
        # Last resort: use order→invoice path
        logger.warning("POST /invoice failed: %s — trying order→invoice path", result.get("error"))
        order_result = await client.post("/order", {
            "customer": {"id": customer_id},
            "orderDate": today,
            "deliveryDate": today,
            "orderLines": [order_line],
        })
        order_id = order_result.get("value", {}).get("id")
        if not order_id:
            return {"error": f"Failed to create fallback order: {order_result}"}

        result = await client.put(
            f"/order/{order_id}/:invoice",
            params={"invoiceDate": today, "sendToCustomer": "true"},
        )

    invoice_id = result.get("value", {}).get("id")
    if invoice_id:
        logger.info("Fallback reminder invoice created: id=%d", invoice_id)
    else:
        logger.error("Fallback reminder invoice failed: %s", result)

    return {
        "value": result.get("value", {}),
        "invoiceId": invoice_id,
        "method": "fallback_invoice",
        "vatRate": 0,
        "chargeAmount": charge_amount,
    }
