"""Project invoice workflow — invoice a project's work to the customer.

Supports two modes:
1. Fixed-price projects: invoice a percentage or amount of the fixed price
2. Time-based projects: invoice registered hours at their rates

Flow: find project → gather billable data → create invoice with embedded order.
"""

import logging
from datetime import date, timedelta

from ..tripletex import TripletexClient
from .invoice import _ensure_bank_account, _lookup_vat_type_by_rate

logger = logging.getLogger("agent.workflows.project_invoice")


async def _find_project(data: dict, client: TripletexClient) -> dict | None:
    """Find a project by ID, number, or name. Returns full project dict."""
    project_id = data.get("projectId")
    if project_id:
        result = await client.get(f"/project/{project_id}")
        return result.get("value")

    project_number = data.get("projectNumber")
    if project_number:
        result = await client.get("/project", params={"number": str(project_number), "count": "1"})
        projects = result.get("values", [])
        if projects:
            return projects[0]

    project_name = data.get("projectName")
    if project_name:
        result = await client.get("/project", params={"name": project_name, "count": "10"})
        for proj in result.get("values", []):
            if proj.get("name", "").lower() == project_name.lower():
                return proj
        # Partial match fallback
        for proj in result.get("values", []):
            if project_name.lower() in proj.get("name", "").lower():
                return proj

    return None


async def _get_project_hours(project_id: int, client: TripletexClient) -> list[dict]:
    """Fetch timesheet entries for a project grouped by activity."""
    # dateTo is exclusive in Tripletex API — add 1 day to include today's entries
    date_to = (date.today() + timedelta(days=1)).isoformat()
    result = await client.get("/timesheet/entry", params={
        "projectId": str(project_id),
        "dateFrom": "2000-01-01",
        "dateTo": date_to,
        "count": "1000",
    })
    return result.get("values", [])


async def _get_project_activities(project_id: int, client: TripletexClient) -> dict[int, dict]:
    """Fetch activities for a project. Returns {activity_id: activity_dict}."""
    result = await client.get("/activity", params={
        "projectId": str(project_id),
        "count": "100",
    })
    return {a["id"]: a for a in result.get("values", [])}


async def _set_project_hourly_rate(project_id: int, rate: float, client: TripletexClient) -> None:
    """Configure a fixed hourly rate on the project so timesheet entries become chargeable.

    Idempotent: checks existing rates first to avoid duplicate writes (B54).
    Uses startDate=2020-01-01 so rate covers all historical entries.
    """
    rate_start = "2020-01-01"
    search = await client.get("/project/hourlyRates", params={
        "projectId": str(project_id),
        "type": "TYPE_FIXED_HOURLY_RATE",
        "count": "1",
        "fields": "id,version,project,startDate,hourlyRateModel,fixedRate,showInProjectOrder",
    })
    existing_rates = search.get("values", [])

    if existing_rates:
        existing = existing_rates[0]
        existing_rate = existing.get("fixedRate")
        if (
            existing.get("hourlyRateModel") == "TYPE_FIXED_HOURLY_RATE"
            and existing_rate is not None
            and abs(float(existing_rate) - rate) < 0.01
            and existing.get("showInProjectOrder") is True
        ):
            logger.info("Project %d already has hourly rate %.2f — skipping", project_id, rate)
            return

        # Update existing rate to match
        payload = {
            "id": existing["id"],
            "version": existing["version"],
            "project": {"id": project_id},
            "startDate": existing.get("startDate") or rate_start,
            "hourlyRateModel": "TYPE_FIXED_HOURLY_RATE",
            "fixedRate": rate,
            "showInProjectOrder": True,
        }
        result = await client.put(f"/project/hourlyRates/{existing['id']}", payload)
    else:
        payload = {
            "project": {"id": project_id},
            "startDate": rate_start,
            "hourlyRateModel": "TYPE_FIXED_HOURLY_RATE",
            "fixedRate": rate,
            "showInProjectOrder": True,
        }
        result = await client.post("/project/hourlyRates", payload)

    if result.get("value", {}).get("id"):
        logger.info("Set hourly rate %.2f on project %d", rate, project_id)
    else:
        logger.warning("Failed to set hourly rate on project %d: %s", project_id, result)


async def create_project_invoice(data: dict, client: TripletexClient) -> dict:
    """Invoice a project's work to the customer.

    Data fields:
    - projectId / projectNumber / projectName: identify the project
    - invoicePercent: for fixed-price, % to invoice (e.g. 50 for 50%)
    - invoiceAmount: specific amount to invoice (overrides percent)
    - includeHours: if True, invoice based on registered time entries
    - hourlyRate: override rate for time-based invoicing
    - description: invoice description / comment
    - invoiceDate: date for the invoice
    - sendToCustomer: whether to send
    """
    today = date.today().isoformat()

    # Step 1: Find the project
    project = await _find_project(data, client)
    if not project:
        return {"error": f"Project not found: {data.get('projectName') or data.get('projectId')}"}

    project_id = project["id"]
    project_name = project.get("name", "")
    is_fixed_price = project.get("isFixedPrice", False)
    fixed_price = project.get("fixedprice") or project.get("fixedPrice") or 0
    customer = project.get("customer") or {}
    customer_id = customer.get("id") if isinstance(customer, dict) else None

    if not customer_id:
        return {"error": f"Project '{project_name}' has no linked customer"}

    logger.info(
        "Project '%s' (id=%d): fixedPrice=%s, isFixedPrice=%s, customer=%d",
        project_name, project_id, fixed_price, is_fixed_price, customer_id,
    )

    # Step 1b: Set hourly rate on project if provided (makes timesheet entries chargeable)
    hourly_rate = data.get("hourlyRate") or data.get("rate")
    if hourly_rate:
        await _set_project_hourly_rate(project_id, float(hourly_rate), client)

    await _ensure_bank_account(client)

    invoice_date = data.get("invoiceDate") or today
    description = data.get("description", "")
    order_lines = []

    # Mode A: Fixed-price project — invoice a percentage or specific amount
    if is_fixed_price and fixed_price and not data.get("includeHours"):
        percent = data.get("invoicePercent")
        invoice_amount = data.get("invoiceAmount")

        if percent:
            invoice_amount = round(float(fixed_price) * float(percent) / 100, 2)
            line_desc = f"{project_name} — {percent}% av fastpris {fixed_price}"
        elif invoice_amount:
            line_desc = f"{project_name} — delfakturering"
        else:
            invoice_amount = float(fixed_price)
            line_desc = f"{project_name} — fastpris"

        if description:
            line_desc = description

        vat_id = await _lookup_vat_type_by_rate(25, client)
        ol = {
            "description": line_desc,
            "count": 1,
            "unitPriceExcludingVatCurrency": invoice_amount,
        }
        if vat_id:
            ol["vatType"] = {"id": vat_id}
        order_lines.append(ol)

        logger.info("Fixed-price invoice: %.2f NOK (%s%%)", invoice_amount, percent or 100)

    # Mode B: Time-based — invoice from registered hours
    else:
        entries = await _get_project_hours(project_id, client)
        if not entries:
            # Fallback: if no hours but we have an invoiceAmount, use that
            fallback_amount = data.get("invoiceAmount") or data.get("amount")
            if fallback_amount:
                vat_id = await _lookup_vat_type_by_rate(25, client)
                ol = {
                    "description": description or f"{project_name} — faktura",
                    "count": 1,
                    "unitPriceExcludingVatCurrency": float(fallback_amount),
                }
                if vat_id:
                    ol["vatType"] = {"id": vat_id}
                order_lines.append(ol)
            else:
                return {"error": f"No timesheet entries or amount for project '{project_name}'"}
        else:
            # Group hours by activity
            activity_hours: dict[int, float] = {}
            activity_chargeable: dict[int, float] = {}
            for entry in entries:
                act = entry.get("activity") or {}
                act_id = act.get("id", 0) if isinstance(act, dict) else 0
                hours = entry.get("hours", 0)
                chargeable = entry.get("chargeableHours", hours)
                activity_hours[act_id] = activity_hours.get(act_id, 0) + hours
                activity_chargeable[act_id] = activity_chargeable.get(act_id, 0) + chargeable

            # Resolve activity names
            activities = await _get_project_activities(project_id, client)

            # Build one invoice line per activity
            override_rate = data.get("hourlyRate") or data.get("rate")
            vat_id = await _lookup_vat_type_by_rate(25, client)

            for act_id, total_hours in activity_hours.items():
                chargeable = activity_chargeable.get(act_id, total_hours)
                if chargeable <= 0:
                    continue

                act_info = activities.get(act_id, {})
                act_name = act_info.get("name", f"Aktivitet {act_id}")
                rate = float(override_rate) if override_rate else act_info.get("rate", data.get("hourlyRate", 0))

                if not rate:
                    # Try to extract rate from the data
                    rate = data.get("hourlyRate") or data.get("rate") or 0

                if rate:
                    ol = {
                        "description": f"{act_name} — {chargeable}h",
                        "count": chargeable,
                        "unitPriceExcludingVatCurrency": float(rate),
                    }
                else:
                    # No rate available — use total hours as description
                    ol = {
                        "description": f"{act_name} — {chargeable}h",
                        "count": 1,
                        "unitPriceExcludingVatCurrency": 0,
                    }

                if vat_id:
                    ol["vatType"] = {"id": vat_id}
                order_lines.append(ol)

                logger.info("Time line: %s — %.1fh × %.2f = %.2f", act_name, chargeable, rate or 0, chargeable * (rate or 0))

    if not order_lines:
        return {"error": "No invoice lines could be built from project data"}

    # Step 3: Create invoice with embedded order
    order_payload = {
        "customer": {"id": customer_id},
        "orderDate": invoice_date,
        "deliveryDate": invoice_date,
        "orderLines": order_lines,
    }
    if data.get("invoiceComment") or description:
        order_payload["invoiceComment"] = data.get("invoiceComment") or description

    # Link to project
    order_payload["project"] = {"id": project_id}

    invoice_payload = {
        "customer": {"id": customer_id},
        "invoiceDate": invoice_date,
        "invoiceDueDate": data.get("dueDate") or data.get("invoiceDueDate") or invoice_date,
        "orders": [order_payload],
    }
    invoice_params = {
        "sendToCustomer": "true" if data.get("sendToCustomer") else "false",
    }

    logger.info("Creating direct project invoice for '%s' (%d lines)", project_name, len(order_lines))
    result = await client.post("/invoice", invoice_payload, params=invoice_params)

    invoice_id = result.get("value", {}).get("id")
    if invoice_id:
        logger.info("Project invoice created: id=%d for project '%s'", invoice_id, project_name)
        result.setdefault("value", {})["project_name"] = project_name
    else:
        logger.error("Failed to invoice project order: %s", result)

    return result
