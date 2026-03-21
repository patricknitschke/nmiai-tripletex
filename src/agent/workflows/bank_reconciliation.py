import csv
import io
import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.bank_reconciliation")


def _parse_csv(csv_text: str) -> list[dict]:
    """Parse bank statement CSV into rows. Handles Norwegian CSV formats."""
    # Try semicolon first (Norwegian standard), then comma
    for delimiter in [";", ","]:
        try:
            reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
            rows = list(reader)
            if rows and len(rows[0]) > 1:
                return rows
        except Exception:
            continue
    # Fallback: just return raw lines
    return []


async def _find_invoice_by_number(invoice_number: str, client: TripletexClient) -> dict | None:
    """Find a customer invoice by its Tripletex invoice number."""
    result = await client.get("/invoice", params={
        "invoiceNumber": str(invoice_number),
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": "2099-12-31",
        "count": "1",
    })
    values = result.get("values", [])
    return values[0] if values else None


async def _find_invoice_by_amount(amount: float, client: TripletexClient, invoices: list) -> dict | None:
    """Find an unpaid invoice matching the given amount from a pre-fetched list."""
    for inv in invoices:
        outstanding = inv.get("amountOutstanding", inv.get("amount", 0))
        if abs(outstanding - amount) < 0.01 and outstanding > 0:
            return inv
    return None


async def _get_all_invoices(client: TripletexClient) -> list:
    """Fetch all customer invoices."""
    result = await client.get("/invoice", params={
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": "2099-12-31",
        "count": "1000",
    })
    return result.get("values", [])


async def _get_all_supplier_invoices(client: TripletexClient) -> list:
    """Fetch all supplier invoices."""
    result = await client.get("/supplierInvoice", params={
        "invoiceDateFrom": "2000-01-01",
        "invoiceDateTo": "2099-12-31",
        "count": "1000",
    })
    return result.get("values", [])


async def _register_customer_payment(invoice_id: int, amount: float, payment_date: str,
                                      client: TripletexClient) -> dict:
    """Register a payment on a customer invoice."""
    # Get payment type
    pt_result = await client.get("/invoice/paymentType", params={"count": "1"})
    payment_types = pt_result.get("values", [])
    payment_type_id = payment_types[0]["id"] if payment_types else None

    if not payment_type_id:
        return {"error": "No payment type found"}

    params = {
        "id": str(invoice_id),
        "paymentDate": payment_date,
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(amount),
    }
    return await client.put(f"/invoice/{invoice_id}/:payment", params=params)


async def reconcile_bank_statement(data: dict, client: TripletexClient) -> dict:
    """Reconcile a bank statement CSV against open invoices in Tripletex.

    Processes three types of transactions:
    1. Incoming payments (Innbetaling) → match to customer invoices, register payment
    2. Outgoing payments (Betaling/Leverandør) → match to supplier invoices, register payment
    3. Bank fees/interest (Bankgebyr/Rente) → create voucher entries

    Input data fields:
    - csvContent: the raw CSV text from the bank statement file
    - rows: alternatively, pre-parsed list of {type, customer/supplier, invoiceNumber, amount, date}
    """
    results = {"payments_registered": [], "errors": [], "skipped": []}

    # Parse CSV if provided as raw text
    csv_content = data.get("csvContent", "")
    rows = data.get("rows", [])

    if csv_content and not rows:
        parsed = _parse_csv(csv_content)
        if not parsed:
            return {"error": "Could not parse CSV content"}

        # Normalize rows — detect column names
        for row in parsed:
            normalized = {}
            for key, val in row.items():
                key_lower = key.strip().lower() if key else ""
                val = val.strip() if val else ""

                if key_lower in ("dato", "date", "bokføringsdato"):
                    normalized["date"] = val
                elif key_lower in ("forklaring", "description", "tekst", "text"):
                    normalized["description"] = val
                elif key_lower in ("inn", "in", "innbetaling", "credit"):
                    try:
                        normalized["amountIn"] = float(val.replace(",", ".").replace(" ", "")) if val else 0
                    except ValueError:
                        normalized["amountIn"] = 0
                elif key_lower in ("ut", "out", "utbetaling", "debit"):
                    try:
                        normalized["amountOut"] = float(val.replace(",", ".").replace(" ", "")) if val else 0
                    except ValueError:
                        normalized["amountOut"] = 0

            if normalized.get("description"):
                rows.append(normalized)

    if not rows:
        return {"error": "No transaction rows to process"}

    logger.info("Processing %d bank statement rows", len(rows))

    # Pre-fetch all invoices for matching
    all_invoices = await _get_all_invoices(client)
    logger.info("Fetched %d customer invoices for matching", len(all_invoices))

    # Cache payment type
    pt_result = await client.get("/invoice/paymentType", params={"count": "1"})
    payment_types = pt_result.get("values", [])
    payment_type_id = payment_types[0]["id"] if payment_types else None

    # Track which invoices we've already paid
    paid_invoice_ids = set()

    for row in rows:
        desc = row.get("description", "")
        amount_in = row.get("amountIn", 0)
        amount_out = abs(row.get("amountOut", 0))
        tx_date = row.get("date", date.today().isoformat())

        desc_lower = desc.lower()

        # Detect supplier keywords (multilingual)
        is_supplier = any(kw in desc_lower for kw in ["leverand", "lieferant", "fournisseur", "fornecedor", "proveedor", "supplier"])

        # Type 1: Customer payment (Innbetaling)
        if amount_in > 0 and ("innbetaling" in desc_lower or ("betaling" in desc_lower and not is_supplier)):
            # Try to match by amount to an unpaid invoice
            matched = None
            for inv in all_invoices:
                inv_id = inv.get("id")
                if inv_id in paid_invoice_ids:
                    continue
                outstanding = inv.get("amountOutstanding", 0)
                if outstanding > 0 and abs(outstanding - amount_in) < 0.01:
                    matched = inv
                    break

            # If no exact match, find any unpaid invoice (partial payment)
            if not matched:
                for inv in all_invoices:
                    inv_id = inv.get("id")
                    if inv_id in paid_invoice_ids:
                        continue
                    outstanding = inv.get("amountOutstanding", 0)
                    if outstanding > 0:
                        matched = inv
                        break

            if matched and payment_type_id:
                inv_id = matched["id"]
                params = {
                    "id": str(inv_id),
                    "paymentDate": tx_date,
                    "paymentTypeId": str(payment_type_id),
                    "paidAmount": str(amount_in),
                }
                result = await client.put(f"/invoice/{inv_id}/:payment", params=params)
                if result.get("value"):
                    paid_invoice_ids.add(inv_id)
                    logger.info("Registered customer payment %.2f on invoice %d", amount_in, inv_id)
                    results["payments_registered"].append({
                        "type": "customer", "invoice": inv_id, "amount": amount_in, "date": tx_date
                    })
                    # Update the cached outstanding amount
                    matched["amountOutstanding"] = matched.get("amountOutstanding", 0) - amount_in
                else:
                    logger.error("Failed to register payment on invoice %d: %s", inv_id, result)
                    results["errors"].append({"description": desc, "error": str(result)})
            else:
                logger.warning("No matching invoice for customer payment: %s (%.2f)", desc, amount_in)
                results["skipped"].append({"description": desc, "amount": amount_in, "reason": "no matching invoice"})

        # Type 2: Supplier payment (Betaling Leverandør/Lieferant/fournisseur)
        elif amount_out > 0 and is_supplier:
            # For now, log as skipped — supplier invoice payment needs supplierInvoice ID
            logger.warning("Supplier payment not yet supported: %s (%.2f)", desc, amount_out)
            results["skipped"].append({"description": desc, "amount": amount_out, "reason": "supplier payment not implemented"})

        # Type 3: Bank fee / interest
        elif "bankgebyr" in desc_lower or "rente" in desc_lower or "gebyr" in desc_lower:
            logger.info("Bank fee/interest: %s (in=%.2f, out=%.2f)", desc, amount_in, amount_out)
            results["skipped"].append({"description": desc, "amount": amount_in or amount_out, "reason": "bank fee — use create_voucher"})

        else:
            results["skipped"].append({"description": desc, "amount": amount_in or amount_out, "reason": "unrecognized transaction type"})

    logger.info("Reconciliation complete: %d payments, %d errors, %d skipped",
                len(results["payments_registered"]), len(results["errors"]), len(results["skipped"]))

    return {"value": results}
