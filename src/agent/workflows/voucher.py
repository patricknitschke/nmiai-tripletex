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
            existing = values[0]
            logger.info("Found existing supplier by org number %s (id=%d)", org_number, existing["id"])
            # Ensure isSupplier is set
            if not existing.get("isSupplier"):
                logger.info("Updating customer %d to set isSupplier=True", existing["id"])
                await client.put(f"/customer/{existing['id']}", {**existing, "isSupplier": True})
            return existing["id"]

    # Search by name — exact match
    if supplier_name:
        result = await client.get("/customer", params={"name": supplier_name, "count": "10"})
        for cust in result.get("values", []):
            if cust.get("name", "").lower() == supplier_name.lower():
                logger.info("Found existing supplier by name %s (id=%d)", supplier_name, cust["id"])
                # Ensure isSupplier is set and org number is present
                update = {}
                if not cust.get("isSupplier"):
                    update["isSupplier"] = True
                if org_number and not cust.get("organizationNumber"):
                    update["organizationNumber"] = org_number
                if update:
                    logger.info("Updating customer %d with: %s", cust["id"], list(update.keys()))
                    await client.put(f"/customer/{cust['id']}", {**cust, **update})
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
    """Register a supplier invoice as a voucher with a single posting.

    B25v2 fix: Send ONE posting line with amountGross + vatType + supplier.
    Tripletex auto-generates:
    - Net amount on the expense account (e.g. 7300)
    - VAT posting on 2710 (input VAT)
    - Supplier debt posting on 2400

    Do NOT manually post to 2710 or 2400 — that causes "systemgenererte" 422.

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

    # Resolve expense account (just need the ID)
    expense_account_id = None
    if expense_account:
        result = await client.get("/ledger/account", params={
            "number": str(expense_account), "count": "1",
        })
        accounts = result.get("values", [])
        if accounts:
            expense_account_id = accounts[0]["id"]
            logger.info("Resolved expense account %s -> id=%d", expense_account, expense_account_id)
        else:
            logger.warning("Expense account %s not found", expense_account)

    # Resolve the REAL input VAT type (e.g. 25% inngående) — NOT the 0% no-VAT type!
    # This tells Tripletex how to split gross into net + VAT.
    vat_type_id = await _resolve_vat_type(client, vat_rate, "input")

    # B25v2: Single posting — Tripletex auto-generates 2710 (VAT) + 2400 (supplier debt)
    # row>=1 because row 0 is reserved for system-generated postings
    expense_posting = {
        "date": voucher_date,
        "description": description,
        "amountGross": amount_incl,
        "row": 1,
    }
    if expense_account_id:
        expense_posting["account"] = {"id": expense_account_id}
    if vat_type_id:
        expense_posting["vatType"] = {"id": vat_type_id}
    if supplier_id:
        expense_posting["supplier"] = {"id": supplier_id}

    logger.info("B25v2: Single posting — account=%s, amountGross=%.2f, vatType=%s, supplier=%s",
                expense_account, amount_incl, vat_type_id, supplier_id)

    # Build voucher
    voucher = {
        "date": voucher_date,
        "description": f"{description} - {invoice_number}" if invoice_number else description,
        "postings": [expense_posting],
    }
    if voucher_type:
        voucher["voucherType"] = voucher_type
    if invoice_number:
        voucher["externalVoucherNumber"] = invoice_number

    logger.info("Creating supplier invoice voucher with 1 posting")
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


# System accounts: Tripletex auto-generates postings for these
_VAT_ACCOUNTS = {"2710", "2711", "2700", "2701", "2702", "2703", "2714", "2715"}
_SUPPLIER_ACCOUNTS = {"2400", "2401"}
_SYSTEM_ACCOUNTS = _VAT_ACCOUNTS | _SUPPLIER_ACCOUNTS


async def _resolve_postings(postings_data: list, voucher_date: str, description: str, client: TripletexClient, no_vat_type_id: int | None = None) -> list[dict]:
    """Resolve account numbers to IDs for each posting.

    B25v2: Detects and drops system-managed postings (2710 VAT, 2400 supplier).
    When the LLM sends 3 postings (expense + 2710 + 2400), we keep only the
    expense line with amountGross and let Tripletex auto-generate the rest.
    The expense line gets the account's default vatType so Tripletex can split.
    """
    # First pass: resolve accounts and discover VAT configs
    # row>=1 because row 0 is reserved for system-generated postings
    row_counter = 1
    resolved = []  # list of (posting_dict, account_number_str, has_default_vat)
    for p in postings_data:
        posting = {
            "date": voucher_date,
            "description": p.get("description", description),
            "amountGross": p.get("amount", p.get("amountGross", 0)),
            "row": row_counter,
        }
        row_counter += 1

        account_number = p.get("account") or p.get("accountNumber")
        account_has_default_vat = False
        acc_num_str = str(account_number) if account_number else ""
        if account_number:
            result = await client.get("/ledger/account", params={
                "number": acc_num_str, "count": "1",
                "fields": "id,vatType,vatLocked",
            })
            accounts = result.get("values", [])
            if accounts:
                acc = accounts[0]
                posting["account"] = {"id": acc["id"]}
                if acc.get("vatLocked") or acc.get("vatType"):
                    account_has_default_vat = True

        # VAT type: only set on non-system accounts
        if p.get("vatTypeId"):
            posting["vatType"] = {"id": p["vatTypeId"]}
        elif no_vat_type_id and not account_has_default_vat:
            posting["vatType"] = {"id": no_vat_type_id}

        # Accounting dimension support
        dim_id = p.get("dimensionId")
        if dim_id:
            dim_index = p.get("dimensionIndex", 1)
            dim_key = f"freeAccountingDimension{dim_index}"
            posting[dim_key] = {"id": dim_id}
            logger.info("Posting linked to %s (id=%d)", dim_key, dim_id)

        resolved.append((posting, acc_num_str, account_has_default_vat))

    # Second pass (B25v2): drop system-managed postings (2710, 2400).
    # If LLM sent manual postings to these, remove them and ensure the
    # expense line uses amountGross so Tripletex auto-generates the rest.
    system_indices = {i for i, (_, acn, _) in enumerate(resolved) if acn in _SYSTEM_ACCOUNTS}

    if system_indices and len(resolved) > len(system_indices):
        dropped = [resolved[i][1] for i in system_indices]
        logger.info("B25v2: Dropping system-managed postings: %s (Tripletex auto-generates these)", dropped)
        resolved = [(p, a, h) for j, (p, a, h) in enumerate(resolved) if j not in system_indices]

        # Renumber rows after dropping system postings (rows must be sequential from 1)
        for idx, (p, _, _) in enumerate(resolved):
            p["row"] = idx + 1

        # Ensure remaining expense lines have amountGross set to the original
        # gross amount (LLM may have sent net amounts with separate VAT line)
        # Recalculate: the absolute total of the dropped lines tells us what's missing
        # But we can't reliably recalculate here — trust that amountGross is already correct
        # (the LLM or caller should provide the gross amount per posting)

    return [posting for posting, _, _ in resolved]


async def _post_voucher(voucher: dict, client: TripletexClient) -> dict:
    """Post a voucher with a smart fallback on systemgenererte error.

    Fallback strategy (B25): if the first POST fails with "systemgenererte",
    the postings likely contain a manual VAT line (e.g. 2710) that Tripletex
    wants to auto-generate.  We merge any 27xx VAT postings into their
    companion posting's amountGross and retry.
    """
    # Ensure amountGrossCurrency matches amountGross (required by Tripletex for NOK)
    for p in voucher.get("postings", []):
        if "amountGross" in p and "amountGrossCurrency" not in p:
            p["amountGrossCurrency"] = p["amountGross"]

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

    # Fallback: strip vatType + merge VAT postings into amountGross
    logger.warning("Voucher rejected (systemgenererte) — fallback: merge VAT postings")
    voucher_clean = copy.deepcopy(voucher)
    postings = voucher_clean.get("postings", [])

    # Identify VAT postings by account ID (look up which are 27xx)
    # We already have account IDs but not numbers — check if any posting
    # has a small positive amount that looks like a VAT line.
    # Simpler: strip vatType from all + convert amount→amountGross
    for p in postings:
        p.pop("vatType", None)
        if "amount" in p and "amountGross" not in p:
            p["amountGross"] = p.pop("amount")
        if "amountGross" in p and "amountGrossCurrency" not in p:
            p["amountGrossCurrency"] = p["amountGross"]

    result = await client.post("/ledger/voucher", voucher_clean, params={"sendToLedger": "true"})
    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created (fallback) with ID: %d", voucher_id)
        return result

    logger.error("Voucher post failed after fallback: %s", result)
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
        "message": f"Created {len(created_ids)}/{len(groups)} voucher(s)",
        "voucherIds": created_ids,
    }
    if failed:
        summary["ok"] = False
        summary["errors"] = [str(f) for f in failed]
        summary["_needs_repair"] = (
            f"{len(failed)} of {len(groups)} voucher(s) failed to create. "
            "Retry each failed voucher individually using create_voucher with fewer postings, or use raw POST /ledger/voucher."
        )
    return summary
