import logging
import unicodedata

from ..tripletex import TripletexClient
from .department import resolve_or_create_department

logger = logging.getLogger("agent.workflows.employee")

ADMIN_KEYWORDS = {"administrator", "admin", "kontoadministrator", "administrateur", "administrador", "verwaltung"}

# Fields that are readonly in the API and must not be sent in PUT requests
_READONLY_FIELDS = {
    "changes", "url", "displayName", "pictureId", "companyId",
    "isAuthProjectOverviewForManager", "signatureStamp",
    "hasAllowanceInSalary", "employments", "holidayAllowanceEarned",
}


def _is_admin_requested(data: dict) -> bool:
    """Check if any field in data signals admin role."""
    role = data.get("role", "").lower().strip()
    return any(kw in role for kw in ADMIN_KEYWORDS)


async def create_employee(data: dict, client: TripletexClient) -> dict:
    """Create an employee and optionally assign role via entitlements.
    If an employee with the same email already exists, returns the existing employee."""

    # Step 0: Search for existing employee by email or name
    email = data.get("email")
    existing_employee = None
    if email:
        # email param is a substring/containing search — fetch multiple and verify exact match
        search = await client.get("/employee", params={"email": email, "count": 25})
        existing = search.get("values", [])
        for emp in existing:
            if emp.get("email", "").lower() == email.lower():
                existing_employee = emp
                break

    # Also search by name if no email match
    first_name = data.get("firstName", "")
    last_name = data.get("lastName", "")
    if not existing_employee and first_name and last_name:
        search = await client.get("/employee", params={"firstName": first_name, "lastName": last_name, "count": 10})
        candidates = search.get("values", [])
        for emp in candidates:
            if emp.get("firstName", "").lower() == first_name.lower() and emp.get("lastName", "").lower() == last_name.lower():
                existing_employee = emp
                break

    if existing_employee:
        emp_id = existing_employee["id"]
        logger.info("Employee already exists (id=%d) — checking if update needed", emp_id)
        update_payload = {}
        _UPDATABLE = ("email", "phoneNumberMobile", "dateOfBirth", "employeeNumber", "bankAccountNumber", "nationalIdentityNumber")
        for field in _UPDATABLE:
            desired = data.get(field)
            if desired is not None and desired != existing_employee.get(field):
                update_payload[field] = desired
        # Phone fallback — map generic phoneNumber to mobile, and phoneNumberWork if provided
        if not update_payload.get("phoneNumberMobile") and data.get("phoneNumber"):
            if data["phoneNumber"] != existing_employee.get("phoneNumberMobile"):
                update_payload["phoneNumberMobile"] = data["phoneNumber"]
        if data.get("phoneNumberWork"):
            if data["phoneNumberWork"] != existing_employee.get("phoneNumberWork"):
                update_payload["phoneNumberWork"] = data["phoneNumberWork"]
        # Check address (including addressLine2)
        desired_addr = data.get("address")
        if desired_addr:
            existing_addr = existing_employee.get("address") or {}
            for af in ("addressLine1", "addressLine2", "postalCode", "city"):
                dv = desired_addr.get(af, desired_addr.get("line1") if af == "addressLine1" else (desired_addr.get("line2") if af == "addressLine2" else None))
                if dv and dv != existing_addr.get(af):
                    if "address" not in update_payload:
                        update_payload["address"] = {k: existing_addr.get(k, "") for k in ("addressLine1", "addressLine2", "postalCode", "city")}
                    update_payload["address"][af] = dv
        # UserType upgrade if admin requested
        if _is_admin_requested(data) and existing_employee.get("userType") != "EXTENDED":
            update_payload["userType"] = "EXTENDED"
        if update_payload:
            logger.info("Updating employee %d with: %s", emp_id, list(update_payload.keys()))
            # Strip readonly fields to avoid sending them in PUT body
            put_body = {k: v for k, v in {**existing_employee, **update_payload}.items() if k not in _READONLY_FIELDS}
            put_result = await client.put(f"/employee/{emp_id}", put_body)
            if put_result.get("value"):
                existing_employee = put_result["value"]  # PUT returns updated employee
            else:
                logger.warning("Employee %d update failed: %s", emp_id, put_result)
        # Grant admin entitlements if needed (even if no field update was needed)
        if _is_admin_requested(data):
            await _grant_admin_entitlements(emp_id, client)
        return {"value": existing_employee}

    # Step 1: Ensure we have a department ID
    department_id = data.get("departmentId")

    # Resolve department by name if specified
    department_name = data.get("departmentName") or data.get("department")
    if not department_id and department_name:
        department_id = await resolve_or_create_department(department_name, client)

    if not department_id:
        dept_result = await client.get("/department", params={"count": 1})
        departments = dept_result.get("values", [])
        if departments:
            department_id = departments[0]["id"]
            logger.info("Using existing department ID: %d", department_id)
        else:
            department_id = await resolve_or_create_department("Avdeling", client)

    # Step 2: Build employee payload
    is_admin = _is_admin_requested(data)
    payload = {
        "firstName": data.get("firstName", ""),
        "lastName": data.get("lastName", ""),
        "userType": "EXTENDED" if is_admin else "STANDARD",
        "department": {"id": department_id},
    }

    # Email is required by Tripletex — generate fallback if not provided
    email = data.get("email")
    if not email:
        def _ascii_ify(s: str) -> str:
            """Transliterate non-ASCII chars to closest ASCII (e.g. á→a, ø→o, ü→u)."""
            # Pre-map chars that NFKD doesn't decompose (Nordic, German, etc.)
            _MAP = {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ð": "d", "Ð": "D",
                    "þ": "th", "Þ": "Th", "ß": "ss", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L"}
            s = "".join(_MAP.get(c, c) for c in s)
            nfkd = unicodedata.normalize("NFKD", s)
            return "".join(c for c in nfkd if unicodedata.category(c) != "Mn").encode("ascii", "ignore").decode("ascii")
        first = _ascii_ify(data.get("firstName", "user")).lower().replace(" ", ".")
        last = _ascii_ify(data.get("lastName", "unknown")).lower().replace(" ", ".")
        email = f"{first}.{last}@example.com"
        logger.info("No email provided, using fallback: %s", email)
    payload["email"] = email
    if data.get("phoneNumberMobile") or data.get("phoneNumber"):
        payload["phoneNumberMobile"] = data.get("phoneNumberMobile") or data["phoneNumber"]
    if data.get("phoneNumberWork"):
        payload["phoneNumberWork"] = data["phoneNumberWork"]
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
            "addressLine2": addr.get("line2", addr.get("addressLine2", "")),
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

    # Step 3: Grant admin entitlements if requested
    if is_admin:
        await _grant_admin_entitlements(employee_id, client)

    return result


async def _grant_admin_entitlements(employee_id: int, client: TripletexClient) -> None:
    """Grant admin entitlements to an employee.

    NOTE: PUT /employee/entitlement/:grantEntitlementsByTemplate is a BETA endpoint
    and CANNOT be used in competition environments. We set userType=EXTENDED on the
    employee instead, which is the non-BETA way to grant elevated access.
    """
    # BETA endpoint skipped — userType=EXTENDED is set during employee creation/update
    logger.info("Admin entitlements: employee %d has userType=EXTENDED (BETA entitlement endpoint skipped)", employee_id)
