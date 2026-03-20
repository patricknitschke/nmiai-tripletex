import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.project")


async def create_project(data: dict, client: TripletexClient) -> dict:
    """Create a project in Tripletex."""

    # projectManager is required — always look up a real employee
    # (LLM can't know real IDs, so we ignore its guesses)
    emp_result = await client.get("/employee", params={"count": "10"})
    employees = emp_result.get("values", [])
    manager_id = None
    if employees:
        # Prefer non-default employees (skip "Historisk ansatt" etc.)
        for emp in employees:
            if emp.get("userType") is not None:
                manager_id = emp["id"]
                break
        if not manager_id:
            manager_id = employees[-1]["id"]  # last resort: most recently created
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
    if data.get("startDate"):
        payload["startDate"] = data["startDate"]
    if data.get("endDate"):
        payload["endDate"] = data["endDate"]
    if data.get("isInternal") is not None:
        payload["isInternal"] = data["isInternal"]
    if data.get("isFixedPrice") is not None:
        payload["isFixedPrice"] = data["isFixedPrice"]

    # Link to customer
    customer_id = data.get("customerId")
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
