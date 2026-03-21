import csv
import io
import logging
import re
from datetime import date

from ..tripletex import TripletexClient
from .voucher import create_supplier_invoice

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
                try:
                    n["amountIn"] = float(val.replace(",", ".").replace(" ", "")) if val else 0
                except ValueError:
                    n["amountIn"] = 0
            elif kl in ("ut", "out", "utbetaling", "debit", "débito"):
                try:
                    n["amountOut"] = float(val.replace(",", ".").replace(" ", "")) if val else 0
                except ValueError:
                    n["amountOut"] = 0
        if n.get("description"):
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
    """Match outflow to existing supplier invoice by name+amount, then amount only."""
    supplier_name = _extract_name(desc, _SUPPLIER_PREFIXES).lower()

    if supplier_name:
        for si in invoices:
            si_amt = si.get("amount", 0)
            if si_amt <= 0:
                continue
            sn = si.get("supplier", {})
            si_name = (sn.get("name", "") if isinstance(sn, dict) else "").lower()
            if supplier_name in si_name and abs(si_amt - amount) < 0.01:
                return si

    for si in invoices:
        si_amt = si.get("amount", 0)
        if abs(si_amt - amount) < 0.01 and si_amt > 0:
            return si

    return None


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
    })


async def _detect_vat_rate(description: str) -> int:
    """Detect likely VAT rate from transaction description.
    
    Returns 0, 12, 15, or 25 based on keywords.
    Norwegian context: 25% general, 15% food, 12% transport, 0% exempt.
    """
    dl = description.lower()
    # Transport/travel keywords → 12%
    if any(kw in dl for kw in ("transport", "fly", "flight", "taxi", "buss", "bus", "tog", "train", "billett", "ticket", "reise")):
        return 12
    # Food/accommodation keywords → 15%
    if any(kw in dl for kw in ("mat", "food", "kantine", "hotell", "hotel", "overnatting", "catering")):
        return 15
    # Default to 25% for general services/goods
    return 25


def _detect_expense_account(description: str) -> str:
    """Detect likely expense account from transaction description.
    
    Returns standard Norwegian chart-of-accounts number.
    """
    dl = description.lower()
    # Rent / lease → 6300
    if any(kw in dl for kw in ("leie", "husleie", "rent", "miete", "alquiler", "loyer")):
        return "6300"
    # IT / software → 6540
    if any(kw in dl for kw in ("software", "lisens", "license", "it-", "data", "hosting", "sky", "cloud")):
        return "6540"
    # Advertising / marketing → 7330
    if any(kw in dl for kw in ("reklame", "annonse", "marketing", "werbung", "publicidad")):
        return "7330"
    # Travel → 7140
    if any(kw in dl for kw in ("reise", "travel", "fly", "flight", "hotell", "hotel")):
        return "7140"
    # Goods / inventory → 4300
    if any(kw in dl for kw in ("varer", "goods", "lager", "inventory", "innkjøp", "purchase", "material")):
        return "4300"
    # Default: external services → 7300
    return "7300"


async def _post_supplier_bank_payment(
    amount_incl: float, tx_date: str, supplier_name: str,
    description: str, client: TripletexClient,
) -> bool:
    """Post direct supplier bank payment as a voucher (fallback).

    3 postings without vatType to avoid systemgenererte conflicts:
      Debit  <expense> (expense)   amount excl VAT
      Debit  2710 (input VAT) VAT amount
      Credit 1920 (bank)      total incl VAT
    
    VAT rate and expense account are detected from description.
    """
    vat_rate = await _detect_vat_rate(description)
    expense_acct = _detect_expense_account(description)
    amount_excl = round(amount_incl / (1 + vat_rate / 100), 2)
    vat_amount = round(amount_incl - amount_excl, 2)

    acct_numbers = [expense_acct, "1920"]
    if vat_amount > 0:
        acct_numbers.append("2710")

    accounts = {}
    for num in acct_numbers:
        result = await client.get("/ledger/account", params={"number": num, "count": "1"})
        vals = result.get("values", [])
        if vals:
            accounts[num] = vals[0]["id"]
        else:
            logger.error("Account %s not found", num)
            return False

    postings = [
        {"date": tx_date, "description": f"{supplier_name} (expense)",
         "amountGross": amount_excl, "account": {"id": accounts[expense_acct]}},
    ]
    if vat_amount > 0:
        postings.append(
            {"date": tx_date, "description": f"{supplier_name} (input VAT {vat_rate}%)",
             "amountGross": vat_amount, "account": {"id": accounts["2710"]}},
        )
    postings.append(
        {"date": tx_date, "description": f"{supplier_name} (bank)",
         "amountGross": -amount_incl, "account": {"id": accounts["1920"]}},
    )
    voucher = {"date": tx_date, "description": f"Supplier payment: {supplier_name}", "postings": postings}

    result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "true"})
    vid = result.get("value", {}).get("id")
    if not vid:
        # Draft fallback
        result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "false"})
        vid = result.get("value", {}).get("id")
        if vid:
            await client.put(f"/ledger/voucher/{vid}/:sendToLedger")

    if vid:
        logger.info("Supplier bank payment voucher id=%d (acct=%s, VAT=%d%%)", vid, expense_acct, vat_rate)
        return True
    logger.error("Supplier bank payment failed: %s", result)
    return False


async def _post_fee_or_interest_voucher(
    amount: float, tx_date: str, description: str, tx_type: str,
    client: TripletexClient,
) -> bool:
    """Post a bank fee or interest voucher.

    Fee:              Debit 7770 (bankgebyr)        / Credit 1920 (bank)
    Interest income:  Debit 1920 (bank)             / Credit 8040 (renteinntekt)
    Interest expense: Debit 8150 (rentekostnad)     / Credit 1920 (bank)
    """
    if tx_type == "fee":
        debit_acct, credit_acct = "7770", "1920"
    elif tx_type == "interest_income":
        debit_acct, credit_acct = "1920", "8040"
    else:  # interest_expense
        debit_acct, credit_acct = "8150", "1920"

    accounts = {}
    for num in (debit_acct, credit_acct):
        result = await client.get("/ledger/account", params={"number": num, "count": "1"})
        vals = result.get("values", [])
        if vals:
            accounts[num] = vals[0]["id"]
        else:
            logger.error("Account %s not found for %s", num, tx_type)
            return False

    postings = [
        {"date": tx_date, "description": description,
         "amountGross": abs(amount), "account": {"id": accounts[debit_acct]}},
        {"date": tx_date, "description": description,
         "amountGross": -abs(amount), "account": {"id": accounts[credit_acct]}},
    ]
    voucher = {"date": tx_date, "description": description, "postings": postings}

    result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "true"})
    vid = result.get("value", {}).get("id")
    if not vid:
        result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "false"})
        vid = result.get("value", {}).get("id")
        if vid:
            await client.put(f"/ledger/voucher/{vid}/:sendToLedger")

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
      2. Supplier payments → match existing supplier invoice OR create one, then pay
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

    logger.info("Loaded %d customer invoices, %d supplier invoices",
                len(all_invoices), len(all_supplier_invoices))

    # Track paid supplier invoice IDs (they lack amountOutstanding in the API)
    paid_supplier_ids = set()

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
            # Try matching an existing supplier invoice
            available_si = [si for si in all_supplier_invoices if si.get("id") not in paid_supplier_ids]
            matched_si = _match_supplier_invoice(amount_out, desc, available_si)

            if matched_si:
                si_id = matched_si["id"]
                result = await _pay_supplier_invoice(si_id, amount_out, tx_date, client)
                ok = result.get("value") or result.get("id") or (
                    isinstance(result.get("status"), int) and result["status"] < 400)
                if ok:
                    paid_supplier_ids.add(si_id)
                    logger.info("Supplier payment %.2f → supplier invoice %d", amount_out, si_id)
                    results["payments_registered"].append(
                        {"type": "supplier", "invoice": si_id, "amount": amount_out, "date": tx_date})
                else:
                    logger.error("Supplier payment failed on invoice %d: %s", si_id, result)
                    results["errors"].append({"description": desc, "error": str(result)})
            else:
                # No existing supplier invoice → create one and pay it, fall back to direct voucher
                supplier_name = _extract_name(desc, _SUPPLIER_PREFIXES)
                logger.info("No supplier invoice for %.2f — creating for '%s'", amount_out, supplier_name)

                si_result = await create_supplier_invoice({
                    "supplierName": supplier_name,
                    "amountInclVat": amount_out,
                    "date": tx_date,
                    "description": f"Supplier invoice: {supplier_name}",
                }, client)

                si_vid = si_result.get("value", {}).get("id")
                if si_vid:
                    logger.info("Created supplier invoice voucher id=%d, recording payment", si_vid)
                    results["payments_registered"].append(
                        {"type": "supplier_created", "voucher": si_vid,
                         "description": supplier_name, "amount": amount_out, "date": tx_date})
                else:
                    # Fallback: direct bank payment voucher
                    logger.warning("Supplier invoice creation failed — falling back to direct voucher")
                    success = await _post_supplier_bank_payment(amount_out, tx_date, supplier_name, desc, client)
                    if success:
                        results["payments_registered"].append(
                            {"type": "supplier_direct", "description": supplier_name,
                             "amount": amount_out, "date": tx_date})
                    else:
                        results["errors"].append({"description": desc,
                                                  "error": "Failed to post supplier payment"})

        # ----- BANK FEE / INTEREST -----
        elif tx_type in ("fee", "interest_income", "interest_expense"):
            if tx_type == "interest_income":
                amount = amount_in
            elif tx_type == "interest_expense":
                amount = amount_out
            else:  # fee
                amount = amount_out if amount_out > 0 else amount_in
            success = await _post_fee_or_interest_voucher(amount, tx_date, desc, tx_type, client)
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
