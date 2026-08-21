"""WebSocket and reload contracts for execution cancellation."""

from __future__ import annotations

import asyncio
import json
import threading

from openprogram.context.nodes import Call, ROLE_CODE
from openprogram.store import SessionNodeWriter
from openprogram.store.session.session_store import SessionStore


class FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


def test_execution_cancel_action_uses_the_public_execution_id(
    tmp_path, monkeypatch,
):
    from openprogram.agent import run_control
    from openprogram.webui import server as server
    from openprogram.webui.ws_actions import runtime

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", store)
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
    run_control._owners.clear()
    store.create_session("session-1", "main")
    SessionNodeWriter(store, "session-1").append(Call(
        id="execution-1",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial",
        metadata={"status": "running", "execution_kind": "agentic_function"},
    ))
    event = threading.Event()
    run_control.CANCEL_GRACE_S = 0.01
    run_control.register_cancel_event(
        "session-1", event, execution_id="execution-1",
    )
    broadcasts: list[dict] = []
    monkeypatch.setattr(
        server, "_broadcast", lambda text: broadcasts.append(json.loads(text)),
    )

    handler = runtime.ACTIONS["execution.cancel"]
    ws = FakeWS()
    try:
        asyncio.run(handler(ws, {
            "action": "execution.cancel",
            "execution_id": "execution-1",
        }))
    finally:
        for execution_id in list(run_control._owners):
            run_control.retire_execution_owner(execution_id)
        for thread in list(run_control._grace_threads.values()):
            thread.join(1)
        run_control._grace_threads.clear()
        run_control._owners.clear()
        run_control.CANCEL_GRACE_S = 4.0

    frames = broadcasts + ws.frames
    update = next(frame for frame in frames if frame["type"] == "execution.updated")
    execution = update["execution"]
    assert execution["execution_id"] == "execution-1"
    assert execution["status"] in {"cancelling", "cancelled"}
    assert execution["reason_code"] == "cancel.user"
    assert "stopped" not in update
    assert "stopped" not in execution
    assert event.is_set()


def test_http_execution_cancel_runs_in_the_worker_handler(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openprogram.agent import run_control
    from openprogram.webui.routes import lifecycle

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", store)
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
    run_control._owners.clear()
    store.create_session("session-http", "main")
    SessionNodeWriter(store, "session-http").append(Call(
        id="http-exec",
        role=ROLE_CODE,
        name="cancellation_probe",
        output="partial",
        metadata={"status": "queued", "execution_kind": "agentic_function"},
    ))
    monkeypatch.setattr("openprogram.events.emit_ws_frame", lambda *a, **k: None)
    app = FastAPI()
    lifecycle.register(app)
    client = TestClient(app)
    try:
        response = client.post(
            "/api/execution/cancel",
            json={"execution_id": "http-exec"},
        )
    finally:
        for execution_id in list(run_control._owners):
            run_control.retire_execution_owner(execution_id)
        run_control._owners.clear()

    assert response.status_code == 200
    body = response.json()["execution"]
    assert body["execution_id"] == "http-exec"
    assert body["status"] == "cancelled"
    assert body["reason_code"] == "cancel.user"
    node = next(
        item for item in store.get_nodes("session-http") if item.id == "http-exec"
    )
    assert node.metadata["status"] == "cancelled"


def test_execution_cancel_errors_for_terminal_and_missing(tmp_path, monkeypatch):
    from openprogram.webui import server as server
    from openprogram.webui.ws_actions import runtime

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", store)
    store.create_session("session-1", "main")
    SessionNodeWriter(store, "session-1").append(Call(
        id="done-1",
        role=ROLE_CODE,
        name="cancellation_probe",
        metadata={"status": "completed", "execution_kind": "agentic_function"},
    ))
    monkeypatch.setattr(server, "_broadcast", lambda text: None)
    handler = runtime.ACTIONS["execution.cancel"]

    ws = FakeWS()
    asyncio.run(handler(ws, {"execution_id": "done-1"}))
    error = next(frame for frame in ws.frames if frame["type"] == "error")
    assert error["data"]["code"] == "ExecutionNotCancellable"

    ws = FakeWS()
    asyncio.run(handler(ws, {"execution_id": "missing"}))
    error = next(frame for frame in ws.frames if frame["type"] == "error")
    assert error["data"]["code"] == "ExecutionNotFound"


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


def test_cancel_releases_session_occupancy_for_next_turn(tmp_path, monkeypatch):
    """Cancel intent must free the session slot before the turn thread dies."""
    from openprogram.agent import run_control
    from openprogram.webui import server as server
    from openprogram.webui.ws_actions import runtime

    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    import openprogram.store.session.session_store as store_module

    monkeypatch.setattr(store_module, "_default_store", store)
    with run_control._cancel_flags_lock:
        run_control._current_tokens.clear()
    run_control._owners.clear()

    session_id = "occ-session"
    msg_id = "user-1"
    execution_id = f"{msg_id}_reply"
    store.create_session(session_id, "main")
    SessionNodeWriter(store, session_id).append(Call(
        id=execution_id,
        role=ROLE_CODE,
        name="_chat",
        metadata={"status": "running", "execution_kind": "chat"},
    ))

    assert server._try_reserve_run(session_id, msg_id)
    assert server._activate_run_reservation(session_id, msg_id, object())
    assert server._is_run_active(session_id)

    broadcasts: list[dict] = []
    monkeypatch.setattr(
        server, "_broadcast", lambda payload: broadcasts.append(json.loads(payload)),
    )

    handler = runtime.ACTIONS["execution.cancel"]
    ws = FakeWS()
    try:
        asyncio.run(handler(ws, {
            "action": "execution.cancel",
            "execution_id": execution_id,
        }))
        assert server._is_run_active(session_id) is False
        assert server._try_reserve_run(session_id, "user-2") is True
        assert server._is_run_active(session_id) is True
        clears = [
            frame for frame in broadcasts
            if frame.get("type") == "running_task_clear"
        ]
        assert clears, "cancel must broadcast running_task_clear"
        assert clears[-1]["data"]["session_id"] == session_id
    finally:
        server._finish_owned_run(session_id, "user-2")
        server._finish_owned_run(session_id, msg_id)
        for execution in list(run_control._owners):
            run_control.retire_execution_owner(execution)
        for thread in list(run_control._grace_threads.values()):
            thread.join(1)
        run_control._grace_threads.clear()
        run_control._owners.clear()

