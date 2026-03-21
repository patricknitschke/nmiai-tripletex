import logging
from datetime import date

from ..tripletex import TripletexClient
from .voucher import _resolve_vat_type, _post_voucher

logger = logging.getLogger("agent.workflows.expense")


async def register_expense(data: dict, client: TripletexClient) -> dict:
    """Register an expense from a receipt as a voucher with correct VAT and department.

    Creates a voucher with:
    - Debit: expense account (with input VAT type if applicable)
    - Credit: bank/payment account (1920 default)

    If a department is specified, the workflow resolves it and links via the department
    field on the posting.

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

    full_desc = f"{supplier_name} - {description}" if supplier_name else description
    logger.info("Registering expense: %s, total=%.2f, excl=%.2f, VAT=%.0f%%",
                full_desc, amount_incl, amount_excl, vat_rate)

    # Resolve expense account
    expense_account_id = None
    if expense_account:
        result = await client.get("/ledger/account", params={"number": str(expense_account), "count": "1"})
        accounts = result.get("values", [])
        if accounts:
            expense_account_id = accounts[0]["id"]
            logger.info("Resolved expense account %s -> id=%d", expense_account, expense_account_id)

    # Resolve payment/bank account
    payment_account_id = None
    result = await client.get("/ledger/account", params={"number": str(payment_account), "count": "1"})
    accounts = result.get("values", [])
    if accounts:
        payment_account_id = accounts[0]["id"]

    # Resolve input VAT type
    vat_type_id = None
    if vat_rate > 0:
        vat_type_id = await _resolve_vat_type(client, vat_rate, "input")

    # Resolve department
    department_id = data.get("departmentId")
    department_name = data.get("departmentName") or data.get("department")

    if not department_id and department_name:
        dept_result = await client.get("/department", params={"query": department_name, "count": "1"})
        depts = dept_result.get("values", [])
        if depts:
            department_id = depts[0]["id"]
            logger.info("Found department '%s' (id=%d)", department_name, department_id)
        else:
            # Create department
            dept = await client.post("/department", {"name": department_name, "departmentNumber": "1"})
            department_id = dept.get("value", {}).get("id")
            if department_id:
                logger.info("Created department '%s' (id=%d)", department_name, department_id)

    # Build postings
    # Expense posting (debit) — with VAT type, Tripletex auto-splits net + VAT
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

    # Payment posting (credit) — negative = credit
    payment_posting = {
        "date": expense_date,
        "description": full_desc,
        "amountGross": -amount_incl,
    }
    if payment_account_id:
        payment_posting["account"] = {"id": payment_account_id}

    voucher = {
        "date": expense_date,
        "description": full_desc,
        "postings": [expense_posting, payment_posting],
    }

    logger.info("Creating expense voucher with 2 postings")
    result = await _post_voucher(voucher, client)

    voucher_id = result.get("value", {}).get("id")
    if voucher_id:
        logger.info("Expense voucher created with ID: %d", voucher_id)
    else:
        logger.error("Failed to create expense voucher: %s", result)

    return result
