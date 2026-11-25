"""surface runtime tests."""
from __future__ import annotations
from ._support import (
    REPO_ROOT,
)
from openprogram.programs.tools.computer_use.computer_use import devices as cu_devices


def test_built_in_pages_are_a_target_of_the_one_screen_tool():
    from openprogram.programs import (
        DEFERRED_DEFAULT_TOOLS,
        DEFAULT_TOOLS,
        agent_tools,
        apply_tool_policy,
    )

    from openprogram.programs import get_agent_tool

    assert get_agent_tool("web_use") is None
    assert "computer_use" in DEFAULT_TOOLS
    assert "computer_use" not in DEFERRED_DEFAULT_TOOLS
    tool = next(item for item in agent_tools(names=["computer_use"]) if item.name == "computer_use")
    assert "browser" in tool.parameters["properties"]["type"]["enum"]
    assert apply_tool_policy([tool], source="plan") == []



def test_surface_tool_is_injected_after_tools_are_resolved():
    source = (REPO_ROOT / "openprogram/runtime/conversation/turn_runtime/loop_runner.py").read_text(
        encoding="utf-8"
    )

    resolve_at = source.index(
        "tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)"
    )
    inject_at = source.index(
        "tools, browser_enabled = _configure_screen_tool"
    )
    assert resolve_at < inject_at



def test_resume_keeps_the_browser_tools_of_a_turn_without_pages():
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _saved_screen_tool

    def contract(*names):
        return {"tools": [{"name": name} for name in names]}

    assert _saved_screen_tool(contract("bash")) is False
    assert _saved_screen_tool(contract("bash", "computer_use")) is True
    assert _saved_screen_tool(None) is None


def test_a_turn_with_named_tools_gets_the_screen_tool_only_when_named(monkeypatch):
    from types import SimpleNamespace

    from openprogram.programs.tools.computer_use.computer_use import tool as entry
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _configure_screen_tool

    monkeypatch.setattr(cu_devices, "available_targets", lambda: ["window", "browser"])
    monkeypatch.setattr(entry, "page_surface_call", lambda: {"type": "browser"})

    screen = SimpleNamespace(name="computer_use")
    read = SimpleNamespace(name="read")
    # An always-on check-in names read-only tools: open Pages add nothing to it.
    tools, enabled = _configure_screen_tool([read, screen], None, enabled=True, tools_override=["read", "grep"])
    assert [tool.name for tool in tools] == ["read"]
    assert enabled is False
    # Without a named list the targets that are set up decide.
    tools, enabled = _configure_screen_tool([read], None, enabled=True, tools_override=None)
    assert [tool.name for tool in tools] == ["read", "computer_use"]
    assert enabled is True


def test_the_screen_tool_follows_its_targets_not_an_open_page(monkeypatch):
    from types import SimpleNamespace

    from openprogram.programs.tools.computer_use.computer_use import tool as entry
    from openprogram.runtime.conversation import surface_context
    from openprogram.runtime.conversation.turn_runtime.loop_runner import _configure_screen_tool

    read = SimpleNamespace(name="read")
    monkeypatch.setattr(surface_context, "browser_pages_available", lambda context: False)
    # Application windows are set up and no Page is open: the tool is there, the Pages are not.
    monkeypatch.setattr(cu_devices, "available_targets", lambda: ["window"])
    monkeypatch.setattr(entry, "page_surface_call", lambda: None)
    tools, pages = _configure_screen_tool([read], None)
    assert [tool.name for tool in tools] == ["read", "computer_use"] and pages is False
    # No target at all: the tool is not offered.
    monkeypatch.setattr(cu_devices, "available_targets", lambda: [])
    tools, pages = _configure_screen_tool([read], None)
    assert [tool.name for tool in tools] == ["read"] and pages is False


def test_no_turn_refuses_the_whole_screen_tool_for_want_of_a_page():
    source = (REPO_ROOT / "openprogram/runtime/conversation/turn_runtime/loop_runner.py").read_text(
        encoding="utf-8"
    )
    assert "no browser Page is open" not in source
