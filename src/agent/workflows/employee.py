import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.employee")

ADMIN_KEYWORDS = {"administrator", "admin", "kontoadministrator", "administrateur", "administrador", "verwaltung"}


async def create_employee(data: dict, client: TripletexClient) -> dict:
    """Create an employee and optionally assign role via entitlements.
    If an employee with the same email already exists, returns the existing employee."""

    # Step 0: Search for existing employee by email (competition accounts may have pre-configured users)
    email = data.get("email")
    if email:
        search = await client.get("/employee", params={"email": email, "count": "1"})
        existing = search.get("values", [])
        if existing:
            logger.info("Employee already exists with email %s (id=%d)", email, existing[0]["id"])
            return {"value": existing[0]}

    # Step 1: Ensure we have a department ID
    department_id = data.get("departmentId")
    if not department_id:
        dept_result = await client.get("/department", params={"count": "1"})
        departments = dept_result.get("values", [])
        if departments:
            department_id = departments[0]["id"]
            logger.info("Using existing department ID: %d", department_id)
        else:
            logger.warning("No departments found — creating default")
            dept = await client.post("/department", {"name": "Avdeling", "departmentNumber": "1"})
            department_id = dept.get("value", {}).get("id")

    # Step 2: Build employee payload
    payload = {
        "firstName": data.get("firstName", ""),
        "lastName": data.get("lastName", ""),
        "userType": "STANDARD",
        "department": {"id": department_id},
    }

    if data.get("email"):
        payload["email"] = data["email"]
    if data.get("phoneNumberMobile") or data.get("phoneNumber"):
        payload["phoneNumberMobile"] = data.get("phoneNumberMobile") or data["phoneNumber"]
    if data.get("dateOfBirth"):
        payload["dateOfBirth"] = data["dateOfBirth"]
    if data.get("employeeNumber"):
        payload["employeeNumber"] = data["employeeNumber"]
    if data.get("bankAccountNumber"):
        payload["bankAccountNumber"] = data["bankAccountNumber"]
    if data.get("nationalIdentityNumber"):
        payload["nationalIdentityNumber"] = data["nationalIdentityNumber"]
    if data.get("address"):
        addr = data["address"]
        payload["address"] = {
            "addressLine1": addr.get("line1", addr.get("addressLine1", "")),
            "postalCode": addr.get("postalCode", ""),
            "city": addr.get("city", ""),
        }

    logger.info("Creating employee: %s %s", payload.get("firstName"), payload.get("lastName"))
    result = await client.post("/employee", payload)

    employee_id = result.get("value", {}).get("id")
    if not employee_id:
        logger.error("Failed to create employee: %s", result)
        return result

    logger.info("Employee created with ID: %d", employee_id)

    # Step 3: Role/entitlements
    # SKIPPED — PUT /employee/entitlement/:grantEntitlementsByTemplate is [BETA]
    # and returns 403 in competition environments. The employee is created with
    # userType=STANDARD which is sufficient for most tasks.
    role = data.get("role", "").lower().strip()
    if role and any(kw in role for kw in ADMIN_KEYWORDS):
        logger.info("Admin role requested for employee %d — skipping entitlement (BETA endpoint blocked)", employee_id)

    return result
