import logging
from datetime import date

from ..tripletex import TripletexClient
from .employee import create_employee

logger = logging.getLogger("agent.workflows.employment")


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

    # Step 2: Create employment record
    start_date = data.get("startDate", today)
    employment_payload = {
        "employee": {"id": employee_id},
        "startDate": start_date,
        "isMainEmployer": True,
        "taxDeductionCode": "loennFraHovedarbeidsgiver",
    }

    logger.info("Creating employment for employee %d (startDate=%s)", employee_id, start_date)
    employment_result = await client.post("/employee/employment", json=employment_payload)

    employment_id = employment_result.get("value", {}).get("id")
    if not employment_id:
        logger.error("Failed to create employment: %s", employment_result)
        return {"error": "Failed to create employment", "employee": employee_result, "details": employment_result}

    logger.info("Employment created with ID: %d", employment_id)

    # Step 3: Create employment details (STYRK, salary, percentage, type)
    details_payload = {
        "employment": {"id": employment_id},
        "date": start_date,
        "employmentType": data.get("employmentType", "ORDINARY"),
        "employmentForm": data.get("employmentForm", "PERMANENT"),
        "remunerationType": data.get("remunerationType", "MONTHLY_WAGE"),
        "workingHoursScheme": data.get("workingHoursScheme", "NOT_SHIFT"),
    }

    occupation_code = data.get("occupationCode") or data.get("styrkCode")
    if occupation_code:
        details_payload["occupationCode"] = {"code": str(occupation_code)}

    percentage = data.get("percentageOfFullTimeEquivalent") or data.get("percentage")
    if percentage is not None:
        details_payload["percentageOfFullTimeEquivalent"] = percentage

    annual_salary = data.get("annualSalary") or data.get("salary")
    if annual_salary is not None:
        details_payload["annualSalary"] = annual_salary

    logger.info("Creating employment details: STYRK=%s, salary=%s, percentage=%s",
                occupation_code, annual_salary, percentage)
    details_result = await client.post("/employee/employment/details", json=details_payload)

    details_id = details_result.get("value", {}).get("id")
    if details_id:
        logger.info("Employment details created with ID: %d", details_id)
    else:
        logger.error("Failed to create employment details: %s", details_result)

    # Step 4: Set standard working hours
    hours_per_day = data.get("hoursPerDay") or data.get("workingHoursPerDay")
    if hours_per_day:
        hours_payload = {
            "employee": {"id": employee_id},
            "fromDate": start_date,
            "hoursPerDay": hours_per_day,
        }
        logger.info("Setting standard time: %.1f hours/day from %s", hours_per_day, start_date)
        hours_result = await client.post("/employee/standardTime", json=hours_payload)
        hours_id = hours_result.get("value", {}).get("id")
        if hours_id:
            logger.info("Standard time created with ID: %d", hours_id)
        else:
            logger.error("Failed to create standard time: %s", hours_result)

    return {
        "value": {
            "employeeId": employee_id,
            "employmentId": employment_id,
            "detailsId": details_id,
            "summary": f"Employee {data.get('firstName')} {data.get('lastName')} registered with employment starting {start_date}"
        }
    }
