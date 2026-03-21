import logging
from datetime import date

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.project")


async def create_project(data: dict, client: TripletexClient) -> dict:
    """Create a project in Tripletex."""

    # projectManager is required — use provided ID, look up by email/name, or fallback
    manager_id = data.get("projectManagerId")

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

    # Fallback: grab any employee
    if not manager_id:
        emp_result = await client.get("/employee", params={"count": "10"})
        employees = emp_result.get("values", [])
        if employees:
            # Prefer non-default employees (skip "Historisk ansatt" etc.)
            for emp in employees:
                if emp.get("userType") is not None:
                    manager_id = emp["id"]
                    break
            if not manager_id:
                manager_id = employees[-1]["id"]  # last resort: most recently created
    if manager_id:
        logger.info("Using employee %d as project manager", manager_id)

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

    # Link to customer — resolve by ID, org number, or name
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
            elif customer_name:
                customers = await client.get("/customer", params={"name": customer_name, "count": "10"})
                for cust in customers.get("values", []):
                    if cust.get("name", "").lower() == customer_name.lower():
                        customer_id = cust["id"]
                        break
            if customer_id:
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

    logger.info("Creating project: %s", payload.get("name"))
    result = await client.post("/project", payload)

    project_id = result.get("value", {}).get("id")
    if project_id:
        logger.info("Project created with ID: %d", project_id)
    else:
        logger.error("Failed to create project: %s", result)

    return result
