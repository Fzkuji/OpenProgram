"""Constraints a spawned turn cannot talk its way out of.

Two seams, both of which used to have a hole:

* ``agent_spawn`` reaching a worktree tool — a non-interactive git side
  effect outside the spawned agent's working directories, previously not
  in the hard-constraint set, so ``permission_mode="bypass"`` ran it.
* An inner ``AgentSession`` built inside ``Runtime.exec`` — its tools were
  handed to the agent loop raw, so a program spawned from a turn ran its
  agent with no gate at all.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def owner_authority(tmp_path, monkeypatch):
    from openprogram import paths
    from openprogram.agent import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    authority._reset_owner_cache_for_tests()
    return authority.local_owner_authority()


def _request(source: str, authority: dict, **extra):
    from openprogram.agent.dispatcher import TurnRequest
    return TurnRequest(
        session_id="s1", user_text="", agent_id="main", source=source,
        permission_mode="bypass", **authority, **extra,
    )


def _echo_tool(name: str):
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent

    async def _execute(_call_id, _args, _cancel, _on_update):
        return AgentToolResult(content=[TextContent(text="RAN")])

    return AgentTool(
        name=name, description=name, parameters={}, label=name,
        execute=_execute,
    )


def _run(tool, args=None):
    return asyncio.run(tool.execute("c", args or {}, None, None))


def _text(result) -> str:
    return "".join(block.text for block in result.content)


# --- worktree tools under agent_spawn --------------------------------------

@pytest.mark.parametrize("tool_name", [
    "worktree_create", "worktree_merge", "worktree_discard",
])
def test_agent_spawn_cannot_run_worktree_tools_even_on_bypass(
    owner_authority, tool_name,
):
    from openprogram.agent.permissions.approval import wrap_with_approval

    req = _request("agent_spawn", owner_authority)
    wrapped = wrap_with_approval(_echo_tool(tool_name), req, lambda _e: None)
    result = _run(wrapped, {"name": "wt"})

    assert result.details["denied"] is True
    assert result.details["reason_code"] == "HARD_CONSTRAINT_DENIED"
    assert tool_name in _text(result)
    assert "RAN" not in _text(result)


def test_worktree_tools_still_run_for_an_interactive_owner_turn(owner_authority):
    from openprogram.agent.permissions.approval import wrap_with_approval

    req = _request("web", owner_authority)
    wrapped = wrap_with_approval(_echo_tool("worktree_create"), req,
                                 lambda _e: None)

    assert _text(_run(wrapped, {"name": "wt"})) == "RAN"


@pytest.mark.parametrize("tool_name", [
    "bash", "exec", "shell", "execute_code", "process",
    "worktree_create", "worktree_merge", "worktree_discard",
])
def test_mcp_cannot_run_risky_or_worktree_tools_even_on_bypass(
    owner_authority, tool_name,
):
    from openprogram.agent.permissions.approval import wrap_with_approval

    authority = {**owner_authority, "authority_tier": "paired"}
    authority.update({
        "speaker_kind": "client",
        "speaker_id": "mcp/0123456789abcdef",
        "speaker_display": "MCP client",
        "interaction": "non-interactive",
    })
    req = _request("mcp", authority)
    wrapped = wrap_with_approval(_echo_tool(tool_name), req, lambda _e: None)

    result = _run(wrapped, {"command": "id"})

    assert result.details["reason_code"] == "HARD_CONSTRAINT_DENIED"
    assert "RAN" not in _text(result)


# --- inner AgentSession inherits the outer execution context ----------------

def test_inner_request_inherits_source_and_authority(owner_authority):
    from openprogram.agent.turn_request_context import (
        inner_turn_request, set_turn_request, reset_turn_request,
    )

    outer = _request("agent_spawn", owner_authority)
    token = set_turn_request(outer)
    try:
        inner = inner_turn_request("program")
    finally:
        reset_turn_request(token)

    assert inner is not None
    assert inner.source == "agent_spawn"
    assert inner.permission_mode == "bypass"
    assert inner.authority_tier == outer.authority_tier
    assert inner.principal_id == outer.principal_id
    assert inner.interaction == "non-interactive"
    assert inner.speaker_kind == "runtime"


def test_inner_request_is_none_without_an_outer_turn():
    from openprogram.agent.turn_request_context import inner_turn_request
    assert inner_turn_request("program") is None


def _gate(tool_names, outer_req):
    """Run Runtime._gate_inner_tools under a bound outer request."""
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.agent.turn_request_context import (
        set_turn_request, reset_turn_request,
    )

    runtime = Runtime.__new__(Runtime)
    token = set_turn_request(outer_req)
    try:
        return runtime._gate_inner_tools([_echo_tool(n) for n in tool_names])
    finally:
        reset_turn_request(token)


def test_inner_tools_are_gated_by_the_outer_hard_constraints(owner_authority):
    outer = _request("agent_spawn", owner_authority)
    gated = _gate(["bash", "worktree_create", "read"], outer)

    by_name = {t.name: t for t in gated}
    for blocked in ("bash", "worktree_create"):
        result = _run(by_name[blocked], {"command": "id"})
        assert result.details["reason_code"] == "HARD_CONSTRAINT_DENIED", blocked
        assert "RAN" not in _text(result), blocked

    assert _text(_run(by_name["read"], {"file_path": "/etc/hosts"})) == "RAN"


def test_inner_tools_are_gated_by_the_outer_deny_rules(owner_authority):
    from openprogram.agent.session_config import PermissionRules

    outer = _request(
        "web", owner_authority,
        permission_rules=PermissionRules(deny=["read"]),
    )
    gated = _gate(["read"], outer)
    result = _run(gated[0], {"file_path": "/etc/hosts"})

    assert result.details["reason_code"] == "PERMISSION_RULE_DENY"
    assert "RAN" not in _text(result)


def test_inner_tools_are_untouched_without_an_outer_turn():
    from openprogram.agentic_programming.runtime import Runtime

    runtime = Runtime.__new__(Runtime)
    tools = [_echo_tool("bash")]
    assert runtime._gate_inner_tools(tools) is tools
