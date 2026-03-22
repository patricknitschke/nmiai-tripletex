"""
Tripletex OpenAPI spec lookup tool.

Gives agents runtime access to the API spec so they can look up
correct field names, enum values, required fields, and available endpoints
instead of guessing.
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger("agent.api_spec")

SPEC_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "tripletex_openapi.json")


@lru_cache(maxsize=1)
def _load_spec() -> dict:
    """Load and cache the OpenAPI spec."""
    with open(SPEC_PATH) as f:
        return json.load(f)


def _resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a $ref pointer like '#/components/schemas/Employee'."""
    parts = ref.lstrip("#/").split("/")
    obj = spec
    for part in parts:
        obj = obj[part]
    return obj


def _format_fields(spec: dict, schema: dict, depth: int = 0, max_depth: int = 1) -> list[str]:
    """Format schema fields as readable lines."""
    if "$ref" in schema:
        schema = _resolve_ref(spec, schema["$ref"])

    # Unwrap array schemas (e.g. POST /activity/list expects array of objects)
    if schema.get("type") == "array" and "items" in schema:
        schema = schema["items"]
        if "$ref" in schema:
            schema = _resolve_ref(spec, schema["$ref"])

    if schema.get("type") != "object" or "properties" not in schema:
        return []

    lines = []
    prefix = "    " * depth

    for name, prop in schema["properties"].items():
        actual = prop
        if "$ref" in prop:
            actual = _resolve_ref(spec, prop["$ref"])

        # Skip readOnly fields (id, changes, url, etc.)
        if actual.get("readOnly"):
            continue

        field_type = actual.get("type", "object")
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            field_type = f"object ref → {ref_name}"
        elif field_type == "array" and "items" in prop:
            items = prop["items"]
            if "$ref" in items:
                ref_name = items["$ref"].split("/")[-1]
                field_type = f"array of → {ref_name}"
            else:
                field_type = f"array of {items.get('type', 'object')}"

        desc = actual.get("description", "")
        if desc:
            desc = f" — {desc[:80]}"

        enum = actual.get("enum")
        enum_str = f", enum: {enum}" if enum else ""

        lines.append(f"{prefix}  - {name} ({field_type}{enum_str}){desc}")

        # Show nested object fields (1 level deep)
        if depth < max_depth and "$ref" in prop:
            nested = _resolve_ref(spec, prop["$ref"])
            if nested.get("type") == "object" and "properties" in nested:
                nested_lines = _format_fields(spec, nested, depth + 1, max_depth)
                lines.extend(nested_lines[:10])  # Limit nested output

    return lines


def search_endpoints(keyword: str) -> str:
    """Search for endpoints matching a keyword. Returns a summary list."""
    spec = _load_spec()
    keyword_lower = keyword.lower()
    results = []

    for path, methods in spec["paths"].items():
        if keyword_lower not in path.lower():
            continue
        for method in ["get", "post", "put", "delete"]:
            if method not in methods:
                continue
            summary = methods[method].get("summary", "")
            beta = "⚠️ [BETA — BLOCKED in competition] " if "[BETA]" in summary else ""
            results.append(f"  {beta}{method.upper()} {path} — {summary}")

    if not results:
        return f"No endpoints found matching '{keyword}'."

    return f"Endpoints matching '{keyword}':\n" + "\n".join(results[:20])


def get_endpoint(path: str, method: str = "post") -> str:
    """Get full schema for an endpoint: params, body fields, required, enums."""
    spec = _load_spec()
    method = method.lower()

    # Try exact path first, then with common variations
    path_data = spec["paths"].get(path)
    if not path_data:
        # Try without leading slash
        path_data = spec["paths"].get(f"/{path.lstrip('/')}")
    if not path_data:
        return f"Endpoint '{path}' not found. Try search_endpoints to find it."

    if method not in path_data:
        available = [m for m in ["get", "post", "put", "delete"] if m in path_data]
        return f"{method.upper()} {path} not found. Available methods: {', '.join(available)}"

    op = path_data[method]
    summary = op.get('summary', '')
    beta_warning = "\n  ⚠️ WARNING: This is a [BETA] endpoint — BLOCKED in competition environments. Do NOT use." if "[BETA]" in summary else ""
    lines = [f"{method.upper()} {path}", f"  Summary: {summary}{beta_warning}"]

    # Query parameters
    params = op.get("parameters", [])
    if params:
        lines.append("  Query params:")
        for p in params:
            req = " REQUIRED" if p.get("required") else ""
            ptype = p.get("schema", {}).get("type", "?")
            enum = p.get("schema", {}).get("enum")
            enum_str = f", enum: {enum}" if enum else ""
            lines.append(f"    - {p['name']} ({ptype}{req}{enum_str})")

    # Request body
    body = op.get("requestBody", {})
    if body:
        content = body.get("content", {})
        json_content = content.get("application/json", content.get("application/json; charset=utf-8", {}))
        if json_content:
            schema = json_content.get("schema", {})
            is_array = schema.get("type") == "array"
            ref = schema.get("$ref", "") or schema.get("items", {}).get("$ref", "")
            ref_name = ref.split("/")[-1] if ref else ""
            array_note = "array of " if is_array else ""
            lines.append(f"  Request body ({array_note}{ref_name}):")
            field_lines = _format_fields(spec, schema)
            lines.extend(field_lines[:30])  # Limit output

    return "\n".join(lines)


def find_enum(keyword: str) -> str:
    """Find enum values for a field across all schemas."""
    spec = _load_spec()
    keyword_lower = keyword.lower()
    results = []

    schemas = spec.get("components", {}).get("schemas", {})
    for schema_name, schema_def in schemas.items():
        props = schema_def.get("properties", {})
        for field_name, prop in props.items():
            if keyword_lower in field_name.lower() or keyword_lower in schema_name.lower():
                enum = prop.get("enum")
                if enum:
                    results.append(f"  {schema_name}.{field_name}: {enum}")

    # Also check endpoint query params
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            for param in op.get("parameters", []):
                if keyword_lower in param.get("name", "").lower():
                    enum = param.get("schema", {}).get("enum")
                    if enum:
                        results.append(f"  {method.upper()} {path} param '{param['name']}': {enum}")

    if not results:
        return f"No enum values found matching '{keyword}'."

    return f"Enum values matching '{keyword}':\n" + "\n".join(results[:15])


def lookup(query: str) -> str:
    """
    Smart API spec lookup. Detects query type and routes to the right method.

    Examples:
        "POST /employee"           → full endpoint schema
        "GET /invoice"             → full endpoint schema
        "/employee"                → defaults to POST
        "invoice"                  → keyword search across endpoints
        "userType enum"            → find enum values
        "template enum"            → find enum values
    """
    query = query.strip()

    # Detect enum queries
    if "enum" in query.lower():
        keyword = query.lower().replace("enum", "").replace("values", "").strip()
        return find_enum(keyword)

    # Detect endpoint queries (contains / or starts with HTTP method)
    upper = query.upper()
    if query.startswith("/") or any(upper.startswith(m) for m in ["GET ", "POST ", "PUT ", "DELETE "]):
        parts = query.split(None, 1)
        if len(parts) == 2 and parts[0].upper() in ("GET", "POST", "PUT", "DELETE"):
            return get_endpoint(parts[1], parts[0])
        else:
            return get_endpoint(query, "post")

    # Default: keyword search
    return search_endpoints(query)
