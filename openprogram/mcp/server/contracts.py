"""Public v1 MCP wrapper-tool contracts and local argument validation."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import mcp.types as mcp_types
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import McpError

from openprogram.web_use_contract import web_use_parameters


_NON_BLANK_STRING = {"type": "string", "minLength": 1, "pattern": r"\S"}


def _object(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_RAW_TOOL_DEFINITIONS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("sessions_list", "List recent OpenProgram sessions.", _object({})),
    (
        "session_get",
        "Read the active branch of one OpenProgram session.",
        _object({"session_id": _NON_BLANK_STRING}, ["session_id"]),
    ),
    (
        "prompt_send",
        "Send a prompt to an existing or new OpenProgram session.",
        _object(
            {"prompt": _NON_BLANK_STRING, "session_id": _NON_BLANK_STRING},
            ["prompt"],
        ),
    ),
    (
        "prompt_cancel",
        "Cancel an active MCP-owned prompt.",
        _object({"session_id": _NON_BLANK_STRING}, ["session_id"]),
    ),
    ("tools_list", "List Runtime tools exposed to this MCP client.", _object({})),
    (
        "tool_call",
        "Call one Runtime tool exposed to this MCP client.",
        _object(
            {
                "name": _NON_BLANK_STRING,
                "arguments": {"type": "object", "default": {}},
            },
            ["name"],
        ),
    ),
    (
        "web_use",
        "Observe or control an authorized OpenProgram browser Page.",
        web_use_parameters(),
    ),
)

_TOOL_DEFINITIONS = tuple(
    (name, description, json.dumps(schema, separators=(",", ":")))
    for name, description, schema in _RAW_TOOL_DEFINITIONS
)
del _RAW_TOOL_DEFINITIONS

_VALIDATORS = MappingProxyType(
    {
        name: Draft202012Validator(json.loads(schema_json))
        for name, _, schema_json in _TOOL_DEFINITIONS
    }
)

for _, _, _schema_json in _TOOL_DEFINITIONS:
    Draft202012Validator.check_schema(json.loads(_schema_json))


def get_mcp_tools() -> tuple[mcp_types.Tool, ...]:
    return tuple(
        mcp_types.Tool(
            name=name,
            description=description,
            inputSchema=json.loads(schema_json),
        )
        for name, description, schema_json in _TOOL_DEFINITIONS
    )


def _error(code: int, message: str) -> McpError:
    return McpError(mcp_types.ErrorData(code=code, message=message))


def _error_path(error) -> str:
    parts = [str(part) for part in error.absolute_path]
    path = "$" + "".join(
        f"[{part}]" if part.isdigit() else f".{part}" for part in parts
    )
    return path[:160]


def _copy_json(value):
    if isinstance(value, Mapping):
        copied = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("object keys must be strings")
            copied[key] = _copy_json(item)
        return copied
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError("value is not JSON-compatible")


def validate_tool_call(
    name: str,
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate one fixed wrapper call and return a detached normalized dict."""
    validator = _VALIDATORS.get(name)
    if validator is None:
        raise _error(mcp_types.METHOD_NOT_FOUND, "unknown MCP wrapper tool")
    if arguments is None:
        normalized: dict[str, Any] = {}
    elif isinstance(arguments, Mapping):
        copy_failed = False
        try:
            normalized = _copy_json(arguments)
        except Exception:
            copy_failed = True
        if copy_failed:
            raise _error(mcp_types.INVALID_PARAMS, "invalid arguments at $: value")
    else:
        raise _error(mcp_types.INVALID_PARAMS, "invalid arguments at $: type")

    if name == "tool_call" and normalized.get("arguments") is None:
        normalized["arguments"] = {}
    if name == "web_use":
        from openprogram.web_use_contract import normalize_web_use_arguments
        normalized = normalize_web_use_arguments(normalized)

    errors = sorted(
        validator.iter_errors(normalized),
        key=lambda error: (_error_path(error), str(error.validator)),
    )
    if errors:
        first = errors[0]
        raise _error(
            mcp_types.INVALID_PARAMS,
            f"invalid arguments at {_error_path(first)}: {first.validator}",
        )
    return normalized


__all__ = ["get_mcp_tools", "validate_tool_call"]
