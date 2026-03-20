import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.employee")


async def create_employee(data: dict, client: TripletexClient) -> dict:
    """Create an employee and optionally assign a role."""

    # Tripletex requires a department — fetch the first available one
    department_id = data.get("departmentId")
    if not department_id:
        dept_result = await client.get("/department", params={"count": "1"})
        departments = dept_result.get("values", [])
        if departments:
            department_id = departments[0]["id"]
            logger.info("Using existing department ID: %d", department_id)
        else:
            logger.warning("No departments found — creating a default one")
            dept = await client.post("/department", {"name": "Avdeling"})
            department_id = dept.get("value", {}).get("id")

    payload = {
        "firstName": data.get("firstName", ""),
        "lastName": data.get("lastName", ""),
        "userType": "STANDARD",
        "department": {"id": department_id},
    }

    if data.get("email"):
        payload["email"] = data["email"]
    if data.get("phoneNumber"):
        payload["phoneNumberMobile"] = data["phoneNumber"]
    if data.get("dateOfBirth"):
        payload["dateOfBirth"] = data["dateOfBirth"]

    logger.info("Creating employee: %s %s", payload.get("firstName"), payload.get("lastName"))
    result = await client.post("/employee", payload)

    employee_id = result.get("value", {}).get("id")
    if not employee_id:
        logger.error("Failed to create employee: %s", result)
        return result

    logger.info("Employee created with ID: %d", employee_id)

    # Handle role assignment if requested
    role = data.get("role", "").lower()
    if role:
        logger.info("Assigning role: %s", role)
        await _assign_role(employee_id, role, client)

    return result


async def _assign_role(employee_id: int, role: str, client: TripletexClient) -> None:
    """Assign a role to an employee."""
    role_map = {
        "administrator": True,
        "admin": True,
        "kontoadministrator": True,
    }

    is_admin = role_map.get(role, False)
    if is_admin:
        result = await client.get(f"/employee/{employee_id}", params={"fields": "*"})
        employee_data = result.get("value", {})
        # Update with all required fields + set admin flag
        update_payload = {
            "id": employee_id,
            "version": employee_data.get("version", 1),
            "firstName": employee_data.get("firstName", ""),
            "lastName": employee_data.get("lastName", ""),
            "userType": "ADMINISTRATOR",
            "department": employee_data.get("department", {}),
        }
        result = await client.put(f"/employee/{employee_id}", update_payload)
        logger.info("Admin role assignment result: status=%s", result.get("value", {}).get("userType"))
