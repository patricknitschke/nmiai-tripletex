import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.department")


async def create_department(data: dict, client: TripletexClient) -> dict:
    """Create a department in Tripletex."""
    payload = {
        "name": data.get("name", ""),
    }

    if data.get("departmentNumber"):
        payload["departmentNumber"] = data["departmentNumber"]

    logger.info("Creating department: %s", payload.get("name"))
    result = await client.post("/department", payload)

    dept_id = result.get("value", {}).get("id")
    if dept_id:
        logger.info("Department created with ID: %d", dept_id)
    else:
        logger.error("Failed to create department: %s", result)

    return result
