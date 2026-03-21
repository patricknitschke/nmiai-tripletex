import logging
from datetime import date

from ..tripletex import TripletexClient
from .employee import create_employee

logger = logging.getLogger("agent.workflows.payroll")


async def register_payroll(data: dict, client: TripletexClient) -> dict:
    """Execute payroll (salary transaction) for an employee.

    Steps:
    1. Find or create the employee
    2. Ensure employment record exists for the period
    3. Look up salary types (base salary + bonus if needed)
    4. POST /salary/transaction with payslip + specifications
    """
    today = date.today()
    year = data.get("year") or today.year
    month = data.get("month") or today.month
    tx_date = data.get("date") or today.isoformat()

    # --- Step 1: Find or create employee ---
    employee_id = data.get("employeeId")
    email = data.get("email") or data.get("employeeEmail")
    first_name = data.get("firstName") or data.get("employeeFirstName") or ""
    last_name = data.get("lastName") or data.get("employeeLastName") or ""

    if not employee_id:
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
    employments = emp_detail.get("value", {}).get("employments", [])

    if not employments:
        logger.info("No employment record found, creating one...")
        # Need dateOfBirth for employment creation — fetch full employee for version + dateOfBirth
        emp_full = await client.get(f"/employee/{employee_id}")
        emp_info = emp_full.get("value", {})
        if not emp_info.get("dateOfBirth"):
            dob = data.get("dateOfBirth", "1990-01-01")
            logger.info("Setting dateOfBirth to %s for employment requirement", dob)
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
        details_result = await client.post("/employee/employment/details", details_payload)
        if not details_result.get("value", {}).get("id"):
            logger.warning("Employment details creation may have failed: %s", details_result)

        # Set standard time
        hours_result = await client.post("/employee/standardTime", {
            "employee": {"id": employee_id},
            "fromDate": start_date,
            "hoursPerDay": 7.5,
        })
        if not hours_result.get("value", {}).get("id"):
            logger.warning("Standard time creation may have failed: %s", hours_result)

        logger.info("Employment + details + hours created for employee %d", employee_id)

    # --- Step 3: Look up salary types ---
    base_salary_type_id = await _resolve_salary_type(client, "Fastlønn")
    if not base_salary_type_id:
        # Try alternate names
        base_salary_type_id = await _resolve_salary_type(client, "Fast")
    if not base_salary_type_id:
        # Fall back: get first available salary type
        all_types = await client.get("/salary/type", params={"count": "5"})
        types_list = all_types.get("values", [])
        if types_list:
            base_salary_type_id = types_list[0]["id"]
            logger.info("Fallback salary type: id=%d name=%s", base_salary_type_id, types_list[0].get("name"))

    if not base_salary_type_id:
        return {"error": "Could not find any salary type"}

    # --- Step 4: Build specifications ---
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
        # Try to find a bonus salary type
        bonus_type_id = await _resolve_salary_type(client, "Bonus")
        if not bonus_type_id:
            bonus_type_id = await _resolve_salary_type(client, "Tillegg")
        if not bonus_type_id:
            # Use same type as base salary with different description
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

    # --- Step 5: Create salary transaction ---
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

    logger.info("Creating salary transaction: year=%d, month=%d, base=%s, bonus=%s",
                year, month, base_salary, bonus)
    result = await client.post("/salary/transaction", transaction_payload)

    if result.get("value"):
        tx_id = result["value"].get("id")
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
                "summary": f"Payroll executed for {first_name} {last_name}: base {base_salary} NOK + bonus {bonus} NOK = {base_salary + bonus} NOK"
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


async def _resolve_salary_type(client: TripletexClient, name: str) -> int | None:
    """Look up a salary type by name, return its ID or None."""
    result = await client.get("/salary/type", params={"name": name, "count": "5"})
    types = result.get("values", [])
    if types:
        logger.info("Found salary type '%s': id=%d", name, types[0]["id"])
        return types[0]["id"]
    return None
