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

