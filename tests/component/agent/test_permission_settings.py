"""Persistent permission settings, scope, and per-operation behavior."""
import asyncio
import json

import pytest

from openprogram.agent.authority import local_owner_authority, owner_authority
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.internals._approval import wrap_with_approval
from openprogram.agent.permissions import permission_state, update_permission, PermissionUpdateError
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.store.session.session_store import SessionStore

@pytest.fixture
def permissions(tmp_path, monkeypatch):
    db = SessionStore(tmp_path / "sessions")
    db.create_session("one", "main", permission_mode="ask")
    db.create_session("two", "main", permission_mode="ask")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
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
    asyncio.run(tool.execute("allowed", {"command":"echo test"}, None, None))
    assert calls == ["allowed"]
    assert tool._interaction_manifest("next", {"command":"echo test"}) is not None
    assert request.permission_mode == "ask"

@pytest.mark.parametrize("source,session,other_owner", [("agent_spawn", "one", False), ("web", "two", False), ("web", "one", True)])
def test_no_cross_source_session_or_principal_override(permissions, source, session, other_owner):
    from openprogram.agent.permissions import current_permission_request
    actor = permissions[1]
    update_permission("one", "bypass", 0, actor)
    if other_owner: actor = owner_authority("owner/install/9999999999999999")
    req = TurnRequest(session_id=session, user_text="test", agent_id="main", source=source,
        permission_mode="ask", **actor)
    assert current_permission_request(req).permission_mode == "ask"

def test_authenticated_ws_update_and_scope(permissions, monkeypatch):
    from openprogram.webui.ws_actions.permissions import handle_set_permission
    monkeypatch.setattr("openprogram.webui.server._broadcast", lambda frame: None)
    async def reconcile(sid): pass
    monkeypatch.setattr("openprogram.agent.permissions.reconcile_permission_waits", reconcile)
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
    from openprogram.agent.session_config import save_session_run_config
    result = save_session_run_config("new-draft", agent_id="main", permission_mode="bypass")
    assert result.permission_mode == "bypass"
    assert permissions[0].get_session("new-draft") is None
