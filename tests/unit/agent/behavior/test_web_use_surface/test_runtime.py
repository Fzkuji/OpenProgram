"""surface runtime tests."""
from __future__ import annotations
from ._support import (
    REPO_ROOT,
)


def test_web_use_is_registered_as_surface_aware_public_tool():
    from openprogram.programs import (
        DEFERRED_DEFAULT_TOOLS,
        DEFAULT_TOOLS,
        agent_tools,
        apply_tool_policy,
    )

    assert "web_use" in DEFAULT_TOOLS
    assert "web_use" not in DEFERRED_DEFAULT_TOOLS
    tool = next(item for item in agent_tools(names=["web_use"]) if item.name == "web_use")
    assert "page" in tool.parameters["properties"]
    assert "command" in tool.parameters["properties"]
    assert "arguments" in tool.parameters["properties"]
    assert apply_tool_policy([tool], source="plan") == []



def test_surface_tool_is_injected_after_tools_are_resolved():
    source = (REPO_ROOT / "openprogram/agent/dispatcher/loop_runner.py").read_text(
        encoding="utf-8"
    )

    resolve_at = source.index(
        "tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)"
    )
    inject_at = source.index(
        "tools, web_use_enabled = _configure_web_use_tools"
    )
    assert resolve_at < inject_at

