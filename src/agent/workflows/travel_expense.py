import logging
from datetime import date as _date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.travel_expense")


async def _get_first_employee_id(client: TripletexClient) -> int | None:
    """Get the first employee ID (often needed as fallback)."""
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
    """Create a travel expense with optional cost lines."""

    # Step 1: Resolve employee
    employee_id = data.get("employeeId")
    if not isinstance(employee_id, int):
        employee_id = await _get_first_employee_id(client)
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

    # Step 3: Resolve payment type for cost lines (object ref, not string)
    costs = data.get("costs", data.get("costLines", []))
    payment_type_id = None
    if costs:
        payment_type_id = await _get_default_payment_type_id(client)
        if not payment_type_id:
            logger.warning("No travel payment type found — cost lines may fail")

    # Step 4: Add cost lines
    for cost in costs:
        cost_date = cost.get("date") or data.get("date") or _date.today().isoformat()

        cost_payload = {
            "travelExpense": {"id": expense_id},
            "date": cost_date,
            "amountCurrencyIncVat": cost.get("amountCurrencyIncVat", cost.get("amount", 0)),
            "isPaidByEmployee": cost.get("isPaidByEmployee", True),
        }

        # paymentType must be an object ref {"id": int}
        if payment_type_id:
            cost_payload["paymentType"] = {"id": payment_type_id}

        # "description" from LLM → "comments" in API
        desc = cost.get("comments", cost.get("description", ""))
        if desc:
            cost_payload["comments"] = desc

        if cost.get("vatTypeId"):
            cost_payload["vatType"] = {"id": cost["vatTypeId"]}
        if cost.get("currencyId"):
            cost_payload["currency"] = {"id": cost["currencyId"]}
        if cost.get("costCategoryId") or cost.get("costCategory"):
            cat = cost.get("costCategory") or {"id": cost["costCategoryId"]}
            cost_payload["costCategory"] = cat if isinstance(cat, dict) else {"id": cat}

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
