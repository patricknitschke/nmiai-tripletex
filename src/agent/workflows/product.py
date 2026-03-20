import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.product")


async def _lookup_vat_type(client: TripletexClient) -> int | None:
    """Get the first standard VAT type ID (typically 25% MVA)."""
    result = await client.get("/ledger/vatType", params={"count": "1"})
    types = result.get("values", [])
    if types:
        return types[0]["id"]
    return None


async def create_product(data: dict, client: TripletexClient) -> dict:
    """Create a product in Tripletex."""
    payload = {
        "name": data.get("name", ""),
    }

    if data.get("number"):
        payload["number"] = str(data["number"])
    if data.get("cost") is not None or data.get("costExcludingVatCurrency") is not None:
        payload["costExcludingVatCurrency"] = data.get("costExcludingVatCurrency", data.get("cost"))
    if data.get("price") is not None or data.get("priceExcludingVatCurrency") is not None:
        payload["priceExcludingVatCurrency"] = data.get("priceExcludingVatCurrency", data.get("price"))
    if data.get("priceIncVat") is not None or data.get("priceIncludingVatCurrency") is not None:
        payload["priceIncludingVatCurrency"] = data.get("priceIncludingVatCurrency", data.get("priceIncVat"))

    # VAT type — accept ID (int), object ({"id": int}), or lookup by name
    vat = data.get("vatType") or data.get("vatTypeId")
    if isinstance(vat, int):
        payload["vatType"] = {"id": vat}
    elif isinstance(vat, dict) and "id" in vat:
        payload["vatType"] = vat
    elif vat is not None:
        # LLM returned a string like "High" — lookup the VAT type
        vat_id = await _lookup_vat_type(client)
        if vat_id:
            payload["vatType"] = {"id": vat_id}

    # Department
    dept = data.get("departmentId") or data.get("department")
    if dept is not None:
        payload["department"] = {"id": dept} if isinstance(dept, int) else dept

    logger.info("Creating product: %s", payload.get("name"))
    result = await client.post("/product", payload)

    product_id = result.get("value", {}).get("id")
    if product_id:
        logger.info("Product created with ID: %d", product_id)
    else:
        logger.error("Failed to create product: %s", result)

    return result
