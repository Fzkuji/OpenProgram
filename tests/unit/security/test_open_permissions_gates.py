"""What used to be hidden gates: the owner in Bypass runs each of them."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions.approval import wrap_with_approval
from openprogram.runtime.permissions.policy import permission_decision

OWNER = "owner/install/0123456789abcdef"


@pytest.fixture
def owner(monkeypatch):
    from openprogram.runtime.permissions.authority import owner_authority

    monkeypatch.setattr("openprogram.runtime.permissions.authority.owner_principal_id", lambda: OWNER)
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _sid: None)

    def request(source="web", mode="bypass", **overrides):
        return TurnRequest(session_id="gates", user_text="x", agent_id="main", source=source,
                           permission_mode=mode, permission_rules=None,
                           **{**owner_authority(OWNER), **overrides})

    return request


def _tool(name, calls):
    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[], details={})

    return AgentTool(name=name, label=name, description="probe", parameters={"type": "object"}, execute=execute)


@pytest.mark.parametrize("name,args", [
    ("write", {"file_path": "~/.openprogram/programs/demo/agentics/__init__.py", "content": "x"}),
    ("edit", {"file_path": "~/.openprogram/program-sources.json", "old_string": "a", "new_string": "b"}),
    ("self_update", {"action": "prepare", "worktree_id": "wt", "candidate_sha": "a" * 40,
                     "goal": "g", "assertions": ["a"]}),
    ("self_update", {"action": "retry", "update_id": "su_1", "candidate_sha": "a" * 40}),
    ("exit_plan_mode", {}),
    ("bash", {"command": "openprogram programs install owner/repo"}),
    ("bash", {"command": "ls", "sandbox_permissions": "require_escalated", "justification": "needs it"}),
    ("computer_use_agent", {"target": "app", "action": "click", "args": {"app": "Keychain Access"}}),
    ("computer_use_agent", {"target": "app", "action": "type_text", "args": {"app": "Notes", "text": "x"}}),
    ("web_fetch", {"url": "http://127.0.0.1:8080/"}),
])
def test_the_owner_in_unrestricted_runs_each_former_gate(owner, name, args):
    calls = []
    tool, request = _tool(name, calls), owner()
    verdict = permission_decision(tool, request, args)
    assert (verdict.outcome, verdict.code) == ("allow", "MODE_UNRESTRICTED")


def test_no_tool_that_runs_commands_or_drives_a_browser_names_a_channel():
    import openprogram.programs  # noqa: F401 - registers the built-in tools
    from openprogram.programs._host.runtime import _unsafe_in_channel
    from openprogram.runtime.permissions.tool_authority import capability_for_tool

    channels = {"wechat", "telegram", "discord", "slack"}
    named = {tool for tool, where in _unsafe_in_channel.items()
             if where & channels and capability_for_tool(tool) in {"process.exec", "browser.control"}}
    # These keep the channel entry for how they display, not for what they may do.
    assert named <= {"todo_create", "todo_update", "create_goal", "update_goal"}


def test_a_scheduled_turn_with_a_missing_macos_grant_ends_with_an_error(owner, monkeypatch):
    """A wait has no timeout and nobody is there to answer one."""
    import sys
    from openprogram.host import system_access

    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_access, "_native_probe", lambda: {
        "identity": {"executable": sys.executable}, "capabilities": {
            "screen_recording": {"status": "granted", "detail": "fresh"},
            "accessibility": {"status": "not_granted", "detail": "fresh"}}})
    calls = []
    request = owner("scheduler", interaction="non-interactive", speaker_kind="runtime")
    wrapped = wrap_with_approval(_tool("computer_use_agent", calls), request, lambda _event: None)
    click = {"target": "app", "action": "click", "args": {"app": "Notes", "element_index": 3}}
    assert wrapped._interaction_manifest("call-1", click) is None
    result = asyncio.run(wrapped.execute("call-1", click, None, None))
    assert result.is_error and "macOS has not granted" in str(result.content)
    assert calls == []


def _named(*names):
    return [SimpleNamespace(name=name) for name in names]


def _visible(owner, monkeypatch, mode):
    from openprogram.runtime.permissions import lifecycle

    monkeypatch.setattr(lifecycle, "permission_state", lambda _sid: {"mode": mode, "version": 2, "principal_id": OWNER})
    return {name for name, tool in owner.tools.items() if tool._permission_visible()}


@pytest.fixture
def planned(owner):
    from openprogram.runtime.permissions.lifecycle import wrap_live_permission

    request = owner(mode="plan")
    tools = {name: wrap_live_permission(_tool(name, []), request, lambda _event: None)
             for name in ("read", "bash", "write", "exit_plan_mode")}
    return SimpleNamespace(tools=tools)


def test_plan_hides_what_it_would_refuse_and_only_plan_offers_the_way_out(planned, monkeypatch):
    assert _visible(planned, monkeypatch, "plan") == {"read", "exit_plan_mode"}


@pytest.mark.parametrize("mode", ["ask", "auto", "acceptEdits", "bypass"])
def test_leaving_plan_inside_a_turn_brings_the_tools_back(planned, monkeypatch, mode):
    assert _visible(planned, monkeypatch, "plan") == {"read", "exit_plan_mode"}
    assert _visible(planned, monkeypatch, mode) == {"read", "bash", "write"}


def test_the_tool_list_of_a_turn_is_not_cut_by_its_starting_mode():
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    assert not hasattr(loop_runner, "_tools_for_mode")


def test_a_saved_rule_does_not_put_unrestricted_back_inside_the_grant(owner, monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use_agent import code
    from openprogram.runtime.agent import turn_request
    from openprogram.runtime.permissions.modes import PermissionRules

    calls = []
    tool = _tool("computer_use_agent", calls)
    monkeypatch.setattr("openprogram.programs.get_agent_tool", lambda _name: tool)
    args = {"target": "app", "args": {"app": "Safari"}}
    rules = PermissionRules(allow=["computer_use_agent"])
    def request(mode):
        found = owner(mode=mode)
        found.permission_rules = rules
        return found

    monkeypatch.setattr(turn_request, "get_turn_request", lambda: request("bypass"))
    assert code._unrestricted(args)
    monkeypatch.setattr(turn_request, "get_turn_request", lambda: request("ask"))
    assert not code._unrestricted(args)


def test_the_model_cannot_enter_plan():
    import openprogram.programs as programs

    assert "enter_plan_mode" not in programs.DEFAULT_TOOLS
    assert programs.get_agent_tool("enter_plan_mode") is None


@pytest.mark.parametrize("before,restored", [("bypass", "bypass"), ("ask", "ask"), (None, None)])
def test_leaving_plan_restores_what_the_selector_said(monkeypatch, before, restored):
    from openprogram.programs.tools.planning.plan_mode import exit as exit_plan
    from openprogram.runtime.permissions import modes, state

    seen, frames = [], []
    monkeypatch.setattr(state, "permission_state",
                        lambda _sid: {"mode": "plan", "version": 3, "before_plan": before})

    def update(sid, mode, version, actor):
        seen.append((sid, mode, version))
        return {"mode": mode, "version": version + 1}

    monkeypatch.setattr(state, "update_permission", update)
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)
    exit_plan._restore_mode("s")
    mode = restored or modes.DEFAULT_PERMISSION
    assert seen == [("s", mode, 3)]
    # The composer's selector moves with it, as when the owner clicks it.
    assert frames == [{"type": "permission_changed", "data": {
        "session_id": "s", "action": "set_permission", "mode": mode, "version": 4}}]


class _SessionRow:
    """As much of the session store as ``update_permission`` reads."""

    def __init__(self, **row):
        self.row = {"extra_meta": {}, **row}

    def get_session(self, _sid):
        return self.row

    def update_session_dict(self, _sid, key, update):
        self.row["extra_meta"][key] = update(self.row["extra_meta"].get(key) or {})
        return self.row["extra_meta"][key]


@pytest.mark.parametrize("row,project,before", [
    ({}, "ask", "ask"),
    ({"permission_mode": "ask"}, None, "ask"),
    ({}, None, "bypass"),
    ({}, "plan", None),
])
def test_picking_plan_remembers_the_mode_a_conversation_inherited(monkeypatch, row, project, before):
    from openprogram.runtime.permissions import session_facts, state
    from openprogram.runtime.permissions.authority import owner_authority

    monkeypatch.setattr(session_facts, "facts", lambda: SimpleNamespace(
        project_defaults=lambda _sid: {"permission_mode": project} if project else {}))
    db = _SessionRow(**row)
    updated = state.update_permission("s", "plan", 0, owner_authority(OWNER), db=db)
    assert updated.get("before_plan") == before


def _open_wait(reason, speaker="owner/local"):
    from openprogram.runtime.execution import waits
    from openprogram.runtime.permissions import session_facts

    wait = SimpleNamespace(kind="approval", execution_id="e", wait_id="w", claim_generation=1, request={
        "tool": "exit_plan_mode", "args": {"plan": "p"}, "approval_reason": reason, "permission_version": 0})
    answers = []

    class Service:
        executions = SimpleNamespace(get_execution=lambda _id: SimpleNamespace(execution_id="e", status_version=1),
                                     get_agent_turn_input=lambda _id: {})

        async def request_wait_answer(self, **kwargs):
            answers.append(kwargs)
            raise waits_conflict()

    def waits_conflict():
        from openprogram.runtime.execution.store import ExecutionConflict
        return ExecutionConflict("conflict", "answered")

    request = SimpleNamespace(session_id="s", source="web", authority_tier="owner", interaction="interactive",
                              principal_id=OWNER, speaker_id=speaker, permission_mode="bypass", permission_rules=None)
    return wait, Service(), answers, request, waits, session_facts


@pytest.mark.parametrize("force", [False, True])
def test_changing_the_selector_does_not_approve_an_open_plan(monkeypatch, force):
    from contextlib import nullcontext
    from openprogram.runtime.permissions import lifecycle

    wait, service, answers, request, waits, session_facts = _open_wait("PLAN_EXIT")
    monkeypatch.setattr(waits.DurableWaitStore, "list_open", lambda _self, session_id: [wait])
    monkeypatch.setattr(session_facts, "facts", lambda: SimpleNamespace(
        turn_running=lambda *_a: False, turn_request_for=lambda *_a: request, bind_execution=lambda _id: nullcontext()))
    monkeypatch.setattr(lifecycle, "current_permission_request",
                        lambda _req: SimpleNamespace(**{**vars(request), "_permission_version": 5}))
    asyncio.run(lifecycle.reconcile_permission_waits("s", service=service, force=force))
    assert answers == []


@pytest.mark.parametrize("speaker,answered", [("owner/local", True), ("7000000", False)])
def test_the_migration_only_resumes_the_local_owners_waits(monkeypatch, speaker, answered):
    from contextlib import nullcontext
    from openprogram.runtime.permissions import lifecycle

    wait, service, answers, request, waits, session_facts = _open_wait("MODE_APPROVAL", speaker)
    monkeypatch.setattr(waits.DurableWaitStore, "list_open", lambda _self, session_id: [wait])
    monkeypatch.setattr(session_facts, "facts", lambda: SimpleNamespace(
        turn_running=lambda *_a: False, turn_request_for=lambda *_a: request, bind_execution=lambda _id: nullcontext()))
    monkeypatch.setattr(lifecycle, "current_permission_request",
                        lambda _req: SimpleNamespace(**{**vars(request), "_permission_version": 0}))
    monkeypatch.setattr("openprogram.runtime.permissions.policy.permission_decision",
                        lambda *_a: SimpleNamespace(outcome="allow"))
    asyncio.run(lifecycle.reconcile_permission_waits("s", service=service, force=True))
    assert bool(answers) is answered


def test_a_sub_agent_is_not_told_it_cannot_delegate():
    from openprogram.programs.tools.agents.agent.agent.prompt import DESCRIPTION

    assert "refused" not in DESCRIPTION


def test_workflow_recording_keeps_its_own_drop_list():
    from openprogram.programs.applications.workflow_recording.macos import DROPPED_BUNDLES

    assert {"com.apple.keychainaccess", "com.apple.securityagent", "ai.openprogram.desktop",
            "com.1password.1password"} <= DROPPED_BUNDLES


@pytest.mark.parametrize("args", [
    {"backend_id": "ssh", "worktree": "/srv"},
])
def test_a_remote_workdir_under_sandbox_is_a_sandbox_refusal(monkeypatch, args):
    from openprogram.host import sandbox
    from openprogram.host.backend.workdir import prepare_command

    monkeypatch.setattr(sandbox, "resolve_policy", lambda *a, **k: object())
    with pytest.raises(sandbox.SandboxPathDenied):
        prepare_command("ls", "/srv", **args)


def test_a_local_workdir_with_no_sandbox_available_is_a_sandbox_refusal(monkeypatch, tmp_path):
    from openprogram.host import sandbox
    from openprogram.host.backend.workdir import prepare_command

    monkeypatch.setattr(sandbox, "validate_read_path", lambda _p: None)
    monkeypatch.setattr(sandbox, "validate_write_path", lambda _p, cwd=None: None)
    monkeypatch.setattr(sandbox, "resolve_policy", lambda *a, **k: object())
    monkeypatch.setattr(sandbox, "unavailable_reason", lambda: "no bubblewrap")
    with pytest.raises(sandbox.SandboxPathDenied, match="no bubblewrap"):
        prepare_command("ls", str(tmp_path), backend_id="local", worktree=str(tmp_path))


def test_listing_retained_versions_is_a_free_read():
    from openprogram.runtime.permissions.classifier import safe_call
    from openprogram.runtime.permissions.tool_authority import capability_for_tool

    assert safe_call("self_update", {"action": "versions"}) is True
    assert capability_for_tool("self_update", {"action": "versions"}) == "fs.read"
    assert safe_call("self_update", {"action": "undo"}) is False
    assert capability_for_tool("self_update", {"action": "undo"}) == "process.exec"
