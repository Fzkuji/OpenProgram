"""Conversation state recovery must not deserialize historical event bodies."""
from __future__ import annotations

import asyncio
import json

from openprogram.runtime.permissions.authority import owner_authority
from openprogram.runtime.execution import CapabilitySet, ExecutionStore


def _admit(tmp_path, monkeypatch):
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    revision = store.create_revision(revision_id="revision", manifest={"entrypoint": "workflow.run"})
    record = store.admit_execution(
        execution_id="execution", run_id="run", session_id="session",
        revision_id=revision.revision_id, input_ref="blob:input", input_hash="hash",
        entrypoint="workflow.run", trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config", capabilities=CapabilitySet(),
    )
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    return store, record


class Socket:
    def __init__(self, session_id=None):
        self.frames = []
        self.scope = {"state": {"authority": owner_authority("owner/install/0123456789abcdef")}}
        if session_id is not None:
            self.scope["state"]["session_id"] = session_id

    async def send_text(self, value):
        self.frames.append(json.loads(value))


def test_snapshot_recovery_never_reads_event_payloads(tmp_path, monkeypatch):
    from openprogram.interfaces.server.ws_actions.runtime import handle_execution_replay

    store, record = _admit(tmp_path, monkeypatch)
    # Decoding an event body would now raise, and the replay would answer with an error.
    with store._transaction() as connection:
        connection.update("execution_events", {"payload_json": "not json"}, execution_id=record.execution_id)
    socket = Socket()
    asyncio.run(handle_execution_replay(socket, {
        "execution_id": record.execution_id, "after_sequence": 0, "snapshot_only": True,
    }))
    frame = socket.frames[-1]
    assert "snapshot" in frame, frame
    assert frame["snapshot"]["status_version"] == record.status_version
    assert frame["event_cursor"]["next_sequence"] == 2
    assert frame["events"] == []


def test_explicit_event_replay_remains_available(tmp_path, monkeypatch):
    from openprogram.interfaces.server.ws_actions.runtime import handle_execution_replay

    _, record = _admit(tmp_path, monkeypatch)
    socket = Socket()
    asyncio.run(handle_execution_replay(socket, {"execution_id": record.execution_id, "after_sequence": 0}))
    assert len(socket.frames[-1]["events"]) == 1
    assert socket.frames[-1]["events"][0]["kind"] == "execution.created"


def test_snapshot_recovery_preserves_session_authorization(tmp_path, monkeypatch):
    from openprogram.interfaces.server.ws_actions.runtime import handle_execution_replay

    _, record = _admit(tmp_path, monkeypatch)
    socket = Socket("another-session")
    asyncio.run(handle_execution_replay(socket, {
        "execution_id": record.execution_id, "after_sequence": 0, "snapshot_only": True,
    }))
    assert socket.frames[-1]["error"] == "not_found"
    assert "snapshot" not in socket.frames[-1]


def test_snapshot_mode_requires_a_boolean(tmp_path, monkeypatch):
    from openprogram.interfaces.server.ws_actions.runtime import handle_execution_replay

    _, record = _admit(tmp_path, monkeypatch)
    for malformed in ("true", 1, None, {}):
        socket = Socket()
        asyncio.run(handle_execution_replay(socket, {
            "execution_id": record.execution_id, "after_sequence": 0, "snapshot_only": malformed,
        }))
        assert socket.frames[-1]["error"] == "invalid_command"
        assert "snapshot" not in socket.frames[-1]
