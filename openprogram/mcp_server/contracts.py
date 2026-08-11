"""Public v1 MCP wrapper-tool contracts and local argument validation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import mcp.types as mcp_types
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import McpError
from pydantic import ConfigDict, field_serializer


_NON_BLANK_STRING = {"type": "string", "minLength": 1, "pattern": r"\S"}


class _FrozenTool(mcp_types.Tool):
    model_config = ConfigDict(extra="allow", frozen=True)

    @field_serializer("inputSchema")
    def _serialize_input_schema(self, value):
        return _thaw(value)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


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


_TOOL_DEFINITIONS: tuple[tuple[str, str, dict[str, Any]], ...] = (
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
)


MCP_TOOL_SCHEMAS: tuple[mcp_types.Tool, ...] = tuple(
    _FrozenTool(name=name, description=description, inputSchema=schema)
    for name, description, schema in _TOOL_DEFINITIONS
)
for _tool in MCP_TOOL_SCHEMAS:
    object.__setattr__(_tool, "inputSchema", _freeze(_tool.inputSchema))

TOOL_BY_NAME: Mapping[str, mcp_types.Tool] = MappingProxyType(
    {tool.name: tool for tool in MCP_TOOL_SCHEMAS}
)
_VALIDATORS = MappingProxyType(
    {
        tool.name: Draft202012Validator(_thaw(tool.inputSchema))
        for tool in MCP_TOOL_SCHEMAS
    }
)

for _tool in MCP_TOOL_SCHEMAS:
    Draft202012Validator.check_schema(_thaw(_tool.inputSchema))


def _error(code: int, message: str) -> McpError:
    return McpError(mcp_types.ErrorData(code=code, message=message))


def _error_path(error) -> str:
    parts = [str(part) for part in error.absolute_path]
    path = "$" + "".join(
        f"[{part}]" if part.isdigit() else f".{part}" for part in parts
    )
    return path[:160]


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
            normalized = copy.deepcopy(dict(arguments))
        except Exception:
            copy_failed = True
        if copy_failed:
            raise _error(mcp_types.INVALID_PARAMS, "invalid arguments at $: value")
    else:
        raise _error(mcp_types.INVALID_PARAMS, "invalid arguments at $: type")

    if name == "tool_call" and normalized.get("arguments") is None:
        normalized["arguments"] = {}

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


__all__ = ["MCP_TOOL_SCHEMAS", "TOOL_BY_NAME", "validate_tool_call"]
