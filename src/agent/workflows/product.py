import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.product")


async def create_product(data: dict, client: TripletexClient) -> dict:
    """Create a product in Tripletex."""
    payload = {
        "name": data.get("name", ""),
    }

    if data.get("number"):
        payload["number"] = data["number"]
    if data.get("cost"):
        payload["costExcludingVatCurrency"] = data["cost"]
    if data.get("price"):
        payload["priceExcludingVatCurrency"] = data["price"]
    if data.get("priceIncVat"):
        payload["priceIncludingVatCurrency"] = data["priceIncVat"]
    if data.get("vatType"):
        payload["vatType"] = {"id": data["vatType"]} if isinstance(data["vatType"], int) else data["vatType"]

    logger.info("Creating product: %s", payload.get("name"))
    result = await client.post("/product", payload)

    product_id = result.get("value", {}).get("id")
    if product_id:
        logger.info("Product created with ID: %d", product_id)
    else:
        logger.error("Failed to create product: %s", result)

    return result
