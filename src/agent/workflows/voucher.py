import copy
import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.voucher")


async def _resolve_supplier(data: dict, client: TripletexClient) -> int | None:
    """Find or create the supplier. Returns supplier ID."""
    supplier_id = data.get("supplierId")
    if supplier_id:
        return supplier_id

    org_number = data.get("supplierOrgNumber") or data.get("organizationNumber")
    supplier_name = data.get("supplierName")

    # Search by org number first
    if org_number:
        result = await client.get("/customer", params={"organizationNumber": org_number, "count": "1"})
        values = result.get("values", [])
        if values:
            logger.info("Found existing supplier by org number %s (id=%d)", org_number, values[0]["id"])
            return values[0]["id"]

    # Search by name — exact match
    if supplier_name:
        result = await client.get("/customer", params={"name": supplier_name, "count": "10"})
        for cust in result.get("values", []):
            if cust.get("name", "").lower() == supplier_name.lower():
                logger.info("Found existing supplier by name %s (id=%d)", supplier_name, cust["id"])
                return cust["id"]

    # Create supplier if we have enough info
    if supplier_name:
        payload = {"name": supplier_name, "isCustomer": False, "isSupplier": True}
        if org_number:
            payload["organizationNumber"] = org_number
        result = await client.post("/customer", payload)
        new_id = result.get("value", {}).get("id")
        if new_id:
            logger.info("Created supplier %s (id=%d)", supplier_name, new_id)
            return new_id
        logger.error("Failed to create supplier: %s", result)

    return None


async def _resolve_vat_type(client: TripletexClient, rate: float, direction: str = "input") -> int | None:
    """Find VAT type ID by rate. direction: 'input' for incoming, 'output' for outgoing."""
    result = await client.get("/ledger/vatType", params={"count": "100"})
    vat_types = result.get("values", [])

    keyword = "inngående" if direction == "input" else "utgående"

    # First pass: match by keyword + rate
    for vt in vat_types:
        name = vt.get("name", "").lower()
        pct = vt.get("percentage", 0)
        if keyword in name and abs(pct - rate) < 0.01:
            logger.info("Resolved %s VAT type: %.1f%% -> id=%d (%s)", direction, rate, vt["id"], vt.get("name"))
            return vt["id"]

    # Second pass: try alternate keywords (some systems use different names)
    alt_keywords = {
        "input": ["innkommende", "fradrag", "input"],
        "output": ["utgående", "salg", "output"],
    }
    for alt in alt_keywords.get(direction, []):
        for vt in vat_types:
            name = vt.get("name", "").lower()
            pct = vt.get("percentage", 0)
            if alt in name and abs(pct - rate) < 0.01:
                logger.info("Resolved %s VAT type (alt '%s'): %.1f%% -> id=%d (%s)", direction, alt, rate, vt["id"], vt.get("name"))
                return vt["id"]

    # Last resort: match by rate only but EXCLUDE the wrong direction
    wrong_keyword = "utgående" if direction == "input" else "inngående"
    for vt in vat_types:
        name = vt.get("name", "").lower()
        pct = vt.get("percentage", 0)
        if abs(pct - rate) < 0.01 and wrong_keyword not in name:
            logger.info("Resolved VAT type by rate (excluding %s): %.1f%% -> id=%d (%s)", wrong_keyword, rate, vt["id"], vt.get("name"))
            return vt["id"]

    logger.warning("Could not find VAT type for %.1f%% (%s)", rate, direction)
    return None


async def create_supplier_invoice(data: dict, client: TripletexClient) -> dict:
    """Register a supplier invoice as a voucher with correct postings.

    Creates a voucher with:
    - Debit: expense account (e.g. 7300) for amount excl VAT
    - Debit: input VAT account (2710) for VAT amount
    - Credit: supplier account (2400) for total amount incl VAT

    Input data fields:
    - supplierName, supplierOrgNumber: supplier identification
    - invoiceNumber: the supplier's invoice reference (e.g. INV-2026-2076)
    - amountInclVat: total amount including VAT
    - amountExclVat: amount excluding VAT (calculated if not provided)
    - vatRate: VAT rate in percent (default 25)
    - expenseAccount: account number for the expense (e.g. 7300)
    - description: what the invoice is for
    - date: invoice date (defaults to today)
    """
    today = date.today().isoformat()
    voucher_date = data.get("date", today)
    description = data.get("description", "Supplier invoice")
    invoice_number = data.get("invoiceNumber", "")
    vat_rate = data.get("vatRate", 25)
    expense_account = data.get("expenseAccount", data.get("account"))
    voucher_type = data.get("voucherType")

    # Calculate amounts
    amount_incl = data.get("amountInclVat") or data.get("amount") or data.get("totalAmount")
    amount_excl = data.get("amountExclVat")

    if amount_incl and not amount_excl:
        amount_excl = round(amount_incl / (1 + vat_rate / 100), 2)
    elif amount_excl and not amount_incl:
        amount_incl = round(amount_excl * (1 + vat_rate / 100), 2)
    elif not amount_incl and not amount_excl:
        return {"error": "No amount provided for supplier invoice"}

    vat_amount = round(amount_incl - amount_excl, 2)

    logger.info("Supplier invoice: %s, total=%.2f, excl=%.2f, VAT=%.2f (%.0f%%)",
                description, amount_incl, amount_excl, vat_amount, vat_rate)

    # Resolve supplier
    supplier_id = await _resolve_supplier(data, client)

    # Resolve expense account
    expense_account_id = None
    if expense_account:
        result = await client.get("/ledger/account", params={"number": str(expense_account), "count": "1"})
        accounts = result.get("values", [])
        if accounts:
            expense_account_id = accounts[0]["id"]
            logger.info("Resolved expense account %s -> id=%d", expense_account, expense_account_id)
        else:
            logger.warning("Expense account %s not found", expense_account)

    # Resolve supplier payable account (2400 leverandorgjeld)
    supplier_account_id = None
    result = await client.get("/ledger/account", params={"number": "2400", "count": "1"})
    accounts = result.get("values", [])
    if accounts:
        supplier_account_id = accounts[0]["id"]
        logger.info("Resolved supplier account 2400 -> id=%d", supplier_account_id)

    # B22 fix: Manually split into 3 postings with 'amount' (net field) and explicit
    # no-VAT type. Using amountGross + vatType on accounts with default VAT config
    # triggers "systemgenererte" 422 errors. The no-VAT type prevents Tripletex from
    # auto-generating system VAT postings.
    no_vat_type_id = await _resolve_no_vat_type(client)

    # Resolve input VAT account (2710 inngående merverdiavgift)
    vat_account_id = None
    result = await client.get("/ledger/account", params={"number": "2710", "count": "1"})
    accounts = result.get("values", [])
    if accounts:
        vat_account_id = accounts[0]["id"]
        logger.info("Resolved VAT account 2710 -> id=%d", vat_account_id)

    postings = []

    # 1. Debit expense account — amount EXCL VAT, explicit no-VAT type
    expense_posting = {
        "date": voucher_date,
        "description": description,
        "amount": amount_excl,
    }
    if expense_account_id:
        expense_posting["account"] = {"id": expense_account_id}
    if no_vat_type_id:
        expense_posting["vatType"] = {"id": no_vat_type_id}
    if supplier_id:
        expense_posting["supplier"] = {"id": supplier_id}
    postings.append(expense_posting)

    # 2. Debit input VAT account (2710) — VAT amount, explicit no-VAT type
    vat_posting = {
        "date": voucher_date,
        "description": f"MVA {description}",
        "amount": vat_amount,
    }
    if vat_account_id:
        vat_posting["account"] = {"id": vat_account_id}
    if no_vat_type_id:
        vat_posting["vatType"] = {"id": no_vat_type_id}
    if supplier_id:
        vat_posting["supplier"] = {"id": supplier_id}
    postings.append(vat_posting)

    # 3. Credit supplier account (2400) — total incl VAT, explicit no-VAT type
    supplier_posting = {
        "date": voucher_date,
        "description": description,
        "amount": -amount_incl,
    }
    if supplier_account_id:
        supplier_posting["account"] = {"id": supplier_account_id}
    if no_vat_type_id:
        supplier_posting["vatType"] = {"id": no_vat_type_id}
    if supplier_id:
        supplier_posting["supplier"] = {"id": supplier_id}
    postings.append(supplier_posting)

    # Build voucher
    voucher = {
        "date": voucher_date,
        "description": f"{description} - {invoice_number}" if invoice_number else description,
        "postings": postings,
    }
    if voucher_type:
        voucher["voucherType"] = voucher_type
    if invoice_number:
        voucher["externalVoucherNumber"] = invoice_number

    logger.info("Creating voucher with %d postings", len(postings))
    return await _post_voucher(voucher, client)


def _split_into_balanced_pairs(postings_data: list) -> list[list[dict]]:
    """Try to split postings into balanced debit/credit pairs.

    For closing tasks the LLM often sends 6 postings (3 journal entries)
    in one call. We split them into groups of 2 (each balanced to 0)
    so each can be posted as a separate voucher.

    Falls back to returning the full list as one group if pairs don't balance.
    """
    if len(postings_data) <= 2:
        return [postings_data]

    # Try greedy pairing: take 2 at a time, check if they sum to 0
    pairs = []
    remaining = list(postings_data)
    i = 0
    while i < len(remaining) - 1:
        pair = [remaining[i], remaining[i + 1]]
        total = sum(p.get("amount", p.get("amountGross", 0)) for p in pair)
        if abs(total) < 0.01:  # balanced pair
            pairs.append(pair)
            i += 2
        else:
            # Not a pair — try as a single group from here
            break

    if i < len(remaining):
        # Remaining postings go as one group
        pairs.append(remaining[i:])

    # Only use split if we got multiple groups
    if len(pairs) > 1:
        return pairs
    return [postings_data]


async def _resolve_no_vat_type(client: TripletexClient) -> int | None:
    """Find the 'no VAT' / exempt (0%) VAT type. Cached after first call."""
    result = await client.get("/ledger/vatType", params={"count": "100"})
    vat_types = result.get("values", [])

    # Prefer explicit 0% / exempt types
    for vt in vat_types:
        pct = vt.get("percentage", -1)
        name = vt.get("name", "").lower()
        if pct == 0 and ("fri" in name or "exempt" in name or "ingen" in name or "utenfor" in name or "0" in name):
            logger.info("Resolved no-VAT type: id=%d (%s)", vt["id"], vt.get("name"))
            return vt["id"]

    # Fallback: any 0% type
    for vt in vat_types:
        if vt.get("percentage", -1) == 0:
            logger.info("Resolved no-VAT type (fallback 0%%): id=%d (%s)", vt["id"], vt.get("name"))
            return vt["id"]

    logger.warning("Could not find any 0%% VAT type")
    return None


async def _resolve_postings(postings_data: list, voucher_date: str, description: str, client: TripletexClient, no_vat_type_id: int | None = None) -> list[dict]:
    """Resolve account numbers to IDs for a list of posting data."""
    postings = []
    for p in postings_data:
        posting = {
            "date": voucher_date,
            "description": p.get("description", description),
            "amountGross": p.get("amount", p.get("amountGross", 0)),
        }

        # Resolve account by number
        account_number = p.get("account") or p.get("accountNumber")
        if account_number:
            result = await client.get("/ledger/account", params={"number": str(account_number), "count": "1"})
            accounts = result.get("values", [])
            if accounts:
                posting["account"] = {"id": accounts[0]["id"]}

        if p.get("vatTypeId"):
            posting["vatType"] = {"id": p["vatTypeId"]}
        elif no_vat_type_id:
            # B21 fix: explicitly set no-VAT type to prevent Tripletex from
            # auto-generating system VAT postings (causes "systemgenererte" 422)
            posting["vatType"] = {"id": no_vat_type_id}

        # Accounting dimension support (freeAccountingDimension1/2/3)
        dim_id = p.get("dimensionId")
        if dim_id:
            dim_index = p.get("dimensionIndex", 1)
            dim_key = f"freeAccountingDimension{dim_index}"
            posting[dim_key] = {"id": dim_id}
            logger.info("Posting linked to %s (id=%d)", dim_key, dim_id)

        postings.append(posting)
    return postings


async def _post_voucher(voucher: dict, client: TripletexClient) -> dict:
    """Post a single voucher with multi-tier retry on systemgenererte error.

    The "systemgenererte" error means Tripletex auto-generates VAT postings
    that conflict with ours.  The root cause varies by account type:
    - Expense accounts (6xxx) have default INPUT VAT → output no-VAT type fails
    - Revenue accounts (3xxx) have default OUTPUT VAT → input no-VAT type fails

    Retry strategy (B23 fix):
    1. Original payload + sendToLedger=true
    2. amount (net) + NO vatType at all + sendToLedger=true
    3. amount (net) + NO vatType + sendToLedger=false
    4. amount (net) + explicit no-VAT type + sendToLedger=true (legacy B22)
    5. amount (net) + explicit no-VAT type + sendToLedger=false
    """
    result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "true"})

    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created with ID: %d", voucher_id)
        return result

    # Check for systemgenererte error
    error_msg = str(result.get("validationMessages", result.get("message", "")))
    if "systemgenererte" not in error_msg.lower():
        logger.error("Failed to create voucher: %s", result)
        return result

    # Build net-amount version: amountGross → amount, STRIP vatType entirely
    # B23 fix: stripping vatType lets the account's default VAT config work
    # without generating conflicting system postings
    voucher_no_vat = copy.deepcopy(voucher)
    for p in voucher_no_vat.get("postings", []):
        if "amountGross" in p:
            p["amount"] = p.pop("amountGross")
        p.pop("vatType", None)

    # Retry 1: net amounts + NO vatType + sendToLedger=true
    logger.warning("Voucher rejected (systemgenererte) — retry 1: amount + strip vatType")
    result = await client.post("/ledger/voucher", voucher_no_vat, params={"sendToLedger": "true"})
    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created (no vatType) with ID: %d", voucher_id)
        return result

    # Retry 2: net amounts + NO vatType + sendToLedger=false (draft)
    logger.warning("Still rejected — retry 2: amount + strip vatType + draft")
    result = await client.post("/ledger/voucher", voucher_no_vat, params={"sendToLedger": "false"})
    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created as draft (no vatType) with ID: %d", voucher_id)
        return result

    # Retry 3: net amounts + explicit no-VAT type + sendToLedger=true (legacy B22)
    logger.warning("No-vatType failed — retry 3: amount + explicit no-VAT type")
    voucher_explicit = copy.deepcopy(voucher)
    no_vat_id = await _resolve_no_vat_type(client)
    for p in voucher_explicit.get("postings", []):
        if "amountGross" in p:
            p["amount"] = p.pop("amountGross")
        if no_vat_id:
            p["vatType"] = {"id": no_vat_id}
        else:
            p.pop("vatType", None)
    result = await client.post("/ledger/voucher", voucher_explicit, params={"sendToLedger": "true"})
    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created (explicit no-VAT) with ID: %d", voucher_id)
        return result

    # Retry 4: explicit no-VAT + draft
    logger.warning("Explicit no-VAT rejected — retry 4: + sendToLedger=false")
    result = await client.post("/ledger/voucher", voucher_explicit, params={"sendToLedger": "false"})
    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created as draft (explicit no-VAT) with ID: %d", voucher_id)
        return result

    logger.error("All voucher retries failed: %s", result)
    return result


async def create_voucher(data: dict, client: TripletexClient) -> dict:
    """Create a manual journal entry / voucher with custom postings.

    For general ledger entries that don't fit the supplier invoice pattern.
    Each posting needs: account (number), amount, description.

    If the postings contain multiple balanced debit/credit pairs (e.g. from
    a closing task), they are automatically split into separate vouchers.
    """
    today = date.today().isoformat()
    voucher_date = data.get("date", today)
    description = data.get("description", "Manual voucher")

    postings_data = data.get("postings", [])
    if not postings_data:
        return {"error": "No postings provided for voucher"}

    # B21 fix: resolve 0% VAT type to explicitly mark postings as no-VAT.
    # This prevents Tripletex from auto-generating system VAT postings on accounts
    # that have default VAT configuration (causes "systemgenererte" 422 errors).
    no_vat_type_id = await _resolve_no_vat_type(client)

    # Auto-split: if LLM sent multiple journal entries as one call, split them
    groups = _split_into_balanced_pairs(postings_data)
    if len(groups) > 1:
        logger.info("Auto-splitting %d postings into %d separate vouchers", len(postings_data), len(groups))

    results = []
    for i, group in enumerate(groups):
        postings = await _resolve_postings(group, voucher_date, description, client, no_vat_type_id=no_vat_type_id)

        voucher = {
            "date": voucher_date,
            "description": description,
            "postings": postings,
        }

        logger.info("Creating voucher %d/%d with %d postings", i + 1, len(groups), len(postings))
        result = await _post_voucher(voucher, client)
        results.append(result)

    # Return last result (or combined summary)
    if len(results) == 1:
        return results[0]

    # Multiple vouchers — return summary
    created_ids = [r.get("value", {}).get("id") for r in results if r.get("value", {}).get("id")]
    failed = [r for r in results if not r.get("value", {}).get("id")]
    summary = {
        "message": f"Created {len(created_ids)} voucher(s)",
        "voucherIds": created_ids,
    }
    if failed:
        summary["errors"] = [str(f) for f in failed]
    return summary
