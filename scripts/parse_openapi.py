"""Parse the Tripletex OpenAPI spec and extract endpoint details for our workflows.

Usage:
    python scripts/parse_openapi.py                    # all key endpoints
    python scripts/parse_openapi.py employee           # just employee endpoints
    python scripts/parse_openapi.py employee customer  # multiple
"""

import json
import sys

SPEC_PATH = "tripletex_openapi.json"

# Endpoints we care about for the competition
KEY_PATHS = [
    "/employee",
    "/customer",
    "/department",
    "/product",
    "/order",
    "/order/{id}/:invoice",
    "/invoice",
    "/invoice/{id}/:payment",
    "/invoice/{id}/:createCreditNote",
    "/travelExpense",
    "/travelExpense/cost",
    "/project",
    "/ledger/voucher",
    "/employee/entitlement",
]


def resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a $ref pointer like '#/components/schemas/EmployeeDTO'."""
    parts = ref.lstrip("#/").split("/")
    obj = spec
    for part in parts:
        obj = obj[part]
    return obj


def get_schema_fields(spec: dict, schema: dict, depth: int = 0) -> list[dict]:
    """Extract fields from a schema, resolving refs. Returns list of field info dicts."""
    if "$ref" in schema:
        schema = resolve_ref(spec, schema["$ref"])

    if schema.get("type") != "object" or "properties" not in schema:
        return [{"name": "(raw)", "type": schema.get("type", "?"), "required": False, "description": schema.get("description", "")}]

    required_fields = set(schema.get("required", []))
    fields = []

    for name, prop in schema["properties"].items():
        # Resolve ref if needed
        actual_prop = prop
        if "$ref" in prop:
            actual_prop = resolve_ref(spec, prop["$ref"])

        field_type = actual_prop.get("type", "object")
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            field_type = f"object({ref_name})"

        # For nested objects, show their fields too (1 level deep)
        nested = None
        if depth < 1 and ("$ref" in prop or (field_type == "object" and "properties" in actual_prop)):
            nested_schema = resolve_ref(spec, prop["$ref"]) if "$ref" in prop else actual_prop
            if nested_schema.get("type") == "object" and "properties" in nested_schema:
                nested = get_schema_fields(spec, nested_schema, depth + 1)

        # Enum values
        enum = actual_prop.get("enum")

        fields.append({
            "name": name,
            "type": field_type,
            "required": name in required_fields,
            "description": actual_prop.get("description", ""),
            "enum": enum,
            "nested": nested,
        })

    return fields


def print_fields(fields: list[dict], indent: int = 0):
    """Pretty-print field info."""
    prefix = "  " * indent
    for f in fields:
        req = " *REQUIRED*" if f["required"] else ""
        enum_str = f"  enum: {f['enum']}" if f.get("enum") else ""
        desc = f"  — {f['description'][:80]}" if f["description"] else ""
        print(f"{prefix}  {'*' if f['required'] else '-'} {f['name']}: {f['type']}{enum_str}{desc}")
        if f.get("nested"):
            print_fields(f["nested"], indent + 1)


def main():
    with open(SPEC_PATH) as fh:
        spec = json.load(fh)

    paths = spec.get("paths", {})
    filter_terms = [a.lower() for a in sys.argv[1:]] if len(sys.argv) > 1 else None

    for path_key in sorted(paths.keys()):
        # Filter by key paths or user-provided terms
        if filter_terms:
            if not any(term in path_key.lower() for term in filter_terms):
                continue
        elif not any(path_key.startswith(kp) or path_key == kp for kp in KEY_PATHS):
            # Default: only show key paths (exact prefix match)
            matched = False
            for kp in KEY_PATHS:
                if path_key == f"/v2{kp}" or path_key.startswith(f"/v2{kp}"):
                    matched = True
                    break
            if not matched:
                continue

        path_data = paths[path_key]

        for method in ["get", "post", "put", "delete"]:
            if method not in path_data:
                continue

            op = path_data[method]
            summary = op.get("summary", "")
            op_id = op.get("operationId", "")

            print(f"\n{'='*70}")
            print(f"{method.upper()} {path_key}")
            print(f"  Summary: {summary}")
            if op_id:
                print(f"  Operation: {op_id}")

            # Query parameters
            params = op.get("parameters", [])
            if params:
                print(f"  Query params:")
                for p in params[:10]:  # limit output
                    req = " *REQUIRED*" if p.get("required") else ""
                    print(f"    - {p.get('name')}: {p.get('schema', {}).get('type', '?')}{req}")

            # Request body
            body = op.get("requestBody", {})
            if body:
                content = body.get("content", {})
                json_content = content.get("application/json", content.get("application/json; charset=utf-8", {}))
                if json_content:
                    schema = json_content.get("schema", {})
                    print(f"  Request body:")
                    fields = get_schema_fields(spec, schema)
                    print_fields(fields)

            # Response schema (just 200/201)
            for code in ["200", "201"]:
                resp = op.get("responses", {}).get(code, {})
                if resp:
                    resp_content = resp.get("content", {})
                    json_resp = resp_content.get("application/json", resp_content.get("application/json; charset=utf-8", {}))
                    if json_resp and json_resp.get("schema"):
                        print(f"  Response ({code}):")
                        fields = get_schema_fields(spec, json_resp["schema"])
                        # Only show top level, not full response
                        for f in fields[:5]:
                            print(f"    - {f['name']}: {f['type']}")


if __name__ == "__main__":
    main()
