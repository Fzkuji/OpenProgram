from __future__ import annotations

import pytest
from jsonschema import Draft7Validator, ValidationError

from openprogram.mcp.server.contracts import validate_tool_call
from openprogram.programs import agent_tools
from openprogram.providers._schema import normalize_for
from openprogram.providers.types import ToolCall
from openprogram.providers.utils.validation import validate_tool_arguments
from openprogram.web_use_contract import normalize_web_use_arguments


NATIVE_GROK_API = "openai-completions"
NATIVE_GROK_MODEL = "grok-4"


def _registered_web_use():
    return next(
        item for item in agent_tools(names=["web_use"]) if item.name == "web_use"
    )


def _consumed_schema(tool) -> dict:
    return normalize_for(NATIVE_GROK_API, tool.parameters, NATIVE_GROK_MODEL)


def _argument_branches(schema: dict) -> list[dict]:
    arguments = schema["properties"]["arguments"]
    branches = arguments.get("anyOf")
    assert isinstance(branches, list)
    return [branch for branch in branches if isinstance(branch, dict)]


def _closed_string(schema: dict, value: str) -> bool:
    if schema.get("const") == value:
        return True
    enum = schema.get("enum")
    return isinstance(enum, list) and enum == [value]


def _verify_argument_branch(schema: dict) -> dict:
    for branch in _argument_branches(schema):
        action = (branch.get("properties") or {}).get("action") or {}
        if _closed_string(action, "verify"):
            return branch
    raise AssertionError("consumed schema has no verify arguments branch")


def _observe_argument_branch(schema: dict) -> dict:
    for branch in _argument_branches(schema):
        if "detail" in (branch.get("properties") or {}):
            return branch
    raise AssertionError("consumed schema has no observe arguments branch")


def _act_argument_branch(schema: dict) -> dict:
    for branch in _argument_branches(schema):
        action = (branch.get("properties") or {}).get("action") or {}
        enum = action.get("enum") or []
        if "click" in enum:
            return branch
    raise AssertionError("consumed schema has no act arguments branch")


def _omit_with_null(schema: dict, payload: dict) -> dict:
    filled = dict(payload)
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name in filled:
            continue
        prop = properties.get(name) or {}
        enum = prop.get("enum")
        if isinstance(enum, list) and enum:
            filled[name] = enum[0]
            continue
        types = prop.get("type")
        if isinstance(types, list) and "null" in types:
            filled[name] = None
    return filled


def _fill_consumed(consumed: dict, payload: dict) -> dict:
    filled = _omit_with_null(consumed, payload)
    nested = filled.get("arguments")
    if not isinstance(nested, dict):
        return filled
    command = filled.get("command")
    if command == "verify":
        branch = _verify_argument_branch(consumed)
    elif command == "observe":
        branch = _observe_argument_branch(consumed)
    elif command == "act":
        branch = _act_argument_branch(consumed)
    else:
        return filled
    filled["arguments"] = _omit_with_null(branch, nested)
    return filled


def test_consumed_native_payloads_validate_on_registered_tool() -> None:
    tool = _registered_web_use()
    consumed = _consumed_schema(tool)
    assert "allOf" not in consumed
    assert "action" not in consumed["properties"]
    verify_branch = _verify_argument_branch(consumed)
    assert _closed_string(verify_branch["properties"]["action"], "verify")
    assert "x" not in verify_branch["properties"]
    assert "y" not in verify_branch["properties"]
    assert "amount" not in verify_branch["properties"]

    cases = {
        "list_pages": {"command": "list_pages", "arguments": None},
        "observe": {
            "command": "observe",
            "page_context_token": "pct_1",
            "arguments": {"detail": "interactive"},
        },
        "act": {
            "command": "act",
            "web_session_id": "cs_1",
            "arguments": {"action": "click", "ref": "e1"},
        },
        "verify": {
            "command": "verify",
            "web_session_id": "cs_1",
            "arguments": {
                "action": "verify",
                "assertion": "text_contains",
                "value": "1",
            },
        },
        "close": {
            "command": "close",
            "web_session_id": "cs_1",
            "arguments": None,
        },
    }
    for command, documented in cases.items():
        payload = _fill_consumed(consumed, documented)
        Draft7Validator(consumed).validate(payload)
        validated = validate_tool_arguments(
            tool,
            ToolCall(
                id=f"call-consumed-{command}",
                name="web_use",
                arguments=payload,
            ),
        )
        assert validated["command"] == command
        if command == "verify":
            assert "x" not in validated["arguments"]
            assert "y" not in validated["arguments"]
            assert "amount" not in validated["arguments"]
            assert validated["arguments"]["assertion"] == "text_contains"
            assert validated["arguments"]["value"] == "1"
            assert validated["arguments"].get("expected_frame_id") is None
        if command == "observe":
            assert validated["arguments"]["detail"] == "interactive"
        if command == "act":
            assert validated["arguments"]["action"] == "click"
            assert validated["arguments"]["ref"] == "e1"


def test_observe_url_consumed_payload_validates_on_registered_tool() -> None:
    tool = _registered_web_use()
    consumed = _consumed_schema(tool)
    payload = _fill_consumed(
        consumed,
        {
            "command": "observe",
            "arguments": {"url": "https://example.test/"},
        },
    )
    Draft7Validator(consumed).validate(payload)
    validated = validate_tool_arguments(
        tool,
        ToolCall(id="call-observe-url", name="web_use", arguments=payload),
    )
    assert validated["arguments"]["url"] == "https://example.test/"


def test_mcp_observe_detail_and_url_are_valid() -> None:
    detail = validate_tool_call(
        "web_use",
        {
            "command": "observe",
            "page_context_token": "pct_1",
            "arguments": {"detail": "interactive"},
        },
    )
    assert detail["arguments"]["detail"] == "interactive"
    opened = validate_tool_call(
        "web_use",
        {
            "command": "observe",
            "arguments": {"url": "https://example.test/"},
        },
    )
    assert opened["arguments"]["url"] == "https://example.test/"


def test_act_keeps_zero_coordinates_and_verify_keeps_required_text() -> None:
    tool = _registered_web_use()
    act = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-act-origin",
            name="web_use",
            arguments={
                "command": "act",
                "web_session_id": "cs_1",
                "arguments": {"action": "click", "x": 0.0, "y": 0, "amount": 0},
            },
        ),
    )
    assert act["arguments"]["x"] == 0.0
    assert act["arguments"]["y"] == 0
    assert act["arguments"]["amount"] == 0

    with pytest.raises(ValueError):
        validate_tool_arguments(
            tool,
            ToolCall(
                id="call-verify-missing-value",
                name="web_use",
                arguments={
                    "command": "verify",
                    "web_session_id": "cs_1",
                    "arguments": {
                        "action": "verify",
                        "assertion": "text_contains",
                    },
                },
            ),
        )


def test_native_required_defaults_do_not_poison_verify_validation() -> None:
    tool = _registered_web_use()
    payload = {
        "command": "verify",
        "backend": "open_claude_chrome",
        "page": "",
        "page_context_token": "",
        "web_session_id": "cs_1",
        "action": "screenshot",
        "x": 0.0,
        "y": 0.0,
        "amount": 0,
        "assertion": "text_contains",
        "value": "1",
        "arguments": {},
    }
    normalized = normalize_web_use_arguments(payload)
    assert normalized["arguments"] == {
        "assertion": "text_contains",
        "value": "1",
    }
    validated = validate_tool_arguments(
        tool,
        ToolCall(id="call-verify-defaults", name="web_use", arguments=payload),
    )
    assert validated["arguments"]["assertion"] == "text_contains"
    assert "x" not in validated["arguments"]
    assert "amount" not in validated["arguments"]


def test_verify_nested_action_is_lifted_and_act_verify_stays_invalid() -> None:
    tool = _registered_web_use()
    lifted = normalize_web_use_arguments({
        "command": "verify",
        "web_session_id": "cs_1",
        "action": "verify",
        "assertion": "text_contains",
        "value": "Counter: 1",
    })
    assert lifted["arguments"]["action"] == "verify"
    validate_tool_arguments(
        tool,
        ToolCall(id="call-verify-nested", name="web_use", arguments=lifted),
    )

    with pytest.raises(ValueError, match="verify"):
        validate_tool_arguments(
            tool,
            ToolCall(
                id="call-act-verify",
                name="web_use",
                arguments={
                    "command": "act",
                    "web_session_id": "cs_1",
                    "arguments": {
                        "action": "verify",
                        "assertion": "text_contains",
                        "value": "1",
                    },
                },
            ),
        )


def test_direct_verify_still_rejects_nested_act_coordinates() -> None:
    tool = _registered_web_use()
    with pytest.raises(ValueError, match="additional properties|amount|x|y"):
        validate_tool_arguments(
            tool,
            ToolCall(
                id="call-verify-extra",
                name="web_use",
                arguments={
                    "command": "verify",
                    "web_session_id": "cs_1",
                    "arguments": {
                        "action": "verify",
                        "assertion": "text_contains",
                        "value": "1",
                        "x": 0.0,
                        "y": 0.0,
                        "amount": 0,
                    },
                },
            ),
        )
    consumed = _consumed_schema(tool)
    with pytest.raises(ValidationError):
        Draft7Validator(_verify_argument_branch(consumed)).validate({
            "action": "verify",
            "assertion": "text_contains",
            "value": "1",
            "x": 0.0,
        })


def test_list_pages_act_shaped_nested_defaults_remain_valid() -> None:
    tool = _registered_web_use()
    payload = {
        "command": "list_pages",
        "backend": "open_claude_chrome",
        "page": "",
        "page_context_token": "",
        "web_session_id": "",
        "arguments": {
            "action": "screenshot",
            "x": 0.0,
            "y": 0.0,
            "amount": 0,
            "assertion": "text_contains",
        },
    }
    assert validate_tool_arguments(
        tool,
        ToolCall(id="call-list-pages-defaults", name="web_use", arguments=payload),
    )["command"] == "list_pages"


def test_act_top_level_fields_still_lift_into_arguments() -> None:
    normalized = normalize_web_use_arguments({
        "command": "act",
        "web_session_id": "cs_1",
        "action": "click",
        "ref": "e2",
        "x": 12.5,
        "y": 40.0,
    })
    assert normalized["arguments"]["action"] == "click"
    assert normalized["arguments"]["ref"] == "e2"
    assert normalized["arguments"]["x"] == 12.5
    assert normalized["arguments"]["y"] == 40.0
    assert "action" not in normalized
