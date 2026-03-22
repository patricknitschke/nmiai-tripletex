import logging
from datetime import date

from ..tripletex import TripletexClient
from .employee import create_employee

logger = logging.getLogger("agent.workflows.timesheet")


async def _resolve_employee(data: dict, client: TripletexClient) -> int | None:
    """Find employee by email or name. Creates if not found and enough info is provided."""
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

    # Employee not found — create if we have name info
    if first and last:
        logger.info("Employee %s %s not found, creating", first, last)
        emp_data = {"firstName": first, "lastName": last}
        if email:
            emp_data["email"] = email
        result = await create_employee(emp_data, client)
        emp_id = result.get("value", {}).get("id")
        if emp_id:
            logger.info("Created employee %s %s (id=%d)", first, last, emp_id)
            return emp_id

    # No identifying info at all — fail explicitly, don't guess
    logger.error("Cannot resolve employee: no ID, email, or name provided")
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
        # Match by name (case-insensitive) in project-specific activities
        for act in activities:
            if act.get("name", "").lower() == activity_name.lower():
                logger.info("Found activity '%s' (id=%d)", activity_name, act["id"])
                return act["id"]

        # Try partial match (e.g., "Utvikling" matches "Utvikling/Development")
        activity_lower = activity_name.lower()
        for act in activities:
            if activity_lower in act.get("name", "").lower():
                logger.info("Found activity by partial match '%s' → '%s' (id=%d)", activity_name, act.get("name"), act["id"])
                return act["id"]

        # Search general activities by name
        result = await client.get("/activity", params={"name": activity_name, "count": "1"})
        values = result.get("values", [])
        if values:
            logger.info("Found general activity '%s' (id=%d)", activity_name, values[0]["id"])
            return values[0]["id"]

    # No activity name given — use first project-specific activity (reasonable default)
    if not activity_name and activities:
        logger.info("No activity name specified, using first project activity '%s' (id=%d)", activities[0].get("name"), activities[0]["id"])
        return activities[0]["id"]

    # Activity name given but no match found anywhere — fail explicitly
    if activity_name:
        logger.error("Activity '%s' not found in project %d or general activities", activity_name, project_id)
    else:
        logger.error("No activities available for project %d", project_id)
    return None


async def _set_project_hourly_rate(project_id: int, rate: float, client: TripletexClient) -> None:
    """Configure a fixed hourly rate on the project so timesheet entries become chargeable.

    hourlyRate is readOnly on TimesheetEntry — it's derived from the project's rate config.
    Must be set via POST /project/hourlyRates before registering time.
    Idempotent: checks existing rates first to avoid duplicate writes (B54).
    Uses startDate=2020-01-01 so rate covers all historical entries.
    """
    rate_start = "2020-01-01"
    search = await client.get("/project/hourlyRates", params={
        "projectId": str(project_id),
        "type": "TYPE_FIXED_HOURLY_RATE",
        "count": "1",
        "fields": "id,version,project,startDate,hourlyRateModel,fixedRate,showInProjectOrder",
    })
    existing_rates = search.get("values", [])

    if existing_rates:
        existing = existing_rates[0]
        existing_rate = existing.get("fixedRate")
        if (
            existing.get("hourlyRateModel") == "TYPE_FIXED_HOURLY_RATE"
            and existing_rate is not None
            and abs(float(existing_rate) - rate) < 0.01
            and existing.get("showInProjectOrder") is True
        ):
            logger.info("Project %d already has hourly rate %.2f — skipping", project_id, rate)
            return

        # Update existing rate to match
        payload = {
            "id": existing["id"],
            "version": existing["version"],
            "project": {"id": project_id},
            "startDate": existing.get("startDate") or rate_start,
            "hourlyRateModel": "TYPE_FIXED_HOURLY_RATE",
            "fixedRate": rate,
            "showInProjectOrder": True,
        }
        result = await client.put(f"/project/hourlyRates/{existing['id']}", payload)
    else:
        payload = {
            "project": {"id": project_id},
            "startDate": rate_start,
            "hourlyRateModel": "TYPE_FIXED_HOURLY_RATE",
            "fixedRate": rate,
            "showInProjectOrder": True,
        }
        result = await client.post("/project/hourlyRates", payload)

    if result.get("value", {}).get("id"):
        logger.info("Set hourly rate %.2f on project %d", rate, project_id)
    else:
        logger.warning("Failed to set hourly rate on project %d: %s", project_id, result)


async def register_time(data: dict, client: TripletexClient) -> dict:
    """Register timesheet hours for an employee on a project activity.

    Input data fields:
    - employeeEmail / employeeFirstName+employeeLastName / employeeId
    - projectName / projectId
    - activityName / activityId: the activity (e.g. "Utvikling", "Konsultering")
    - hours: number of hours to register
    - date: date for the entry (defaults to today)
    - chargeableHours: billable hours (defaults to same as hours)
    - hourlyRate: rate per hour — sets project hourly rate config (NOT on entry directly)
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

    # Set hourly rate on project if provided (hourlyRate is readOnly on TimesheetEntry)
    hourly_rate = data.get("hourlyRate") or data.get("rate")
    if hourly_rate:
        await _set_project_hourly_rate(project_id, float(hourly_rate), client)

    # Clamp entry_date to project startDate (Tripletex rejects entries before it)
    try:
        proj_result = await client.get(f"/project/{project_id}")
        proj_start = proj_result.get("value", {}).get("startDate")
        if proj_start and entry_date < proj_start:
            logger.warning("Entry date %s is before project start %s — clamping to start date", entry_date, proj_start)
            entry_date = proj_start
    except Exception:
        pass  # non-fatal — worst case the API will reject it

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
    result = await client.post("/timesheet/entry", entry)

    entry_id = result.get("value", {}).get("id")
    if entry_id:
        logger.info("Timesheet entry created with ID: %d", entry_id)
    else:
        logger.error("Failed to create timesheet entry: %s", result)

    return result
