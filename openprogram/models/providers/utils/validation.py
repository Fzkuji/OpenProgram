"""
Tool argument validation — mirrors packages/ai/src/utils/validation.ts

Uses jsonschema for full TypeBox/AJV-equivalent validation including:
- Type checking
- Type coercion (via custom validator)
- Required field validation
- Format validation
"""
from __future__ import annotations

import copy
from typing import Any

from ..types import Tool, ToolCall

# Try to import jsonschema
try:
    import jsonschema
    from jsonschema import Draft7Validator
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False


def validate_tool_call(tools: list[Tool], tool_call: ToolCall) -> dict[str, Any]:
    """
    Find a tool by name and validate the tool call arguments.
    
    Args:
        tools: Array of tool definitions
        tool_call: The tool call from the LLM
        
    Returns:
        The validated arguments
        
    Raises:
        ValueError: If tool is not found or validation fails
    """
    tool = next((t for t in tools if t.name == tool_call.name), None)
    if not tool:
        raise ValueError(f'Tool "{tool_call.name}" not found')
    return validate_tool_arguments(tool, tool_call)


def _coerce_types(instance: Any, schema: dict[str, Any]) -> Any:
    """
    Coerce types to match schema, similar to AJV's coerceTypes.
    
    Supports:
    - string -> number (int/float)
    - number -> string
    - string -> boolean ("true"/"false")
    """
    if not isinstance(schema, dict):
        return instance
    
    schema_type = schema.get("type")
    
    if schema_type == "number" or schema_type == "integer":
        if isinstance(instance, str):
            candidate = instance.strip()
            try:
                return int(candidate) if schema_type == "integer" else float(candidate)
            except (ValueError, TypeError):
                pass
    elif schema_type == "string":
        if isinstance(instance, (int, float, bool)):
            return str(instance)
    elif schema_type == "boolean":
        if isinstance(instance, str):
            candidate = instance.strip().lower()
            if candidate in ("true", "1", "yes"):
                return True
            elif candidate in ("false", "0", "no"):
                return False
    elif schema_type == "array":
        if isinstance(instance, list):
            items_schema = schema.get("items")
            if items_schema:
                return [_coerce_types(item, items_schema) for item in instance]
    elif schema_type == "object":
        if isinstance(instance, dict):
            properties = schema.get("properties", {})
            result = {}
            for key, value in instance.items():
                if key in properties:
                    result[key] = _coerce_types(value, properties[key])
                else:
                    result[key] = value
            return result
    
    return instance


def _accepts_null(schema: Any, validator: Any) -> bool | None:
    if validator is not None:
        try:
            return validator.evolve(schema=schema).is_valid(None)
        except Exception:
            # Unresolved references are not permission to discard a value.
            return None
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict) or "$ref" in schema:
        return None
    if "enum" in schema and None not in schema["enum"]:
        return False
    if "const" in schema and schema["const"] is not None:
        return False
    kind = schema.get("type")
    if kind is not None and kind != "null" and not (isinstance(kind, list) and "null" in kind):
        return False
    if any(keyword in schema for keyword in ("not", "if", "then", "else")):
        # The lightweight fallback does not evaluate these constraints.
        # Unknown null acceptance cannot authorize removal of a property.
        return None
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            matches = [_accepts_null(branch, None) for branch in schema[keyword]]
            if None in matches:
                return None
            if keyword == "allOf" and not all(matches):
                return False
            if keyword == "anyOf" and not any(matches):
                return False
            if keyword == "oneOf" and matches.count(True) != 1:
                return False
    return True


def _restore_optional_nulls(value: Any, schema: Any, wire: Any, validator: Any) -> Any:
    """Decode strict-added nulls while leaving canonical validation in charge."""
    if not isinstance(schema, dict) or not isinstance(wire, dict):
        return value
    if isinstance(value, list):
        items, wire_items = schema.get("items", {}), wire.get("items", {})
        result = [
            _restore_optional_nulls(
                item,
                items[index] if isinstance(items, list) and index < len(items) else items,
                wire_items[index] if isinstance(wire_items, list) and index < len(wire_items) else wire_items,
                validator,
            ) for index, item in enumerate(value)
        ]
    elif not isinstance(value, dict):
        return value
    else:
        result = dict(value)
        properties, wire_properties = schema.get("properties", {}), wire.get("properties", {})
        for name, prop in properties.items():
            if name not in result or name not in wire_properties:
                continue
            wire_prop = wire_properties[name]
            if (result[name] is None and name not in schema.get("required", [])
                    and _accepts_null(prop, validator) is False
                    and _accepts_null(wire_prop, validator) is True):
                result.pop(name)
            else:
                result[name] = _restore_optional_nulls(result[name], prop, wire_prop, validator)
    for branch in schema.get("allOf", []):
        # Strict drops allOf; only properties retained in its wire schema can
        # be decoded. The original conditional constraints still validate.
        result = _restore_optional_nulls(result, branch, wire, validator)
    if validator is not None:
        for keyword in ("anyOf", "oneOf"):
            for index, branch in enumerate(schema.get(keyword, [])):
                wire_branches = wire.get(keyword, [])
                if index >= len(wire_branches):
                    continue
                candidate = _restore_optional_nulls(result, branch, wire_branches[index], validator)
                if validator.evolve(schema=schema).is_valid(candidate):
                    return candidate
    return result


def _prepare_arguments(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    from openprogram.models.providers.schema import SchemaNormalizationError, normalize

    copied = _coerce_types(copy.deepcopy(args), schema)
    validator = Draft7Validator(schema) if JSONSCHEMA_AVAILABLE else None
    if validator is not None and validator.is_valid(copied):
        return copied
    try:
        wire = normalize(schema, "openai_strict")
    except SchemaNormalizationError:
        # A passthrough-only schema must retain its canonical validation.
        return copied
    return _restore_optional_nulls(copied, schema, wire, validator)


def validate_tool_arguments(tool: Tool, tool_call: ToolCall) -> dict[str, Any]:
    """
    Validate and coerce tool arguments against the tool's parameter schema.
    
    Mirrors TypeScript validateToolArguments() with AJV:
    - Full JSON Schema validation
    - Type coercion (coerceTypes: true)
    - Formatted error messages
    
    Returns the validated (and potentially coerced) arguments dict.
    Raises ValueError if validation fails.
    """
    args = tool_call.arguments
    schema = tool.parameters

    if not isinstance(args, dict):
        raise ValueError(f"Tool arguments must be an object, got {type(args).__name__}")

    # If jsonschema is not available, fall back to basic validation
    if not JSONSCHEMA_AVAILABLE:
        return _validate_basic(tool, tool_call, args, schema)
    
    # Restore strict optional defaults, then coerce without mutating input.
    coerced_args = _prepare_arguments(args, schema)
    
    # Validate with jsonschema
    try:
        validator = Draft7Validator(schema)
        validator.validate(coerced_args)
        return coerced_args
    except jsonschema.ValidationError as e:
        # Format error messages similar to AJV
        errors = []
        for error in validator.iter_errors(coerced_args):
            path = ".".join(str(p) for p in error.path) if error.path else "root"
            errors.append(f"  - {path}: {error.message}")
        
        error_text = "\n".join(errors) if errors else f"  - {e.message}"
        error_message = (
            f'Validation failed for tool "{tool.name}":\n'
            f'{error_text}\n\n'
            f'Received arguments:\n'
            f'{_format_json(tool_call.arguments)}'
        )
        raise ValueError(error_message)


def _validate_basic(tool: Tool, tool_call: ToolCall, args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """
    Basic validation fallback when jsonschema is not available.

    We still apply the same lightweight type coercion as the full jsonschema
    path so callers get consistent behavior across environments.
    """
    coerced_args = _prepare_arguments(args, schema)

    required = schema.get("required", [])
    for field in required:
        if field not in coerced_args:
            raise ValueError(
                f'Validation failed for tool "{tool.name}":\n'
                f'  - {field}: Missing required parameter\n\n'
                f'Received arguments:\n'
                f'{_format_json(tool_call.arguments)}'
            )
        if (coerced_args[field] is None
                and _accepts_null(schema.get("properties", {}).get(field, {}), None) is False):
            raise ValueError(f'Validation failed for tool "{tool.name}": {field}: null is not allowed')

    return coerced_args


def _format_json(obj: Any) -> str:
    """Format JSON with indentation for error messages."""
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)
