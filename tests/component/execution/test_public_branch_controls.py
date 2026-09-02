from __future__ import annotations

import asyncio
import hashlib
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
    contract_hash = hashlib.sha256(b"contract:first").hexdigest()
    receipt_ref = store.put_state_blob(
        execution.execution_id,
        '{"receipt":"first"}',
    )["ref"]
    completion = service.arrive_safe_point(
        attempt_id=active.attempt_id, generation=1, command_id="pause",
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="after", frontier=({"step_id": "first"},),
            completed_frontier=({
                "step_id": "first",
                "action_id": "action:first",
                "contract_hash": contract_hash,
                "branch_path": ["root"],
                "input_schema_hash": hashlib.sha256(b"input:first").hexdigest(),
                "output_schema_hash": hashlib.sha256(b"output:first").hexdigest(),
                "dependency_hash": hashlib.sha256(b"dependency:first").hexdigest(),
                "effect_contract_hash": hashlib.sha256(b"effect:first").hexdigest(),
            },),
            state_refs={},
            effect_receipts=({
                "effect_id": "effect-first",
                "frontier_step_id": "first",
                "action_id": "action:first",
                "outcome": "committed",
                "receipt_ref": receipt_ref,
            },),
        ),
    )
    return store, service, completion.execution, completion.checkpoint


def _fork_payload(store, source, checkpoint) -> dict[str, str]:
    from openprogram.execution import RevisionControlService

    revisions = RevisionControlService(store)
    artifact = revisions.put_artifact(
        kind="program_artifact",
        content={
            "entrypoint": "workflow.edited",
            "source_hash": hashlib.sha256(b"edited").hexdigest(),
        },
    )
    frontier = checkpoint.completed_frontier[0]
    draft = revisions.create_draft(
        project_binding={
            "project_id": "default",
            "worktree_id": "worktree-1",
            "root_identity": "root-1",
            "source_commit": hashlib.sha256(b"source").hexdigest(),
        },
        source_execution_id=source.execution_id,
        base_revision_id=source.revision_id,
        source_checkpoint_id=checkpoint.checkpoint_id,
        changes=({
            "kind": "program_artifact",
            "target": "workflow",
            "before_hash": hashlib.sha256(b"before").hexdigest(),
            "after_ref": artifact.artifact_ref,
            "rationale": "use validated workflow",
        },),
        frontier_mapping=({
            "old_step_id": "first",
            "new_step_id": "first",
            "relation": "preserved",
            "old_contract_hash": frontier["contract_hash"],
            "new_contract_hash": frontier["contract_hash"],
        },),
        requested_by={"subject": "author"},
    )
    validation = revisions.validate_draft(
        draft_id=draft.draft_id,
        expected_draft_version=draft.draft_version,
    )
    approval = revisions.approve_draft(
        draft_id=draft.draft_id,
        expected_draft_version=draft.draft_version,
        validation_id=validation.validation_id,
        actor={"subject": "reviewer"},
        policy_version="policy-v1",
    )
    manifest = revisions.publish_draft(
        draft_id=draft.draft_id,
        expected_draft_version=draft.draft_version,
        validation_id=validation.validation_id,
        approval_id=approval.approval_id,
        actor={"subject": "publisher"},
    )
    return {
        "manifest_id": manifest.manifest_id,
        "checkpoint_id": checkpoint.checkpoint_id,
        "proof_hash": manifest.proof_hash,
    }


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
    fork_payload = _fork_payload(store, source, checkpoint)

    command, snapshot = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.fork",
            "command_id": "fork-1", "execution_id": source.execution_id,
            "expected_version": source.status_version,
            "payload": fork_payload,
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

    scoped_out, _latest = asyncio.run(submit_execution_control(
        {
            "type": "execution.command", "action": "execution.retry",
            "command_id": "retry-scoped-out", "execution_id": source.execution_id,
            "expected_version": source.status_version,
            "payload": {},
        },
        "retry",
        actor={**actor, "session_ids": ["another-session"]},
        bound_session=None,
    ))
    assert scoped_out["rejection_code"] == "not_found"


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
    fork_payload = _fork_payload(store, source, checkpoint)

    response = TestClient(app).post("/api/execution/fork", json={
        "type": "execution.command", "action": "execution.fork",
        "command_id": "fork-rest", "execution_id": source.execution_id,
        "expected_version": source.status_version,
        "payload": fork_payload,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["command"]["status"] == "applied"
    assert body["execution"]["execution_id"] == source.execution_id
    assert body["event_cursor"]["execution_id"] == source.execution_id
    assert body["event_cursor"]["next_sequence"] > 0
