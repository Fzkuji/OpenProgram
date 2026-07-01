"""Permission system tests — rule parsing/matching + decision precedence.

Design: docs/design/runtime/permission-model.md.
"""
from __future__ import annotations

from openprogram.functions.permission_rule import (
    parse_rule, rule_to_string, parse_command, pattern_matches, PermissionRuleValue,
)
from openprogram.agent.internals._approval import _match_rule
from openprogram.agent.session_config import (
    PermissionRules, _normalize_permission, VALID_PERMISSION,
)


# ── rule string parse / serialize ──

def test_parse_per_tool():
    assert parse_rule("bash") == PermissionRuleValue("bash", None)


def test_parse_per_pattern():
    assert parse_rule("bash(git:*)") == PermissionRuleValue("bash", "git:*")


def test_roundtrip_with_escaped_parens():
    for s in ("bash", "bash(git:*)", "read_file(/etc/**)", r"bash(echo \(hi\))"):
        assert rule_to_string(parse_rule(s)) == s


# ── pattern matching ──

def test_prefix_star():
    assert pattern_matches("git:*", "git status")
    assert not pattern_matches("git:*", "github")   # not a prefix boundary


def test_glob():
    assert pattern_matches("/etc/**", "/etc/passwd")


def test_exact():
    assert pattern_matches("git status", "git status")
    assert not pattern_matches("git status", "git log")


# ── parse_command ──

def test_parse_command_bash():
    assert parse_command("bash", {"command": "git status"}) == "git status"


def test_parse_command_write():
    assert parse_command("write_file", {"path": "/tmp/x"}) == "/tmp/x"


def test_parse_command_no_field():
    assert parse_command("web_search", {"query": "x"}) is None


# ── _match_rule precedence deny > ask > allow ──

def test_match_none_rules():
    assert _match_rule(None, "bash", {}) is None


def test_match_per_tool_deny():
    r = PermissionRules(deny=["bash"])
    assert _match_rule(r, "bash", {"command": "ls"}) == "deny"


def test_match_per_pattern():
    r = PermissionRules(deny=["bash(rm -rf:*)"], allow=["bash(git:*)"])
    assert _match_rule(r, "bash", {"command": "rm -rf /x"}) == "deny"
    assert _match_rule(r, "bash", {"command": "git status"}) == "allow"
    assert _match_rule(r, "bash", {"command": "ls"}) is None


def test_deny_beats_allow():
    # same tool in both — deny wins (scanned first)
    r = PermissionRules(deny=["bash"], allow=["bash"])
    assert _match_rule(r, "bash", {"command": "x"}) == "deny"


def test_ask_beats_allow():
    r = PermissionRules(ask=["write_file"], allow=["write_file"])
    assert _match_rule(r, "write_file", {"path": "/x"}) == "ask"


# ── permission mode normalize (camel modes) ──

def test_camel_modes_valid():
    assert _normalize_permission("acceptEdits") == "acceptEdits"
    assert _normalize_permission("ACCEPTEDITS") == "acceptEdits"
    assert _normalize_permission("dontask") == "dontAsk"


def test_all_six_modes_registered():
    assert VALID_PERMISSION == {"ask", "acceptEdits", "plan", "dontAsk", "bypass"}


def test_invalid_mode():
    assert _normalize_permission("bogus") is None


# ── _gated_execute decision branches (end-to-end via wrap_with_approval) ──

import asyncio
import pytest
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.internals import _approval


def _make_tool(name: str):
    """A tool whose execute records that it ran and returns a marker."""
    ran = {"called": False}

    async def _exec(call_id, args, cancel, on_update):
        ran["called"] = True
        return AgentToolResult(content=[], details={"ok": True})

    tool = AgentTool(name=name, description="", parameters={}, label=name, execute=_exec)
    return tool, ran


def _run(tool, req, approve=True, scope="once"):
    """Wrap tool with approval under req, run its execute, return (result, ran)."""
    async def _fake_approval(*, req, tool_name, args, on_event, timeout=300.0):
        return (approve, None, scope)

    wrapped = _approval.wrap_with_approval(tool, req, on_event=lambda e: None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_approval, "await_user_approval", _fake_approval)
        result = asyncio.run(wrapped.execute("c1", {"command": "x"}, None, None))
    return result


def _denied(result) -> bool:
    return bool(result.details.get("denied"))


def test_bypass_runs_without_approval():
    tool, ran = _make_tool("bash")
    req = TurnRequest(session_id="s", user_text="", agent_id="main",
                      source="web", permission_mode="bypass")
    _run(tool, req)
    assert ran["called"]


def test_deny_rule_blocks_even_under_bypass():
    # THE key safety property: deny beats bypass.
    tool, ran = _make_tool("bash")
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="bypass",
                      permission_rules=PermissionRules(deny=["bash"]))
    result = _run(tool, req)
    assert _denied(result)
    assert not ran["called"]


def test_allow_rule_runs_without_approval_in_ask():
    tool, ran = _make_tool("bash")
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="ask",
                      permission_rules=PermissionRules(allow=["bash"]))
    # even if approval would deny, allow rule short-circuits to run
    _run(tool, req, approve=False)
    assert ran["called"]


def test_dontask_denies_risky():
    tool, ran = _make_tool("bash")   # bash is in _RISKY_TOOLS
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="dontAsk")
    result = _run(tool, req)
    assert _denied(result)
    assert not ran["called"]


def test_ask_denies_when_user_declines():
    tool, ran = _make_tool("bash")
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="ask")
    result = _run(tool, req, approve=False)
    assert _denied(result)
    assert not ran["called"]


def test_ask_runs_when_user_approves():
    tool, ran = _make_tool("bash")
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="ask")
    _run(tool, req, approve=True)
    assert ran["called"]
