import logging
import re

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.product")

_UPDATABLE_FIELDS = (
    "name",
    "costExcludingVatCurrency",
    "priceExcludingVatCurrency",
    "priceIncludingVatCurrency",
)
_FIELD_MAP = {
    "cost": "costExcludingVatCurrency",
    "price": "priceExcludingVatCurrency",
    "priceIncVat": "priceIncludingVatCurrency",
}
_REVERSE_FIELD_MAP = {v: k for k, v in _FIELD_MAP.items()}
_HIGH_VAT_HINTS = ("high", "hoy", "h\u00f8y", "standard", "default", "25")


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


async def _lookup_vat_type_from_string(client: TripletexClient, vat_hint: str) -> int | None:
    """Resolve VAT type from a free-text hint like '25%', 'High', or a VAT type name."""
    if not vat_hint:
        return None

    hint = vat_hint.strip().lower()
    result = await client.get("/ledger/vatType", params={"count": "100"})
    vat_types = result.get("values", [])

    pct_match = re.search(r"(\d+(?:[\.,]\d+)?)", hint)
    if pct_match:
        pct = float(pct_match.group(1).replace(",", "."))
        for vt in vat_types:
            if float(vt.get("percentage", -1)) == pct:
                return vt["id"]

    for vt in vat_types:
        name = (vt.get("name") or "").lower()
        if hint in name or name in hint:
            return vt["id"]

    return None


async def create_product(data: dict, client: TripletexClient) -> dict:
    """Create a product in Tripletex. Searches first to avoid duplicates, updates if needed."""

    name = data.get("name", "")
    number = data.get("number")

    # Search for existing product by number or name
    existing_product = None
    if number:
        search = await client.get("/product", params={"productNumber": str(number), "count": "1"})
        existing = search.get("values", [])
        if existing:
            existing_product = existing[0]
    if not existing_product and name:
        # name is a containing search, so fetch a wider window and exact-match client-side.
        search = await client.get("/product", params={"name": name, "count": "100"})
        for prod in search.get("values", []):
            if prod.get("name", "").lower() == name.lower():
                existing_product = prod
                break

    if existing_product:
        prod_id = existing_product["id"]
        logger.info("Product already exists (id=%d) — checking if update needed", prod_id)
        update_payload = {}
        for field in _UPDATABLE_FIELDS:
            desired = data.get(field)
            if desired is None:
                desired = data.get(_REVERSE_FIELD_MAP.get(field, ""))
            if desired is not None and desired != existing_product.get(field):
                update_payload[field] = desired
        if update_payload:
            logger.info("Updating product %d with: %s", prod_id, list(update_payload.keys()))
            put_body = {"id": prod_id, "version": existing_product["version"], **update_payload}
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
    vat_id = None
    if isinstance(vat, int):
        payload["vatType"] = {"id": vat}
    elif isinstance(vat, dict) and "id" in vat:
        payload["vatType"] = vat
    elif isinstance(vat, str):
        vat_id = await _lookup_vat_type_from_string(client, vat)
        if not vat_id:
            hint = vat.strip().lower()
            if any(token in hint for token in _HIGH_VAT_HINTS):
                vat_id = await _lookup_vat_type_25(client)
            else:
                logger.warning("Could not resolve VAT type from string '%s'; leaving vatType unset", vat)
    elif vat is not None:
        logger.warning("Ignoring unsupported vatType value of type %s", type(vat).__name__)

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
