import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.customer")


async def create_customer(data: dict, client: TripletexClient) -> dict:
    """Create a customer in Tripletex. Searches by org number or name first to avoid duplicates."""

    # Search for existing customer by org number or name
    org_number = data.get("organizationNumber")
    name = data.get("name", "")
    if org_number:
        search = await client.get("/customer", params={"organizationNumber": org_number, "count": "1"})
        existing = search.get("values", [])
        if existing:
            logger.info("Customer already exists with org %s (id=%d)", org_number, existing[0]["id"])
            return {"value": existing[0]}
    elif name:
        search = await client.get("/customer", params={"name": name, "count": "10"})
        for cust in search.get("values", []):
            if cust.get("name", "").lower() == name.lower():
                logger.info("Customer already exists with name '%s' (id=%d)", name, cust["id"])
                return {"value": cust}

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
    else:
        logger.error("Failed to create customer: %s", result)

    return result
