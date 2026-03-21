import logging
from datetime import date

from ..tripletex import TripletexClient
from .customer import create_customer
from .employee import create_employee

logger = logging.getLogger("agent.workflows.project")


async def create_project(data: dict, client: TripletexClient) -> dict:
    """Create a project in Tripletex. Searches first to avoid duplicates, updates if needed."""

    # Search for existing project by name or number
    project_name = data.get("name", "")
    project_number = data.get("number")
    existing_project = None

    if project_number:
        search = await client.get("/project", params={"number": str(project_number), "count": "1"})
        existing = search.get("values", [])
        if existing:
            existing_project = existing[0]
    if not existing_project and project_name:
        search = await client.get("/project", params={"name": project_name, "count": "10"})
        for proj in search.get("values", []):
            if proj.get("name", "").lower() == project_name.lower():
                existing_project = proj
                break

    if existing_project:
        proj_id = existing_project["id"]
        logger.info("Project already exists (id=%d) — checking if update needed", proj_id)
        update_payload = {}
        _SIMPLE = ("name", "description", "startDate", "endDate")
        for field in _SIMPLE:
            desired = data.get(field)
            if desired is not None and desired != existing_project.get(field):
                update_payload[field] = desired
        if data.get("isInternal") is not None and data["isInternal"] != existing_project.get("isInternal"):
            update_payload["isInternal"] = data["isInternal"]
        if data.get("isFixedPrice") is not None and data["isFixedPrice"] != existing_project.get("isFixedPrice"):
            update_payload["isFixedPrice"] = data["isFixedPrice"]
        # Check fixedprice amount (data may use various casing/aliases)
        desired_fp = data.get("fixedprice") or data.get("fixedPrice") or data.get("fixedPriceAmount") or data.get("price") or data.get("budget")
        if desired_fp is not None and desired_fp != existing_project.get("fixedprice"):
            update_payload["fixedprice"] = desired_fp
            update_payload["isFixedPrice"] = True
        if update_payload:
            logger.info("Updating project %d with: %s", proj_id, list(update_payload.keys()))
            put_body = {**existing_project, **update_payload}
            # Strip fields that Tripletex rejects on PUT (must use separate endpoints)
            for field in ("projectHourlyRates", "participants", "projectActivities",
                          "orderLines", "invoicingPlan", "preliminaryInvoice"):
                put_body.pop(field, None)
            put_result = await client.put(f"/project/{proj_id}", put_body)
            return put_result if put_result.get("value") else {"value": existing_project, "update_error": put_result}
        logger.info("Project %d already matches desired state", proj_id)
        return {"value": existing_project}

    # projectManager is required — use provided ID, look up by email/name, or create
    manager_id = data.get("projectManagerId")
    pm_first = None
    pm_last = None

    # Try to resolve by email first (most reliable)
    if not manager_id:
        pm_email = data.get("projectManagerEmail")
        if pm_email:
            emp_result = await client.get("/employee", params={"email": pm_email, "count": "1"})
            employees = emp_result.get("values", [])
            if employees:
                manager_id = employees[0]["id"]
                logger.info("Resolved project manager by email %s → id=%d", pm_email, manager_id)

    # Try to resolve by first+last name fields
    if not manager_id:
        pm_first = data.get("projectManagerFirstName")
        pm_last = data.get("projectManagerLastName")
        if pm_first and pm_last:
            emp_result = await client.get("/employee", params={"firstName": pm_first, "lastName": pm_last, "count": "10"})
            for emp in emp_result.get("values", []):
                if emp.get("firstName", "").lower() == pm_first.lower() and emp.get("lastName", "").lower() == pm_last.lower():
                    manager_id = emp["id"]
                    logger.info("Resolved project manager by name %s %s → id=%d", pm_first, pm_last, manager_id)
                    break

    # Try to resolve by full name string (LLMs often pass "projectManagerName" instead of split fields)
    if not manager_id:
        pm_name = data.get("projectManagerName")
        if pm_name and " " in pm_name.strip():
            parts = pm_name.strip().split()
            pm_first = parts[0]
            pm_last = " ".join(parts[1:])
            emp_result = await client.get("/employee", params={"firstName": pm_first, "lastName": pm_last, "count": "10"})
            for emp in emp_result.get("values", []):
                if emp.get("firstName", "").lower() == pm_first.lower() and emp.get("lastName", "").lower() == pm_last.lower():
                    manager_id = emp["id"]
                    logger.info("Resolved project manager by full name '%s' → id=%d", pm_name, manager_id)
                    break

    # PM not found — create if we have name/email info
    if not manager_id and (pm_first and pm_last):
        logger.info("Project manager %s %s not found, creating", pm_first, pm_last)
        emp_data = {"firstName": pm_first, "lastName": pm_last}
        pm_email = data.get("projectManagerEmail")
        if pm_email:
            emp_data["email"] = pm_email
        emp_result = await create_employee(emp_data, client)
        manager_id = emp_result.get("value", {}).get("id")
        if manager_id:
            logger.info("Created project manager %s %s (id=%d)", pm_first, pm_last, manager_id)

    # Still no PM — last resort: use first existing employee (Tripletex requires a PM)
    if not manager_id:
        emp_result = await client.get("/employee", params={"count": "1"})
        employees = emp_result.get("values", [])
        if employees:
            manager_id = employees[0]["id"]
            logger.warning("No PM info provided, using existing employee %d as fallback", manager_id)

    if not manager_id:
        logger.error("No employee available for project manager")
        return {"error": "No project manager available"}

    payload = {
        "name": data.get("name", ""),
        "projectManager": {"id": manager_id},
    }

    if data.get("number"):
        payload["number"] = str(data["number"])
    if data.get("description"):
        payload["description"] = data["description"]
    payload["startDate"] = data.get("startDate", date.today().isoformat())
    if data.get("endDate"):
        payload["endDate"] = data["endDate"]
    if data.get("isInternal") is not None:
        payload["isInternal"] = data["isInternal"]
    if data.get("isFixedPrice") is not None:
        payload["isFixedPrice"] = data["isFixedPrice"]
    fp = data.get("fixedprice") or data.get("fixedPrice") or data.get("fixedPriceAmount") or data.get("price")
    if fp is not None:
        payload["fixedprice"] = fp
        payload["isFixedPrice"] = True
    if data.get("budget") is not None:
        # budget often means fixed price in competition tasks
        if "fixedprice" not in payload:
            payload["fixedprice"] = data["budget"]
            payload["isFixedPrice"] = True

    # Link to customer — resolve by ID, org number, or name; create if missing
    customer_id = data.get("customerId")
    if not customer_id:
        customer_name = data.get("customerName")
        org_number = data.get("customerOrgNumber") or data.get("organizationNumber")
        if customer_name or org_number:
            if org_number:
                customers = await client.get("/customer", params={"organizationNumber": org_number, "count": "1"})
                customer_list = customers.get("values", [])
                if customer_list:
                    customer_id = customer_list[0]["id"]
            if not customer_id and customer_name:
                customers = await client.get("/customer", params={"name": customer_name, "count": "10"})
                for cust in customers.get("values", []):
                    if cust.get("name", "").lower() == customer_name.lower():
                        customer_id = cust["id"]
                        break
            if not customer_id:
                # Customer not found — create it
                cust_data = {"name": customer_name or ""}
                if org_number:
                    cust_data["organizationNumber"] = org_number
                logger.info("Customer not found, creating: %s", customer_name or org_number)
                cust_result = await create_customer(cust_data, client)
                customer_id = cust_result.get("value", {}).get("id")
                if customer_id:
                    logger.info("Created customer '%s' (id=%d)", customer_name or org_number, customer_id)
            else:
                logger.info("Resolved customer '%s' → id=%d", customer_name or org_number, customer_id)
    if customer_id:
        payload["customer"] = {"id": customer_id}

    # Link to department
    dept_id = data.get("departmentId")
    if dept_id:
        payload["department"] = {"id": dept_id}

    # Sub-project
    if data.get("mainProjectId"):
        payload["mainProject"] = {"id": data["mainProjectId"]}

    # Embed project activities in the creation payload (saves a separate POST)
    # Handle activityName / defaultActivityName shorthand from LLM
    activity_name = data.get("activityName") or data.get("defaultActivityName")
    if activity_name and not data.get("projectActivities") and not data.get("activities"):
        data["projectActivities"] = [activity_name]
    activities = data.get("projectActivities") or data.get("activities")
    if activities:
        pa_list = []
        for act in activities:
            if isinstance(act, str):
                pa_list.append({"activity": {"name": act, "activityType": "PROJECT_SPECIFIC_ACTIVITY"}})
            elif isinstance(act, dict):
                pa_list.append(act if "activity" in act else {"activity": {**act, "activityType": act.get("activityType", "PROJECT_SPECIFIC_ACTIVITY")}})
        if pa_list:
            payload["projectActivities"] = pa_list

    logger.info("Creating project: %s", payload.get("name"))
    result = await client.post("/project", payload)

    project_id = result.get("value", {}).get("id")
    if project_id:
        logger.info("Project created with ID: %d (PM=%d)", project_id, manager_id)
    else:
        logger.error("Failed to create project: %s", result)

    return result


# ---------------------------------------------------------------------------
# Helper: resolve PM once (shared by single + batch)
# ---------------------------------------------------------------------------

async def _resolve_project_manager(data: dict, client: TripletexClient) -> int | None:
    """Resolve project manager ID from email/name/ID. Returns ID or None."""
    manager_id = data.get("projectManagerId")
    if manager_id:
        return manager_id

    pm_email = data.get("projectManagerEmail")
    if pm_email:
        emp_result = await client.get("/employee", params={"email": pm_email, "count": "1"})
        employees = emp_result.get("values", [])
        if employees:
            return employees[0]["id"]

    pm_first = data.get("projectManagerFirstName")
    pm_last = data.get("projectManagerLastName")
    pm_name = data.get("projectManagerName")
    if not pm_first and pm_name and " " in pm_name.strip():
        parts = pm_name.strip().split()
        pm_first = parts[0]
        pm_last = " ".join(parts[1:])
    if pm_first and pm_last:
        emp_result = await client.get("/employee", params={"firstName": pm_first, "lastName": pm_last, "count": "10"})
        for emp in emp_result.get("values", []):
            if emp.get("firstName", "").lower() == pm_first.lower() and emp.get("lastName", "").lower() == pm_last.lower():
                return emp["id"]

    # Fallback: first existing employee
    emp_result = await client.get("/employee", params={"count": "1"})
    employees = emp_result.get("values", [])
    if employees:
        logger.warning("PM fallback: using employee %d", employees[0]["id"])
        return employees[0]["id"]

    return None


def _build_activity_list(data: dict) -> list[dict] | None:
    """Build projectActivities array from various input formats."""
    activity_name = data.get("activityName") or data.get("defaultActivityName")
    activities = data.get("projectActivities") or data.get("activities")
    if activity_name and not activities:
        activities = [activity_name]
    if not activities:
        return None
    pa_list = []
    for act in activities:
        if isinstance(act, str):
            pa_list.append({"activity": {"name": act, "activityType": "PROJECT_SPECIFIC_ACTIVITY"}})
        elif isinstance(act, dict):
            pa_list.append(act if "activity" in act else {"activity": {**act, "activityType": act.get("activityType", "PROJECT_SPECIFIC_ACTIVITY")}})
    return pa_list or None


async def create_projects_batch(data: dict, client: TripletexClient) -> dict:
    """Create multiple projects in a single POST /project/list call.

    Resolves PM once and reuses the ID for all projects.
    Embeds projectActivities in each project payload.

    Input data fields:
    - projects: list of project dicts (each with name, activityName, etc.)
    - projectManagerId/Email/Name: shared PM for all projects (resolved once)
    - startDate: shared start date (default: today)
    - isInternal: shared flag (default: not set)
    - Any other shared fields applied to all projects
    """
    projects = data.get("projects", [])
    if not projects:
        return {"error": "No projects provided. Pass a 'projects' array."}

    # Resolve PM once using shared PM fields
    manager_id = await _resolve_project_manager(data, client)
    if not manager_id:
        return {"error": "No project manager available"}
    logger.info("Batch: resolved PM id=%d (once for %d projects)", manager_id, len(projects))

    # Shared defaults
    shared_start = data.get("startDate", date.today().isoformat())
    shared_internal = data.get("isInternal")

    payloads = []
    for proj in projects:
        p = {
            "name": proj.get("name", ""),
            "projectManager": {"id": manager_id},
            "startDate": proj.get("startDate", shared_start),
        }
        if proj.get("number"):
            p["number"] = str(proj["number"])
        if proj.get("description"):
            p["description"] = proj["description"]
        if proj.get("endDate"):
            p["endDate"] = proj["endDate"]
        is_internal = proj.get("isInternal", shared_internal)
        if is_internal is not None:
            p["isInternal"] = is_internal
        if proj.get("isFixedPrice") is not None:
            p["isFixedPrice"] = proj["isFixedPrice"]
        fp = proj.get("fixedprice") or proj.get("fixedPrice") or proj.get("price")
        if fp is not None:
            p["fixedprice"] = fp
            p["isFixedPrice"] = True

        # Embed activities
        pa_list = _build_activity_list(proj)
        if pa_list:
            p["projectActivities"] = pa_list

        payloads.append(p)

    logger.info("Batch creating %d projects via POST /project/list", len(payloads))
    result = await client.post("/project/list", payloads)

    created = result.get("values", [])
    if created:
        ids = [p.get("id") for p in created]
        logger.info("Batch created %d projects: %s", len(created), ids)
    else:
        logger.error("Batch project creation failed: %s", result)

    return result
