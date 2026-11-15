"""Permission system tests — rule parsing/matching + decision precedence.

Design: docs/reference/design/runtime/security/permission-decision.html.
"""
from __future__ import annotations

from openprogram.runtime.permissions.rule import (
    parse_rule, rule_to_string, parse_command, pattern_matches, PermissionRuleValue,
)
from openprogram.runtime.permissions.policy import match_rule

from openprogram.runtime.permissions.modes import (
    PermissionRules,
    normalize_permission as _normalize_permission,
    VALID_PERMISSION,
)


# ── rule string parse / serialize ──

def _match_rule(rules, tool_name, args):
    return match_rule(rules, tool_name, args)[0]


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


def test_parse_command_normalizes_unicode_ansi_and_nul():
    command = "\x1b[31mｅｎｖ\x1b[0m Ｘ=1 ｇｉｔ status\x00"
    assert parse_command("bash", {"command": command}) == "env X=1 git status"


def test_env_wrapper_matches_real_executable():
    command = parse_command("bash", {"command": "env X=1 git status"})
    assert command is not None
    assert pattern_matches("git:*", command)
    assert not pattern_matches("rm:*", command)


def test_env_wrapper_cannot_evade_deny_rule():
    rules = PermissionRules(deny=["bash(rm:*)"])
    assert _match_rule(rules, "bash", {"command": "env X=1 rm -rf build"}) == "deny"


def test_compound_shell_command_never_matches_prefix_rule():
    rules = PermissionRules(allow=["bash(git:*)"])
    assert _match_rule(
        rules, "bash", {"command": "git status; rm -rf build"}
    ) is None


def test_parse_command_write():
    import os
    assert parse_command("write_file", {"path": "/tmp/x"}) == os.path.realpath("/tmp/x")


def test_parse_command_no_field():
    assert parse_command("web_search", {"query": "x"}) == '{"query":"x"}'


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


def test_permission_from_config_defaults_to_unrestricted():
    from openprogram.runtime.conversation.session_config import (
        SessionRunConfig, permission_from_config,
    )
    empty = SessionRunConfig()
    assert permission_from_config(empty) == "bypass"
    assert permission_from_config(empty, default="not-a-mode") == "bypass"
    assert permission_from_config(empty, default="ask") == "ask"
    assert permission_from_config(SessionRunConfig(permission_mode="plan"), default="ask") == "plan"


# ── _gated_execute decision branches (end-to-end via wrap_with_approval) ──

import asyncio
import pytest
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions import approval as _approval


def _make_tool(name: str):
    """A tool whose execute records that it ran and returns a marker."""
    ran = {"called": False}

    async def _exec(call_id, args, cancel, on_update):
        ran["called"] = True
        return AgentToolResult(content=[], details={"ok": True})

    tool = AgentTool(name=name, description="", parameters={}, label=name, execute=_exec)
    return tool, ran


def _ensure_test_authority(req):
    if req.authority_tier is not None:
        return
    from openprogram.runtime.permissions.authority import local_owner_authority

    for key, value in local_owner_authority().items():
        setattr(req, key, value)


def _frozen_runtime_request(**kwargs):
    """Non-interactive child keeps the admitted snapshot; live owner rereads."""
    from openprogram.runtime.permissions.authority import local_owner_authority, runtime_authority

    parent = TurnRequest(
        session_id=kwargs.get("session_id", "s"),
        user_text="",
        agent_id="main",
        source="web",
        **local_owner_authority(),
    )
    return TurnRequest(**kwargs, **runtime_authority(parent, "agentic/child"))


def _permission_request(authority_path, *, permission_mode, permission_rules, monkeypatch):
    kwargs = dict(
        session_id="s", user_text="", agent_id="main", source="web",
        permission_mode=permission_mode,
        permission_rules=permission_rules,
    )
    if authority_path == "frozen_child":
        return _frozen_runtime_request(**kwargs)
    monkeypatch.setattr(
        "openprogram.runtime.permissions.rule.load_merged_rules",
        lambda _sid: permission_rules,
    )
    return TurnRequest(**kwargs)


def _run(tool, req, approve=True, scope="once"):
    """Wrap tool with approval under req, run its execute, return (result, ran)."""
    async def _fake_approval(*, req, tool_name, args, on_event, timeout=300.0, tool_call_id=None):
        return (approve, None, scope)

    _ensure_test_authority(req)
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


@pytest.mark.parametrize("authority_path", ["live_owner", "frozen_child"])
def test_deny_rule_blocks_even_under_bypass(authority_path, monkeypatch):
    # THE key safety property: deny beats bypass on live owner rules and frozen child snapshot.
    tool, ran = _make_tool("bash")
    req = _permission_request(
        authority_path, permission_mode="bypass",
        permission_rules=PermissionRules(deny=["bash"]),
        monkeypatch=monkeypatch,
    )
    result = _run(tool, req)
    assert _denied(result)
    assert result.is_error is True
    assert "is_error" not in result.details
    assert not ran["called"]


@pytest.mark.parametrize("authority_path", ["live_owner", "frozen_child"])
def test_allow_rule_runs_without_approval_in_ask(authority_path, monkeypatch):
    tool, ran = _make_tool("bash")
    req = _permission_request(
        authority_path, permission_mode="ask",
        permission_rules=PermissionRules(allow=["bash"]),
        monkeypatch=monkeypatch,
    )
    # even if approval would deny, allow rule short-circuits to run
    _run(tool, req, approve=False)
    assert ran["called"]


def test_auto_denies_command_with_unsafe_verdict(monkeypatch):
    async def classify(*args, **kwargs):
        return True, "unsafe command"
    monkeypatch.setattr("openprogram.runtime.permissions.classifier.auto_classify_tool", classify)
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
    import openprogram.runtime.permissions.approval as _ap
    _ensure_test_authority(req)
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
    from openprogram.programs.tools.files.file_safety import check_path_safety
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
    from openprogram.runtime.permissions.identity import path_is_safe as _path_is_safe
    cwd = tmp_path / "cwd"; cwd.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    monkeypatch.chdir(cwd)
    req = TurnRequest(session_id="s", user_text="", agent_id="main", source="web",
                      additional_working_dirs=[str(extra)])
    assert _path_is_safe("write_file", {"path": str(extra / "f.txt")}, req)


def test_path_is_safe_outside_all_dirs_blocks(tmp_path, monkeypatch):
    # 主目录 + 额外目录都不包含 → 拦。
    from openprogram.runtime.permissions.identity import path_is_safe as _path_is_safe
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
    from openprogram.runtime.permissions.identity import path_is_safe as _path_is_safe
    import openprogram.state.worktree.context as wt_ctx
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
    import openprogram.runtime.permissions.approval as _ap
    async def _fake(*a, **k): return (approve, None, scope)
    _ensure_test_authority(req)
    wrapped = _ap.wrap_with_approval(tool, req, on_event=lambda e: None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_ap, "await_user_approval", _fake)
        return asyncio.run(wrapped.execute("c", args, None, None))


@pytest.mark.parametrize(("tool_name", "args"), [
    ("bash", {"command": "echo harmless"}),
    ("write_stdin", {"session_id": "abc", "chars": "echo harmless\n"}),
    ("execute_code", {"code": "print('harmless')"}),
])
@pytest.mark.parametrize("source", ["agent_spawn", "mcp"])
def test_external_bypass_respects_owner_delegation(tool_name, args, source):
    tool, ran = _make_tool(tool_name)
    req = TurnRequest(session_id="s", user_text="", agent_id="worker",
                      source=source, permission_mode="bypass",
                      permission_rules=PermissionRules(allow=[tool_name]))
    result = _run_with_args(tool, req, args)
    # Owner-delegated Agents inherit approval policy; MCP keeps its hard ban.
    assert _denied(result) is (source == "mcp")
    assert ran["called"] is (source == "agent_spawn")


@pytest.mark.parametrize(("tool_name", "args"), [
    ("write", {"file_path": "/outside/new.txt", "content": "x"}),
    ("edit", {"file_path": "/outside/existing.txt",
              "old_string": "a", "new_string": "b"}),
    ("apply_patch", {"patch": "*** Begin Patch\n"
                                "*** Add File: /outside/new.txt\n"
                                "+x\n"
                                "*** End Patch"}),
])
def test_outside_client_on_bypass_cannot_write_outside(tmp_path, monkeypatch,
                                                       tool_name, args):
    monkeypatch.chdir(tmp_path)
    tool, ran = _make_tool(tool_name)
    req = TurnRequest(session_id="s", user_text="", agent_id="worker",
                      source="mcp", permission_mode="bypass")
    result = _run_with_args(tool, req, args)
    assert _denied(result)
    assert not ran["called"]


def test_agent_spawn_bypass_allows_workspace_write_and_safe_read(tmp_path,
                                                                 monkeypatch):
    monkeypatch.chdir(tmp_path)
    req = TurnRequest(session_id="s", user_text="", agent_id="worker",
                      source="agent_spawn", permission_mode="bypass")
    for name, args in (
        ("write", {"file_path": str(tmp_path / "new.txt"), "content": "x"}),
        ("read", {"file_path": str(tmp_path / "input.txt")}),
    ):
        tool, ran = _make_tool(name)
        result = _run_with_args(tool, req, args)
        assert not _denied(result)
        assert ran["called"]


@pytest.mark.parametrize(("tool_name", "args"), [
    ("write", {"file_path": "blocked.py", "content": "x"}),
    ("edit", {"file_path": "blocked.py", "old_string": "a", "new_string": "b"}),
    ("apply_patch", {"patch": "*** Begin Patch\n"
                                "*** Update File: blocked.py\n"
                                "@@\n-a\n+b\n"
                                "*** End Patch"}),
])
def test_program_python_is_written_like_any_other_file(monkeypatch, tmp_path, tool_name, args):
    from openprogram.state import paths

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    root = tmp_path / "programs" / "applications"
    root.mkdir(parents=True)
    monkeypatch.chdir(root)
    tool, ran = _make_tool(tool_name)
    req = TurnRequest(session_id="s", user_text="", agent_id="main",
                      source="web", permission_mode="bypass",
                      permission_rules=PermissionRules(allow=[tool_name]))
    result = _run_with_args(tool, req, args)
    assert not _denied(result)
    assert ran["called"]


def test_agent_spawn_ask_rule_denies_without_waiting_for_approval():
    tool, ran = _make_tool("write")
    req = _frozen_runtime_request(
        session_id="s", user_text="", agent_id="worker",
        source="agent_spawn", permission_mode="bypass",
        permission_rules=PermissionRules(ask=["write"]),
    )
    result = _run_with_args(tool, req, {"file_path": "inside.txt", "content": "x"},
                            approve=True)
    assert _denied(result)
    assert not ran["called"]


@pytest.mark.parametrize(("mode", "runs"), [("bypass", True), ("plan", False)])
def test_a_spawned_agent_leaving_plan_follows_its_mode(mode, runs):
    """Bypass has no plan to leave; in Plan nobody is there to approve."""
    tool, ran = _make_tool("exit_plan_mode")
    req = _frozen_runtime_request(session_id="s", user_text="", agent_id="worker",
                                  source="agent_spawn", permission_mode=mode)
    result = _run_with_args(tool, req, {}, approve=True)
    assert _denied(result) is not runs
    assert ran["called"] is runs


@pytest.mark.parametrize("source", ["cron", "scheduler"])
@pytest.mark.parametrize("tool_name", ["bash", "write", "send_message"])
def test_scheduled_turn_follows_its_conversations_permission_mode(source, tool_name):
    def run(mode, rules=None):
        tool, ran = _make_tool(tool_name)
        req = _frozen_runtime_request(session_id="s", user_text="", agent_id="main", source=source,
                                      permission_mode=mode, permission_rules=rules)
        return _run_with_args(tool, req, {"command": "echo x"}, approve=True), ran

    for mode, rules in (("bypass", None), ("ask", PermissionRules(allow=[tool_name]))):
        result, ran = run(mode, rules)
        assert not _denied(result) and ran["called"]
    if tool_name != "bash":
        return
    # Nobody is there to answer a mode that asks.
    result, ran = run("ask")
    assert _denied(result) and result.details["reason_code"] == "NO_ONE_TO_APPROVE"
    assert not ran["called"]


@pytest.mark.parametrize("source", ["cron", "scheduler"])
@pytest.mark.parametrize(
    "tool_name", ["memory_search", "memory_get", "memory_update"]
)
def test_scheduled_owner_turn_can_use_memory_lifecycle_tools(
    source, tool_name,
):
    tool, ran = _make_tool(tool_name)
    req = TurnRequest(
        session_id="s",
        user_text="",
        agent_id="main",
        source=source,
        permission_mode="ask",
    )
    result = _run_with_args(tool, req, {}, approve=False)
    assert not _denied(result)
    assert ran["called"]


def test_scheduled_memory_update_still_respects_an_explicit_deny_rule():
    tool, ran = _make_tool("memory_update")
    req = TurnRequest(
        session_id="s",
        user_text="",
        agent_id="main",
        source="scheduler",
        permission_mode="ask",
        permission_rules=PermissionRules(deny=["memory_update"]),
    )
    result = _run_with_args(tool, req, {}, approve=False)
    assert _denied(result)
    assert result.details["reason_code"] == "RULE_DENY"
    assert not ran["called"]


def test_scheduled_memory_update_requires_owner_authority():
    tool, ran = _make_tool("memory_update")
    req = TurnRequest(
        session_id="s",
        user_text="",
        agent_id="main",
        source="scheduler",
        permission_mode="bypass",
        speaker_kind="runtime",
        speaker_id="runtime/scheduler",
        speaker_display="scheduler",
        principal_id="paired/test",
        authority_tier="paired",
        interaction="non-interactive",
    )
    result = _run_with_args(tool, req, {}, approve=False)
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


def test_always_allow_persists_exact_normalized_operation(monkeypatch):
    from openprogram.state.store.project import project_store
    saved = {}
    project = type("Project", (), {"id": "p"})()
    monkeypatch.setattr(project_store, "project_for_session", lambda _sid: project)
    monkeypatch.setattr(project_store, "load_project_settings", lambda _pid: saved)
    monkeypatch.setattr(
        project_store, "save_project_settings",
        lambda _pid, settings: saved.update(settings),
    )

    assert _approval._persist_always_allow_rule(
        "s", "bash", {"command": "env X=1 ｇｉｔ status"}
    )
    assert saved["permission_rules"]["allow"] == [
        "bash(env X=1 git status)"
    ]


def test_always_allow_does_not_persist_complex_shell(monkeypatch):
    from openprogram.state.store.project import project_store
    saved = []
    project = type("Project", (), {"id": "p"})()
    monkeypatch.setattr(project_store, "project_for_session", lambda _sid: project)
    monkeypatch.setattr(project_store, "load_project_settings", lambda _pid: {})
    monkeypatch.setattr(
        project_store, "save_project_settings",
        lambda _pid, settings: saved.append(settings),
    )

    assert not _approval._persist_always_allow_rule(
        "s", "bash", {"command": "git status && rm -rf build"}
    )
    assert saved == []


@pytest.mark.parametrize(("speaker_kind", "interaction", "tier"), [
    ("owner", "interactive", "paired"),
    ("human", "non-interactive", "owner"),
    ("owner", "non-interactive", "owner"),
])
def test_only_interactive_owner_can_request_approval(
    speaker_kind, interaction, tier, monkeypatch,
):
    from openprogram.runtime.permissions.authority import owner_principal_id

    tool, ran = _make_tool("bash")
    req = TurnRequest(
        session_id="s", user_text="", agent_id="main", source="web",
        permission_mode="ask", speaker_kind=speaker_kind,
        speaker_id="owner/local" if speaker_kind == "owner" else "u456",
        speaker_display="Owner" if speaker_kind == "owner" else "B",
        principal_id=owner_principal_id(), interaction=interaction,
        authority_tier=tier,
    )

    async def _unexpected(**_kwargs):
        raise AssertionError("approval UI must not be opened")

    wrapped = _approval.wrap_with_approval(tool, req, on_event=lambda _e: None)
    monkeypatch.setattr(_approval, "await_user_approval", _unexpected)
    result = asyncio.run(wrapped.execute(
        "c", {"command": "git status"}, None, None
    ))
    assert _denied(result)
    assert not ran["called"]


def test_mcp_source_denies_approval_without_registering_question(
    tmp_path, monkeypatch,
):
    from openprogram.state import paths
    from openprogram.runtime.permissions import authority
    from openprogram.runtime.agent.questions import get_question_registry

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    authority._reset_owner_cache_for_tests()
    registry = get_question_registry()
    registry._events.clear()
    events = []
    tool, ran = _make_tool("custom_owner_tool")
    req = TurnRequest(
        session_id="mcp-session", user_text="", agent_id="main",
        source="mcp", permission_mode="ask",
        **{**authority.local_owner_authority(), "speaker_kind": "client", "interaction": "non-interactive"},
    )
    wrapped = _approval.wrap_with_approval(tool, req, events.append)

    async def unexpected_approval(**_kwargs):
        raise AssertionError("MCP must not enter the interactive approval path")

    monkeypatch.setattr(_approval, "await_user_approval", unexpected_approval)

    result = asyncio.run(wrapped.execute("c1", {}, None, None))

    assert result.is_error is True
    assert result.details["denied"] is True
    assert result.details["reason_code"] == "NO_ONE_TO_APPROVE"
    assert result.details["outcome"] == "not_started"
    assert result.details["execution_started"] is False
    assert ran["called"] is False
    assert registry.list_pending("mcp-session") == []
    assert events == []


def _denied_then_ok_exec(calls):
    from openprogram.models.providers.types import TextContent

    async def _exec(call_id, args, cancel, on_update):
        calls.append(dict(args))
        if len(calls) == 1:
            return AgentToolResult(
                content=[TextContent(text="Operation not permitted")],
                details={
                    "sandbox": {"kind": "denied", "backend": "seatbelt"},
                },
                is_error=True,
            )
        return AgentToolResult(
            content=[TextContent(text="exit_code=0")], details={"ok": True}
        )

    return _exec


def test_sandbox_denial_in_bypass_returns_error_without_asking(monkeypatch):
    """bypass 不弹 Sandbox 升级卡；拒绝结果直接返回给模型。"""
    calls = []
    approvals = []
    events = []

    async def _approve(**kwargs):
        approvals.append(kwargs)
        return True, None, "once"

    req = TurnRequest(
        session_id="s", user_text="", agent_id="main", source="web",
        permission_mode="bypass",
    )
    _ensure_test_authority(req)
    tool = AgentTool(
        name="bash", description="", parameters={}, label="bash",
        execute=_denied_then_ok_exec(calls),
    )
    wrapped = _approval.wrap_with_approval(tool, req, on_event=events.append)
    monkeypatch.setattr(_approval, "await_user_approval", _approve)
    result = asyncio.run(wrapped.execute(
        "c", {"command": "ps -p 1"}, None, None
    ))

    assert result.is_error is True
    assert calls == [{"command": "ps -p 1"}]
    assert approvals == []
    # 观测事件仍然要发，只是不再打断用户。
    assert events[0]["type"] == "sandbox.violation"


def _escalation_fixture(monkeypatch, mode="ask"):
    from contextlib import contextmanager
    from openprogram.host.sandbox import SandboxPolicy

    seen = {"calls": [], "approvals": [], "escalated": [], "events": []}

    async def _approve(**kwargs):
        seen["approvals"].append(kwargs)
        return True, None, seen.get("scope", "once")

    @contextmanager
    def _escalated():
        seen["escalated"].append(True)
        yield

    async def _exec(call_id, args, cancel, on_update):
        from openprogram.models.providers.types import TextContent
        seen["calls"].append((dict(args), bool(seen["escalated"])))
        return AgentToolResult(content=[TextContent(text="exit_code=0")], details={"ok": True})

    req = TurnRequest(
        session_id="s-escalation", user_text="", agent_id="main", source="web",
        permission_mode=mode, permission_rules=PermissionRules(allow=["bash"]),
    )
    _ensure_test_authority(req)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
                        lambda _sid: req.permission_rules)
    monkeypatch.setattr("openprogram.host.sandbox.resolve_policy", lambda **_: SandboxPolicy())
    monkeypatch.setattr("openprogram.host.sandbox.escalated_policy", _escalated)
    monkeypatch.setattr(_approval, "await_user_approval", _approve)
    monkeypatch.setattr(_approval, "_session_escalations", {})
    tool = AgentTool(name="bash", description="", parameters={}, label="bash", execute=_exec)
    return _approval.wrap_with_approval(tool, req, on_event=seen["events"].append), seen


_ESCALATED = {"command": "git push", "sandbox_permissions": "require_escalated",
              "justification": "Push the commit to GitHub."}


def test_sandbox_denial_returns_the_result_without_asking(monkeypatch):
    """A denial after the run is reported, never turned into a late approval."""
    calls, approvals, events = [], [], []

    async def _approve(**kwargs):
        approvals.append(kwargs)
        return True, None, "once"

    req = TurnRequest(
        session_id="s", user_text="", agent_id="main", source="web",
        permission_mode="ask", permission_rules=PermissionRules(allow=["bash"]),
    )
    _ensure_test_authority(req)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
                        lambda _sid: req.permission_rules)
    tool = AgentTool(name="bash", description="", parameters={}, label="bash",
                     execute=_denied_then_ok_exec(calls))
    wrapped = _approval.wrap_with_approval(tool, req, on_event=events.append)
    monkeypatch.setattr(_approval, "await_user_approval", _approve)
    result = asyncio.run(wrapped.execute("c", {"command": "cat /outside/file"}, None, None))

    assert result.is_error is True
    assert calls == [{"command": "cat /outside/file"}]
    assert approvals == []
    assert events[0]["type"] == "sandbox.violation"


def test_requested_escalation_is_published_before_dispatch(monkeypatch):
    wrapped, _ = _escalation_fixture(monkeypatch)
    manifest = wrapped._interaction_manifest("c", dict(_ESCALATED))

    assert manifest["kind"] == "approval"
    meta = manifest["request_metadata"]
    assert meta["tool"] == "bash:sandbox-escalation"
    assert meta["approval_reason"] == "SANDBOX_ESCALATION"
    assert meta["args"]["_sandbox_escalation"]["justification"] == "Push the commit to GitHub."
    assert meta["allowed_scopes"] == ["once", "session"]
    assert "Push the commit to GitHub." in manifest["detail"]


def test_requested_escalation_asks_once_and_runs_outside_the_sandbox(monkeypatch):
    wrapped, seen = _escalation_fixture(monkeypatch)
    result = asyncio.run(wrapped.execute("c", dict(_ESCALATED), None, None))

    assert result.details == {"ok": True}
    assert len(seen["approvals"]) == 1
    assert seen["approvals"][0]["tool_name"] == "bash:sandbox-escalation"
    assert seen["calls"] == [(_ESCALATED, True)]


def test_session_scope_skips_the_next_escalation_card(monkeypatch):
    wrapped, seen = _escalation_fixture(monkeypatch)
    seen["scope"] = "session"
    asyncio.run(wrapped.execute("c1", dict(_ESCALATED), None, None))

    assert wrapped._interaction_manifest("c2", dict(_ESCALATED)) is None
    asyncio.run(wrapped.execute("c2", dict(_ESCALATED), None, None))
    assert len(seen["approvals"]) == 1
    assert [escalated for _, escalated in seen["calls"]] == [True, True]


def test_declined_escalation_does_not_run(monkeypatch):
    wrapped, seen = _escalation_fixture(monkeypatch)

    async def _decline(**kwargs):
        return False, None, "once"

    monkeypatch.setattr(_approval, "await_user_approval", _decline)
    result = asyncio.run(wrapped.execute("c", dict(_ESCALATED), None, None))

    assert result.details["reason_code"] == "SANDBOX_ESCALATION_NOT_APPROVED"
    assert seen["calls"] == []


def test_unrestricted_leaves_the_sandbox_without_a_card(monkeypatch):
    wrapped, seen = _escalation_fixture(monkeypatch, mode="bypass")
    assert wrapped._interaction_manifest("c", dict(_ESCALATED)) is None
    result = asyncio.run(wrapped.execute("c", dict(_ESCALATED), None, None))

    assert result.details == {"ok": True}
    assert seen["approvals"] == []
    assert seen["calls"] == [(_ESCALATED, True)]


def test_escalation_without_a_sandbox_runs_normally(monkeypatch):
    wrapped, seen = _escalation_fixture(monkeypatch)
    monkeypatch.setattr("openprogram.host.sandbox.resolve_policy", lambda **_: None)
    result = asyncio.run(wrapped.execute("c", dict(_ESCALATED), None, None))

    assert result.details == {"ok": True}
    assert seen["approvals"] == []
    assert seen["calls"] == [(_ESCALATED, False)]


def test_bypass_keeps_configured_sandbox(monkeypatch):
    from openprogram.models.providers.types import TextContent
    from openprogram.host.sandbox import MODE_WORKSPACE_WRITE, resolve_policy

    monkeypatch.setattr(
        "openprogram.state.config.read_config",
        lambda: {"sandbox": {"mode": MODE_WORKSPACE_WRITE}},
    )
    seen = []

    async def _exec(call_id, args, cancel, on_update):
        seen.append(resolve_policy())
        return AgentToolResult(content=[TextContent(text="ok")])

    req = TurnRequest(
        session_id="s", user_text="", agent_id="main", source="web",
        permission_mode="bypass",
    )
    _ensure_test_authority(req)
    tool = AgentTool(
        name="bash", description="", parameters={}, label="bash", execute=_exec,
    )
    wrapped = _approval.wrap_with_approval(tool, req, on_event=lambda _e: None)
    asyncio.run(wrapped.execute("c", {"command": "true"}, None, None))

    policy = seen[-1]
    assert policy is not None
    assert policy == resolve_policy()
    assert policy.deny_read != ()
    assert policy.network is False


def test_ordinary_nonzero_tool_result_does_not_trigger_sandbox_approval(monkeypatch):
    from openprogram.models.providers.types import TextContent

    async def _exec(call_id, args, cancel, on_update):
        return AgentToolResult(
            content=[TextContent(text="exit_code=1\nordinary failure")],
            is_error=True,
        )

    async def _unexpected(**_kwargs):
        raise AssertionError("ordinary failures must not request approval")

    req = TurnRequest(
        session_id="s", user_text="", agent_id="main", source="web",
        permission_mode="bypass",
    )
    _ensure_test_authority(req)
    tool = AgentTool(
        name="bash", description="", parameters={}, label="bash", execute=_exec,
    )
    wrapped = _approval.wrap_with_approval(tool, req, on_event=lambda _e: None)
    monkeypatch.setattr(_approval, "await_user_approval", _unexpected)
    result = asyncio.run(wrapped.execute("c", {"command": "false"}, None, None))
    assert result.is_error is True
    assert result.details is None


# ── production install point ──
# The gate above is only worth anything if the real dispatcher installs it
# on every turn. These run the actual run_loop_blocking and capture the
# tools it hands to agent_loop, then execute them directly.

def _tools_handed_to_agent_loop(req, tool_names):
    """Run the real run_loop_blocking; return the tools agent_loop received."""
    _ensure_test_authority(req)
    import importlib
    import openprogram.runtime.conversation.turn_runtime.loop_runner as _lr
    # By name: the package attribute `loop` can be the function, not the module.
    _al = importlib.import_module("openprogram.runtime.agent.loop")
    # The driver binds agent_loop by name when first imported; imported under
    # the patch below it would keep the fake loop for the rest of the process.
    importlib.import_module("openprogram.runtime.agent.loop.driver")
    from openprogram.runtime.conversation import turn_runtime as _dispatcher

    captured: list = []

    async def _fake_loop(prompts, context, config, cancel, stream_fn):
        captured.extend(context.tools or [])
        if False:
            yield None

    probes = {}
    resolved = []
    for n in tool_names:
        t, ran = _make_tool(n)
        probes[n] = ran
        resolved.append(t)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_al, "agent_loop", _fake_loop)
        mp.setattr(_lr, "_resolve_tools", lambda *a, **k: resolved)
        mp.setattr(_dispatcher, "_load_agent_profile", lambda _id: {})
        mp.setattr(_dispatcher, "_resolve_model", lambda *a, **k: _stub_model_for_gate())
        _lr.run_loop_blocking(req=req, history=[], on_event=lambda e: None,
                              cancel_event=None)
    return {t.name: t for t in captured}, probes


def _stub_model_for_gate():
    from openprogram.models.providers.types import Model
    return Model(id="stub", name="stub", api="completion", provider="openai",
                 base_url="https://api.openai.com/v1")


def test_dispatcher_installs_gate_on_every_turn(tmp_path, monkeypatch):
    """Default production path wraps tools — not just the opt-in gate API."""
    monkeypatch.chdir(tmp_path)
    req = TurnRequest(session_id="s", user_text="hi", agent_id="worker",
                      source="agent_spawn", permission_mode="bypass",
                      history_override=[],
                      permission_rules=PermissionRules(deny=["bash"]))
    tools, probes = _tools_handed_to_agent_loop(req, ["bash", "read"])
    # The screen tool stays listed (refused) when no Page is open, so the
    # tools array never changes; the usable tools are the resolved ones.
    usable = {name for name, tool in tools.items()
              if getattr(tool, "_unavailable_reason", None) is None}
    assert usable == {"bash", "read"}
    assert set(tools) - usable <= {"computer_use_agent"}

    # An explicit deny still applies to an owner-delegated bypass turn.
    result = asyncio.run(tools["bash"].execute(
        "c", {"command": "echo x"}, None, None))
    assert _denied(result)
    assert not probes["bash"]["called"]

    # A safe read still runs — the gate denies, it does not disable spawning.
    result = asyncio.run(tools["read"].execute(
        "c", {"file_path": str(tmp_path / "f.txt")}, None, None))
    assert not _denied(result)
    assert probes["read"]["called"]


# ── the one decision path ──

def _owner_request(mode, *, rules=None, **overrides):
    from openprogram.runtime.permissions import authority
    return TurnRequest(session_id="s", user_text="", agent_id="main", source="web", permission_mode=mode,
                       permission_rules=rules, **{**authority.local_owner_authority(), **overrides})


@pytest.fixture
def owner(tmp_path, monkeypatch):
    from openprogram.state import paths
    from openprogram.runtime.permissions import authority
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    authority._reset_owner_cache_for_tests()


def test_an_ask_rule_nobody_can_answer_names_the_rule_to_change(owner):
    from openprogram.runtime.permissions.policy import permission_decision
    tool, _ran = _make_tool("bash")
    request = _owner_request("bypass", rules=PermissionRules(ask=["bash(git:*)"], allow=["bash"]),
                             speaker_kind="runtime", interaction="non-interactive")
    decision = permission_decision(tool, request, {"command": "git status"})
    assert (decision.outcome, decision.code) == ("deny", "NO_ONE_TO_APPROVE")
    assert 'ask rule "bash(git:*)"' in decision.refusal.text and "Permission rules in" in decision.refusal.text
    assert "Bypass permissions" not in decision.refusal.text and "allow rule" not in decision.refusal.text


def test_the_refusal_points_at_the_layer_holding_the_rule_that_decided(monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.permissions import session_facts
    from openprogram.runtime.permissions.rule import rule_origin
    from openprogram.state.store.project import project_store
    monkeypatch.setattr(session_facts, "facts",
                        lambda: SimpleNamespace(session_rules=lambda _id: PermissionRules(allow=["bash"])))
    monkeypatch.setattr(project_store, "project_for_session", lambda _id: None)
    assert rule_origin("s", "bash", "allow") == "this conversation"
    assert rule_origin("s", "bash", "ask") == "global settings"


def test_an_escalation_nobody_can_answer_offers_only_what_lifts_it(monkeypatch):
    wrapped, seen = _escalation_fixture(monkeypatch)
    monkeypatch.setattr(_approval, "can_approve", lambda _req: False)
    result = asyncio.run(wrapped.execute("c", dict(_ESCALATED), None, None))
    assert result.details["reason_code"] == "NO_ONE_TO_APPROVE" and seen["calls"] == []
    text = result.content[0].text
    assert "Sandbox switch" in text and "allow rule" not in text


def test_plan_beats_an_allow_rule_but_not_a_deny_rule(owner):
    from openprogram.runtime.permissions.policy import permission_decision
    tool, _ran = _make_tool("bash")
    args = {"command": "git status"}

    decision = permission_decision(tool, _owner_request("plan", rules=PermissionRules(allow=["bash(git:*)"])), args)
    assert (decision.outcome, decision.code, decision.layer) == ("deny", "PLAN_MODE_DENY", "mode")
    assert decision.refusal.text.startswith("[blocked by mode] bash")

    decision = permission_decision(tool, _owner_request("plan", rules=PermissionRules(deny=["bash"])), args)
    assert (decision.code, decision.rule) == ("RULE_DENY", "bash")
    assert 'your deny rule "bash" matches' in decision.refusal.text


def test_legacy_workspace_mismatch_does_not_override_normal_permissions(owner, monkeypatch):
    from openprogram.runtime.permissions.policy import permission_decision
    from openprogram.runtime.permissions import session_facts
    monkeypatch.setattr(session_facts.facts(), "files_await_decision", lambda _sid: True, raising=False)
    for name, args in (("edit", {"file_path": "/tmp/a.py"}), ("bash", {"command": "git status"}),
                       ("computer_use_agent", {"target": "browser"}), ("send_file", {"path": "/tmp/a.py"})):
        tool, _ = _make_tool(name)
        assert permission_decision(tool, _owner_request("bypass"), args).outcome == "allow"
    tool, _ = _make_tool("edit")
    assert permission_decision(tool, _owner_request("plan"), {}).outcome == "deny"


def test_a_turn_no_one_can_answer_is_refused_and_never_asks(owner):
    from openprogram.runtime.permissions.policy import can_approve, permission_decision
    tool, _ran = _make_tool("bash")
    attended = _owner_request("ask")
    assert can_approve(attended)
    assert permission_decision(tool, attended, {"command": "x"}).outcome == "ask"

    background = _owner_request("ask", speaker_kind="runtime", interaction="non-interactive")
    assert not can_approve(background)
    decision = permission_decision(tool, background, {"command": "x"})
    assert (decision.outcome, decision.code) == ("deny", "NO_ONE_TO_APPROVE")
    assert "no one to approve" in decision.refusal.text and "add an allow rule" in decision.refusal.text


def test_a_program_gets_its_pages_under_unrestricted(owner, monkeypatch):
    from openprogram.runtime.permissions import session_facts
    from openprogram.runtime.permissions.policy import permission_decision, surface_grant_applies
    tool, _ran = _make_tool("computer_use_agent")
    list_pages = {"target": "browser", "action": "tabs"}
    monkeypatch.setattr(type(session_facts.facts()), "browser_pages_available", lambda self, context: True)

    decision = permission_decision(tool, _owner_request("bypass"), list_pages)
    assert (decision.outcome, decision.code) == ("allow", "MODE_UNRESTRICTED")
    assert surface_grant_applies("computer_use_agent", _owner_request("bypass"), list_pages)


def test_a_malformed_call_goes_back_to_the_model_without_a_card(owner, monkeypatch):
    from openprogram.programs._authoring import prechecks
    from openprogram.runtime.permissions import approval as _approval
    tool, ran = _make_tool("needs_a_shape")
    monkeypatch.setitem(prechecks._registry, "needs_a_shape", lambda args: "shape is required")
    wrapped = _approval.wrap_with_approval(tool, _owner_request("ask"), lambda _event: None, _live=False)

    assert wrapped._interaction_manifest("c1", {}) is None
    result = asyncio.run(wrapped.execute("c1", {}, None, None))
    assert result.is_error and result.content[0].text == "needs_a_shape: shape is required"
    assert "denied" not in result.details and result.details["reason_code"] == "INVALID_ARGUMENTS"
    assert not ran["called"]


def test_a_tool_refusal_becomes_the_standard_denied_result(owner):
    from openprogram.models.providers.types import AgentTool
    from openprogram.runtime.permissions import approval as _approval, refusal

    async def execute(*_args):
        raise refusal.sandbox("bash", "path is outside writable roots: /x")

    tool = AgentTool(name="bash", label="bash", description="", parameters={}, execute=execute)
    request = _owner_request("bypass")
    result = asyncio.run(_approval.wrap_with_approval(tool, request, lambda _event: None, _live=False)
                         .execute("c1", {"command": "x"}, None, None))
    assert result.is_error and result.content[0].text.startswith("[blocked by sandbox] bash did not run:")
    assert result.details["denied"] is True and result.details["layer"] == "sandbox"
    assert result.details["mode"] == "bypass" and result.details["execution_started"] is False
