import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.customer")


async def create_customer(data: dict, client: TripletexClient) -> dict:
    """Create a customer in Tripletex. Searches by org number or name first to avoid duplicates."""

    # Search for existing customer by org number or name
    org_number = data.get("organizationNumber")
    name = data.get("name", "")
    existing_customer = None
    if org_number:
        search = await client.get("/customer", params={"organizationNumber": org_number, "count": "1"})
        existing = search.get("values", [])
        if existing:
            existing_customer = existing[0]
    elif name:
        search = await client.get("/customer", params={"name": name, "count": "10"})
        for cust in search.get("values", []):
            if cust.get("name", "").lower() == name.lower():
                existing_customer = cust
                break

    if existing_customer:
        cust_id = existing_customer["id"]
        logger.info("Customer already exists (id=%d) — checking if update needed", cust_id)
        update_payload = {}
        _UPDATABLE = ("email", "invoiceEmail", "phoneNumber", "organizationNumber", "isSupplier", "isCustomer", "language", "invoiceSendMethod", "invoicesDueIn")
        for field in _UPDATABLE:
            desired = data.get(field)
            if desired is not None and desired != existing_customer.get(field):
                update_payload[field] = desired
        # Check address
        desired_addr = data.get("address") or data.get("postalAddress")
        if desired_addr:
            existing_addr = existing_customer.get("postalAddress") or {}
            for af in ("addressLine1", "postalCode", "city"):
                dv = desired_addr.get(af, desired_addr.get("line1") if af == "addressLine1" else None)
                if dv and dv != existing_addr.get(af):
                    if "postalAddress" not in update_payload:
                        update_payload["postalAddress"] = {k: existing_addr.get(k, "") for k in ("addressLine1", "postalCode", "city")}
                    update_payload["postalAddress"][af] = dv
        if update_payload:
            logger.info("Updating customer %d with: %s", cust_id, list(update_payload.keys()))
            put_body = {**existing_customer, **update_payload}
            put_result = await client.put(f"/customer/{cust_id}", put_body)
            return put_result if put_result.get("value") else {"value": existing_customer, "update_error": put_result}
        logger.info("Customer %d already matches desired state", cust_id)
        return {"value": existing_customer}

    # If isSupplier is set but isCustomer isn't explicitly provided, default isCustomer to false
    default_is_customer = not data.get("isSupplier", False)
    payload = {
        "name": name,
        "isCustomer": data.get("isCustomer", default_is_customer),
    }

    if data.get("email"):
        payload["email"] = data["email"]
    if data.get("invoiceEmail"):
        payload["invoiceEmail"] = data["invoiceEmail"]
        # Also set general email if not explicitly provided
        if not data.get("email"):
            payload["email"] = data["invoiceEmail"]
    if data.get("phone") or data.get("phoneNumber"):
        payload["phoneNumber"] = data.get("phone") or data.get("phoneNumber")
    if data.get("organizationNumber"):
        payload["organizationNumber"] = data["organizationNumber"]
    if data.get("isSupplier") is not None:
        payload["isSupplier"] = data["isSupplier"]
    if data.get("isPrivateIndividual") is not None:
        payload["isPrivateIndividual"] = data["isPrivateIndividual"]
    if data.get("language"):
        payload["language"] = data["language"].upper()
    if data.get("invoiceSendMethod"):
        payload["invoiceSendMethod"] = data["invoiceSendMethod"].upper()
    if data.get("invoicesDueIn") is not None:
        payload["invoicesDueIn"] = data["invoicesDueIn"]
        payload["invoicesDueInType"] = data.get("invoicesDueInType", "DAYS")

    # Address — accept both flat and nested formats
    address = data.get("address") or data.get("postalAddress")
    if address:
        payload["postalAddress"] = {
            "addressLine1": address.get("line1", address.get("addressLine1", "")),
            "postalCode": address.get("postalCode", ""),
            "city": address.get("city", ""),
        }
    if data.get("physicalAddress"):
        pa = data["physicalAddress"]
        payload["physicalAddress"] = {
            "addressLine1": pa.get("line1", pa.get("addressLine1", "")),
            "postalCode": pa.get("postalCode", ""),
            "city": pa.get("city", ""),
        }

    logger.info("Creating customer: %s", payload.get("name"))
    result = await client.post("/customer", payload)

    customer_id = result.get("value", {}).get("id")
    if customer_id:
        logger.info("Customer created with ID: %d", customer_id)

        # Free GET: verify critical fields were persisted correctly
        verify = await client.get(f"/customer/{customer_id}")
        actual = verify.get("value", {})
        mismatches = []
        for field in ("name", "email", "invoiceEmail", "organizationNumber", "isSupplier", "isCustomer", "phoneNumber"):
            expected = payload.get(field)
            got = actual.get(field)
            if expected is not None and got != expected:
                mismatches.append(f"{field}: sent {expected!r}, stored {got!r}")
        if payload.get("postalAddress"):
            actual_addr = actual.get("postalAddress", {})
            for af in ("addressLine1", "postalCode", "city"):
                expected_val = payload["postalAddress"].get(af)
                got_val = actual_addr.get(af)
                if expected_val and got_val != expected_val:
                    mismatches.append(f"postalAddress.{af}: sent {expected_val!r}, stored {got_val!r}")
        if mismatches:
            result["warnings"] = [f"Customer {customer_id} field mismatch: {m}" for m in mismatches]
            logger.warning("Customer %d verification mismatches: %s", customer_id, mismatches)
        else:
            logger.info("Customer %d verified OK", customer_id)
    else:
        logger.error("Failed to create customer: %s", result)

    return result
