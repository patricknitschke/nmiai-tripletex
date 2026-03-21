import logging
from datetime import date

from ..tripletex import TripletexClient
from .employee import create_employee
from .employment import _resolve_occupation_code

logger = logging.getLogger("agent.workflows.payroll")


def _normalize(value: str) -> str:
    return value.strip().lower() if value else ""


async def register_payroll(data: dict, client: TripletexClient) -> dict:
    """Execute payroll (salary transaction) for an employee.

    Steps:
    1. Find or create the employee
    2. Ensure employment record exists for the period
    3. Look up salary types (base salary + bonus if needed)
    4. Check idempotency for existing payslip in the same period
    5. Build salary specifications
    6. POST /salary/transaction with payslip + specifications
    """
    today = date.today()
    tx_date_obj = date.fromisoformat(data["date"]) if data.get("date") else today
    tx_date = tx_date_obj.isoformat()
    year = data.get("year") or tx_date_obj.year
    month = data.get("month") or tx_date_obj.month

    # --- Step 1: Find or create employee ---
    employee_id = data.get("employeeId")
    email = data.get("email") or data.get("employeeEmail")
    first_name = data.get("firstName") or data.get("employeeFirstName") or ""
    last_name = data.get("lastName") or data.get("employeeLastName") or ""
    existing_employee = None

    if not employee_id:
        existing_employee = await _find_existing_employee(client, email, first_name, last_name)
        if existing_employee:
            employee_id = existing_employee["id"]
            first_name = first_name or existing_employee.get("firstName", "")
            last_name = last_name or existing_employee.get("lastName", "")

    if not employee_id:
        if not data.get("dateOfBirth"):
            return {"error": "dateOfBirth is required when payroll must create a new employee/employment"}

        emp_data = {}
        if first_name:
            emp_data["firstName"] = first_name
        if last_name:
            emp_data["lastName"] = last_name
        if email:
            emp_data["email"] = email
        if data.get("dateOfBirth"):
            emp_data["dateOfBirth"] = data["dateOfBirth"]

        emp_result = await create_employee(emp_data, client)
        employee_id = emp_result.get("value", {}).get("id")
        if not employee_id:
            return {"error": "Failed to find/create employee", "details": emp_result}

    logger.info("Employee ID: %d", employee_id)

    # --- Step 2: Ensure employment exists for this period ---
    emp_detail = await client.get(f"/employee/{employee_id}", params={"fields": "employments(*)"})
    emp_value = emp_detail.get("value", {})
    first_name = first_name or emp_value.get("firstName", "")
    last_name = last_name or emp_value.get("lastName", "")
    employments = emp_value.get("employments", [])

    if not employments:
        logger.info("No employment record found, creating one...")
        # Need dateOfBirth for employment creation — fetch full employee for version + dateOfBirth
        emp_full = await client.get(f"/employee/{employee_id}")
        emp_info = emp_full.get("value", {})
        if not emp_info.get("dateOfBirth"):
            dob = data.get("dateOfBirth")
            if not dob:
                return {"error": "dateOfBirth is required to create employment for payroll"}
            logger.info("Setting employee %d dateOfBirth for employment requirement", employee_id)
            await client.put(f"/employee/{employee_id}", {
                "id": employee_id,
                "version": emp_info.get("version", 1),
                "dateOfBirth": dob,
            })

        start_date = f"{year}-{month:02d}-01"
        employment_payload = {
            "employee": {"id": employee_id},
            "startDate": start_date,
            "isMainEmployer": True,
            "taxDeductionCode": "loennFraHovedarbeidsgiver",
        }
        # Link employment to company division (required for salary transactions)
        division_id = await _resolve_division(client)
        if division_id:
            employment_payload["division"] = {"id": division_id}
        emp_result = await client.post("/employee/employment", employment_payload)
        employment_id = emp_result.get("value", {}).get("id")
        if not employment_id:
            return {"error": "Failed to create employment", "details": emp_result}

        # Create employment details
        annual_salary = data.get("baseSalary", 0) * 12 if data.get("baseSalary") else None
        details_payload = {
            "employment": {"id": employment_id},
            "date": start_date,
            "employmentType": "ORDINARY",
            "employmentForm": "PERMANENT",
            "remunerationType": "MONTHLY_WAGE",
            "workingHoursScheme": "NOT_SHIFT",
            "percentageOfFullTimeEquivalent": 100,
        }
        if annual_salary:
            details_payload["annualSalary"] = annual_salary

        # Resolve STYRK occupation code (required for a-melding)
        occupation_code = data.get("occupationCode") or data.get("styrkCode") or "2411"
        oc_str = str(occupation_code).strip()
        matched_oc = await _resolve_occupation_code(oc_str, client)
        if matched_oc:
            details_payload["occupationCode"] = {"id": matched_oc["id"]}
            logger.info("Resolved STYRK %s → id=%d (code=%s)", occupation_code, matched_oc["id"], matched_oc.get("code"))
        else:
            logger.warning("STYRK code %s not found, employment details may fail", occupation_code)

        details_result = await client.post("/employee/employment/details", details_payload)
        if not details_result.get("value", {}).get("id"):
            logger.warning("Employment details creation may have failed: %s", details_result)

        # Set standard time
        hours_result = await client.post("/employee/standardTime", {
            "employee": {"id": employee_id},
            "fromDate": start_date,
            "hoursPerDay": data.get("hoursPerDay") or data.get("workingHoursPerDay") or 7.5,
        })
        if not hours_result.get("value", {}).get("id"):
            logger.warning("Standard time creation may have failed: %s", hours_result)

        logger.info("Employment + details + hours created for employee %d", employee_id)

    # --- Step 3: Look up salary types ---
    base_salary_type_id = await _resolve_salary_type(client, search_term="Fast", preferred_names=("Fastlønn", "Fast"))
    if not base_salary_type_id:
        return {"error": "Could not find an active base salary type matching Fast/Fastlønn"}

    # --- Step 4: Idempotency check ---
    allow_duplicate = data.get("allowDuplicate", False)
    if not allow_duplicate:
        year_to = year + 1
        month_to = month + 1
        if month == 12:
            month_to = 1
            year_to = year + 2

        payslip_result = await client.get(
            "/salary/payslip",
            params={
                "employeeId": str(employee_id),
                "yearFrom": str(year),
                "yearTo": str(year_to),
                "monthFrom": str(month),
                "monthTo": str(month_to),
                "count": "1",
            },
        )
        existing_payslips = payslip_result.get("values", [])
        if existing_payslips:
            return {
                "error": "Payroll already exists for this employee/period",
                "existingPayslipId": existing_payslips[0].get("id"),
                "employeeId": employee_id,
                "year": year,
                "month": month,
            }

    # --- Step 5: Build specifications ---
    base_salary = data.get("baseSalary", 0)
    bonus = data.get("bonus", 0)

    specifications = []

    if base_salary:
        specifications.append({
            "salaryType": {"id": base_salary_type_id},
            "employee": {"id": employee_id},
            "rate": base_salary,
            "count": 1,
            "amount": base_salary,
        })

    if bonus:
        bonus_type_id = await _resolve_salary_type(client, search_term="Bonus", preferred_names=("Bonus",))
        if not bonus_type_id:
            bonus_type_id = base_salary_type_id

        specifications.append({
            "salaryType": {"id": bonus_type_id},
            "employee": {"id": employee_id},
            "rate": bonus,
            "count": 1,
            "amount": bonus,
            "description": "Bonus",
        })

    if not specifications:
        return {"error": "No salary amounts provided (need baseSalary or bonus)"}

    # --- Step 6: Create salary transaction ---
    transaction_payload = {
        "date": tx_date,
        "year": year,
        "month": month,
        "payslips": [
            {
                "employee": {"id": employee_id},
                "specifications": specifications,
            }
        ],
    }
    generate_tax_deduction = data.get("generateTaxDeduction")
    if isinstance(generate_tax_deduction, str):
        generate_tax_deduction = generate_tax_deduction.strip().lower() == "true"
    if generate_tax_deduction is None:
        generate_tax_deduction = True

    logger.info("Creating salary transaction: year=%d, month=%d, base=%s, bonus=%s",
                year, month, base_salary, bonus)
    result = await client.post(
        "/salary/transaction",
        transaction_payload,
        params={"generateTaxDeduction": str(generate_tax_deduction).lower()},
    )

    if result.get("value"):
        tx_id = result["value"].get("id")
        employee_label = " ".join(part for part in (first_name, last_name) if part).strip() or f"employee {employee_id}"
        logger.info("Salary transaction created with ID: %s", tx_id)
        return {
            "value": {
                "transactionId": tx_id,
                "employeeId": employee_id,
                "baseSalary": base_salary,
                "bonus": bonus,
                "total": base_salary + bonus,
                "year": year,
                "month": month,
                "summary": f"Payroll executed for {employee_label}: base {base_salary} NOK + bonus {bonus} NOK = {base_salary + bonus} NOK"
            }
        }

    return {"error": "Failed to create salary transaction", "details": result}


async def _resolve_division(client: TripletexClient) -> int | None:
    """Look up the first available company division."""
    result = await client.get("/division", params={"count": "1"})
    divisions = result.get("values", [])
    if divisions:
        logger.info("Found division: id=%d name=%s", divisions[0]["id"], divisions[0].get("name"))
        return divisions[0]["id"]
    return None


async def _find_existing_employee(client: TripletexClient, email: str, first_name: str, last_name: str) -> dict | None:
    """Find an existing employee by exact email or exact first/last name."""
    if email:
        result = await client.get("/employee", params={"email": email, "count": "25"})
        for employee in result.get("values", []):
            if _normalize(employee.get("email", "")) == _normalize(email):
                return employee

    if first_name and last_name:
        result = await client.get(
            "/employee",
            params={"firstName": first_name, "lastName": last_name, "count": "10"},
        )
        for employee in result.get("values", []):
            if (
                _normalize(employee.get("firstName", "")) == _normalize(first_name)
                and _normalize(employee.get("lastName", "")) == _normalize(last_name)
            ):
                return employee

    return None


async def _resolve_salary_type(client: TripletexClient, search_term: str, preferred_names: tuple[str, ...]) -> int | None:
    """Look up an active salary type and return the best preferred match."""
    result = await client.get(
        "/salary/type",
        params={"name": search_term, "count": "10", "isInactive": "false"},
    )
    types = result.get("values", [])
    normalized_preferences = tuple(_normalize(name) for name in preferred_names)

    for salary_type in types:
        salary_type_name = _normalize(salary_type.get("name", ""))
        if salary_type_name in normalized_preferences:
            logger.info("Found salary type '%s': id=%d", salary_type.get("name"), salary_type["id"])
            return salary_type["id"]

    for salary_type in types:
        salary_type_name = _normalize(salary_type.get("name", ""))
        if any(pref in salary_type_name for pref in normalized_preferences):
            logger.info("Found salary type '%s': id=%d", salary_type.get("name"), salary_type["id"])
            return salary_type["id"]

    return None
