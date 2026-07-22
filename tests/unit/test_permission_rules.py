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
    assert _normalize_permission("AUTO") == "auto"


def test_all_modes_registered():
    # 对齐 Claude Code 网页端 Mode 菜单 5 档。
    assert VALID_PERMISSION == {"ask", "acceptEdits", "plan", "auto", "bypass"}


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


def test_auto_denies_risky_without_llm():
    # auto 档：bash 在 RISKY_AUTO_DENYLIST → 硬规则直接拒，不调 LLM。
    tool, ran = _make_tool("bash")
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="auto")
    result = _run(tool, req)
    assert _denied(result)
    assert not ran["called"]


def test_auto_allows_safe_without_llm():
    # auto 档：read 在 SAFE_AUTO_ALLOWLIST → 硬规则直接放行，不调 LLM。
    tool, ran = _make_tool("read")
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="auto")
    _run(tool, req)
    assert ran["called"]


def test_acceptedits_auto_allows_safe_file_tool(tmp_path, monkeypatch):
    # acceptEdits: a write-safe tool whose path is inside cwd runs without asking.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    tool, ran = _make_tool("write_file")
    tool._accept_edits_safe = True
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="acceptEdits")
    # path inside cwd → safe → auto-allow
    async def _fake(*a, **k): return (False, None, "once")  # would deny if asked
    import openprogram.agent.internals._approval as _ap
    wrapped = _ap.wrap_with_approval(tool, req, on_event=lambda e: None)
    import asyncio
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_ap, "await_user_approval", _fake)
        asyncio.run(wrapped.execute("c", {"path": str(tmp_path / "f.txt")}, None, None))
    assert ran["called"]   # auto-allowed, never asked


def test_acceptedits_command_still_asks():
    # acceptEdits: bash (not accept_edits_safe) still goes through approval.
    tool, ran = _make_tool("bash")   # _accept_edits_safe defaults False
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="acceptEdits")
    result = _run(tool, req, approve=False)
    assert _denied(result)
    assert not ran["called"]


# ── path safety (file_safety.py) ──

def test_path_safety(tmp_path):
    from openprogram.functions.tools.file_safety import check_path_safety
    import os
    d = str(tmp_path)
    assert check_path_safety(os.path.join(d, "a.txt"), [d])["safe"]
    assert not check_path_safety(os.path.join(d, ".bashrc"), [d])["safe"]      # dangerous file
    assert not check_path_safety(os.path.join(d, ".git", "config"), [d])["safe"]  # dangerous dir
    assert not check_path_safety("/etc/passwd", [d])["safe"]                    # outside cwd
    assert not check_path_safety(os.path.join(d, "a.txt::$DATA"), [d])["safe"]  # NTFS stream
    assert not check_path_safety("CON", [d])["safe"]                           # DOS device


def test_path_is_safe_additional_dir_allows(tmp_path, monkeypatch):
    # 额外工作目录内的写目标放行（additional-working-directories.md §3.1）。
    from openprogram.agent.internals._approval import _path_is_safe
    cwd = tmp_path / "cwd"; cwd.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    monkeypatch.chdir(cwd)
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      additional_working_dirs=[str(extra)])
    assert _path_is_safe("write_file", {"path": str(extra / "f.txt")}, req)


def test_path_is_safe_outside_all_dirs_blocks(tmp_path, monkeypatch):
    # 主目录 + 额外目录都不包含 → 拦。
    from openprogram.agent.internals._approval import _path_is_safe
    cwd = tmp_path / "cwd"; cwd.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    monkeypatch.chdir(cwd)
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      additional_working_dirs=[str(extra)])
    assert not _path_is_safe("write_file", {"path": str(outside / "f.txt")}, req)


def test_path_is_safe_uses_worktree_contextvar(tmp_path, monkeypatch):
    # 围栏基准与 system prompt 同源：ContextVar 绑定的项目 cwd 优先于
    # os.getcwd()（服务器进程启动目录）。
    from openprogram.agent.internals._approval import _path_is_safe
    import openprogram.worktree.context as wt_ctx
    proc_cwd = tmp_path / "proc"; proc_cwd.mkdir()
    project = tmp_path / "project"; project.mkdir()
    monkeypatch.chdir(proc_cwd)
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web")
    token = wt_ctx._current_worktree_path.set(str(project))
    try:
        assert _path_is_safe("write_file", {"path": str(project / "f.txt")}, req)
        # 进程 cwd 不再是围栏基准。
        assert not _path_is_safe("write_file", {"path": str(proc_cwd / "f.txt")}, req)
    finally:
        wt_ctx._current_worktree_path.reset(token)


def test_is_dangerous_allow_rule():
    from openprogram.functions.tools.file_safety import is_dangerous_allow_rule
    assert is_dangerous_allow_rule("bash", "python:*")   # interpreter
    assert not is_dangerous_allow_rule("bash", "git:*")  # ordinary
    assert is_dangerous_allow_rule("bash", None)         # whole bash tool


def test_acceptedits_denies_dangerous_path(tmp_path, monkeypatch):
    # acceptEdits: writing to .bashrc (dangerous file) is NOT auto-allowed → asks.
    monkeypatch.chdir(tmp_path)
    tool, ran = _make_tool("write_file")
    tool._accept_edits_safe = True
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      permission_mode="acceptEdits")
    result = _run_with_args(tool, req, {"path": str(tmp_path / ".bashrc")}, approve=False)
    assert _denied(result)         # unsafe path → falls through to approval → denied
    assert not ran["called"]


def _run_with_args(tool, req, args, approve=True, scope="once"):
    import asyncio
    import openprogram.agent.internals._approval as _ap
    async def _fake(*a, **k): return (approve, None, scope)
    wrapped = _ap.wrap_with_approval(tool, req, on_event=lambda e: None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_ap, "await_user_approval", _fake)
        return asyncio.run(wrapped.execute("c", args, None, None))


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
