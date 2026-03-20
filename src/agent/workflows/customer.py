import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.customer")


async def create_customer(data: dict, client: TripletexClient) -> dict:
    """Create a customer in Tripletex."""
    payload = {
        "name": data.get("name", ""),
        "isCustomer": data.get("isCustomer", True),
    }

    if data.get("email"):
        payload["email"] = data["email"]
    if data.get("phone"):
        payload["phoneNumber"] = data["phone"]
    if data.get("isSupplier"):
        payload["isSupplier"] = data["isSupplier"]
    if data.get("organizationNumber"):
        payload["organizationNumber"] = data["organizationNumber"]

    # Address fields
    if data.get("address"):
        payload["postalAddress"] = {
            "addressLine1": data["address"].get("line1", ""),
            "postalCode": data["address"].get("postalCode", ""),
            "city": data["address"].get("city", ""),
        }

    logger.info("Creating customer: %s", payload.get("name"))
    result = await client.post("/customer", payload)

    customer_id = result.get("value", {}).get("id")
    if customer_id:
        logger.info("Customer created with ID: %d", customer_id)
    else:
        logger.error("Failed to create customer: %s", result)

    return result
