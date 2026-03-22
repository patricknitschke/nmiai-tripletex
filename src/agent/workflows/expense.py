import asyncio
import logging
from datetime import date

from ..tripletex import TripletexClient
from .voucher import _resolve_supplier, _resolve_vat_type, _resolve_no_vat_type, _post_voucher

logger = logging.getLogger("agent.workflows.expense")


async def register_expense(data: dict, client: TripletexClient) -> dict:
    """Register an expense from a receipt as a voucher with correct VAT and department.

    B25v2 fix: Two postings only:
    1. Debit: expense account with amountGross + real vatType (e.g. 25% input)
       - Tripletex auto-generates the VAT posting on 2710
    2. Credit: bank/payment account with negative amountGross

    Do NOT manually post to 2710 — Tripletex creates it automatically.

    Input data fields:
    - description: what the expense is for (e.g. "Oppbevaringsboks")
    - amountInclVat: total amount including VAT
    - amountExclVat: amount excluding VAT (calculated if not provided)
    - vatRate: VAT rate percent (default 25)
    - expenseAccount: expense account number (e.g. 6540 for inventory, 7140 for travel)
    - departmentName: department to allocate to (e.g. "Lager", "Markedsføring")
    - date: expense/receipt date
    - paymentAccount: credit account (default 1920 bank)
    - supplierName: who issued the receipt (e.g. "Biltema")
    """
    today = date.today().isoformat()
    expense_date = data.get("date", today)
    description = data.get("description", "Expense")
    vat_rate = data.get("vatRate", 25)
    expense_account = data.get("expenseAccount", data.get("account"))
    payment_account = data.get("paymentAccount", 1920)
    supplier_name = data.get("supplierName", "")

    # Calculate amounts with consistency guard
    amount_incl = data.get("amountInclVat") or data.get("amount") or data.get("totalAmount")
    amount_excl = data.get("amountExclVat")

    if amount_incl and amount_excl:
        # Both provided — verify consistency
        expected_incl = round(amount_excl * (1 + vat_rate / 100), 2)
        if abs(amount_incl - expected_incl) > 1.0:
            return {"error": f"Amount mismatch: amountInclVat={amount_incl} but amountExclVat={amount_excl} × {1 + vat_rate/100} = {expected_incl}"}
    elif amount_incl and not amount_excl:
        amount_excl = round(amount_incl / (1 + vat_rate / 100), 2)
    elif amount_excl and not amount_incl:
        amount_incl = round(amount_excl * (1 + vat_rate / 100), 2)
    else:
        return {"error": "No amount provided for expense"}

    full_desc = f"{supplier_name} - {description}" if supplier_name else description
    logger.info("Registering expense: %s, total=%.2f, excl=%.2f, VAT rate=%.0f%%",
                full_desc, amount_incl, amount_excl, vat_rate)

    # Resolve accounts, VAT type, supplier, and department in parallel
    # Batch both account numbers in one API call
    account_numbers = ",".join(str(a) for a in {expense_account, payment_account} if a)

    async def _resolve_accounts():
        result = await client.get("/ledger/account", params={
            "number": account_numbers, "count": "10",
        })
        return {a.get("number"): a["id"] for a in result.get("values", []) if a.get("id")}

    async def _resolve_dept():
        department_id = data.get("departmentId")
        if department_id:
            return department_id
        dept_name = data.get("departmentName") or data.get("department")
        if dept_name:
            from .department import resolve_or_create_department
            return await resolve_or_create_department(dept_name, client)
        return None

    async def _resolve_sup():
        if supplier_name:
            return await _resolve_supplier(data, client)
        return None

    accounts_map, vat_type_id, no_vat_type_id, department_id, supplier_id = await asyncio.gather(
        _resolve_accounts(),
        _resolve_vat_type(client, vat_rate, "input"),
        _resolve_no_vat_type(client),
        _resolve_dept(),
        _resolve_sup(),
    )

    expense_account_id = accounts_map.get(expense_account)
    payment_account_id = accounts_map.get(payment_account)

    # Fail fast if expense account not found — that's the critical one
    if not expense_account_id:
        return {"error": f"Expense account {expense_account} not found in Tripletex"}

    if not payment_account_id:
        return {"error": f"Payment account {payment_account} not found in Tripletex"}

    if vat_type_id is None:
        return {"error": f"Could not find incoming VAT type for rate {vat_rate}%"}

    warnings = []

    # B25v2: Two postings — expense with amountGross + vatType, bank with negative amountGross
    # Tripletex auto-generates the VAT posting on 2710.
    logger.info("B25v2: 2-posting structure — expense=%s, gross=%.2f, vatType=%s",
                expense_account, amount_incl, vat_type_id)

    postings = []

    # row>=1 because row 0 is reserved for system-generated postings
    expense_posting = {
        "date": expense_date,
        "description": full_desc,
        "amountGross": amount_incl,
        "account": {"id": expense_account_id},
        "row": 1,
    }
    if vat_type_id:
        expense_posting["vatType"] = {"id": vat_type_id}
    if department_id:
        expense_posting["department"] = {"id": department_id}
    if supplier_id:
        expense_posting["supplier"] = {"id": supplier_id}
    postings.append(expense_posting)

    payment_posting = {
        "date": expense_date,
        "description": full_desc,
        "amountGross": -amount_incl,
        "account": {"id": payment_account_id},
        "row": 2,
    }
    # Explicitly set no-VAT to prevent Tripletex auto-applying default VAT on bank account
    if no_vat_type_id:
        payment_posting["vatType"] = {"id": no_vat_type_id}
    postings.append(payment_posting)

    voucher = {
        "date": expense_date,
        "description": full_desc,
        "postings": postings,
    }

    logger.info("Creating expense voucher with %d postings", len(postings))
    result = await _post_voucher(voucher, client)

    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Expense voucher created with ID: %d", voucher_id)
    else:
        logger.error("Failed to create expense voucher: %s", result)

    if warnings:
        result["warnings"] = warnings
    return result
