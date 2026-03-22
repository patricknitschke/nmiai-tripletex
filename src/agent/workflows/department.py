import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.department")


async def _next_dept_number(client: TripletexClient) -> str:
    """Return the next free department number (max existing + 1)."""
    result = await client.get("/department", params={
        "count": "1", "sorting": "departmentNumber", "order": "desc",
        "fields": "id,departmentNumber",
    })
    existing = result.get("values", [])
    if not existing:
        return "1"
    try:
        max_num = int(existing[0].get("departmentNumber", 0))
    except (ValueError, TypeError):
        max_num = 0
    return str(max_num + 1)


async def resolve_or_create_department(name: str, client: TripletexClient) -> int | None:
    """Find a department by exact name, or create it with the next free number. Returns ID."""
    if not name:
        return None
    search = await client.get("/department", params={"name": name, "count": "10", "fields": "id,name"})
    for dept in search.get("values", []):
        if dept.get("name", "").lower() == name.lower():
            logger.info("Found department '%s' (id=%d)", name, dept["id"])
            return dept["id"]
    # Create with next free number
    dept_number = await _next_dept_number(client)
    logger.info("Creating department '%s' (number=%s)", name, dept_number)
    result = await client.post("/department", {"name": name, "departmentNumber": dept_number})
    dept_id = result.get("value", {}).get("id")
    if dept_id:
        logger.info("Department '%s' created (id=%d)", name, dept_id)
    else:
        logger.error("Failed to create department '%s': %s", name, result)
    return dept_id


async def create_department(data: dict, client: TripletexClient) -> dict:
    """Create a department in Tripletex. Searches first to avoid duplicates, updates if needed."""

    name = data.get("name", "")

    # Search for existing department by name
    if name:
        search = await client.get("/department", params={"name": name, "count": "10"})
        for dept in search.get("values", []):
            if dept.get("name", "").lower() == name.lower():
                dept_id = dept["id"]
                logger.info("Department already exists (id=%d) — checking if update needed", dept_id)
                update_payload = {}
                if data.get("departmentNumber") and str(data["departmentNumber"]) != str(dept.get("departmentNumber", "")):
                    update_payload["departmentNumber"] = str(data["departmentNumber"])
                if data.get("departmentManagerId") and dept.get("departmentManager", {}).get("id") != data["departmentManagerId"]:
                    update_payload["departmentManager"] = {"id": data["departmentManagerId"]}
                if update_payload:
                    logger.info("Updating department %d with: %s", dept_id, list(update_payload.keys()))
                    put_body = {**dept, **update_payload}
                    put_result = await client.put(f"/department/{dept_id}", put_body)
                    return put_result if put_result.get("value") else {"value": dept, "update_error": put_result}
                logger.info("Department %d already matches desired state", dept_id)
                return {"value": dept}

    # Allocate next free department number if not specified
    dept_number = data.get("departmentNumber") or await _next_dept_number(client)
    payload = {
        "name": name,
        "departmentNumber": str(dept_number),
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
