"""WebSocket and reload contracts for execution cancellation."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.agent.authority import owner_authority
from openprogram.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


class FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.scope = {
            "state": {"authority": owner_authority("owner/install/0123456789abcdef")},
        }

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


def _canonical_execution(tmp_path, *, execution_id="exec-web-cancel"):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    revision = store.create_revision(
        revision_id=f"revision-{execution_id}", manifest={"entrypoint": "agent"},
    )
    record = store.admit_execution(
        execution_id=execution_id,
        run_id=f"run-{execution_id}",
        session_id="session-canonical",
        revision_id=revision.revision_id,
        input_ref=f"agent-turn:{execution_id}",
        input_hash="hash",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "test", "session_id": "session-canonical"},
        config_snapshot_ref="test",
        user_message_id="user-canonical",
        assistant_message_id=None,
        capabilities=CapabilitySet(),
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "cancel me",
                "agent_id": "main",
                "source": "test",
            },
        },
    )
    return store, record


def _patch_canonical_store(monkeypatch, store):
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: service)
    return service


def test_ws_execution_cancel_returns_canonical_status_and_releases_occupancy(
    tmp_path, monkeypatch,
):
    store, record = _canonical_execution(tmp_path)
    _patch_canonical_store(monkeypatch, store)
    from openprogram.webui import server
    from openprogram.webui.ws_actions import runtime

    released: list[str] = []
    broadcasts: list[dict] = []
    monkeypatch.setattr(
        server, "_release_session_occupancy_for_execution",
        lambda execution: released.append(execution["execution_id"]),
    )
    monkeypatch.setattr(
        server, "_broadcast", lambda payload: broadcasts.append(json.loads(payload)),
    )
    ws = FakeWS()

    asyncio.run(runtime.ACTIONS["execution.cancel"](
        ws, {
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-web-1",
            "execution_id": record.execution_id,
            "expected_version": record.status_version,
        },
    ))

    execution = store.get_execution(record.execution_id)
    assert execution is not None
    assert execution.status is ExecutionStatus.CANCELLED
    update = next(frame for frame in broadcasts if frame["type"] == "execution.updated")
    assert update["execution"]["execution_id"] == record.execution_id
    assert update["event_cursor"]["execution_id"] == record.execution_id
    assert update["data"]["execution"] == update["execution"]
    assert update["data"]["event_cursor"] == update["event_cursor"]
    command = next(frame for frame in ws.frames if frame["type"] == "execution.command.updated")
    assert command["command"]["status"] == "applied"
    assert released == [record.execution_id]
    assert not any(frame["type"] == "error" for frame in ws.frames)

    # Repeating the exact cancel is idempotent after the terminal transition.
    asyncio.run(runtime.ACTIONS["execution.cancel"](
        ws, {
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-web-1",
            "execution_id": record.execution_id,
            "expected_version": record.status_version,
        },
    ))
    assert not any(frame["type"] == "error" for frame in ws.frames)

    asyncio.run(runtime.ACTIONS["execution.cancel"](
        ws, {
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-web-stale",
            "execution_id": record.execution_id,
            "expected_version": 999,
        },
    ))
    rejected = ws.frames[-2]["command"]
    assert rejected["status"] == "rejected"
    assert rejected["latest_snapshot"]["status_version"] == execution.status_version


def test_ws_rejected_control_does_not_publish_an_unauthorized_snapshot(
    tmp_path, monkeypatch,
):
    store, record = _canonical_execution(tmp_path, execution_id="exec-private")
    _patch_canonical_store(monkeypatch, store)
    from openprogram.webui import server
    from openprogram.webui.ws_actions import runtime

    broadcasts: list[dict] = []
    monkeypatch.setattr(
        server, "_broadcast", lambda payload: broadcasts.append(json.loads(payload)),
    )
    ws = FakeWS()
    ws.scope = {"state": {}}

    asyncio.run(runtime.ACTIONS["execution.cancel"](
        ws, {
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-private",
            "execution_id": record.execution_id,
            "expected_version": record.status_version,
        },
    ))

    assert len(ws.frames) == 1
    assert ws.frames[0]["type"] == "execution.command.updated"
    assert ws.frames[0]["command"]["rejection_code"] == "unauthorized"
    assert "execution" not in ws.frames[0]
    assert "execution" not in ws.frames[0]["data"]
    assert broadcasts == []


def test_http_execution_cancel_returns_canonical_status_and_body(
    tmp_path, monkeypatch,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    store, record = _canonical_execution(tmp_path, execution_id="exec-http-cancel")
    _patch_canonical_store(monkeypatch, store)
    from openprogram.webui.routes import lifecycle

    released: list[str] = []
    emitted: list[dict] = []
    monkeypatch.setattr(lifecycle, "emit_ws_frame", emitted.append)
    monkeypatch.setattr(
        "openprogram.webui.server._release_session_occupancy_for_execution",
        lambda execution: released.append(execution["execution_id"]),
    )
    app = FastAPI()
    app.state.owner_auth = type("OwnerAuth", (), {
        "authority": owner_authority("owner/install/0123456789abcdef"),
    })()
    lifecycle.register(app)
    response = TestClient(app).post(
        "/api/execution/cancel", json={
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-http-1",
            "execution_id": record.execution_id,
            "expected_version": record.status_version,
        },
    )

    assert response.status_code == 200
    result = response.json()
    body = result["execution"]
    assert body["execution_id"] == record.execution_id
    assert body["status"] == "cancelled"
    assert result["command"]["status"] == "applied"
    update = next(frame for frame in emitted if frame["type"] == "execution.updated")
    assert update["event_cursor"]["execution_id"] == record.execution_id
    assert update["data"]["execution"] == update["execution"]
    assert update["data"]["event_cursor"] == update["event_cursor"]
    assert released == [record.execution_id]
    repeated = TestClient(app).post(
        "/api/execution/cancel", json={
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-http-1",
            "execution_id": record.execution_id,
            "expected_version": record.status_version,
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["execution"]["status"] == "cancelled"

    stale = TestClient(app).post(
        "/api/execution/cancel", json={
            "type": "execution.command",
            "action": "execution.cancel",
            "command_id": "cancel-http-stale",
            "execution_id": record.execution_id,
            "expected_version": 999,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["command"]["status"] == "rejected"
    latest = store.get_execution(record.execution_id)
    assert latest is not None
    assert stale.json()["command"]["latest_snapshot"]["status_version"] == latest.status_version


def test_session_reload_preserves_cancelling_execution_status(
    tmp_path, monkeypatch,
):
    from openprogram.webui import server as server
    from openprogram.webui.ws_actions.session import handle_load_session

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", store)
    session_id = "reload-cancelling"
    store.create_session(session_id, "main")
    store.append_message(session_id, {
        "id": "user-1",
        "role": "user",
        "content": "run",
        "predecessor": "ROOT",
    })
    store.append_message(session_id, {
        "id": "assistant-1",
        "role": "assistant",
        "content": "running",
        "predecessor": "user-1",
    })
    SessionNodeWriter(store, session_id).append(Call(
        id="execution-1",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial output",
        predecessor="assistant-1",
        metadata={
            "status": "cancelling",
            "reason_code": "cancel.user",
            "execution_kind": "agentic_function",
        },
    ))
    with server._sessions_lock:
        server._sessions[session_id] = {"id": session_id}
    monkeypatch.setattr(server, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda sid: True)

    ws = FakeWS()
    try:
        asyncio.run(handle_load_session(ws, {"session_id": session_id}))
    finally:
        with server._sessions_lock:
            server._sessions.pop(session_id, None)

    loaded = next(frame for frame in ws.frames if frame["type"] == "session_loaded")
    execution = next(
        message for message in loaded["data"]["messages"]
        if message["id"] == "execution-1"
    )
    assert execution["status"] == "cancelling"
    assert execution["reason_code"] == "cancel.user"
    assert loaded["data"]["run_active"] is True


@pytest.mark.parametrize("quiet_seconds", [0, 600])
def test_reload_checks_foreground_thread_liveness_and_preserves_cancel_identity(
    tmp_path, monkeypatch, quiet_seconds,
):
    import time
    from openprogram.webui import server
    from openprogram.webui.ws_actions.session import handle_load_session
    from openprogram.agent.run_control import register_active_runtime, unregister_active_runtime

    sessions = SessionStore(tmp_path / "reload-sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: sessions)
    monkeypatch.setattr("openprogram.store.session.session_store._default_store", sessions)
    execution_store = ExecutionStore(tmp_path / "reload-executions.sqlite3")
    monkeypatch.setattr("openprogram.execution.default_store", lambda: execution_store)
    monkeypatch.setattr(server, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(server, "refresh_context_stats", lambda sid: None)
    monkeypatch.setattr(server, "_broadcast", lambda payload: None)
    sid = "reload-thread-liveness"
    sessions.create_session(sid, "main")
    with server._sessions_lock:
        server._sessions[sid] = {"id": sid}
    release = threading.Event()
    worker = threading.Thread(target=release.wait)
    register_active_runtime(sid, worker)
    try:
        # Registration precedes start: this handoff must still block admission.
        assert server._is_run_active(sid)
        assert not server._try_reserve_run(sid, "other-user")
        worker.start()
        with server._running_tasks_lock:
            server._running_tasks[sid] = {
                "msg_id": "user-1", "func_name": "agent",
                "execution_id": "execution-quiet", "status_version": 7,
                "started_at": time.time() - quiet_seconds,
                "last_event_at": time.time() - quiet_seconds,
            }
        ws = FakeWS()
        asyncio.run(handle_load_session(ws, {"session_id": sid}))
        loaded = next(f["data"] for f in ws.frames if f["type"] == "session_loaded")
        assert loaded["run_active"] is True
        replay = next((f["data"] for f in ws.frames if f["type"] == "running_task"), None)
        assert replay is not None, "quiet live executions retain their cancellation controls"
        assert (replay["execution_id"], replay["status_version"]) == ("execution-quiet", 7)
        assert server._is_run_active(sid)
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(timeout=5)
        unregister_active_runtime(sid)
        with server._running_tasks_lock:
            server._running_tasks.pop(sid, None)
        with server._sessions_lock:
            server._sessions.pop(sid, None)


def test_reload_prunes_completed_orphan_foreground_thread(tmp_path, monkeypatch):
    from openprogram.webui import server
    from openprogram.webui.ws_actions.session import handle_load_session
    from openprogram.agent.run_control import register_active_runtime, unregister_active_runtime

    sessions = SessionStore(tmp_path / "orphan-sessions")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: sessions)
    monkeypatch.setattr("openprogram.store.session.session_store._default_store", sessions)
    execution_store = ExecutionStore(tmp_path / "orphan-executions.sqlite3")
    monkeypatch.setattr("openprogram.execution.default_store", lambda: execution_store)
    monkeypatch.setattr(server, "_get_provider_info", lambda sid=None: {})
    monkeypatch.setattr(server, "refresh_context_stats", lambda sid: None)
    monkeypatch.setattr(server, "_broadcast", lambda payload: None)
    sid = "reload-completed-orphan"
    sessions.create_session(sid, "main")
    with server._sessions_lock:
        server._sessions[sid] = {"id": sid}
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    register_active_runtime(sid, worker)
    try:
        for _ in range(2):
            ws = FakeWS()
            asyncio.run(handle_load_session(ws, {"session_id": sid}))
            loaded = next(f["data"] for f in ws.frames if f["type"] == "session_loaded")
            assert loaded["run_active"] is False
            assert not any(f["type"] == "running_task" for f in ws.frames)
        assert server._try_reserve_run(sid, "new-user"), "orphan cleanup permits a new turn"
    finally:
        unregister_active_runtime(sid)
        with server._running_tasks_lock:
            server._running_tasks.pop(sid, None)
        with server._sessions_lock:
            server._sessions.pop(sid, None)


def test_orphan_cleanup_preserves_new_reservation(monkeypatch):
    from openprogram.webui import server as s
    from openprogram.agent.run_control import register_active_runtime, unregister_active_runtime
    sid = 'orphan-cleanup-replacement'
    old = threading.Thread(target=lambda: None)
    old.start()
    old.join(timeout=5)
    assert not old.is_alive()
    register_active_runtime(sid, old)
    s._running_tasks[sid] = {'msg_id': 'old', 'func_name': 'agent'}
    original = s._has_active_runtime
    injected = False
    def check(session):
        nonlocal injected
        result = original(session)
        if not result and not injected:
            injected = True
            # Another observer runs after the first liveness result, before
            # its caller acquires the running-task lock for stale cleanup.
            assert s._try_reserve_run(sid, 'new')
        return result
    monkeypatch.setattr(s, '_has_active_runtime', check)
    monkeypatch.setattr(s, '_emit_running_task_event', lambda *a, **kw: None)
    try:
        assert s._is_run_active(sid)
        assert s._running_tasks.get(sid, {}).get('msg_id') == 'new'
    finally:
        s._running_tasks.pop(sid, None)
        unregister_active_runtime(sid)
