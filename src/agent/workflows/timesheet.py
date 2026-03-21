import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.timesheet")


async def _resolve_employee(data: dict, client: TripletexClient) -> int | None:
    """Find employee by email, name, or ID."""
    employee_id = data.get("employeeId")
    if employee_id:
        return employee_id

    email = data.get("employeeEmail") or data.get("email")
    if email:
        result = await client.get("/employee", params={"email": email, "count": "1"})
        values = result.get("values", [])
        if values:
            logger.info("Found employee by email %s (id=%d)", email, values[0]["id"])
            return values[0]["id"]

    # Search by name — fetch multiple and exact match
    first = data.get("employeeFirstName") or data.get("firstName")
    last = data.get("employeeLastName") or data.get("lastName")
    if first and last:
        result = await client.get("/employee", params={"firstName": first, "lastName": last, "count": "10"})
        for emp in result.get("values", []):
            if emp.get("firstName", "").lower() == first.lower() and emp.get("lastName", "").lower() == last.lower():
                logger.info("Found employee %s %s (id=%d)", first, last, emp["id"])
                return emp["id"]

    # Fallback: get first employee
    result = await client.get("/employee", params={"count": "1"})
    values = result.get("values", [])
    if values:
        logger.info("Using first employee as fallback (id=%d)", values[0]["id"])
        return values[0]["id"]

    return None


async def _resolve_project(data: dict, client: TripletexClient) -> int | None:
    """Find project by name or ID."""
    project_id = data.get("projectId")
    if project_id:
        return project_id

    project_name = data.get("projectName")
    if project_name:
        result = await client.get("/project", params={"name": project_name, "count": "10"})
        for proj in result.get("values", []):
            if proj.get("name", "").lower() == project_name.lower():
                logger.info("Found project '%s' (id=%d)", project_name, proj["id"])
                return proj["id"]

    return None


async def _resolve_activity(data: dict, client: TripletexClient, project_id: int, employee_id: int) -> int | None:
    """Find or create an activity for timesheet registration."""
    activity_id = data.get("activityId")
    if activity_id:
        return activity_id

    activity_name = data.get("activityName") or data.get("activity")

    # First try to find applicable activities for this project
    today = date.today().isoformat()
    result = await client.get("/activity/>forTimeSheet", params={
        "projectId": str(project_id),
        "employeeId": str(employee_id),
        "date": today,
        "count": "100",
    })
    activities = result.get("values", [])

    if activity_name:
        # Match by name (case-insensitive)
        for act in activities:
            if act.get("name", "").lower() == activity_name.lower():
                logger.info("Found activity '%s' (id=%d)", activity_name, act["id"])
                return act["id"]

    # If no match found but activities exist, use the first one
    if activities:
        logger.info("Using first available activity '%s' (id=%d)", activities[0].get("name"), activities[0]["id"])
        return activities[0]["id"]

    # No activities found — search general activities
    if activity_name:
        result = await client.get("/activity", params={"name": activity_name, "count": "1"})
        values = result.get("values", [])
        if values:
            logger.info("Found general activity '%s' (id=%d)", activity_name, values[0]["id"])
            return values[0]["id"]

    # Last resort: get any activity
    result = await client.get("/activity", params={"count": "1"})
    values = result.get("values", [])
    if values:
        logger.info("Using first general activity as fallback (id=%d)", values[0]["id"])
        return values[0]["id"]

    return None


async def register_time(data: dict, client: TripletexClient) -> dict:
    """Register timesheet hours for an employee on a project activity.

    Input data fields:
    - employeeEmail / employeeFirstName+employeeLastName / employeeId
    - projectName / projectId
    - activityName / activityId: the activity (e.g. "Utvikling", "Konsultering")
    - hours: number of hours to register
    - date: date for the entry (defaults to today)
    - chargeableHours: billable hours (defaults to same as hours)
    - hourlyRate: rate per hour (informational, set on project hourly rates)
    """
    today = date.today().isoformat()
    entry_date = data.get("date", today)
    hours = data.get("hours", 0)
    chargeable = data.get("chargeableHours", hours)

    if not hours:
        return {"error": "No hours specified for timesheet entry"}

    # Resolve employee
    employee_id = await _resolve_employee(data, client)
    if not employee_id:
        return {"error": "Could not find employee for timesheet entry"}

    # Resolve project
    project_id = await _resolve_project(data, client)
    if not project_id:
        return {"error": "Could not find project for timesheet entry"}

    # Resolve activity
    activity_id = await _resolve_activity(data, client, project_id, employee_id)
    if not activity_id:
        return {"error": "Could not find activity for timesheet entry"}

    # Build timesheet entry
    entry = {
        "employee": {"id": employee_id},
        "project": {"id": project_id},
        "activity": {"id": activity_id},
        "date": entry_date,
        "hours": hours,
        "chargeableHours": chargeable,
    }

    logger.info("Registering %s hours on project %d, activity %d, employee %d (date=%s)",
                hours, project_id, activity_id, employee_id, entry_date)
    result = await client.post("/timesheet/entry", json=entry)

    entry_id = result.get("value", {}).get("id")
    if entry_id:
        logger.info("Timesheet entry created with ID: %d", entry_id)
    else:
        logger.error("Failed to create timesheet entry: %s", result)

    return result
