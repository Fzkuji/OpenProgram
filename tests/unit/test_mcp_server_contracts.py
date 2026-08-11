from __future__ import annotations

import copy
from importlib.metadata import version

import mcp.types as mcp_types
import pytest
from mcp.shared.exceptions import McpError

from openprogram.mcp_server.contracts import (
    MCP_TOOL_SCHEMAS,
    TOOL_BY_NAME,
    validate_tool_call,
)


EXPECTED_SCHEMAS = {
    "sessions_list": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "session_get": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
    "prompt_send": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "session_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
    "prompt_cancel": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "minLength": 1, "pattern": r"\S"},
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
    "tools_list": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    "tool_call": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "arguments": {"type": "object", "default": {}},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}


def test_contract_exposes_exact_ordered_wrapper_tools_and_schemas() -> None:
    assert [tool.name for tool in MCP_TOOL_SCHEMAS] == [
        "sessions_list",
        "session_get",
        "prompt_send",
        "prompt_cancel",
        "tools_list",
        "tool_call",
    ]
    assert {
        tool.name: tool.model_dump()["inputSchema"] for tool in MCP_TOOL_SCHEMAS
    } == EXPECTED_SCHEMAS
    assert tuple(TOOL_BY_NAME) == tuple(EXPECTED_SCHEMAS)
    assert all(TOOL_BY_NAME[tool.name] is tool for tool in MCP_TOOL_SCHEMAS)


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("sessions_list", None, {}),
        ("sessions_list", {}, {}),
        ("session_get", {"session_id": "s_1"}, {"session_id": "s_1"}),
        ("prompt_send", {"prompt": "hello"}, {"prompt": "hello"}),
        (
            "prompt_send",
            {"prompt": "hello", "session_id": "s_1"},
            {"prompt": "hello", "session_id": "s_1"},
        ),
        ("prompt_cancel", {"session_id": "s_1"}, {"session_id": "s_1"}),
        ("tools_list", None, {}),
        ("tool_call", {"name": "read"}, {"name": "read", "arguments": {}}),
        (
            "tool_call",
            {"name": "read", "arguments": None},
            {"name": "read", "arguments": {}},
        ),
        (
            "tool_call",
            {"name": "read", "arguments": {"path": "a.txt"}},
            {"name": "read", "arguments": {"path": "a.txt"}},
        ),
    ],
)
def test_validate_tool_call_returns_normalized_copy(name, arguments, expected) -> None:
    before = copy.deepcopy(arguments)
    result = validate_tool_call(name, arguments)
    assert result == expected
    assert arguments == before
    if arguments is not None:
        assert result is not arguments
    if (
        name == "tool_call"
        and isinstance(arguments, dict)
        and isinstance(arguments.get("arguments"), dict)
    ):
        assert result["arguments"] is not arguments["arguments"]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("sessions_list", {"extra": True}),
        ("session_get", None),
        ("session_get", {"session_id": ""}),
        ("session_get", {"session_id": "   \t"}),
        ("session_get", {"session_id": 7}),
        ("prompt_send", {}),
        ("prompt_send", {"prompt": "\n\t"}),
        ("prompt_send", {"prompt": "ok", "session_id": "   "}),
        ("prompt_cancel", {"session_id": " "}),
        ("tools_list", {"extra": 1}),
        ("tool_call", {}),
        ("tool_call", {"name": " "}),
        ("tool_call", {"name": "read", "arguments": []}),
        ("tool_call", {"name": "read", "unexpected": {}}),
        ("tool_call", ["not", "an", "object"]),
    ],
)
def test_invalid_wrapper_arguments_raise_sanitized_invalid_params(
    name, arguments
) -> None:
    secret = "do-not-echo-this-secret"
    if isinstance(arguments, dict) and "extra" in arguments:
        arguments["extra"] = secret
    with pytest.raises(McpError) as exc_info:
        validate_tool_call(name, arguments)
    error = exc_info.value.error
    assert error.code == mcp_types.INVALID_PARAMS
    rendered = f"{exc_info.value!s} {exc_info.value!r} {error!s}"
    assert secret not in rendered
    assert len(error.message) <= 240


def test_unknown_wrapper_is_method_not_found_without_echoing_the_name() -> None:
    unknown = "unknown-secret-wrapper"
    with pytest.raises(McpError) as exc_info:
        validate_tool_call(unknown, {})
    assert exc_info.value.error.code == mcp_types.METHOD_NOT_FOUND
    assert unknown not in str(exc_info.value)


def test_contract_matches_locked_mcp_protocol_without_startup_side_effects() -> None:
    assert version("mcp") == "1.29.0"
    assert mcp_types.LATEST_PROTOCOL_VERSION == "2025-11-25"


def test_exported_tool_schema_mutation_cannot_change_validation_contract() -> None:
    tool = TOOL_BY_NAME["sessions_list"]
    with pytest.raises((AttributeError, TypeError, ValueError)):
        tool.inputSchema["properties"]["unexpected"] = {"type": "string"}
    with pytest.raises(McpError) as exc_info:
        validate_tool_call("sessions_list", {"unexpected": "value"})
    assert exc_info.value.error.code == mcp_types.INVALID_PARAMS


def test_exported_tool_contracts_are_immutable_and_sdk_serializable() -> None:
    tool = TOOL_BY_NAME["sessions_list"]
    with pytest.raises((AttributeError, TypeError, ValueError)):
        tool.name = "mutated"
    with pytest.raises((AttributeError, TypeError, ValueError)):
        tool.inputSchema["properties"]["unexpected"] = {"type": "string"}
    with pytest.raises((AttributeError, TypeError, ValueError)):
        tool.inputSchema |= {"unexpected": True}
    assert "unexpected" not in tool.inputSchema
    required = TOOL_BY_NAME["session_get"].inputSchema["required"]
    with pytest.raises((AttributeError, TypeError, ValueError)):
        required += ["unexpected"]
    assert required == ("session_id",)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        dict.__setitem__(tool.inputSchema, "unexpected", True)
    with pytest.raises((AttributeError, TypeError, ValueError)):
        list.append(required, "unexpected")
    assert '"name":"sessions_list"' in tool.model_dump_json()


def test_mapping_copy_failure_is_sanitized_as_invalid_params() -> None:
    secret = "PEER-SECRET-IN-DEEPCOPY"

    class HostileValue:
        def __deepcopy__(self, memo):
            raise RuntimeError(secret)

    with pytest.raises(McpError) as exc_info:
        validate_tool_call("sessions_list", {"extra": HostileValue()})
    assert exc_info.value.error.code == mcp_types.INVALID_PARAMS
    assert secret not in f"{exc_info.value!s} {exc_info.value!r}"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
