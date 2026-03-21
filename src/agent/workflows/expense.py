import logging
from datetime import date

from ..tripletex import TripletexClient
from .voucher import _resolve_vat_type, _post_voucher

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

    # Calculate amounts
    amount_incl = data.get("amountInclVat") or data.get("amount") or data.get("totalAmount")
    amount_excl = data.get("amountExclVat")

    if amount_incl and not amount_excl:
        amount_excl = round(amount_incl / (1 + vat_rate / 100), 2)
    elif amount_excl and not amount_incl:
        amount_incl = round(amount_excl * (1 + vat_rate / 100), 2)
    elif not amount_incl and not amount_excl:
        return {"error": "No amount provided for expense"}

    vat_amount = round(amount_incl - amount_excl, 2)

    full_desc = f"{supplier_name} - {description}" if supplier_name else description
    logger.info("Registering expense: %s, total=%.2f, excl=%.2f, VAT=%.2f (%.0f%%)",
                full_desc, amount_incl, amount_excl, vat_amount, vat_rate)

    # Resolve expense account (just need the ID)
    warnings = []
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
            warnings.append(f"Expense account {expense_account} not found — posting will lack account reference")
            logger.warning("Expense account %s not found", expense_account)

    # Resolve payment/bank account
    payment_account_id = None
    result = await client.get("/ledger/account", params={"number": str(payment_account), "count": "1"})
    accounts = result.get("values", [])
    if accounts:
        payment_account_id = accounts[0]["id"]
    else:
        warnings.append(f"Payment account {payment_account} not found — credit posting will lack account reference")
        logger.warning("Payment account %s not found", payment_account)

    # Resolve the REAL input VAT type (e.g. 25% inngående) — NOT the 0% no-VAT type!
    vat_type_id = await _resolve_vat_type(client, vat_rate, "input")

    # Resolve department
    department_id = data.get("departmentId")
    department_name = data.get("departmentName") or data.get("department")

    if not department_id and department_name:
        from .department import resolve_or_create_department
        department_id = await resolve_or_create_department(department_name, client)

    # B25v2: Two postings — expense with amountGross + vatType, bank with negative amountGross
    # Tripletex auto-generates the VAT posting on 2710.
    logger.info("B25v2: 2-posting structure — expense=%s, gross=%.2f, vatType=%s",
                expense_account, amount_incl, vat_type_id)

    postings = []

    expense_posting = {
        "date": expense_date,
        "description": full_desc,
        "amountGross": amount_incl,
    }
    if expense_account_id:
        expense_posting["account"] = {"id": expense_account_id}
    if vat_type_id:
        expense_posting["vatType"] = {"id": vat_type_id}
    if department_id:
        expense_posting["department"] = {"id": department_id}
    postings.append(expense_posting)

    payment_posting = {
        "date": expense_date,
        "description": full_desc,
        "amountGross": -amount_incl,
    }
    if payment_account_id:
        payment_posting["account"] = {"id": payment_account_id}
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
