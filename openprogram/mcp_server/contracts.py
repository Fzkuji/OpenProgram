"""Public v1 MCP wrapper-tool contracts and local argument validation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import mcp.types as mcp_types
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import McpError


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
    mcp_types.Tool(
        name=name, description=description, inputSchema=copy.deepcopy(schema)
    )
    for name, description, schema in _TOOL_DEFINITIONS
)
TOOL_BY_NAME: Mapping[str, mcp_types.Tool] = MappingProxyType(
    {tool.name: tool for tool in MCP_TOOL_SCHEMAS}
)
_VALIDATORS = MappingProxyType(
    {
        tool.name: Draft202012Validator(copy.deepcopy(tool.inputSchema))
        for tool in MCP_TOOL_SCHEMAS
    }
)

for _tool in MCP_TOOL_SCHEMAS:
    Draft202012Validator.check_schema(_tool.inputSchema)


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
        normalized = copy.deepcopy(dict(arguments))
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
