import logging
from datetime import date as _date

from ..tripletex import TripletexClient
from .employee import create_employee

logger = logging.getLogger("agent.workflows.travel_expense")


async def _resolve_employee_id(data: dict, client: TripletexClient) -> int | None:
    """Resolve employee: create if named in prompt, else use first existing."""
    # If explicit ID given
    employee_id = data.get("employeeId")
    if isinstance(employee_id, int):
        return employee_id

    # If employee name/email provided, create them
    first_name = data.get("employeeFirstName")
    last_name = data.get("employeeLastName")
    if first_name and last_name:
        emp_data = {"firstName": first_name, "lastName": last_name}
        if data.get("employeeEmail"):
            emp_data["email"] = data["employeeEmail"]
        result = await create_employee(emp_data, client)
        emp_id = result.get("value", {}).get("id")
        if emp_id:
            logger.info("Created employee %s %s (id=%d)", first_name, last_name, emp_id)
            return emp_id

    # Fallback: use first existing employee
    result = await client.get("/employee", params={"count": "1"})
    employees = result.get("values", [])
    if employees:
        return employees[0]["id"]
    return None


async def _get_default_payment_type_id(client: TripletexClient) -> int | None:
    """Get the first active travel payment type ID."""
    result = await client.get("/travelExpense/paymentType", params={"count": "1"})
    types = result.get("values", [])
    if types:
        return types[0]["id"]
    return None


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

    # Step 3: Resolve payment type for cost lines
    costs = data.get("costs", data.get("costLines", []))
    per_diem = data.get("perDiem")
    payment_type_id = None
    if costs or per_diem:
        payment_type_id = await _get_default_payment_type_id(client)

    # Step 4: Add per diem as a cost line (total amount)
    if per_diem:
        days = per_diem.get("days", 1)
        daily_rate = per_diem.get("dailyRate", 0)
        total = days * daily_rate
        cost_date = data.get("date") or _date.today().isoformat()

        per_diem_payload = {
            "travelExpense": {"id": expense_id},
            "date": cost_date,
            "amountCurrencyIncVat": total,
            "isPaidByEmployee": True,
            "comments": f"Dagpenger ({days} dager × {daily_rate} NOK)",
        }
        if payment_type_id:
            per_diem_payload["paymentType"] = {"id": payment_type_id}

        logger.info("Adding per diem to expense %d: %d days × %s = %s", expense_id, days, daily_rate, total)
        await client.post("/travelExpense/cost", per_diem_payload)

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
        await client.post("/travelExpense/cost", cost_payload)

    return result


async def delete_travel_expense(data: dict, client: TripletexClient) -> dict:
    """Delete a travel expense by ID."""
    expense_id = data.get("travelExpenseId") or data.get("id")
    if not expense_id:
        logger.error("No travel expense ID provided for deletion")
        return {"error": "No travel expense ID"}

    logger.info("Deleting travel expense %d", expense_id)
    result = await client.delete(f"/travelExpense/{expense_id}")
    return result
