from __future__ import annotations

import asyncio
import hashlib
import sqlite3

import pytest

from openprogram.execution import (
    AttemptStore,
    CapabilitySet,
    CheckpointFragment,
    DriverRegistry,
    ExecutionStatus,
    RevisionControlService,
    RuntimeControlService,
)
from openprogram.execution.store import ExecutionConflict, ExecutionStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _binding() -> dict[str, str]:
    return {
        "project_id": "project-a",
        "worktree_id": "worktree-a",
        "root_identity": "root-a",
        "source_commit": _hash("source"),
    }


def _frontier(step_id: str = "collect") -> dict[str, object]:
    return {
        "step_id": step_id,
        "action_id": f"action:{step_id}",
        "contract_hash": _hash(f"contract:{step_id}"),
        "branch_path": ["root"],
        "input_schema_hash": _hash(f"input:{step_id}"),
        "output_schema_hash": _hash(f"output:{step_id}"),
        "dependency_hash": _hash(f"dependency:{step_id}"),
        "effect_contract_hash": _hash(f"effect:{step_id}"),
    }


def _source(tmp_path):
    store = ExecutionStore(tmp_path / "executions.db")
    base = store.create_revision(manifest={"entrypoint": "workflow"})
    source = store.admit_execution(
        execution_id="source",
        run_id="run",
        session_id="session",
        revision_id=base.revision_id,
        input_ref="blob:input",
        input_hash="input-hash",
        entrypoint="workflow",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config",
        capabilities=CapabilitySet(fork=True, pause=True, safe_point_kinds=("after",)),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        source.execution_id, expected_version=source.status_version,
        owner_id="owner", ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=1,
        expected_execution_version=reserved.status_version,
    )
    receipt_ref = store.put_state_blob(source.execution_id, '{"receipt":"collect"}')["ref"]
    control = RuntimeControlService(store, attempts, DriverRegistry())
    paused = asyncio.run(control.request_pause(
        command_id="pause", execution_id=source.execution_id,
        expected_version=running.status_version, actor={"subject": "owner"},
    ))
    frontier = _frontier()
    completion = control.arrive_safe_point(
        attempt_id=active.attempt_id, generation=1, command_id="pause",
        expected_execution_version=paused.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="after", frontier=({"step_id": "collect"},),
            completed_frontier=(frontier,),
            state_refs={},
            effect_receipts=({
                "effect_id": "effect-collect",
                "frontier_step_id": "collect",
                "action_id": "action:collect",
                "outcome": "committed",
                "receipt_ref": receipt_ref,
            },),
        ),
    )
    return store, control, completion.execution, completion.checkpoint, frontier


def _published_manifest(tmp_path):
    store, control, source, checkpoint, frontier = _source(tmp_path)
    revisions = RevisionControlService(store)
    artifact = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.edited", "source_hash": _hash("edited")},
    )
    draft = revisions.create_draft(
        project_binding=_binding(),
        source_execution_id=source.execution_id,
        base_revision_id=source.revision_id,
        source_checkpoint_id=checkpoint.checkpoint_id,
        changes=({
            "kind": "program_artifact",
            "target": "workflow",
            "before_hash": _hash("before"),
            "after_ref": artifact.artifact_ref,
            "rationale": "use edited workflow",
        },),
        frontier_mapping=({
            "old_step_id": "collect",
            "new_step_id": "collect",
            "relation": "preserved",
            "old_contract_hash": frontier["contract_hash"],
            "new_contract_hash": frontier["contract_hash"],
        },),
        requested_by={"subject": "author"},
    )
    validation = revisions.validate_draft(
        draft_id=draft.draft_id, expected_draft_version=draft.draft_version,
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
    return store, control, source, checkpoint, revisions, draft, validation, manifest


def test_revision_draft_publish_and_fork_use_only_bound_manifest(tmp_path):
    _store, control, source, checkpoint, _revisions, _draft, _validation, manifest = _published_manifest(tmp_path)

    result = control.request_fork(
        command_id="fork",
        execution_id=source.execution_id,
        expected_version=source.status_version,
        actor={"subject": "owner"},
        manifest_id=manifest.manifest_id,
        checkpoint_id=checkpoint.checkpoint_id,
        proof_hash=manifest.proof_hash,
    )

    assert result.child.revision_id == manifest.revision_id
    assert result.child.source_checkpoint_id == checkpoint.checkpoint_id
    assert result.command.result_json["manifest_id"] == manifest.manifest_id


def test_draft_rejects_invalid_binding_and_fork_does_not_accept_inline_manifest(tmp_path):
    store, control, source, checkpoint, _frontier = _source(tmp_path)
    revisions = RevisionControlService(store)
    with pytest.raises(ExecutionConflict, match="project binding"):
        revisions.create_draft(
            project_binding={"project_id": "project-a"},
            source_execution_id=source.execution_id,
            base_revision_id=source.revision_id,
            source_checkpoint_id=checkpoint.checkpoint_id,
            changes=(), frontier_mapping=(), requested_by={"subject": "author"},
        )

    with pytest.raises(ExecutionConflict) as rejected:
        control.request_fork(
            command_id="inline",
            execution_id=source.execution_id,
            expected_version=source.status_version,
            actor={"subject": "owner"},
            manifest_id="not-a-manifest",
            checkpoint_id=checkpoint.checkpoint_id,
            proof_hash=_hash("proof"),
        )
    assert rejected.value.code == "revision_manifest_not_found"
    assert store.get_command("inline") is None


def test_changed_completed_step_cannot_publish_from_later_checkpoint(tmp_path):
    store, _control, source, checkpoint, frontier = _source(tmp_path)
    revisions = RevisionControlService(store)
    artifact = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.changed", "source_hash": _hash("changed")},
    )
    draft = revisions.create_draft(
        project_binding=_binding(), source_execution_id=source.execution_id,
        base_revision_id=source.revision_id, source_checkpoint_id=checkpoint.checkpoint_id,
        changes=({
            "kind": "program_artifact", "target": "workflow",
            "before_hash": _hash("before"), "after_ref": artifact.artifact_ref,
            "rationale": "change completed action",
        },),
        frontier_mapping=({
            "old_step_id": "collect", "new_step_id": "collect",
            "relation": "preserved", "old_contract_hash": frontier["contract_hash"],
            "new_contract_hash": _hash("different-contract"),
        },), requested_by={"subject": "author"},
    )

    with pytest.raises(ExecutionConflict) as rejected:
        revisions.validate_draft(
            draft_id=draft.draft_id, expected_draft_version=draft.draft_version,
        )
    assert rejected.value.code == "compatible_checkpoint_required"
    assert store.get_execution("child") is None


def test_draft_revision_invalidates_old_validation_and_authorizes_mutation(tmp_path):
    store, _control, source, checkpoint, frontier = _source(tmp_path)
    revisions = RevisionControlService(store)
    first = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.first", "source_hash": _hash("first")},
    )
    second = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.second", "source_hash": _hash("second")},
    )
    draft = revisions.create_draft(
        project_binding=_binding(), source_execution_id=source.execution_id,
        base_revision_id=source.revision_id, source_checkpoint_id=checkpoint.checkpoint_id,
        changes=({"kind": "program_artifact", "target": "workflow", "before_hash": _hash("before"), "after_ref": first.artifact_ref, "rationale": "first"},),
        frontier_mapping=({"old_step_id": "collect", "new_step_id": "collect", "relation": "preserved", "old_contract_hash": frontier["contract_hash"], "new_contract_hash": frontier["contract_hash"]},),
        requested_by={"subject": "author"},
    )
    validation = revisions.validate_draft(draft_id=draft.draft_id, expected_draft_version=1)
    with pytest.raises(ExecutionConflict) as forbidden:
        revisions.replace_draft(
            draft_id=draft.draft_id, expected_draft_version=1, actor={"subject": "other"},
            changes=draft.changes, frontier_mapping=draft.frontier_mapping,
        )
    assert forbidden.value.code == "draft_authorization_denied"
    changed = revisions.replace_draft(
        draft_id=draft.draft_id, expected_draft_version=1, actor={"subject": "author"},
        changes=({"kind": "program_artifact", "target": "workflow", "before_hash": _hash("before"), "after_ref": second.artifact_ref, "rationale": "second"},),
        frontier_mapping=draft.frontier_mapping,
    )
    with pytest.raises(ExecutionConflict) as stale:
        revisions.approve_draft(
            draft_id=draft.draft_id, expected_draft_version=changed.draft_version,
            validation_id=validation.validation_id, actor={"subject": "reviewer"}, policy_version="policy-v1",
        )
    assert stale.value.code == "validation_stale"


@pytest.mark.parametrize("partial_version", [11, 12, 13])
def test_partial_runtime_store_migrates_all_control_authorities(
    tmp_path, partial_version,
):
    path = tmp_path / "executions.db"
    ExecutionStore(path)
    with sqlite3.connect(path) as connection:
        for table in (
            "execution_waits", "execution_audit_events",
            "revision_manifests", "revision_approvals", "revision_validations",
            "revision_drafts", "revision_artifacts",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(f"PRAGMA user_version = {partial_version}")
    ExecutionStore(path)
    with sqlite3.connect(path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "revision_artifacts", "revision_drafts", "revision_validations",
        "revision_approvals", "revision_manifests", "execution_waits",
        "execution_audit_events",
    } <= names
