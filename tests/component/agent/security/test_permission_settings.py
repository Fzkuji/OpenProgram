"""Persistent permission settings, scope, and per-operation behavior."""
import asyncio
import json

import pytest

from openprogram.runtime.permissions.authority import local_owner_authority, owner_authority
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.permissions.approval import wrap_with_approval
from openprogram.runtime.permissions import permission_state, update_permission, PermissionUpdateError
from openprogram.models.providers.types import AgentTool, AgentToolResult
from openprogram.state.store.session.session_store import SessionStore

@pytest.fixture
def permissions(tmp_path, monkeypatch):
    db = SessionStore(tmp_path / "sessions")
    db.create_session("one", "main", permission_mode="ask")
    db.create_session("two", "main", permission_mode="ask")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    return db, local_owner_authority()

def test_version_persistence_and_conflict(permissions):
    db, actor = permissions
    assert update_permission("one", "bypass", 0, actor)["version"] == 1
    assert permission_state("one", db=SessionStore(db.root_path))["mode"] == "bypass"
    with pytest.raises(PermissionUpdateError, match="version_conflict"):
        update_permission("one", "ask", 0, actor)
    assert permission_state("one")["mode"] == "bypass"
    assert permission_state("two")["version"] == 0

@pytest.mark.parametrize("mode,version", [("invalid", 0), ("bypass", True), ("bypass", -1)])
def test_invalid_update_does_not_write(permissions, mode, version):
    with pytest.raises(PermissionUpdateError):
        update_permission("one", mode, version, permissions[1])
    assert permission_state("one")["version"] == 0

def test_call_snapshot_and_next_call_refresh(permissions):
    db, actor = permissions
    calls = []
    async def execute(*args):
        calls.append(args[0])
        return AgentToolResult(content=[], details={})
    request = TurnRequest(session_id="one", user_text="test", agent_id="main", source="web",
        permission_mode="ask", **actor)
    tool = wrap_with_approval(AgentTool(name="bash", label="bash", description="test",
        parameters={"type":"object"}, execute=execute), request, lambda event: None)
    assert tool._interaction_manifest("ask", {"command":"echo test"}) is not None
    update_permission("one", "bypass", 0, actor)
    assert tool._interaction_manifest("allowed", {"command":"echo test"}) is None
    update_permission("one", "ask", 1, actor)
    result = asyncio.run(tool.execute("allowed", {"command":"echo test"}, None, None))
    assert result.details["reason_code"] == "PERMISSION_REVIEW_STALE"
    assert calls == []
    assert tool._interaction_manifest("next", {"command":"echo test"}) is not None
    assert request.permission_mode == "ask"

@pytest.mark.parametrize("source,session,other_owner", [("agent_spawn", "one", False), ("web", "two", False), ("web", "one", True)])
def test_no_cross_source_session_or_principal_override(permissions, source, session, other_owner):
    from openprogram.runtime.permissions import current_permission_request
    actor = permissions[1]
    update_permission("one", "bypass", 0, actor)
    if other_owner: actor = owner_authority("owner/install/9999999999999999")
    req = TurnRequest(session_id=session, user_text="test", agent_id="main", source=source,
        permission_mode="ask", **actor)
    assert current_permission_request(req).permission_mode == "ask"

def test_authenticated_ws_update_and_scope(permissions, monkeypatch):
    from openprogram.interfaces.server.ws_actions.permissions import handle_set_permission
    monkeypatch.setattr("openprogram.interfaces.server.server._broadcast", lambda frame: None)
    async def reconcile(sid): pass
    monkeypatch.setattr("openprogram.runtime.permissions.reconcile_permission_waits", reconcile)
    class WS:
        scope = {"state": {"authority": {**permissions[1], "session_ids": ["one"]}}}
        frames = []
        async def send_text(self, value): self.frames.append(json.loads(value))
    ws = WS()
    asyncio.run(handle_set_permission(ws, {"session_id":"one", "mode":"bypass", "expected_version":0}))
    assert ws.frames[-1]["data"]["version"] == 1
    asyncio.run(handle_set_permission(ws, {"session_id":"two", "mode":"bypass", "expected_version":0}))
    assert ws.frames[-1]["data"]["error"] == "permission_scope_denied"
    assert permission_state("two")["version"] == 0


def test_plan_switch_restricts_pending_write_and_can_leave_plan(permissions, monkeypatch):
    import openprogram.programs as programs
    monkeypatch.setattr(programs, "_unsafe_in_for", lambda name: {"plan"} if name == "write" else set())
    actor = permissions[1]
    calls = []
    async def execute(*args):
        calls.append(args[0])
        return AgentToolResult(content=[], details={})
    req = TurnRequest(session_id="one", user_text="test", agent_id="main", source="web", permission_mode="bypass", **actor)
    tool = wrap_with_approval(AgentTool(name="write", label="write", description="test", parameters={"type":"object"}, execute=execute), req, lambda event: None)
    update_permission("one", "plan", 0, actor)
    denied = asyncio.run(tool.execute("denied", {"path":"/tmp/plan-test"}, None, None))
    assert denied.details["reason_code"] == "PLAN_MODE_DENY"
    update_permission("one", "bypass", 1, actor)
    asyncio.run(tool.execute("allowed", {"path":"/tmp/plan-test"}, None, None))
    assert calls == ["allowed"]


def test_first_message_retains_draft_permission_without_ghost_session(permissions):
    from openprogram.runtime.conversation.session_config import save_session_run_config
    result = save_session_run_config("new-draft", agent_id="main", permission_mode="bypass")
    assert result.permission_mode == "bypass"
    assert permissions[0].get_session("new-draft") is None


def test_reading_inherited_mode_does_not_pin_project_default(permissions, monkeypatch):
    import openprogram.runtime.conversation.session_config as config
    db, actor = permissions
    db.create_session("inherited", "main")
    defaults = {"permission_mode": "bypass"}
    monkeypatch.setattr(config, "project_defaults", lambda sid: defaults)
    assert permission_state("inherited")["mode"] == "bypass"
    config.save_session_run_config("inherited", agent_id="main", permission_mode="inherit")
    assert db.get_session("inherited").get("permission_mode") is None
    defaults["permission_mode"] = "ask"
    assert permission_state("inherited")["mode"] == "ask"
    update_permission("inherited", "bypass", 0, actor)
    assert permission_state("inherited")["mode"] == "bypass"


@pytest.mark.parametrize("name", ["memory_search", "memory_get"])
@pytest.mark.parametrize("denied", [False, True])
def test_inner_memory_reads_need_no_interactive_approval(permissions, name, denied):
    from openprogram.runtime.permissions.modes import PermissionRules
    calls = []
    async def execute(*args):
        calls.append(name)
        return AgentToolResult(content=[], details={})
    actor = {**permissions[1], "interaction": "non-interactive", "speaker_kind": "runtime"}
    req = TurnRequest(session_id="one", user_text="read weekly memory", agent_id="main",
                      source="web", permission_mode="ask", **actor)
    req.permission_rules = PermissionRules(deny=[name] if denied else [])
    tool = wrap_with_approval(AgentTool(name=name, label=name, description="Read memory",
        parameters={"type":"object"}, execute=execute), req, lambda event: None, _live=False)
    result = asyncio.run(tool.execute("read-memory", {}, None, None))
    assert calls == ([] if denied else [name]), result

def test_spawn_snapshot_of_a_web_owner_is_valid_job_input(permissions, monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.jobs.input import JobAgentInputV1
    from openprogram.runtime.jobs.types import Job
    from openprogram.runtime.permissions.lifecycle import spawn_permission_snapshot
    from openprogram.runtime.permissions.modes import PermissionRules
    actor = permissions[1]
    monkeypatch.setattr("openprogram.runtime.execution.run_control.get_current_execution_id", lambda: "exec-parent")
    monkeypatch.setattr("openprogram.runtime.execution.run_control.get_current_session_id", lambda: "one")
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules",
        lambda session_id: PermissionRules(allow=["bash(git status)"]))
    parent = SimpleNamespace(execution_id="exec-parent", session_id="one",
        status=SimpleNamespace(value="running"), current_attempt_id="attempt-1")
    request = {"session_id": "one", "user_text": "go", "agent_id": "main", "source": "web",
        "permission_mode": "ask", **actor}
    store = SimpleNamespace(get_job_agent_input=lambda execution_id: None,
        get_agent_turn_input=lambda execution_id: {"kind": "chat", "request": request})
    child = Job(id="job-child", parent_session_id="one", prompt="check", agent_id="main",
        subject="check", description="check", context_mode="clean", source="agent_spawn",
        caller_msg_id="msg-1", caller_session_id="one", **actor)
    snapshot = spawn_permission_snapshot(store, parent, child)
    assert snapshot == {"mode": "ask", "rules": {"allow": ["bash(git status)"], "deny": [], "ask": []}}
    turn = JobAgentInputV1.from_job(child, permission_snapshot=snapshot).to_turn_request()
    assert turn.permission_rules == PermissionRules(allow=["bash(git status)"])
