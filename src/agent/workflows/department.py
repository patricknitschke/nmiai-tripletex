import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.department")


async def create_department(data: dict, client: TripletexClient) -> dict:
    """Create a department in Tripletex."""
    payload = {
        "name": data.get("name", ""),
        "departmentNumber": data.get("departmentNumber", "1"),
    }

    # Optional: link to a department manager (employee)
    if data.get("departmentManagerId"):
        payload["departmentManager"] = {"id": data["departmentManagerId"]}

    logger.info("Creating department: %s (number: %s)", payload["name"], payload["departmentNumber"])
    result = await client.post("/department", payload)

    dept_id = result.get("value", {}).get("id")
    if dept_id:
        logger.info("Department created with ID: %d", dept_id)
    else:
        logger.error("Failed to create department: %s", result)

    return result
