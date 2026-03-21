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

    # Search by name
    if supplier_name:
        result = await client.get("/customer", params={"name": supplier_name, "count": "1"})
        values = result.get("values", [])
        if values:
            logger.info("Found existing supplier by name %s (id=%d)", supplier_name, values[0]["id"])
            return values[0]["id"]

    # Create supplier if we have enough info
    if supplier_name:
        payload = {"name": supplier_name, "isCustomer": False, "isSupplier": True}
        if org_number:
            payload["organizationNumber"] = org_number
        result = await client.post("/customer", json=payload)
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

    keyword = "Inngående" if direction == "input" else "Utgående"

    for vt in vat_types:
        name = vt.get("name", "")
        pct = vt.get("percentage", 0)
        if keyword in name and abs(pct - rate) < 0.01:
            logger.info("Resolved %s VAT type: %.1f%% -> id=%d (%s)", direction, rate, vt["id"], name)
            return vt["id"]

    # Fallback: match by percentage only
    for vt in vat_types:
        pct = vt.get("percentage", 0)
        if abs(pct - rate) < 0.01:
            logger.info("Resolved VAT type by rate only: %.1f%% -> id=%d", rate, vt["id"])
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

    # Resolve input VAT type
    vat_type_id = None
    if vat_rate > 0:
        vat_type_id = await _resolve_vat_type(client, vat_rate, "input")

    # Get voucher type for supplier invoice
    vt_result = await client.get("/ledger/voucherType", params={"name": "Leverandorfaktura", "count": "1"})
    voucher_types = vt_result.get("values", [])
    if not voucher_types:
        # Fallback: try without special chars, or other names
        for name in ["Leverandørfaktura", "Inngående faktura"]:
            vt_result = await client.get("/ledger/voucherType", params={"name": name, "count": "1"})
            voucher_types = vt_result.get("values", [])
            if voucher_types:
                break

    voucher_type = None
    if voucher_types:
        voucher_type = {"id": voucher_types[0]["id"]}
        logger.info("Voucher type: %s (id=%d)", voucher_types[0].get("name"), voucher_types[0]["id"])

    # Build postings
    # When vatType is set on the expense posting, Tripletex auto-splits into
    # net amount on expense account + VAT amount on the VAT account.
    # So we only need 2 postings: expense (gross=incl VAT) and supplier credit.
    postings = []

    # 1. Debit expense account with VAT type — Tripletex handles the VAT split
    expense_posting = {
        "date": voucher_date,
        "description": description,
        "amountGross": amount_incl,
    }
    if expense_account_id:
        expense_posting["account"] = {"id": expense_account_id}
    if vat_type_id:
        expense_posting["vatType"] = {"id": vat_type_id}
    if supplier_id:
        expense_posting["supplier"] = {"id": supplier_id}
    postings.append(expense_posting)

    # 2. Credit supplier account (total amount incl VAT) — negative = credit
    supplier_posting = {
        "date": voucher_date,
        "description": description,
        "amountGross": -amount_incl,
    }
    if supplier_account_id:
        supplier_posting["account"] = {"id": supplier_account_id}
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
    result = await client.post("/ledger/voucher", json=voucher, params={"sendToLedger": "true"})

    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Voucher created with ID: %d", voucher_id)
    else:
        logger.error("Failed to create voucher: %s", result)

    return result


async def create_voucher(data: dict, client: TripletexClient) -> dict:
    """Create a manual journal entry / voucher with custom postings.

    For general ledger entries that don't fit the supplier invoice pattern.
    Each posting needs: account (number), amount, description.
    """
    today = date.today().isoformat()
    voucher_date = data.get("date", today)
    description = data.get("description", "Manual voucher")

    postings_data = data.get("postings", [])
    if not postings_data:
        return {"error": "No postings provided for voucher"}

    # Resolve accounts for each posting
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

        # Accounting dimension support (freeAccountingDimension1/2/3)
        dim_id = p.get("dimensionId")
        if dim_id:
            dim_index = p.get("dimensionIndex", 1)
            dim_key = f"freeAccountingDimension{dim_index}"
            posting[dim_key] = {"id": dim_id}
            logger.info("Posting linked to %s (id=%d)", dim_key, dim_id)

        postings.append(posting)

    voucher = {
        "date": voucher_date,
        "description": description,
        "postings": postings,
    }

    logger.info("Creating manual voucher with %d postings", len(postings))
    result = await client.post("/ledger/voucher", json=voucher, params={"sendToLedger": "true"})

    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Manual voucher created with ID: %d", voucher_id)
    else:
        logger.error("Failed to create manual voucher: %s", result)

    return result
