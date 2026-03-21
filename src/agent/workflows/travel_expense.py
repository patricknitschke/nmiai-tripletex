import logging
from datetime import date as _date, timedelta

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
    """Create a travel expense with optional cost lines and per diem — single POST with nested arrays."""
    import asyncio

    # Step 1: Resolve employee
    employee_id = await _resolve_employee_id(data, client)
    if not employee_id:
        logger.error("No employee found for travel expense")
        return {"error": "No employee available"}

    # Step 2: Build main payload with nested sub-items
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

    per_diem = data.get("perDiem")
    costs = data.get("costs", data.get("costLines", []))
    travel_date = data.get("date") or _date.today().isoformat()
    departure_date = data.get("departureDate") or travel_date
    days = per_diem.get("days", 1) if per_diem else 1
    return_date = data.get("returnDate") or (
        _date.fromisoformat(departure_date) + timedelta(days=max(days - 1, 0))
    ).isoformat()

    payload["travelDetails"] = {
        "departureDate": departure_date,
        "returnDate": return_date,
        "departureTime": data.get("departureTime", "08:00"),
        "returnTime": data.get("returnTime", "18:00"),
    }

    # Parallel lookups for payment type + per diem rates (GETs are free)
    payment_type_coro = _get_default_payment_type_id(client) if costs else None
    rate_coro = _get_per_diem_rate_and_category(client) if per_diem else None

    payment_type_id = None
    rate_id, category_id = None, None

    coros = {}
    if payment_type_coro:
        coros["payment"] = payment_type_coro
    if rate_coro:
        coros["rate"] = rate_coro

    if coros:
        results = await asyncio.gather(*coros.values())
        keys = list(coros.keys())
        for i, key in enumerate(keys):
            if key == "payment":
                payment_type_id = results[i]
            elif key == "rate":
                rate_id, category_id = results[i]

    # Build nested per diem compensations
    if per_diem:
        daily_rate = per_diem.get("dailyRate", 0)
        pd_item = {
            "count": days,
            "rate": daily_rate,
            "amount": days * daily_rate,
            "overnightAccommodation": "HOTEL",
            "location": data.get("title", ""),
        }
        if rate_id:
            pd_item["rateType"] = {"id": rate_id}
        if category_id:
            pd_item["rateCategory"] = {"id": category_id}
        payload["perDiemCompensations"] = [pd_item]

    # Build nested cost lines
    if costs:
        cost_items = []
        for cost in costs:
            cost_date = cost.get("date") or data.get("date") or _date.today().isoformat()
            item = {
                "date": cost_date,
                "amountCurrencyIncVat": cost.get("amountCurrencyIncVat", cost.get("amount", 0)),
            }
            if payment_type_id:
                item["paymentType"] = {"id": payment_type_id}
            desc = cost.get("comments", cost.get("description", ""))
            if desc:
                item["comments"] = desc
            if cost.get("vatTypeId"):
                item["vatType"] = {"id": cost["vatTypeId"]}
            if cost.get("currencyId"):
                item["currency"] = {"id": cost["currencyId"]}
            cost_items.append(item)
        payload["costs"] = cost_items

    logger.info("Creating travel expense: '%s' for employee %d", payload["title"], employee_id)
    result = await client.post("/travelExpense", payload)

    expense_id = result.get("value", {}).get("id")
    if not expense_id:
        logger.error("Failed to create travel expense: %s", result)
    else:
        logger.info("Travel expense created with ID: %d", expense_id)

    return result


async def delete_travel_expense(data: dict, client: TripletexClient) -> dict:
    """Delete a travel expense by ID, or search by employee/title. Fails explicitly if not found."""
    expense_id = data.get("travelExpenseId") or data.get("id")

    if not expense_id:
        params = {"count": "100"}
        if data.get("employeeEmail"):
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
                break

    if not expense_id:
        logger.error("No travel expense found for deletion: %s", data)
        return {"error": "No travel expense found"}

    logger.info("Deleting travel expense %d", expense_id)
    return await client.delete(f"/travelExpense/{expense_id}")
