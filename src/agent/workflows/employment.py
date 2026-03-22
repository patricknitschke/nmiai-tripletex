import logging
from datetime import date

from ..tripletex import TripletexClient
from .employee import create_employee

logger = logging.getLogger("agent.workflows.employment")


async def _resolve_occupation_code(code: str, client: TripletexClient) -> dict | None:
    """Resolve a STYRK occupation code string to its Tripletex object.

    Searches broadly (count=25) since the API code param is substring match.
    Prefers exact prefix match, falls back to first result.
    If nothing found, retries with shorter prefix (first 2 digits).
    """
    # First attempt: search with full code, broad count
    oc_result = await client.get("/employee/employment/occupationCode", params={"code": code, "count": "25"})
    oc_values = oc_result.get("values", [])

    if oc_values:
        # Prefer exact match or prefix match (e.g. "3323" matches "3323.101")
        for oc in oc_values:
            oc_code = oc.get("code", "")
            if oc_code == code or oc_code.startswith(code):
                return oc
        # No exact/prefix match — return first result (closest substring match)
        return oc_values[0]

    # Fallback: search with shorter prefix (first 2 digits) to catch format variations
    if len(code) >= 3:
        logger.info("STYRK %s: no results with full code, trying prefix %s", code, code[:2])
        oc_result2 = await client.get("/employee/employment/occupationCode", params={"code": code[:2], "count": "50"})
        oc_values2 = oc_result2.get("values", [])
        for oc in oc_values2:
            if code in oc.get("code", ""):
                return oc
        # Still try first result with matching prefix
        for oc in oc_values2:
            if oc.get("code", "").startswith(code[:2]):
                return oc

    return None


async def register_employment(data: dict, client: TripletexClient) -> dict:
    """Register a full employment contract: employee + department + employment + salary + working hours.

    This is a self-contained workflow that handles everything from a PDF employment contract:
    1. Creates the employee (with department, DOB, NIN, bank account, email)
    2. Creates an employment record (start date, tax deduction code)
    3. Creates employment details (STYRK code, percentage, salary, employment type)
    4. Sets standard working hours

    Input data fields:
    - firstName, lastName, email, dateOfBirth, nationalIdentityNumber, bankAccountNumber
    - departmentName: department to assign (resolved or created)
    - startDate: employment start date (tiltredelse)
    - occupationCode: STYRK code (e.g. "2411")
    - percentageOfFullTimeEquivalent: e.g. 100 for full time
    - annualSalary: annual salary in NOK
    - hoursPerDay: standard working hours per day (e.g. 7.5)
    - employmentType: ORDINARY (default), MARITIME, FREELANCE
    - employmentForm: PERMANENT (default), TEMPORARY
    - remunerationType: MONTHLY_WAGE (default), HOURLY_WAGE
    """
    from src.agent.workflows.payroll import _resolve_or_create_division
    today = date.today().isoformat()

    # Step 1: Create the employee (handles department, search-before-create, etc.)
    employee_data = {}
    for field in ["firstName", "lastName", "email", "dateOfBirth", "nationalIdentityNumber",
                  "bankAccountNumber", "departmentName", "departmentId", "address"]:
        if data.get(field):
            employee_data[field] = data[field]

    employee_result = await create_employee(employee_data, client)
    employee_id = employee_result.get("value", {}).get("id")
    if not employee_id:
        logger.error("Failed to create employee: %s", employee_result)
        return {"error": "Failed to create employee", "details": employee_result}

    logger.info("Employee created/found with ID: %d", employee_id)

    # Step 2: Build employment details (inline with employment to save a POST)
    start_date = data.get("startDate", today)
    errors = []

    details_obj = {
        "date": start_date,
        "employmentType": data.get("employmentType", "ORDINARY"),
        "employmentForm": data.get("employmentForm", "PERMANENT"),
        "remunerationType": data.get("remunerationType", "MONTHLY_WAGE"),
        "workingHoursScheme": data.get("workingHoursScheme", "NOT_SHIFT"),
    }

    occupation_code = data.get("occupationCode") or data.get("styrkCode")
    if occupation_code:
        # STYRK code must be resolved to its Tripletex internal ID
        # Use broad count — API code param is substring/"Containing" match
        oc_str = str(occupation_code).strip()
        matched_oc = await _resolve_occupation_code(oc_str, client)
        if matched_oc:
            details_obj["occupationCode"] = {"id": matched_oc["id"]}
            logger.info("Resolved STYRK %s → id=%d (code=%s)", occupation_code, matched_oc["id"], matched_oc.get("code"))
        else:
            logger.warning("STYRK code %s not found in Tripletex after broad search, skipping", occupation_code)
            errors.append(f"STYRK/yrkeskode '{occupation_code}' not found — employment created without occupation code (may affect a-melding reporting)")

    percentage = data.get("percentageOfFullTimeEquivalent") or data.get("percentage")
    if percentage is not None:
        details_obj["percentageOfFullTimeEquivalent"] = percentage

    annual_salary = data.get("annualSalary") or data.get("salary")
    if annual_salary is not None:
        details_obj["annualSalary"] = annual_salary

    # Step 3: Create employment with details inlined (single POST instead of two)
    employment_payload = {
        "employee": {"id": employee_id},
        "startDate": start_date,
        "isMainEmployer": True,
        "taxDeductionCode": "loennFraHovedarbeidsgiver",
        "employmentDetails": [details_obj],
    }
    # Link employment to company division (required for salary transactions)
    division_id = await _resolve_or_create_division(client, employee_result.get("value", {}).get("companyId"))
    if division_id:
        employment_payload["division"] = {"id": division_id}

    logger.info("Creating employment for employee %d (startDate=%s, STYRK=%s, salary=%s, percentage=%s)",
                employee_id, start_date, occupation_code, annual_salary, percentage)
    employment_result = await client.post("/employee/employment", employment_payload)

    employment_id = employment_result.get("value", {}).get("id")
    if not employment_id:
        logger.error("Failed to create employment: %s", employment_result)
        return {"error": "Failed to create employment", "employee": employee_result, "details": employment_result}

    logger.info("Employment created with ID: %d", employment_id)

    # Extract details ID from the inlined response
    details_list = employment_result.get("value", {}).get("employmentDetails", [])
    details_id = details_list[0]["id"] if details_list else None
    if details_id:
        logger.info("Employment details created with ID: %d", details_id)
    else:
        logger.warning("Employment details not returned inline, may need separate creation")
        errors.append("Employment details not returned in employment response")

    # Step 4: Set standard working hours
    hours_id = None
    hours_per_day = data.get("hoursPerDay") or data.get("workingHoursPerDay")
    if hours_per_day:
        hours_payload = {
            "employee": {"id": employee_id},
            "fromDate": start_date,
            "hoursPerDay": hours_per_day,
        }
        logger.info("Setting standard time: %.1f hours/day from %s", hours_per_day, start_date)
        hours_result = await client.post("/employee/standardTime", hours_payload)
        hours_id = hours_result.get("value", {}).get("id")
        if hours_id:
            logger.info("Standard time created with ID: %d", hours_id)
        else:
            logger.error("Failed to create standard time: %s", hours_result)
            errors.append(f"Standard working hours failed ({hours_per_day}h/day not set): {hours_result}")

    result = {
        "value": {
            "employeeId": employee_id,
            "employmentId": employment_id,
            "detailsId": details_id,
            "hoursId": hours_id,
            "summary": f"Employee {data.get('firstName')} {data.get('lastName')} registered with employment starting {start_date}"
        }
    }

    # Verify employment details only when we actually set values worth checking
    warnings = []
    if details_id and (occupation_code or annual_salary is not None or percentage is not None):
        verify = await client.get(f"/employee/employment/details/{details_id}")
        actual = verify.get("value", {})
        if occupation_code:
            actual_code = actual.get("occupationCode", {})
            actual_code_str = actual_code.get("code", "") if isinstance(actual_code, dict) else str(actual_code)
            if str(occupation_code) != actual_code_str:
                warnings.append(f"STYRK code: sent {occupation_code}, stored {actual_code_str!r}")
        if annual_salary is not None:
            actual_salary = actual.get("annualSalary")
            if actual_salary is not None and abs(actual_salary - annual_salary) > 1:
                warnings.append(f"annualSalary: sent {annual_salary}, stored {actual_salary}")
        if percentage is not None:
            actual_pct = actual.get("percentageOfFullTimeEquivalent")
            if actual_pct is not None and abs(actual_pct - percentage) > 0.1:
                warnings.append(f"percentage: sent {percentage}, stored {actual_pct}")
    if warnings:
        logger.warning("Employment details %s verification mismatches: %s", details_id, warnings)
        result.setdefault("warnings", []).extend(warnings)

    if errors:
        result["ok"] = False
        result["errors"] = errors
        result["_needs_repair"] = (
            f"Employee {employee_id} and employment {employment_id} created, but {len(errors)} step(s) failed. "
            "Use raw API calls (POST /employee/employment/details or POST /employee/standardTime) to fix."
        )
    return result
