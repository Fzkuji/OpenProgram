"""An Agent with a selected tool list is limited to it when tools are added per turn."""
from types import SimpleNamespace

from openprogram.runtime.conversation.turn_runtime import loop_runner


def test_a_selected_list_limits_the_turn_and_a_request_list_wins():
    explorer = {"tools": {"mode": "selected", "allowed": ["read", "grep"]}}
    assert loop_runner._named_tools(explorer, {"inherit": True}) == ["read", "grep"]
    assert loop_runner._named_tools(explorer, ["bash"]) == ["bash"]
    assert loop_runner._named_tools({"tools": {"mode": "all"}}, None) is None


def test_the_screen_tool_is_not_added_to_an_agent_that_did_not_name_it():
    tools = [SimpleNamespace(name="read"), SimpleNamespace(name="computer_use_agent")]
    kept, enabled = loop_runner._configure_browser_tool(
        tools, None, enabled=True, tools_override=loop_runner._named_tools(
            {"tools": {"mode": "selected", "allowed": ["read"]}}, {"inherit": True}))
    assert [tool.name for tool in kept] == ["read"]
    assert enabled is False
