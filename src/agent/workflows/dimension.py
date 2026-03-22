import logging
from datetime import date

from ..tripletex import TripletexClient
from .voucher import create_voucher

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

    failed_values = [v for v in created_values if "error" in v]
    result = {
        "dimensionName": dimension_name,
        "dimensionIndex": dimension_index,
        "values": created_values,
        "message": f"Created dimension '{dimension_name}' (index {dimension_index}) with {len(created_values) - len(failed_values)}/{len(created_values)} values",
    }
    if failed_values:
        result["ok"] = False
        result["errors"] = [v["error"] for v in failed_values]
        result["_needs_repair"] = (
            f"{len(failed_values)} dimension value(s) failed to create. "
            "Retry with raw POST /ledger/accountingDimensionValue for each failed value."
        )
    return result


async def create_dimension_voucher(data: dict, client: TripletexClient) -> dict:
    """Create dimension + values, then post a voucher linked to a dimension value.

    Combines create_dimension and create_voucher into a single atomic call so
    Senior doesn't drop the dimension value ID between steps.

    Extra data fields (beyond create_dimension fields):
    - voucherAccount: account number for the voucher (e.g. 6540, 6300)
    - voucherAmount: amount for the voucher posting
    - voucherDescription: description for the voucher
    - voucherDate: date for the voucher
    - linkValue: name of the dimension value to link the voucher to
    - balancingAccount: credit account (defaults to 2400)
    """
    # Step 1: Create dimension with values
    dim_result = await create_dimension(data, client)
    if dim_result.get("error"):
        return dim_result

    dimension_index = dim_result.get("dimensionIndex")
    created_values = dim_result.get("values", [])

    # Step 2: Find the dimension value to link
    link_value = data.get("linkValue") or data.get("linkedValue") or data.get("dimensionValue")
    target_value_id = None

    if link_value:
        link_lower = link_value.strip().lower()
        for v in created_values:
            if "error" not in v and v.get("displayName", "").strip().lower() == link_lower:
                target_value_id = v["id"]
                break

    # Fallback: use first successfully created value
    if not target_value_id:
        for v in created_values:
            if "error" not in v and v.get("id"):
                target_value_id = v["id"]
                break

    if not target_value_id:
        dim_result["_needs_repair"] = (
            "Dimension created but no value ID available for voucher. "
            "Create values manually, then post voucher with freeAccountingDimension linkage."
        )
        return dim_result

    # Step 3: Build and post voucher with dimension link
    account = data.get("voucherAccount") or data.get("account")
    amount = data.get("voucherAmount") or data.get("amount")
    desc = data.get("voucherDescription") or data.get("description") or dim_result.get("dimensionName", "Dimension voucher")
    voucher_date = data.get("voucherDate") or data.get("date") or date.today().isoformat()
    balancing = data.get("balancingAccount", 2400)

    if not account or not amount:
        # Still return the dimension result — Senior can post voucher manually
        dim_result["dimensionValueId"] = target_value_id
        dim_result["_needs_repair"] = (
            f"Dimension created OK. To post voucher: use create_voucher with "
            f"freeAccountingDimension{dimension_index}: {{\"id\": {target_value_id}}} on each posting"
        )
        return dim_result

    amount = float(amount)
    dim_field = f"freeAccountingDimension{dimension_index}"
    voucher_data = {
        "description": desc,
        "date": voucher_date,
        "postings": [
            {
                "account": account,
                "amount": amount,
                "description": desc,
                dim_field: {"id": target_value_id},
            },
            {
                "account": balancing,
                "amount": -amount,
                "description": desc,
            },
        ],
    }

    logger.info(
        "Posting voucher: account %s, %.2f NOK, linked to dimension %d value %d",
        account, amount, dimension_index, target_value_id,
    )
    voucher_result = await create_voucher(voucher_data, client)
    voucher_id = voucher_result.get("value", {}).get("id")

    # Merge results
    combined = {**dim_result}
    combined["voucher"] = voucher_result
    if voucher_id:
        combined["voucher_id"] = voucher_id
        combined["message"] = (
            f"Dimension '{dim_result.get('dimensionName')}' created with "
            f"{len([v for v in created_values if 'error' not in v])} values + "
            f"voucher {voucher_id} linked to value {target_value_id}"
        )
    else:
        combined["_needs_repair"] = (
            f"Dimension created but voucher failed. Use create_voucher with "
            f"freeAccountingDimension{dimension_index}: {{\"id\": {target_value_id}}} on each posting"
        )

    return combined
