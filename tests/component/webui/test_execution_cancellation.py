"""WebSocket and reload contracts for execution cancellation."""

from __future__ import annotations

import asyncio
import json
import threading

from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


class FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

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
        ws, {"action": "execution.cancel", "execution_id": record.execution_id},
    ))

    execution = store.get_execution(record.execution_id)
    assert execution is not None
    assert execution.status is ExecutionStatus.CANCELLED
    update = next(frame for frame in broadcasts if frame["type"] == "execution.updated")
    assert update["execution"]["execution_id"] == record.execution_id
    assert released == [record.execution_id]
    assert not any(frame["type"] == "error" for frame in ws.frames)


def test_http_execution_cancel_returns_canonical_status_and_body(
    tmp_path, monkeypatch,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    store, record = _canonical_execution(tmp_path, execution_id="exec-http-cancel")
    _patch_canonical_store(monkeypatch, store)
    from openprogram.webui.routes import lifecycle

    released: list[str] = []
    monkeypatch.setattr(
        "openprogram.webui.server._release_session_occupancy_for_execution",
        lambda execution: released.append(execution["execution_id"]),
    )
    app = FastAPI()
    lifecycle.register(app)
    response = TestClient(app).post(
        "/api/execution/cancel", json={"execution_id": record.execution_id},
    )

    assert response.status_code == 200
    body = response.json()["execution"]
    assert body["execution_id"] == record.execution_id
    assert body["status"] == "cancelled"
    assert released == [record.execution_id]


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
