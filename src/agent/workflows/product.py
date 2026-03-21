import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.product")


async def _lookup_vat_type_25(client: TripletexClient) -> int | None:
    """Get the 25% output (Utgående) VAT type ID."""
    result = await client.get("/ledger/vatType", params={"count": "100"})
    for vt in result.get("values", []):
        if vt.get("percentage") == 25 and "utgående" in vt.get("name", "").lower():
            return vt["id"]
    # Fallback: any 25% type
    for vt in result.get("values", []):
        if vt.get("percentage") == 25:
            return vt["id"]
    return None


async def create_product(data: dict, client: TripletexClient) -> dict:
    """Create a product in Tripletex. Searches first to avoid duplicates, updates if needed."""

    name = data.get("name", "")
    number = data.get("number")

    # Search for existing product by number or name
    existing_product = None
    if number:
        search = await client.get("/product", params={"number": str(number), "count": "1"})
        existing = search.get("values", [])
        if existing:
            existing_product = existing[0]
    if not existing_product and name:
        search = await client.get("/product", params={"name": name, "count": "10"})
        for prod in search.get("values", []):
            if prod.get("name", "").lower() == name.lower():
                existing_product = prod
                break

    if existing_product:
        prod_id = existing_product["id"]
        logger.info("Product already exists (id=%d) — checking if update needed", prod_id)
        update_payload = {}
        _UPDATABLE = ("name", "costExcludingVatCurrency", "priceExcludingVatCurrency", "priceIncludingVatCurrency")
        field_map = {"cost": "costExcludingVatCurrency", "price": "priceExcludingVatCurrency", "priceIncVat": "priceIncludingVatCurrency"}
        for field in _UPDATABLE:
            desired = data.get(field) or data.get({v: k for k, v in field_map.items()}.get(field, ""))
            if desired is not None and desired != existing_product.get(field):
                update_payload[field] = desired
        if update_payload:
            logger.info("Updating product %d with: %s", prod_id, list(update_payload.keys()))
            put_body = {**existing_product, **update_payload}
            put_result = await client.put(f"/product/{prod_id}", put_body)
            return put_result if put_result.get("value") else {"value": existing_product, "update_error": put_result}
        logger.info("Product %d already matches desired state", prod_id)
        return {"value": existing_product}

    payload = {
        "name": name,
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
        vat_id = await _lookup_vat_type_25(client)
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
