import csv
import io
import logging
import re
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.bank_reconciliation")


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

async def _fetch_all_pages(client: TripletexClient, endpoint: str, params: dict, page_size: int = 1000) -> list:
    """Fetch all records from a paginated Tripletex endpoint."""
    all_values = []
    offset = 0
    while True:
        page_params = {**params, "count": str(page_size), "from": str(offset)}
        result = await client.get(endpoint, params=page_params)
        values = result.get("values", [])
        all_values.extend(values)
        if len(values) < page_size:
            break
        offset += page_size
    return all_values


# ---------------------------------------------------------------------------
# Description parsing helpers
# ---------------------------------------------------------------------------

_SUPPLIER_PREFIXES = [
    r"betaling\s+leverand(?:ør|or)\s+",
    r"leverand(?:ør|or)(?:betaling)?\s+",
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

_CUSTOMER_PREFIXES = [
    r"innbetaling\s+fra\s+",
    r"innbetaling\s+",
    r"indbetaling\s+fra\s+",
    r"indbetaling\s+",
    r"einzahlung\s+(?:von\s+)?",
    r"paiement\s+(?:de\s+)?",
    r"pagamento\s+(?:de\s+)?",
    r"pago\s+(?:de\s+)?",
    r"recebimento\s+(?:de\s+)?",
    r"payment\s+(?:from\s+)?",
]

_INVOICE_REF_PATTERNS = [
    r"(?:faktura|fakt|inv|invoice|factura|rechnung|fatura)\s*[#:.\\-]?\s*(\d[\w\-]*)",
    r"(?:ref|reference)\s*[#:.\\-]?\s*(\d[\w\-]*)",
]

_FEE_KEYWORDS = ["bankgebyr", "gebyr", "gebühr", "fee", "taxa", "tarifa", "frais"]
_INTEREST_KEYWORDS = ["rente", "zinsen", "interest", "juros", "interés", "intérêt"]
_SUPPLIER_KEYWORDS = [
    "leverand", "lieferant", "fournisseur", "fornecedor", "proveedor", "supplier",
]


def _extract_name(description: str, prefixes: list[str]) -> str:
    """Strip known prefixes and trailing reference info to extract a name."""
    text = description.strip()
    for prefix in prefixes:
        m = re.match(prefix, text, re.IGNORECASE)
        if m:
            text = text[m.end():].strip()
            break
    for sep in [" - ", " – ", " — ", " faktura ", " fakt ", " inv ", " invoice ", " ref "]:
        idx = text.lower().find(sep)
        if idx > 0:
            text = text[:idx].strip()
            break
    return text or description.strip()


def _extract_invoice_ref(description: str) -> str | None:
    """Try to extract an invoice/reference number from a description."""
    for pattern in _INVOICE_REF_PATTERNS:
        m = re.search(pattern, description, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _normalize_date(raw: str) -> str:
    """Convert DD.MM.YYYY, DD/MM/YYYY, or ISO with time component to YYYY-MM-DD.
    
    Assumes European day-first format for DD.MM and DD/MM patterns.
    ISO-like formats (YYYY-MM-DD...) are truncated to date-only.
    """
    raw = raw.strip()
    # Already ISO with optional time component: 2025-03-04T... or 2025-03-04 10:...
    iso_m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if iso_m:
        return f"{iso_m.group(1)}-{iso_m.group(2)}-{iso_m.group(3)}"
    # European: DD.MM.YYYY or DD/MM/YYYY
    m = re.match(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", raw)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return raw


def _parse_amount(raw: str) -> float:
    """Parse locale-formatted amount text into float.

    Supports formats like 1 282,21 / 1,282.21 / -1282.21 / (1 282,21).
    """
    if raw is None:
        return 0.0

    text = str(raw).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return 0.0

    is_negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("()-+")

    if "," in text and "." in text:
        # If both separators are present, treat the rightmost as decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "")
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        amount = float(text)
    except ValueError:
        logger.warning("Could not parse amount: %r", raw)
        return 0.0
    return -amount if is_negative else amount


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_csv(csv_text: str) -> list[dict]:
    """Parse bank statement CSV into rows. Handles Norwegian CSV formats."""
    for delimiter in [";", ","]:
        try:
            reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
            rows = list(reader)
            if rows and len(rows[0]) > 1:
                return rows
        except Exception:
            continue
    return []


def _normalize_rows(parsed: list[dict]) -> list[dict]:
    """Normalize parsed CSV rows into standard fields."""
    rows = []
    for row in parsed:
        n = {}
        for key, val in row.items():
            kl = key.strip().lower() if key else ""
            val = val.strip() if val else ""
            if kl in ("dato", "date", "bokføringsdato", "fecha", "datum"):
                n["date"] = _normalize_date(val)
            elif kl in ("forklaring", "description", "tekst", "text", "beschreibung",
                         "descripción", "descrição"):
                n["description"] = val
            elif kl in ("inn", "in", "innbetaling", "credit", "kredit", "crédito"):
                n["amountIn"] = max(_parse_amount(val), 0)
            elif kl in ("ut", "out", "utbetaling", "debit", "débito", "debet"):
                n["amountOut"] = abs(_parse_amount(val))
            elif kl in ("beløp", "belop", "amount", "belopp", "importe", "valor"):
                amt = _parse_amount(val)
                if amt >= 0:
                    n["amountIn"] = amt
                else:
                    n["amountOut"] = abs(amt)
        if n.get("description"):
            n.setdefault("amountIn", 0)
            n.setdefault("amountOut", 0)
            rows.append(n)
    return rows


# ---------------------------------------------------------------------------
# Transaction classification
# ---------------------------------------------------------------------------

def _classify(desc: str, amount_in: float, amount_out: float) -> str:
    """Classify a bank row.
    
    Returns: 'customer'|'supplier'|'fee'|'interest_income'|'interest_expense'|'unknown'.
    """
    dl = desc.lower()
    if any(kw in dl for kw in _FEE_KEYWORDS):
        return "fee"
    if any(kw in dl for kw in _INTEREST_KEYWORDS):
        # Differentiate income vs expense based on money direction
        if amount_in > 0:
            return "interest_income"
        return "interest_expense"
    if amount_out > 0 and any(kw in dl for kw in _SUPPLIER_KEYWORDS):
        return "supplier"
    if amount_in > 0:
        return "customer"
    if amount_out > 0:
        return "supplier"
    return "unknown"


# ---------------------------------------------------------------------------
# Invoice matching
# ---------------------------------------------------------------------------

def _match_customer_invoice(amount: float, desc: str, invoices: list[dict]) -> dict | None:
    """Multi-pass match: ref number → name+amount → exact amount → name-only → any unpaid."""
    customer_name = _extract_name(desc, _CUSTOMER_PREFIXES).lower()
    invoice_ref = _extract_invoice_ref(desc)

    def _outstanding(inv: dict) -> float:
        return inv.get("amountOutstanding", 0)

    def _customer_name_of(inv: dict) -> str:
        c = inv.get("customer", {})
        return (c.get("name", "") if isinstance(c, dict) else "").lower()

    # Pass 1: invoice number
    if invoice_ref:
        for inv in invoices:
            if _outstanding(inv) <= 0:
                continue
            if str(inv.get("invoiceNumber", "")) == invoice_ref:
                return inv

    # Pass 2: customer name + exact amount
    if customer_name:
        for inv in invoices:
            o = _outstanding(inv)
            if o <= 0:
                continue
            if customer_name in _customer_name_of(inv) and abs(o - amount) < 0.01:
                return inv

    # Pass 3: customer name + any unpaid (partial payment)
    if customer_name:
        for inv in invoices:
            if _outstanding(inv) <= 0:
                continue
            if customer_name in _customer_name_of(inv):
                return inv

    # Pass 4: exact amount (only if no name-based match found)
    for inv in invoices:
        o = _outstanding(inv)
        if o <= 0:
            continue
        if abs(o - amount) < 0.01:
            return inv

    return None


def _match_supplier_invoice(amount: float, desc: str, invoices: list[dict]) -> dict | None:
    """Match outflow to existing supplier invoice by name+amount, then amount only.

    Uses _si_outstanding to support partial payments across multiple rows.
    """
    supplier_name = _extract_name(desc, _SUPPLIER_PREFIXES).lower()

    if supplier_name:
        for si in invoices:
            o = _si_outstanding(si)
            if o <= 0:
                continue
            sn = si.get("supplier", {})
            si_name = (sn.get("name", "") if isinstance(sn, dict) else "").lower()
            if supplier_name in si_name and abs(o - amount) < 0.01:
                return si

    # Pass 2: name + any remaining outstanding (partial payment)
    if supplier_name:
        for si in invoices:
            o = _si_outstanding(si)
            if o <= 0:
                continue
            sn = si.get("supplier", {})
            si_name = (sn.get("name", "") if isinstance(sn, dict) else "").lower()
            if supplier_name in si_name:
                return si

    # Pass 3: exact amount only
    for si in invoices:
        o = _si_outstanding(si)
        if o <= 0:
            continue
        if abs(o - amount) < 0.01:
            return si

    return None


def _si_outstanding(si: dict) -> float:
    """Get remaining outstanding amount for a supplier invoice."""
    return si.get("_outstanding", si.get("amount", 0))


# ---------------------------------------------------------------------------
# Payment helpers
# ---------------------------------------------------------------------------

async def _pay_customer_invoice(
    inv_id: int, amount: float, tx_date: str,
    payment_type_id: int, client: TripletexClient,
) -> dict:
    """Register a payment on a customer invoice."""
    return await client.put(f"/invoice/{inv_id}/:payment", params={
        "paymentDate": tx_date,
        "paymentTypeId": str(payment_type_id),
        "paidAmount": str(amount),
    })


async def _pay_supplier_invoice(
    si_id: int, amount: float, tx_date: str, client: TripletexClient,
) -> dict:
    """Register a payment on a supplier invoice."""
    return await client.post(f"/supplierInvoice/{si_id}/:addPayment", params={
        "paymentType": "0",
        "amount": str(amount),
        "paymentDate": tx_date,
        "useDefaultPaymentType": "true",
        "partialPayment": "true",
    })


# All accounts used by fee/interest vouchers — pre-fetched once in reconcile_bank_statement.
_FEE_INTEREST_ACCOUNTS = {"1920", "7770", "8040", "8050"}


async def _prefetch_fee_accounts(client: TripletexClient) -> dict[str, int]:
    """Fetch account IDs for all fee/interest accounts in one GET."""
    acct_numbers = ",".join(sorted(_FEE_INTEREST_ACCOUNTS))
    result = await client.get("/ledger/account", params={"number": acct_numbers, "count": "10"})
    return {str(a.get("number")): a["id"] for a in result.get("values", []) if a.get("id")}


async def _post_fee_or_interest_voucher(
    amount: float, tx_date: str, description: str, tx_type: str,
    client: TripletexClient, account_cache: dict[str, int],
) -> bool:
    """Post a bank fee or interest voucher.

    Fee:              Debit 7770 (bankgebyr)        / Credit 1920 (bank)
    Interest income:  Debit 1920 (bank)             / Credit 8040 (renteinntekt)
    Interest expense: Debit 8050 (rentekostnad)     / Credit 1920 (bank)
    """
    if tx_type == "fee":
        debit_acct, credit_acct = "7770", "1920"
    elif tx_type == "interest_income":
        debit_acct, credit_acct = "1920", "8040"
    else:  # interest_expense
        debit_acct, credit_acct = "8050", "1920"

    if debit_acct not in account_cache:
        logger.error("Account %s not found for %s", debit_acct, tx_type)
        return False
    if credit_acct not in account_cache:
        logger.error("Account %s not found for %s", credit_acct, tx_type)
        return False

    accounts = account_cache

    postings = [
        {"date": tx_date, "description": description,
            "amountGross": abs(amount), "amountGrossCurrency": abs(amount),
            "account": {"id": accounts[debit_acct]}, "row": 1},
        {"date": tx_date, "description": description,
            "amountGross": -abs(amount), "amountGrossCurrency": -abs(amount),
            "account": {"id": accounts[credit_acct]}, "row": 2},
    ]
    voucher = {"date": tx_date, "description": description, "postings": postings}

    result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "true"})
    vid = result.get("value", {}).get("id")

    if vid:
        logger.info("%s voucher id=%d", tx_type.capitalize(), vid)
        return True
    logger.error("%s voucher failed: %s", tx_type.capitalize(), result)
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def reconcile_bank_statement(data: dict, client: TripletexClient) -> dict:
    """Reconcile a bank statement CSV against open invoices in Tripletex.

    Deterministic, mostly LLM-free pipeline:
      1. Customer payments → multi-pass match to invoices, register payment
         (supports repeated partial payments on the same invoice)
            2. Supplier payments → match existing supplier invoice and register payment
      3. Bank fees/interest → create proper voucher entries
    """
    results = {"payments_registered": [], "fees_posted": [], "errors": [], "skipped": []}

    # --- Parse CSV ---
    csv_content = data.get("csvContent", "")
    rows = data.get("rows", [])
    if csv_content and not rows:
        parsed = _parse_csv(csv_content)
        if not parsed:
            return {"error": "Could not parse CSV content"}
        rows = _normalize_rows(parsed)

    if not rows:
        return {"error": "No transaction rows to process"}

    logger.info("Processing %d bank statement rows", len(rows))

    # --- Pre-fetch all data (GETs are free) ---
    all_invoices = await _fetch_all_pages(client, "/invoice", params={
        "invoiceDateFrom": "2000-01-01", "invoiceDateTo": "2099-12-31",
    })

    all_supplier_invoices = await _fetch_all_pages(client, "/supplierInvoice", params={
        "invoiceDateFrom": "2000-01-01", "invoiceDateTo": "2099-12-31",
    })

    pt_result = await client.get("/invoice/paymentType", params={"count": "1"})
    payment_type_id = (pt_result.get("values") or [{}])[0].get("id")

    # Pre-fetch fee/interest account IDs once (saves 1 GET per extra fee/interest row)
    fee_account_cache = await _prefetch_fee_accounts(client)

    logger.info("Loaded %d customer invoices, %d supplier invoices",
                len(all_invoices), len(all_supplier_invoices))

    # Seed supplier invoice outstanding tracking for partial payment support
    for si in all_supplier_invoices:
        si["_outstanding"] = si.get("outstandingAmount", si.get("amount", 0))

    for row in rows:
        desc = row.get("description", "")
        amount_in = row.get("amountIn", 0)
        amount_out = abs(row.get("amountOut", 0))
        tx_date = row.get("date", date.today().isoformat())
        tx_type = _classify(desc, amount_in, amount_out)

        # ----- CUSTOMER PAYMENT -----
        if tx_type == "customer":
            matched = _match_customer_invoice(amount_in, desc, all_invoices)
            if matched and payment_type_id:
                inv_id = matched["id"]
                result = await _pay_customer_invoice(inv_id, amount_in, tx_date, payment_type_id, client)
                if result.get("value"):
                    matched["amountOutstanding"] = matched.get("amountOutstanding", 0) - amount_in
                    logger.info("Customer payment %.2f → invoice %d (outstanding: %.2f)",
                                amount_in, inv_id, matched["amountOutstanding"])
                    results["payments_registered"].append(
                        {"type": "customer", "invoice": inv_id, "amount": amount_in, "date": tx_date})
                else:
                    logger.error("Customer payment failed on invoice %d: %s", inv_id, result)
                    results["errors"].append({"description": desc, "error": str(result)})
            else:
                logger.warning("No match for customer payment: %s (%.2f)", desc, amount_in)
                results["skipped"].append({"description": desc, "amount": amount_in,
                                           "reason": "no matching customer invoice"})

        # ----- SUPPLIER PAYMENT -----
        elif tx_type == "supplier":
            matched_si = _match_supplier_invoice(amount_out, desc, all_supplier_invoices)

            if matched_si:
                si_id = matched_si["id"]
                result = await _pay_supplier_invoice(si_id, amount_out, tx_date, client)
                ok = result.get("value") or result.get("id") or (
                    isinstance(result.get("status"), int) and result["status"] < 400)
                if ok:
                    matched_si["_outstanding"] = _si_outstanding(matched_si) - amount_out
                    logger.info("Supplier payment %.2f → supplier invoice %d (outstanding: %.2f)",
                                amount_out, si_id, matched_si["_outstanding"])
                    results["payments_registered"].append(
                        {"type": "supplier", "invoice": si_id, "amount": amount_out, "date": tx_date})
                else:
                    logger.error("Supplier payment failed on invoice %d: %s", si_id, result)
                    results["errors"].append({"description": desc, "error": str(result)})
            else:
                supplier_name = _extract_name(desc, _SUPPLIER_PREFIXES)
                logger.warning(
                    "No matching supplier invoice for payment %.2f (%s)",
                    amount_out,
                    supplier_name,
                )
                results["skipped"].append({
                    "description": desc,
                    "amount": amount_out,
                    "reason": "no matching supplier invoice",
                })

        # ----- BANK FEE / INTEREST -----
        elif tx_type in ("fee", "interest_income", "interest_expense"):
            if tx_type == "interest_income":
                amount = amount_in
            elif tx_type == "interest_expense":
                amount = amount_out
            else:  # fee
                amount = amount_out if amount_out > 0 else amount_in
            success = await _post_fee_or_interest_voucher(amount, tx_date, desc, tx_type, client, fee_account_cache)
            if success:
                results["fees_posted"].append(
                    {"type": tx_type, "description": desc, "amount": amount, "date": tx_date})
            else:
                results["errors"].append({"description": desc,
                                          "error": f"Failed to post {tx_type} voucher"})

        else:
            results["skipped"].append({"description": desc,
                                       "amount": amount_in or amount_out,
                                       "reason": "unrecognized transaction"})

    logger.info("Reconciliation: %d payments, %d fees, %d errors, %d skipped",
                len(results["payments_registered"]), len(results["fees_posted"]),
                len(results["errors"]), len(results["skipped"]))

    output = {"value": results}
    if results["errors"]:
        output["ok"] = False
        output["errors"] = [e.get("error", str(e)) for e in results["errors"]]
    if results["skipped"]:
        output["warnings"] = [
            f"Skipped: {s.get('description')} — {s.get('reason')}" for s in results["skipped"]]
    return output
