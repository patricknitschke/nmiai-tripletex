import logging
from datetime import date as _date

from ..tripletex import TripletexClient
from .employee import create_employee

logger = logging.getLogger("agent.workflows.travel_expense")


async def _resolve_employee_id(data: dict, client: TripletexClient) -> int | None:
    """Resolve employee: find by email/name, create if not found. Never picks a random employee."""
    # If explicit ID given
    employee_id = data.get("employeeId")
    if isinstance(employee_id, int):
        return employee_id

    # Try to find by email first
    email = data.get("employeeEmail")
    if email:
        result = await client.get("/employee", params={"email": email, "count": "1"})
        values = result.get("values", [])
        if values:
            logger.info("Found employee by email %s (id=%d)", email, values[0]["id"])
            return values[0]["id"]

    # If employee name provided, search then create
    first_name = data.get("employeeFirstName")
    last_name = data.get("employeeLastName")
    if first_name and last_name:
        # Search first to avoid duplicates
        result = await client.get("/employee", params={"firstName": first_name, "lastName": last_name, "count": "10"})
        for emp in result.get("values", []):
            if emp.get("firstName", "").lower() == first_name.lower() and emp.get("lastName", "").lower() == last_name.lower():
                logger.info("Found employee %s %s (id=%d)", first_name, last_name, emp["id"])
                return emp["id"]

        # Not found — create
        emp_data = {"firstName": first_name, "lastName": last_name}
        if email:
            emp_data["email"] = email
        result = await create_employee(emp_data, client)
        emp_id = result.get("value", {}).get("id")
        if emp_id:
            logger.info("Created employee %s %s (id=%d)", first_name, last_name, emp_id)
            return emp_id

    # No identifying info — fail explicitly, don't guess
    logger.error("Cannot resolve employee for travel expense: no ID, email, or name provided")
    return None


async def _get_default_payment_type_id(client: TripletexClient) -> int | None:
    """Get the first active travel payment type ID."""
    result = await client.get("/travelExpense/paymentType", params={"count": "1"})
    types = result.get("values", [])
    if types:
        return types[0]["id"]
    return None


async def _get_per_diem_rate_and_category(client: TripletexClient) -> tuple[int | None, int | None]:
    """Get the first PER_DIEM rate type and rate category IDs."""
    # Get rate category for PER_DIEM
    cat_result = await client.get("/travelExpense/rateCategory", params={"type": "PER_DIEM", "count": "1"})
    categories = cat_result.get("values", [])
    category_id = categories[0]["id"] if categories else None

    # Get rate for PER_DIEM
    rate_result = await client.get("/travelExpense/rate", params={"type": "PER_DIEM", "count": "1"})
    rates = rate_result.get("values", [])
    rate_id = rates[0]["id"] if rates else None

    return rate_id, category_id


async def create_travel_expense(data: dict, client: TripletexClient) -> dict:
    """Create a travel expense with optional cost lines and per diem."""

    # Step 1: Resolve employee
    employee_id = await _resolve_employee_id(data, client)
    if not employee_id:
        logger.error("No employee found for travel expense")
        return {"error": "No employee available"}

    # Step 2: Create travel expense header
    payload = {
        "employee": {"id": employee_id},
        "title": data.get("title", data.get("description", "Reise")),
    }

    if data.get("projectId"):
        payload["project"] = {"id": data["projectId"]}
    if data.get("departmentId"):
        payload["department"] = {"id": data["departmentId"]}
    if data.get("date"):
        payload["date"] = data["date"]
    if data.get("isChargeable") is not None:
        payload["isChargeable"] = data["isChargeable"]

    logger.info("Creating travel expense: '%s' for employee %d", payload["title"], employee_id)
    result = await client.post("/travelExpense", payload)

    expense_id = result.get("value", {}).get("id")
    if not expense_id:
        logger.error("Failed to create travel expense: %s", result)
        return result

    logger.info("Travel expense created with ID: %d", expense_id)

    errors = []

    # Step 3: Resolve payment type for cost lines
    costs = data.get("costs", data.get("costLines", []))
    per_diem = data.get("perDiem")
    payment_type_id = None
    if costs:
        payment_type_id = await _get_default_payment_type_id(client)

    # Step 4: Add per diem as proper perDiemCompensation (not a cost line)
    if per_diem:
        days = per_diem.get("days", 1)
        daily_rate = per_diem.get("dailyRate", 0)
        total = days * daily_rate

        rate_id, category_id = await _get_per_diem_rate_and_category(client)

        per_diem_payload = {
            "travelExpense": {"id": expense_id},
            "count": days,
            "rate": daily_rate,
            "amount": total,
            "overnightAccommodation": "HOTEL",
            "location": data.get("title", ""),
        }
        if rate_id:
            per_diem_payload["rateType"] = {"id": rate_id}
        if category_id:
            per_diem_payload["rateCategory"] = {"id": category_id}

        logger.info("Adding per diem compensation to expense %d: %d days × %s = %s", expense_id, days, daily_rate, total)
        pd_result = await client.post("/travelExpense/perDiemCompensation", per_diem_payload)
        if not pd_result.get("value", {}).get("id"):
            errors.append(f"Per diem compensation failed: {pd_result}")
            logger.error("Failed to add per diem to expense %d: %s", expense_id, pd_result)

    # Step 5: Add regular cost lines
    for cost in costs:
        cost_date = cost.get("date") or data.get("date") or _date.today().isoformat()

        cost_payload = {
            "travelExpense": {"id": expense_id},
            "date": cost_date,
            "amountCurrencyIncVat": cost.get("amountCurrencyIncVat", cost.get("amount", 0)),
            "isPaidByEmployee": cost.get("isPaidByEmployee", True),
        }

        if payment_type_id:
            cost_payload["paymentType"] = {"id": payment_type_id}

        desc = cost.get("comments", cost.get("description", ""))
        if desc:
            cost_payload["comments"] = desc

        if cost.get("vatTypeId"):
            cost_payload["vatType"] = {"id": cost["vatTypeId"]}
        if cost.get("currencyId"):
            cost_payload["currency"] = {"id": cost["currencyId"]}

        logger.info("Adding cost line to expense %d: %s", expense_id, desc)
        cl_result = await client.post("/travelExpense/cost", cost_payload)
        if not cl_result.get("value", {}).get("id"):
            errors.append(f"Cost line '{desc}' failed: {cl_result}")
            logger.error("Failed to add cost line to expense %d: %s", expense_id, cl_result)

    if errors:
        result["ok"] = False
        result["errors"] = errors
        result["_needs_repair"] = (
            f"Travel expense {expense_id} created but {len(errors)} sub-item(s) failed. "
            "Use raw API calls (POST /travelExpense/perDiemCompensation or POST /travelExpense/cost) to retry."
        )
    return result


async def delete_travel_expense(data: dict, client: TripletexClient) -> dict:
    """Delete a travel expense by ID, or search by employee/title."""
    expense_id = data.get("travelExpenseId") or data.get("id")

    if not expense_id:
        # Search by employee email or title
        params = {"count": "100"}
        if data.get("employeeEmail"):
            # Find employee first
            emp_result = await client.get("/employee", params={"email": data["employeeEmail"], "count": "1"})
            employees = emp_result.get("values", [])
            if employees:
                params["employeeId"] = str(employees[0]["id"])

        result = await client.get("/travelExpense", params=params)
        expenses = result.get("values", [])
        title = data.get("title", "").lower()
        for exp in expenses:
            if title and title in exp.get("title", "").lower():
                expense_id = exp["id"]
                logger.info("Found travel expense by title '%s' (id=%d)", title, expense_id)
                break
        if not expense_id and expenses:
            expense_id = expenses[0]["id"]
            logger.info("Using first travel expense (id=%d)", expense_id)

    if not expense_id:
        logger.error("No travel expense found for deletion: %s", data)
        return {"error": "No travel expense found"}

    logger.info("Deleting travel expense %d", expense_id)
    result = await client.delete(f"/travelExpense/{expense_id}")
    return result
