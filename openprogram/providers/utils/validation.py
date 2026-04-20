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
    from jsonschema import Draft7Validator, validators
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
            try:
                return int(instance) if schema_type == "integer" else float(instance)
            except (ValueError, TypeError):
                pass
    elif schema_type == "string":
        if isinstance(instance, (int, float, bool)):
            return str(instance)
    elif schema_type == "boolean":
        if isinstance(instance, str):
            if instance.lower() in ("true", "1", "yes"):
                return True
            elif instance.lower() in ("false", "0", "no"):
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
    
    # Clone arguments for coercion (don't mutate original)
    coerced_args = copy.deepcopy(args)
    
    # Apply type coercion first (like AJV's coerceTypes)
    coerced_args = _coerce_types(coerced_args, schema)
    
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
    Only checks required fields (original behavior).
    """
    required = schema.get("required", [])
    for field in required:
        if field not in args:
            raise ValueError(
                f'Validation failed for tool "{tool.name}":\n'
                f'  - {field}: Missing required parameter\n\n'
                f'Received arguments:\n'
                f'{_format_json(tool_call.arguments)}'
            )

    return args


def _format_json(obj: Any) -> str:
    """Format JSON with indentation for error messages."""
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)
