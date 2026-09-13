"""web use public api tests."""
from __future__ import annotations


def test_public_web_use_schema_is_command_based_and_legacy_name_is_hidden():
    from jsonschema import Draft7Validator

    from openprogram.providers._schema import normalize_for
    from openprogram.providers.types import ToolCall
    from openprogram.providers.utils.validation import validate_tool_arguments
    from openprogram.programs import agent_tools

    names = {item.name for item in agent_tools(names=["web_use", "computer_use"])}
    assert names == {"web_use"}
    tool = next(
        item for item in agent_tools(names=["web_use"])
        if item.name == "web_use"
    )
    properties = tool.parameters["properties"]
    assert properties["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]
    assert properties["backend"]["enum"] == [
        "", "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    ]
    assert "task" not in properties
    assert "web_session_id" in properties
    assert "computer_session_id" not in properties
    assert tool.parameters["required"] == ["command"]
    act_rule = next(
        rule["then"] for rule in tool.parameters["allOf"]
        if rule["if"]["properties"]["command"].get("const") == "act"
    )
    act_arguments = act_rule["properties"]["arguments"]
    assert "web_session_id" not in (act_rule.get("required") or [])
    assert "action" not in properties
    assert "action" in act_arguments["properties"]
    assert "verify" not in act_arguments["properties"]["action"]["enum"]
    assert "expected_frame_id" not in act_arguments.get("required", [])
    assert act_arguments["additionalProperties"] is False
    verify_rule = next(
        rule["then"] for rule in tool.parameters["allOf"]
        if rule["if"]["properties"]["command"].get("const") == "verify"
    )
    assert verify_rule["required"] == ["web_session_id"]
    verify_arguments = verify_rule["properties"]["arguments"]
    assert verify_arguments["required"] == [
        "assertion", "value",
    ]
    assert verify_arguments["properties"]["action"]["enum"] == ["verify"]
    assert "x" not in verify_arguments["properties"]
    assert "y" not in verify_arguments["properties"]
    assert "amount" not in verify_arguments["properties"]
    observe_arguments = next(
        branch for branch in tool.parameters["properties"]["arguments"]["anyOf"]
        if "detail" in (branch.get("properties") or {})
    )
    assert "url" in observe_arguments["properties"]
    assert observe_arguments["additionalProperties"] is False
    close_rule = next(
        rule["then"] for rule in tool.parameters["allOf"]
        if rule["if"]["properties"]["command"].get("const") == "close"
    )
    assert close_rule["required"] == ["web_session_id"]

    retried_list_pages = {
        "command": "list_pages",
        "backend": "",
        "page": "",
        "page_context_token": "",
        "web_session_id": "",
    }
    assert validate_tool_arguments(
        tool,
        ToolCall(
            id="call-list-pages-retry",
            name="web_use",
            arguments=retried_list_pages,
        ),
    ) == retried_list_pages

    session_act = {
        "command": "act",
        "backend": "playwright_mcp",
        "page": "",
        "page_context_token": "page_ctx_9516a306add9441fbae27d4e394a153c",
        "web_session_id": "pending",
        "arguments": {},
    }
    assert validate_tool_arguments(
        tool,
        ToolCall(id="call-act-pending", name="web_use", arguments=session_act),
    ) == session_act

    lifted = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-act-top-level",
            name="web_use",
            arguments={
                "command": "act",
                "web_session_id": "cs_1",
                "action": "navigate",
                "url": "https://example.test/",
            },
        ),
    )
    assert lifted["arguments"]["action"] == "navigate"
    assert lifted["arguments"]["url"] == "https://example.test/"
    assert "action" not in lifted or lifted.get("action") is None

    opened = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-act-open",
            name="web_use",
            arguments={
                "command": "act",
                "action": "navigate",
                "url": "https://example.test/",
            },
        ),
    )
    assert opened["arguments"]["action"] == "navigate"
    assert opened["arguments"]["url"] == "https://example.test/"
    assert "url" not in lifted

    consumed = normalize_for(
        "openai-completions", tool.parameters, "grok-4",
    )
    assert "action" not in consumed["properties"]
    verify_branch = next(
        branch for branch in consumed["properties"]["arguments"]["anyOf"]
        if (branch.get("properties") or {}).get("action", {}).get("enum") == ["verify"]
    )

    def fill_required(schema, payload):
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

    consumed_verify = fill_required(
        consumed,
        {
            "command": "verify",
            "web_session_id": "cs_1",
            "arguments": {
                "action": "verify",
                "assertion": "text_contains",
                "value": "1",
            },
        },
    )
    consumed_verify["arguments"] = fill_required(
        verify_branch, consumed_verify["arguments"],
    )
    Draft7Validator(consumed).validate(consumed_verify)
    assert "x" not in consumed_verify["arguments"]
    assert "amount" not in consumed_verify["arguments"]
    same_object = validate_tool_arguments(
        tool,
        ToolCall(id="call-verify", name="web_use", arguments=consumed_verify),
    )
    assert same_object["arguments"]["action"] == "verify"
    assert "x" not in same_object["arguments"]
    assert "amount" not in same_object["arguments"]
    observe_detail = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-observe-detail",
            name="web_use",
            arguments={
                "command": "observe",
                "page_context_token": "pct_1",
                "arguments": {"detail": "interactive"},
            },
        ),
    )
    assert observe_detail["arguments"]["detail"] == "interactive"
    native_defaults = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-verify-native-defaults",
            name="web_use",
            arguments={
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
            },
        ),
    )
    assert native_defaults["arguments"] == {
        "assertion": "text_contains",
        "value": "1",
    }



def test_openprogram_mcp_exposes_only_web_use_as_browser_control_tool():
    from openprogram.mcp.server.contracts import get_mcp_tools, validate_tool_call

    tools = {tool.name: tool for tool in get_mcp_tools()}
    assert "web_use" in tools
    assert "computer_use" not in tools
    schema = tools["web_use"].inputSchema
    assert schema["properties"]["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]
    assert "web_session_id" in schema["properties"]
    assert validate_tool_call(
        "web_use",
        {
            "command": "observe",
            "page_context_token": "pct_1",
            "arguments": {"detail": "interactive"},
        },
    )["arguments"]["detail"] == "interactive"
    assert validate_tool_call(
        "web_use",
        {
            "command": "observe",
            "arguments": {"url": "https://example.test/"},
        },
    )["arguments"]["url"] == "https://example.test/"

