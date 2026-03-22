import asyncio
import logging
from datetime import date, timedelta

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.voucher")


async def _search_vouchers_in_window(client: TripletexClient, voucher_date: str, *, count: int = 200) -> list[dict]:
    """Search vouchers for the given accounting date.

    Tripletex requires dateFrom/dateTo for voucher search. We search the exact
    booking day and do exact idempotency matching client-side.
    """
    date_to = (date.fromisoformat(voucher_date) + timedelta(days=1)).isoformat()
    result = await client.get(
        "/ledger/voucher",
        params={
            "dateFrom": voucher_date,
            "dateTo": date_to,
            "count": str(count),
            "fields": "id,date,description,vendorInvoiceNumber,externalVoucherNumber,postings(account(*),amountGross,supplier(*),customer(*))",
        },
    )
    return result.get("values", [])


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _posting_signature(postings: list[dict]) -> list[tuple[str, float, int | None, int | None]]:
    signature = []
    for posting in postings:
        account = posting.get("account") or {}
        supplier = posting.get("supplier") or {}
        customer = posting.get("customer") or {}
        signature.append(
            (
                str(account.get("id") or ""),
                round(float(posting.get("amountGross", 0) or 0), 2),
                supplier.get("id"),
                customer.get("id"),
            )
        )
    return sorted(signature)


async def _find_existing_supplier_invoice(
    client: TripletexClient,
    *,
    voucher_date: str,
    invoice_number: str,
    supplier_id: int | None,
) -> dict | None:
    if not invoice_number:
        return None

    for voucher in await _search_vouchers_in_window(client, voucher_date):
        if _normalize_text(voucher.get("vendorInvoiceNumber")) != _normalize_text(invoice_number):
            continue
        if supplier_id is None:
            return voucher
        for posting in voucher.get("postings", []):
            posting_supplier = (posting.get("supplier") or {}).get("id")
            if posting_supplier == supplier_id:
                return voucher
    return None


async def _find_existing_voucher_by_external_ref(
    client: TripletexClient,
    *,
    voucher_date: str,
    external_voucher_number: str,
    description: str,
    postings: list[dict],
) -> dict | None:
    if not external_voucher_number:
        return None

    target_signature = _posting_signature(postings)
    normalized_description = _normalize_text(description)
    normalized_external_ref = _normalize_text(external_voucher_number)

    for voucher in await _search_vouchers_in_window(client, voucher_date):
        if _normalize_text(voucher.get("externalVoucherNumber")) != normalized_external_ref:
            continue
        if _normalize_text(voucher.get("description")) != normalized_description:
            continue
        if _posting_signature(voucher.get("postings", [])) == target_signature:
            return voucher
    return None


async def _resolve_accounts_batch(client: TripletexClient, account_numbers: set[str]) -> dict[str, dict]:
    """Resolve account numbers to account objects using one GET call."""
    if not account_numbers:
        return {}

    requested = {str(n) for n in account_numbers}
    result = await client.get(
        "/ledger/account",
        params={
            "number": ",".join(sorted(requested)),
            # Account search can return fuzzy/expanded matches; request enough rows and
            # filter strictly by exact number below.
            "count": str(max(len(requested) * 10, 100)),
            "fields": "id,number,vatType,vatLocked,ledgerType",
        },
    )
    return {
        str(acc.get("number")): acc
        for acc in result.get("values", [])
        if acc.get("number") is not None and str(acc.get("number")) in requested
    }


def _extract_account_numbers(postings_data: list[dict]) -> set[str]:
    return {
        str(p.get("account") or p.get("accountNumber"))
        for p in postings_data
        if p.get("account") or p.get("accountNumber")
    }


async def _ensure_accounts_exist(client: TripletexClient, account_numbers: set[str]) -> tuple[dict[str, dict], list[str]]:
    """Resolve accounts once and fail fast on missing accounts.

    Guardrail: manual voucher workflows must not auto-create accounts. Missing
    chart-of-accounts configuration should be surfaced explicitly.
    """
    if not account_numbers:
        return {}, []

    account_map = await _resolve_accounts_batch(client, account_numbers)
    unresolved = sorted(acc for acc in account_numbers if acc not in account_map)
    if unresolved:
        logger.error("Missing account(s) in chart of accounts: %s", ", ".join(unresolved))
    return account_map, unresolved


async def _get_vat_types(client: TripletexClient, direction: str | None = None) -> list[dict]:
    """Fetch VAT types, cached per client+direction to avoid repeated GETs."""
    cache_key = f"_vat_types_{direction or 'all'}"
    cached = getattr(client, cache_key, None)
    if cached is not None:
        return cached

    params = {"count": "100"}
    if direction in {"input", "output"}:
        params["typeOfVat"] = "INCOMING" if direction == "input" else "OUTGOING"
    result = await client.get("/ledger/vatType", params=params)
    values = result.get("values", [])

    # B-VAT fix: if filtered fetch returned empty (API 500 or no results),
    # fallback to unfiltered fetch before caching empty list
    if not values and direction is not None:
        logger.warning("Filtered vatType fetch (%s) returned empty — falling back to unfiltered", direction)
        all_types = await _get_vat_types(client, direction=None)
        type_filter = "INCOMING" if direction == "input" else "OUTGOING"
        values = [vt for vt in all_types if vt.get("typeOfVat") == type_filter]

    if not values and "error" in result:
        logger.warning("vatType fetch failed (%s), NOT caching empty result", result.get("error", ""))
        return []

    setattr(client, cache_key, values)
    return values


async def _resolve_supplier(data: dict, client: TripletexClient) -> int | None:
    """Find or create the supplier. Returns supplier ID."""
    supplier_id = data.get("supplierId")
    if supplier_id:
        return supplier_id

    org_number = data.get("supplierOrgNumber") or data.get("organizationNumber")
    supplier_name = data.get("supplierName")

    # Search by org number first — use /supplier endpoint (not /customer)
    if org_number:
        result = await client.get("/supplier", params={"organizationNumber": org_number, "count": "1"})
        values = result.get("values", [])
        if values:
            existing = values[0]
            logger.info("Found existing supplier by org number %s (id=%d)", org_number, existing["id"])
            return existing["id"]

    # Search by name via /supplier, then keep strict exact match locally.
    if supplier_name:
        result = await client.get("/supplier", params={"name": supplier_name, "count": "20"})
        for sup in result.get("values", []):
            if sup.get("name", "").lower() == supplier_name.lower():
                logger.info("Found existing supplier by name %s (id=%d)", supplier_name, sup["id"])
                return sup["id"]

    # Create supplier if we have enough info — use POST /supplier
    if supplier_name:
        payload = {"name": supplier_name}
        if org_number:
            payload["organizationNumber"] = org_number
        result = await client.post("/supplier", payload)
        new_id = result.get("value", {}).get("id")
        if new_id:
            logger.info("Created supplier %s (id=%d)", supplier_name, new_id)
            return new_id
        logger.error("Failed to create supplier: %s", result)

    return None


async def _resolve_vat_type(client: TripletexClient, rate: float, direction: str = "input") -> int | None:
    """Find VAT type ID by rate. direction: 'input' for incoming, 'output' for outgoing."""
    vat_types = await _get_vat_types(client, direction=direction)
    for vt in vat_types:
        pct = vt.get("percentage", 0)
        if abs(pct - rate) < 0.01:
            logger.info("Resolved %s VAT type: %.1f%% -> id=%d (%s)", direction, rate, vt["id"], vt.get("name"))
            return vt["id"]

    # B-VAT fix: fallback to unfiltered vat types and match by rate + typeOfVat
    logger.warning("Filtered VAT type lookup failed for %.1f%% (%s) — trying unfiltered", rate, direction)
    all_types = await _get_vat_types(client, direction=None)
    type_filter = "INCOMING" if direction == "input" else "OUTGOING"
    for vt in all_types:
        pct = vt.get("percentage", 0)
        if abs(pct - rate) < 0.01 and vt.get("typeOfVat") == type_filter:
            logger.info("Resolved %s VAT type (unfiltered fallback): %.1f%% -> id=%d (%s)", direction, rate, vt["id"], vt.get("name"))
            return vt["id"]

    logger.warning("Could not find VAT type for %.1f%% (%s) even in unfiltered list", rate, direction)
    return None


async def create_supplier_invoice(data: dict, client: TripletexClient) -> dict:
    """Register a supplier invoice as a balanced 2-posting voucher.

    B36 fix: Two postings (same pattern as register_expense):
    1. Debit: expense account (e.g. 7300) with amountGross + vatType (input)
       - Tripletex auto-generates VAT posting on 2710
    2. Credit: AP account 2400 (Leverandørgjeld) with -amountGross + supplier ref

    Do NOT manually post to 2710 — Tripletex creates it from vatType.
    DO manually post to 2400 — Tripletex does NOT auto-generate the AP entry.

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
    project_id = data.get("projectId")

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

    # Resolve supplier, accounts, and VAT type in parallel (independent lookups)
    account_numbers = {"2400"}
    if expense_account:
        account_numbers.add(str(expense_account))

    supplier_id, (account_map, unresolved_accounts), vat_type_id, no_vat_type_id = await asyncio.gather(
        _resolve_supplier(data, client),
        _ensure_accounts_exist(client, account_numbers),
        _resolve_vat_type(client, vat_rate, "input"),
        _resolve_no_vat_type(client),
    )

    if not data.get("allowDuplicate") and invoice_number:
        existing_voucher = await _find_existing_supplier_invoice(
            client,
            voucher_date=voucher_date,
            invoice_number=invoice_number,
            supplier_id=supplier_id,
        )
        if existing_voucher:
            return {
                "error": "Supplier invoice already exists for this supplier/invoice number/date",
                "existingVoucherId": existing_voucher.get("id"),
                "vendorInvoiceNumber": invoice_number,
                "supplierId": supplier_id,
                "date": voucher_date,
            }

    if unresolved_accounts:
        return {
            "error": f"Missing ledger account(s): {', '.join(unresolved_accounts)}"
        }

    expense_account_id = None
    if expense_account:
        exp_acc = account_map.get(str(expense_account))
        if exp_acc:
            expense_account_id = exp_acc["id"]
            logger.info("Resolved expense account %s -> id=%d", expense_account, expense_account_id)
        else:
            logger.warning("Expense account %s not found", expense_account)

    ap_account_id = None
    ap_acc = account_map.get("2400")
    if ap_acc:
        ap_account_id = ap_acc["id"]
        logger.info("Resolved AP account 2400 -> id=%d", ap_account_id)
    else:
        logger.warning("AP account 2400 not found")

    # B36: Two postings — expense debit + AP credit (same pattern as register_expense)
    # Tripletex auto-generates the VAT posting on 2710 from vatType.
    postings = []

    # Row 1: Debit expense account with gross amount + VAT type
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
    if project_id:
        expense_posting["project"] = {"id": project_id}
    postings.append(expense_posting)

    # Row 2: Credit AP account 2400 with negative gross + supplier ref
    # Explicitly set no-VAT type to prevent Tripletex auto-applying default VAT on 2400
    # B71: no_vat_type_id already resolved in parallel gather above
    ap_posting = {
        "date": voucher_date,
        "description": description,
        "amountGross": -amount_incl,
        "row": 2,
    }
    if ap_account_id:
        ap_posting["account"] = {"id": ap_account_id}
    if no_vat_type_id:
        ap_posting["vatType"] = {"id": no_vat_type_id}
    if supplier_id:
        ap_posting["supplier"] = {"id": supplier_id}
    postings.append(ap_posting)

    logger.info("B36: 2-posting structure — expense=%s (%.2f), AP=2400 (%.2f), vatType=%s, supplier=%s",
                expense_account, amount_incl, -amount_incl, vat_type_id, supplier_id)

    # Build voucher
    voucher = {
        "date": voucher_date,
        "description": f"{description} - {invoice_number}" if invoice_number else description,
        "postings": postings,
    }
    if voucher_type:
        voucher["voucherType"] = voucher_type
    if invoice_number:
        voucher["vendorInvoiceNumber"] = invoice_number

    logger.info("Creating supplier invoice voucher with %d postings", len(postings))
    return await _post_voucher(voucher, client)


def _split_into_balanced_pairs(postings_data: list) -> list[list[dict]]:
    """Compatibility helper: splitting is handled by the caller, not this workflow."""
    return [postings_data]


async def _resolve_no_vat_type(client: TripletexClient) -> int | None:
    """Find the universal 'no VAT' type (MVA-kode 0: Ingen avgiftsbehandling).

    B-VAT fix: Account 2400 is locked to MVA-kode 0. We must NOT return kode 5
    ("Ingen utgående avgift") or any other 0% type. Priority order:
    1. number==0 (the actual MVA-kode 0)
    2. "ingen avgiftsbehandling" in name (exact canonical name)
    3. Any 0% type that is NOT an OUTGOING/INCOMING specific type
    4. Last resort: any 0% type
    """
    cached = getattr(client, "_no_vat_type_id", None)
    if cached is not None:
        return cached if cached != -1 else None

    vat_types = await _get_vat_types(client)

    # Priority 1: MVA-kode 0 by number field
    # B70 fix: number is a string from the API ("0"), not int
    for vt in vat_types:
        if str(vt.get("number", "")).strip() == "0" and vt.get("percentage", -1) == 0:
            logger.info("Resolved no-VAT type by number=0: id=%d (%s)", vt["id"], vt.get("name"))
            client._no_vat_type_id = vt["id"]
            return vt["id"]

    # Priority 2: exact canonical name "ingen avgiftsbehandling"
    for vt in vat_types:
        if vt.get("percentage", -1) == 0:
            name = vt.get("name", "").lower()
            if "ingen avgiftsbehandling" in name:
                logger.info("Resolved no-VAT type by name match: id=%d (%s)", vt["id"], vt.get("name"))
                client._no_vat_type_id = vt["id"]
                return vt["id"]

    # Priority 3: 0% type that is NOT direction-specific (not INCOMING/OUTGOING)
    for vt in vat_types:
        if vt.get("percentage", -1) == 0:
            type_of_vat = vt.get("typeOfVat", "")
            if type_of_vat not in ("INCOMING", "OUTGOING"):
                logger.info("Resolved no-VAT type (non-directional 0%%): id=%d (%s)", vt["id"], vt.get("name"))
                client._no_vat_type_id = vt["id"]
                return vt["id"]

    # Priority 4: any 0% type as last resort
    for vt in vat_types:
        if vt.get("percentage", -1) == 0:
            logger.info("Resolved no-VAT type (last resort 0%%): id=%d (%s)", vt["id"], vt.get("name"))
            client._no_vat_type_id = vt["id"]
            return vt["id"]

    logger.warning("Could not find any 0%% VAT type")
    client._no_vat_type_id = -1
    return None


async def _resolve_postings(postings_data: list, voucher_date: str, description: str, client: TripletexClient, no_vat_type_id: int | None = None, voucher_customer_id: int | None = None, account_map: dict[str, dict] | None = None, force_no_vat: bool = False) -> list[dict]:
    """Resolve account numbers to IDs for each posting using one account GET."""
    if account_map is None:
        account_numbers = _extract_account_numbers(postings_data)
        account_map = await _resolve_accounts_batch(client, account_numbers)

    row_counter = 1
    resolved = []
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
        account_ledger_type = None
        if account_number and acc_num_str in account_map:
            acc = account_map[acc_num_str]
            posting["account"] = {"id": acc["id"]}
            if acc.get("vatLocked") or acc.get("vatType"):
                account_has_default_vat = True
            account_ledger_type = acc.get("ledgerType")
        elif account_number:
            logger.warning("Account %s could not be resolved", acc_num_str)

        # VAT type: only set on non-system accounts
        # B59 fix: force_no_vat overrides account defaults for accruals/reversals/salary
        if force_no_vat and no_vat_type_id:
            posting["vatType"] = {"id": no_vat_type_id}
        elif p.get("vatTypeId"):
            posting["vatType"] = {"id": p["vatTypeId"]}
        elif no_vat_type_id and not account_has_default_vat:
            posting["vatType"] = {"id": no_vat_type_id}

        # Customer / supplier references (required for AR / AP accounts)
        # B41v2: also detect via ledgerType from account lookup (authoritative)
        customer_id = p.get("customerId")
        if customer_id:
            posting["customer"] = {"id": customer_id}
        elif account_ledger_type == "CUSTOMER" and voucher_customer_id:
            posting["customer"] = {"id": voucher_customer_id}
            logger.info("B41v2: Auto-attached voucher customer %d to ledgerType=CUSTOMER account %s", voucher_customer_id, acc_num_str)
        elif account_ledger_type == "CUSTOMER":
            logger.warning("B41v2: Account %s has ledgerType=CUSTOMER but no customerId available — voucher may 422", acc_num_str)
        supplier_id = p.get("supplierId")
        if supplier_id:
            posting["supplier"] = {"id": supplier_id}
        elif account_ledger_type == "SUPPLIER" and not p.get("supplierId"):
            logger.warning("B41v2: Account %s has ledgerType=SUPPLIER but no supplierId on posting — voucher may 422", acc_num_str)

        # Accounting dimension support
        dim_id = p.get("dimensionId")
        if dim_id:
            dim_index = p.get("dimensionIndex", 1)
            dim_key = f"freeAccountingDimension{dim_index}"
            posting[dim_key] = {"id": dim_id}
            logger.info("Posting linked to %s (id=%d)", dim_key, dim_id)

        # Safety net: forward any freeAccountingDimensionX keys passed directly
        for key in ("freeAccountingDimension1", "freeAccountingDimension2", "freeAccountingDimension3"):
            if key in p and key not in posting:
                posting[key] = p[key]
                logger.info("Forwarded %s from posting data", key)

        resolved.append(posting)

    return resolved


async def _post_voucher(voucher: dict, client: TripletexClient) -> dict:
    """Post voucher once; caller must provide a valid payload."""
    currency_code = str(voucher.get("currency", {}).get("code", "")).upper()
    should_set_amount_gross_currency = not currency_code or currency_code == "NOK"

    for p in voucher.get("postings", []):
        if should_set_amount_gross_currency and "amountGross" in p and "amountGrossCurrency" not in p:
            p["amountGrossCurrency"] = p["amountGross"]

    result = await client.post("/ledger/voucher", voucher, params={"sendToLedger": "true"})

    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created with ID: %d", voucher_id)
        return result

    logger.error("Failed to create voucher: %s", result)
    return result


async def _resolve_customer_for_voucher(data: dict, client: TripletexClient) -> int | None:
    """Resolve customer ID from voucher-level customerName/customerId.

    B41 fix: Tripletex requires customer.id on postings to AR accounts (1500-1599).
    This resolves the customer once at the voucher level and injects it into AR postings.
    """
    customer_id = data.get("customerId")
    if customer_id:
        return customer_id

    customer_name = data.get("customerName")
    if not customer_name:
        return None

    result = await client.get("/customer", params={"customerName": customer_name, "count": "20"})
    for cust in result.get("values", []):
        if cust.get("name", "").lower() == customer_name.lower():
            logger.info("Resolved voucher customer '%s' -> id=%d", customer_name, cust["id"])
            return cust["id"]

    logger.warning("Could not resolve customer '%s' for voucher", customer_name)
    return None


async def create_voucher(data: dict, client: TripletexClient) -> dict:
    """Create a manual journal entry / voucher with custom postings.

    For general ledger entries that don't fit the supplier invoice pattern.
    Each posting needs: account (number), amount, description.
    """
    today = date.today().isoformat()
    voucher_date = data.get("date", today)
    description = data.get("description", "Manual voucher")
    external_voucher_number = data.get("externalVoucherNumber") or data.get("idempotencyKey")

    postings_data = data.get("postings", [])
    if not postings_data:
        return {"error": "No postings provided for voucher"}

    # B35 guardrail: strip system-generated accounts (2710 VAT) when expense accounts
    # are present. Tripletex auto-generates 2710 from vatType on the expense posting.
    # Manually posting to 2710 conflicts with auto-generated postings → 422.
    _SYSTEM_VAT_ACCOUNTS = {"2710", "2711", "2712", "2713", "2714", "2715"}
    account_nums_in_postings = {
        str(p.get("account") or p.get("accountNumber") or "") for p in postings_data
    }
    has_expense_account = any(
        str(p.get("account") or p.get("accountNumber") or "").startswith(("4", "5", "6", "7"))
        for p in postings_data
    )
    if has_expense_account and account_nums_in_postings & _SYSTEM_VAT_ACCOUNTS and not data.get("keepVatAccounts"):
        stripped = [str(n) for n in account_nums_in_postings & _SYSTEM_VAT_ACCOUNTS]
        postings_data = [
            p for p in postings_data
            if str(p.get("account") or p.get("accountNumber") or "") not in _SYSTEM_VAT_ACCOUNTS
        ]
        logger.info("B35 guardrail: stripped LLM-generated system accounts %s (auto-generated by Tripletex from vatType)", stripped)

    # Never invent voucher amounts; every posting must include amount or amountGross.
    missing_amount_rows = [
        idx + 1
        for idx, posting in enumerate(postings_data)
        if posting.get("amount") is None and posting.get("amountGross") is None
    ]
    if missing_amount_rows:
        rows = ", ".join(str(r) for r in missing_amount_rows)
        return {"error": f"Missing amount on posting row(s): {rows}"}

    # Pre-validate all accounts in one GET and fail fast on unresolved account numbers.
    account_numbers = _extract_account_numbers(postings_data)
    account_map, unresolved_accounts = await _ensure_accounts_exist(client, account_numbers)
    if unresolved_accounts:
        return {
            "error": f"Missing ledger account(s): {', '.join(unresolved_accounts)}"
        }

    # Resolve customer once; _resolve_postings attaches it to CUSTOMER ledger lines.
    voucher_customer_id = await _resolve_customer_for_voucher(data, client)

    # B21 fix: resolve 0% VAT type to explicitly mark postings as no-VAT.
    # This prevents Tripletex from auto-generating system VAT postings on accounts
    # that have default VAT configuration (causes "systemgenererte" 422 errors).
    no_vat_type_id = await _resolve_no_vat_type(client)

    # B59 fix: for accruals, reversals, salary entries — force no-VAT on ALL postings
    # to prevent Tripletex from auto-generating VAT on expense accounts with defaults.
    force_no_vat = data.get("noVat", False)

    postings = await _resolve_postings(
        postings_data,
        voucher_date,
        description,
        client,
        no_vat_type_id=no_vat_type_id,
        voucher_customer_id=voucher_customer_id,
        account_map=account_map,
        force_no_vat=force_no_vat,
    )

    if external_voucher_number and not data.get("allowDuplicate"):
        existing_voucher = await _find_existing_voucher_by_external_ref(
            client,
            voucher_date=voucher_date,
            external_voucher_number=str(external_voucher_number),
            description=description,
            postings=postings,
        )
        if existing_voucher:
            return {
                "error": "Voucher already exists for this externalVoucherNumber/date",
                "existingVoucherId": existing_voucher.get("id"),
                "externalVoucherNumber": str(external_voucher_number),
                "date": voucher_date,
            }

    voucher = {
        "date": voucher_date,
        "description": description,
        "postings": postings,
    }
    if external_voucher_number:
        voucher["externalVoucherNumber"] = str(external_voucher_number)

    logger.info("Creating voucher with %d postings", len(postings))
    return await _post_voucher(voucher, client)
