"""A steer's outcome is published and readable by its command id; a waiting one can be taken back."""
from __future__ import annotations

import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import openprogram.runtime.execution as execution_module
from openprogram.runtime.execution.attempts import AttemptStore
from openprogram.runtime.execution.model import CapabilitySet, CommandKind, CommandStatus
from openprogram.runtime.execution.event_effects import ExecutionEventEffects
from openprogram.runtime.execution.store import ExecutionStore
from openprogram.runtime.permissions.authority import owner_authority


@pytest.mark.parametrize("kind", ["command.applied", "command.rejected"])
def test_steer_outcome_frame_names_the_command_without_its_payload(monkeypatch, kind):
    frames: list[dict] = []
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)
    command = {
        "command_id": "steer-1", "execution_id": "e1", "kind": "execution.steer",
        "status": kind.split(".")[1], "payload": {"message": "also do X"},
        "actor": {"source": "web"}, "rejection_code": None,
    }

    ExecutionEventEffects._publish_steer_outcome(
        types.SimpleNamespace(kind=kind, payload={"command": command}),
    )

    assert len(frames) == 1
    published = frames[0]["command"]
    assert frames[0]["type"] == "execution.command.updated"
    assert published["command_id"] == "steer-1"
    assert published["status"] == kind.split(".")[1]
    assert "payload" not in published and "actor" not in published


def test_other_command_outcomes_are_not_republished(monkeypatch):
    frames: list[dict] = []
    monkeypatch.setattr("openprogram.runtime.events.emit_ws_frame", frames.append)

    ExecutionEventEffects._publish_steer_outcome(types.SimpleNamespace(
        kind="command.applied",
        payload={"command": {"kind": "execution.cancel", "command_id": "c"}},
    ))
    ExecutionEventEffects._publish_steer_outcome(types.SimpleNamespace(
        kind="command.accepted",
        payload={"command": {"kind": "execution.steer", "command_id": "s"}},
    ))

    assert frames == []


@pytest.fixture
def client(monkeypatch, tmp_path):
    from openprogram.interfaces.server.routes.execution import lifecycle

    store = ExecutionStore(tmp_path / "execution.db")
    revision = store.create_revision(manifest={"entrypoint": "test"})
    execution = store.create_execution(
        execution_id="exec_steer", run_id="run_steer", session_id="s",
        revision_id=revision.revision_id, capabilities=CapabilitySet(pause=True, steer=True),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="test",
    )
    _attempt, execution = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    store.accept_command(
        command_id="steer-read", execution_id=execution.execution_id,
        expected_version=execution.status_version, kind=CommandKind.STEER,
        payload={"message": "also do X"}, actor={"source": "web"},
    )
    store.accept_command(
        command_id="pause-read", execution_id=execution.execution_id,
        expected_version=execution.status_version, kind=CommandKind.PAUSE,
        payload={}, actor={"source": "web"},
    )
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    app = FastAPI()
    app.state.store = store
    app.state.owner_auth = type("OwnerAuth", (), {
        "authority": owner_authority("owner/install/0123456789abcdef"),
    })()
    lifecycle.register(app)
    return TestClient(app)


def test_command_is_readable_by_id(client):
    response = client.get("/api/execution/command/steer-read")

    assert response.status_code == 200
    command = response.json()["command"]
    assert command["command_id"] == "steer-read"
    assert command["kind"] == "execution.steer"
    assert command["status"] == "accepted"


def test_unknown_command_is_not_found(client):
    assert client.get("/api/execution/command/missing").status_code == 404


def test_waiting_steer_is_taken_back_once(client):
    store = client.app.state.store

    response = client.post("/api/execution/command/steer-read/withdraw")

    assert response.status_code == 200
    body = response.json()
    assert body["withdrawn"] is True
    assert body["command"]["status"] == "rejected"
    assert body["command"]["rejection_code"] == "withdrawn"
    assert store.get_command("steer-read").status is CommandStatus.REJECTED
    # Asking again reports the same outcome and writes nothing new.
    events = len(store.list_events("exec_steer"))
    again = client.post("/api/execution/command/steer-read/withdraw").json()
    assert again["withdrawn"] is True
    assert len(store.list_events("exec_steer")) == events


def test_steer_a_safe_point_took_is_not_taken_back(client):
    store = client.app.state.store
    store.transition_command(
        "steer-read", expected_status=CommandStatus.ACCEPTED, target=CommandStatus.APPLYING,
    )

    body = client.post("/api/execution/command/steer-read/withdraw").json()

    assert body["withdrawn"] is False
    assert body["command"]["status"] == "applying"
    assert store.get_command("steer-read").status is CommandStatus.APPLYING


def test_only_a_known_steer_can_be_taken_back(client):
    store = client.app.state.store

    assert client.post("/api/execution/command/missing/withdraw").status_code == 404
    assert client.post("/api/execution/command/pause-read/withdraw").status_code == 404
    assert store.get_command("pause-read").status is CommandStatus.ACCEPTED
    assert store.withdraw_steer("missing") == (None, False)
