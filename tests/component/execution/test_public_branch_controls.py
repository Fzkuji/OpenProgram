from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _paused_source(tmp_path):
    from openprogram.execution import (
        AttemptStore,
        CapabilitySet,
        CheckpointFragment,
        DriverRegistry,
        ExecutionStore,
        RuntimeControlService,
    )

    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "workflow"})
    execution = store.admit_execution(
        execution_id="source", run_id="run", session_id="session-1",
        revision_id=revision.revision_id, input_ref="blob:input",
        input_hash="input-hash", entrypoint="workflow", trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config", user_message_id="user", assistant_message_id="assistant",
        capabilities=CapabilitySet(pause=True, steer=True, fork=True, retry=True, safe_point_kinds=("after",)),
    )
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease(execution.execution_id, expected_version=1, owner_id="owner", ttl_seconds=30)
    active, running = attempts.activate(lease.attempt_id, generation=1, expected_execution_version=reserved.status_version)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    pausing = asyncio.run(service.request_pause(
        command_id="pause", execution_id=execution.execution_id,
        expected_version=running.status_version, actor={"subject": "owner"},
    ))
    completion = service.arrive_safe_point(
        attempt_id=active.attempt_id, generation=1, command_id="pause",
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="after", frontier=({"step_id": "first"},),
            completed_frontier=({"step_id": "first", "contract_hash": "h"},),
            state_refs={},
        ),
    )
    return store, service, completion.execution, completion.checkpoint


def test_public_branch_commands_use_control_service_and_exact_session_scope(tmp_path, monkeypatch) -> None:
    from openprogram.webui.ws_actions.runtime import submit_execution_control

    store, service, source, checkpoint = _paused_source(tmp_path)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.agent.job.runner.runner_for_execution_store", lambda _store: None)
    actor = {
        "speaker_kind": "owner", "speaker_id": "owner/local", "speaker_display": "Owner",
        "authority_tier": "owner", "principal_id": "owner/install/0123456789abcdef",
        "interaction": "interactive",
    }

    command, snapshot = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.fork",
            "command_id": "fork-1", "execution_id": source.execution_id,
            "expected_version": source.status_version,
            "payload": {
                "checkpoint_id": checkpoint.checkpoint_id,
                "revision_manifest": {"entrypoint": "edited"},
                "compatible_prefix": [{"step_id": "first", "contract_hash": "h"}],
            },
        },
        "fork", actor=actor, bound_session="session-1",
    ))
    assert command.status.value == "applied"
    assert command.result_json["child_execution_id"] != source.execution_id
    assert snapshot.execution_id == source.execution_id

    rejected, latest = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.retry",
            "command_id": "retry-1", "execution_id": source.execution_id,
            "expected_version": source.status_version,
            "payload": {},
        },
        "retry", actor=actor, bound_session="another-session",
    ))
    assert rejected["rejection_code"] == "not_found"
    assert latest["execution_id"] == source.execution_id


def test_rest_branch_command_returns_the_canonical_snapshot_and_cursor(tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.webui.routes.lifecycle import register

    store, service, source, checkpoint = _paused_source(tmp_path)
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.agent.job.runner.runner_for_execution_store", lambda _store: None)
    app = FastAPI()
    app.state.owner_auth = SimpleNamespace(authority={
        "speaker_kind": "owner", "speaker_id": "owner/local", "speaker_display": "Owner",
        "authority_tier": "owner", "principal_id": "owner/install/0123456789abcdef",
        "interaction": "interactive",
    })
    register(app)

    response = TestClient(app).post("/api/execution/fork", json={
        "type": "execution.command", "action": "execution.fork",
        "command_id": "fork-rest", "execution_id": source.execution_id,
        "expected_version": source.status_version,
        "payload": {
            "checkpoint_id": checkpoint.checkpoint_id,
            "revision_manifest": {"entrypoint": "rest-edited"},
            "compatible_prefix": [{"step_id": "first", "contract_hash": "h"}],
        },
    })
    assert response.status_code == 200
    body = response.json()
    assert body["command"]["status"] == "applied"
    assert body["execution"]["execution_id"] == source.execution_id
    assert body["event_cursor"]["execution_id"] == source.execution_id
    assert body["event_cursor"]["next_sequence"] > 0
