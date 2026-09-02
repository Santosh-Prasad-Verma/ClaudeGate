"""JSON Schema Sanitizer and Normalizer for Tool Definitions.

Transforms Draft-07 / Draft 2020-12 JSON Schema keywords (emitted by Claude Code / Anthropic SDK)
into clean, OpenAPI 3.0 / Gemini-compatible schema definitions.
"""

import copy
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Keywords that are invalid in OpenAPI 3.0 / Gemini Function Declarations and must be removed
DISALLOWED_SCHEMA_KEYWORDS: Set[str] = {
    "$schema",
    "$id",
    "$comment",
    "id",
    "propertyNames",
    "patternProperties",
    "prefixItems",
    "contains",
    "minContains",
    "maxContains",
    "unevaluatedProperties",
    "unevaluatedItems",
    "dependentRequired",
    "dependentSchemas",
    "dependencies",
    "contentMediaType",
    "contentEncoding",
    "contentSchema",
    "if",
    "then",
    "else",
    "not",
    "writeOnly",
}


def _infer_type_from_value(val: Any) -> str:
    """Infer JSON Schema type from Python value."""
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "number"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return "array"
    if isinstance(val, dict):
        return "object"
    return "string"


def inline_and_resolve_refs(schema: Dict[str, Any], root_defs: Optional[Dict[str, Any]] = None, depth: int = 0) -> Dict[str, Any]:
    """Inline all $ref definitions ($defs / definitions) into the schema to support backends that reject $ref."""
    if depth > 15:
        # Prevent infinite recursion for cyclic references
        return schema

    if root_defs is None:
        root_defs = {}
        if isinstance(schema.get("$defs"), dict):
            root_defs.update(schema["$defs"])
        if isinstance(schema.get("definitions"), dict):
            root_defs.update(schema["definitions"])

    if not isinstance(schema, dict):
        return schema

    # If this object is a $ref
    if "$ref" in schema and isinstance(schema["$ref"], str):
        ref_path = schema["$ref"]
        ref_key = ref_path.split("/")[-1]
        if ref_key in root_defs:
            resolved = copy.deepcopy(root_defs[ref_key])
            # Merge any local overrides
            for k, v in schema.items():
                if k != "$ref":
                    resolved[k] = v
            return inline_and_resolve_refs(resolved, root_defs, depth + 1)

    result = {}
    for k, v in schema.items():
        if k in ("$defs", "definitions"):
            # Omit root definition blocks from final output after inlining
            continue
        if isinstance(v, dict):
            result[k] = inline_and_resolve_refs(v, root_defs, depth + 1)
        elif isinstance(v, list):
            result[k] = [
                inline_and_resolve_refs(item, root_defs, depth + 1) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            result[k] = v

    return result


def sanitize_schema_node(node: Any) -> Any:
    """Recursively clean an individual schema node for OpenAPI 3.0 / Gemini compliance."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            return [sanitize_schema_node(item) for item in node]
        return node

    cleaned: Dict[str, Any] = {}

    # 1. Convert 'const' to 'enum: [val]'
    if "const" in node:
        val = node["const"]
        cleaned["enum"] = [val]
        if "type" not in node:
            cleaned["type"] = _infer_type_from_value(val)

    # 2. Convert 'exclusiveMinimum' and 'exclusiveMaximum' numbers to minimum/maximum
    if "exclusiveMinimum" in node:
        ex_min = node["exclusiveMinimum"]
        if isinstance(ex_min, (int, float)) and not isinstance(ex_min, bool):
            if "minimum" not in node and "minimum" not in cleaned:
                cleaned["minimum"] = ex_min

    if "exclusiveMaximum" in node:
        ex_max = node["exclusiveMaximum"]
        if isinstance(ex_max, (int, float)) and not isinstance(ex_max, bool):
            if "maximum" not in node and "maximum" not in cleaned:
                cleaned["maximum"] = ex_max

    # 3. Process existing keys
    for k, v in node.items():
        if k in DISALLOWED_SCHEMA_KEYWORDS or k in ("const", "exclusiveMinimum", "exclusiveMaximum"):
            continue

        if k == "type":
            if isinstance(v, list):
                # Handle multi-types e.g. ["string", "null"]
                if "null" in v:
                    non_null = [t for t in v if t != "null"]
                    if len(non_null) == 1:
                        cleaned["type"] = non_null[0]
                        cleaned["nullable"] = True
                    elif len(non_null) > 1:
                        cleaned["anyOf"] = [{"type": t} for t in non_null]
                        cleaned["nullable"] = True
                    else:
                        cleaned["type"] = "string"
                        cleaned["nullable"] = True
                else:
                    if len(v) == 1:
                        cleaned["type"] = v[0]
                    else:
                        cleaned["anyOf"] = [{"type": t} for t in v]
            elif isinstance(v, str):
                cleaned["type"] = v
            continue

        if k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
            # Check if anyOf is a list of {"const": val} -> collapse to enum
            if k in ("anyOf", "oneOf") and len(v) > 0 and all(isinstance(item, dict) and "const" in item for item in v):
                vals = [item["const"] for item in v]
                cleaned["enum"] = vals
                if "type" not in cleaned and vals:
                    cleaned["type"] = _infer_type_from_value(vals[0])
                continue

            # Check if anyOf is [{"type": T}, {"type": "null"}] -> nullable T
            if k in ("anyOf", "oneOf") and len(v) == 2 and any(isinstance(it, dict) and it.get("type") == "null" for it in v):
                non_null_item = next((it for it in v if isinstance(it, dict) and it.get("type") != "null"), None)
                if non_null_item is not None:
                    inner_cleaned = sanitize_schema_node(non_null_item)
                    if isinstance(inner_cleaned, dict):
                        cleaned.update(inner_cleaned)
                    cleaned["nullable"] = True
                    continue

            cleaned_list = [sanitize_schema_node(item) for item in v if item is not None]
            cleaned[k] = cleaned_list
            continue

        if k == "properties" and isinstance(v, dict):
            cleaned_props = {}
            for prop_name, prop_val in v.items():
                cleaned_props[str(prop_name)] = sanitize_schema_node(prop_val)
            cleaned["properties"] = cleaned_props
            continue

        if k == "items":
            if isinstance(v, dict):
                cleaned["items"] = sanitize_schema_node(v)
            elif isinstance(v, list):
                if v:
                    cleaned["items"] = sanitize_schema_node(v[0])
            continue

        if k == "additionalProperties":
            if isinstance(v, dict):
                cleaned["additionalProperties"] = sanitize_schema_node(v)
            elif isinstance(v, bool):
                cleaned["additionalProperties"] = v
            continue

        if k == "required" and isinstance(v, list):
            cleaned["required"] = [str(r) for r in v if isinstance(r, (str, int))]
            continue

        # Recurse for nested dicts or lists
        if isinstance(v, dict):
            cleaned[k] = sanitize_schema_node(v)
        elif isinstance(v, list):
            cleaned[k] = [sanitize_schema_node(item) for item in v]
        else:
            cleaned[k] = v

    return cleaned


def sanitize_tool_parameters(parameters: Any) -> Dict[str, Any]:
    """Sanitize and normalize tool parameters into a standard OpenAPI 3.0 / Gemini compatible schema."""
    if not isinstance(parameters, dict):
        return {"type": "object", "properties": {}}

    # 1. Resolve & inline $ref definitions
    inlined = inline_and_resolve_refs(copy.deepcopy(parameters))

    # 2. Recursively sanitize all nodes
    sanitized = sanitize_schema_node(inlined)

    if not isinstance(sanitized, dict):
        return {"type": "object", "properties": {}}

    # 3. Ensure top-level structure is a valid object schema
    if "type" not in sanitized or sanitized["type"] != "object":
        sanitized["type"] = "object"

    if "properties" not in sanitized or not isinstance(sanitized["properties"], dict):
        sanitized["properties"] = {}

    return sanitized
