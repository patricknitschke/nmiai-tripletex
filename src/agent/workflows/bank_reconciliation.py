import csv
import io
import logging
import re
from datetime import date

from ..tripletex import TripletexClient
from .voucher import create_supplier_invoice  # noqa: F401 — kept for standalone supplier invoice tasks

logger = logging.getLogger("agent.workflows.bank_reconciliation")

# Multilingual supplier keyword prefixes used to extract supplier name from descriptions
_SUPPLIER_PREFIXES = [
    r"betaling\s+leverand(?:ør|or)\s+",
    r"leverand(?:ør|or)(?:betaling)?\s+",
    # Mixed-language combos (Norwegian "Betaling" + other language supplier keyword)
    r"betaling\s+fornecedor\s+",
    r"betaling\s+proveedor\s+",
    r"betaling\s+fournisseur\s+",
    r"betaling\s+lieferant\s+",
    r"betaling\s+supplier\s+",
    r"zahlung\s+lieferant\s+",
    r"lieferant\s+",
    r"pago\s+proveedor\s+",
    r"proveedor\s+",
    r"paiement\s+fournisseur\s+",
    r"fournisseur\s+",
    r"pagamento\s+fornecedor\s+",
    r"fornecedor\s+",
    r"supplier\s+payment\s+",
    r"payment\s+supplier\s+",
    r"supplier\s+",
]


async def _post_supplier_bank_payment(
    amount_incl: float, tx_date: str, supplier_name: str, description: str,
    client: TripletexClient,
) -> bool:
    """Post a direct supplier bank payment as a voucher.

    When no supplier invoice exists in the system, we record the bank outflow
    directly. This avoids the systemgenererte 422 that happens when posting to
    accounts with default VAT types.

    Accounting (3 postings, no vatType to avoid system-generated conflict):
      Debit  7300 (expense)      amount excl VAT
      Debit  2710 (input VAT)    VAT amount
      Credit 1920 (bank)         total amount incl VAT
    """
    vat_rate = 25
    amount_excl = round(amount_incl / (1 + vat_rate / 100), 2)
    vat_amount = round(amount_incl - amount_excl, 2)

    # Resolve accounts
    accounts_needed = {"7300": None, "2710": None, "1920": None}
    for acct_num in accounts_needed:
        result = await client.get("/ledger/account", params={"number": acct_num, "count": "1"})
        values = result.get("values", [])
        if values:
            accounts_needed[acct_num] = values[0]["id"]
        else:
            logger.error("Account %s not found for supplier bank payment", acct_num)
            return False

    # Build 3 manual postings — NO vatType to avoid system-generated conflict
    postings = [
        {
            "date": tx_date,
            "description": f"{supplier_name} (expense excl VAT)",
            "amountGross": amount_excl,
            "account": {"id": accounts_needed["7300"]},
        },
        {
            "date": tx_date,
            "description": f"{supplier_name} (input VAT 25%)",
            "amountGross": vat_amount,
            "account": {"id": accounts_needed["2710"]},
        },
        {
            "date": tx_date,
            "description": f"{supplier_name} (bank payment)",
            "amountGross": -amount_incl,
            "account": {"id": accounts_needed["1920"]},
        },
    ]

    voucher = {
        "date": tx_date,
        "description": f"Supplier payment: {supplier_name}",
        "postings": postings,
    }

    logger.info("Posting supplier bank payment: %.2f (excl=%.2f, VAT=%.2f)", amount_incl, amount_excl, vat_amount)

    # Try sendToLedger=true first, fall back to false (draft)
    result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "true"})
    voucher_id = result.get("value", {}).get("id")

    if not voucher_id:
        error_msg = str(result.get("validationMessages", result.get("message", "")))
        if "systemgenererte" in error_msg.lower():
            logger.warning("Bank payment voucher hit systemgenererte — trying draft mode")
            result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "false"})
            voucher_id = result.get("value", {}).get("id")
            if voucher_id:
                # Try to send draft to ledger
                send_result = await client.put(f"/ledger/voucher/{voucher_id}/:sendToLedger")
                if send_result.get("value", {}).get("id"):
                    logger.info("Draft voucher %d sent to ledger", voucher_id)
                else:
                    logger.warning("Draft voucher %d created but could not send to ledger: %s", voucher_id, send_result)

    if voucher_id:
        logger.info("Supplier bank payment voucher created: id=%d", voucher_id)
        return True

    logger.error("All supplier bank payment attempts failed: %s", result)
    return False


def _extract_supplier_name(description: str) -> str:
    """Extract supplier name from a bank statement description.

    Examples:
        'Betaling Leverandør Polaris AS' -> 'Polaris AS'
        'Zahlung Lieferant Berg GmbH - faktura 123' -> 'Berg GmbH'
        'Proveedor Montaña SL' -> 'Montaña SL'
    """
    text = description.strip()
    for prefix in _SUPPLIER_PREFIXES:
        m = re.match(prefix, text, re.IGNORECASE)
        if m:
            text = text[m.end():].strip()
            break
    # Strip trailing invoice/reference info after common separators
    for sep in [" - ", " – ", " — ", " faktura ", " inv ", " invoice "]:
        idx = text.lower().find(sep)
        if idx > 0:
            text = text[:idx].strip()
            break
    return text or description.strip()


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

    # Pre-fetch supplier invoices for outgoing payment matching
    all_supplier_invoices = await _get_all_supplier_invoices(client)
    logger.info("Fetched %d supplier invoices for matching", len(all_supplier_invoices))

    # Cache payment type
    pt_result = await client.get("/invoice/paymentType", params={"count": "1"})
    payment_types = pt_result.get("values", [])
    payment_type_id = payment_types[0]["id"] if payment_types else None

    # Track which invoices we've already paid
    paid_invoice_ids = set()
    paid_supplier_ids = set()

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
            # First try: match to an existing supplier invoice and pay it
            matched_si = None
            for si in all_supplier_invoices:
                si_id = si.get("id")
                if si_id in paid_supplier_ids:
                    continue
                si_amount = si.get("amount", 0)
                if abs(si_amount - amount_out) < 0.01:
                    matched_si = si
                    break

            if not matched_si:
                for si in all_supplier_invoices:
                    si_id = si.get("id")
                    if si_id in paid_supplier_ids:
                        continue
                    si_amount = si.get("amount", 0)
                    if si_amount > 0:
                        matched_si = si
                        break

            if matched_si:
                # Pay existing supplier invoice via addPayment
                si_id = matched_si["id"]
                pay_params = {
                    "invoiceId": str(si_id),
                    "paymentType": "0",
                    "amount": str(amount_out),
                    "paymentDate": tx_date,
                    "useDefaultPaymentType": "true",
                }
                result = await client.post(f"/supplierInvoice/{si_id}/:addPayment", params=pay_params)
                if result.get("value") or result.get("id") or (isinstance(result.get("status"), int) and result["status"] < 400):
                    paid_supplier_ids.add(si_id)
                    logger.info("Registered supplier payment %.2f on supplier invoice %d", amount_out, si_id)
                    results["payments_registered"].append({
                        "type": "supplier", "invoice": si_id, "amount": amount_out, "date": tx_date
                    })
                else:
                    logger.error("Failed to register supplier payment on invoice %d: %s", si_id, result)
                    results["errors"].append({"description": desc, "error": str(result)})
            else:
                # No existing supplier invoice — record as direct bank payment voucher.
                # This avoids the systemgenererte 422 from create_supplier_invoice.
                # Accounting: debit expense 7300 (net) + debit VAT 2710 + credit bank 1920
                supplier_name = _extract_supplier_name(desc)
                logger.info("No supplier invoice for %.2f — posting direct bank payment for '%s'", amount_out, supplier_name)
                success = await _post_supplier_bank_payment(
                    amount_out, tx_date, supplier_name, desc, client
                )
                if success:
                    logger.info("Posted supplier bank payment %.2f for '%s'", amount_out, supplier_name)
                    results["payments_registered"].append({
                        "type": "supplier_direct", "description": supplier_name,
                        "amount": amount_out, "date": tx_date
                    })
                else:
                    results["errors"].append({"description": desc, "error": "Failed to post supplier bank payment voucher"})

        # Type 3: Bank fee / interest
        elif "bankgebyr" in desc_lower or "rente" in desc_lower or "gebyr" in desc_lower:
            logger.info("Bank fee/interest: %s (in=%.2f, out=%.2f)", desc, amount_in, amount_out)
            results["skipped"].append({"description": desc, "amount": amount_in or amount_out, "reason": "bank fee — use create_voucher"})

        else:
            results["skipped"].append({"description": desc, "amount": amount_in or amount_out, "reason": "unrecognized transaction type"})

    logger.info("Reconciliation complete: %d payments, %d errors, %d skipped",
                len(results["payments_registered"]), len(results["errors"]), len(results["skipped"]))

    return {"value": results}
