import logging

from ..tripletex import TripletexClient

logger = logging.getLogger("agent.workflows.dimension")


async def create_dimension(data: dict, client: TripletexClient) -> dict:
    """Create a custom accounting dimension with values.

    Creates the dimension name, then creates each value with the correct
    fields (displayName is REQUIRED by the API).

    Returns the dimension index and value IDs so they can be used in
    voucher postings via freeAccountingDimension1/2/3.
    """
    dimension_name = data.get("dimensionName") or data.get("name")
    if not dimension_name:
        return {"error": "dimensionName is required"}

    values = data.get("values", [])
    description = data.get("description", "")

    # Step 1: Create the dimension name
    dim_payload = {
        "dimensionName": dimension_name,
        "active": True,
    }
    if description:
        dim_payload["description"] = description

    result = await client.post("/ledger/accountingDimensionName", dim_payload)
    dim_data = result.get("value", {})
    dimension_index = dim_data.get("dimensionIndex")

    if not dimension_index:
        logger.error("Failed to create dimension name: %s", result)
        return {"error": f"Failed to create dimension: {result}"}

    logger.info("Created dimension '%s' with index %d", dimension_name, dimension_index)

    # Step 2: Create each dimension value
    created_values = []
    for i, val in enumerate(values):
        # Support both string values and dict values
        if isinstance(val, str):
            display_name = val
            number = str(i + 1)
        else:
            display_name = val.get("displayName") or val.get("name", f"Value {i+1}")
            number = val.get("number", str(i + 1))

        val_payload = {
            "displayName": display_name,
            "number": number,
            "dimensionIndex": dimension_index,
            "active": True,
            "showInVoucherRegistration": True,
        }

        val_result = await client.post("/ledger/accountingDimensionValue", val_payload)
        val_data = val_result.get("value", {})
        val_id = val_data.get("id")

        if val_id:
            logger.info("Created dimension value '%s' (id=%d, index=%d)", display_name, val_id, dimension_index)
            created_values.append({
                "id": val_id,
                "displayName": display_name,
                "number": number,
            })
        else:
            logger.error("Failed to create dimension value '%s': %s", display_name, val_result)
            created_values.append({"error": f"Failed: {val_result}", "displayName": display_name})

    return {
        "dimensionName": dimension_name,
        "dimensionIndex": dimension_index,
        "values": created_values,
        "message": f"Created dimension '{dimension_name}' (index {dimension_index}) with {len(created_values)} values",
    }
