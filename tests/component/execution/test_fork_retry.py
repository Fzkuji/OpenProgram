from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from openprogram.execution import (
    AttemptStore,
    CapabilitySet,
    CheckpointFragment,
    DriverRegistry,
    ExecutionStatus,
    RuntimeControlService,
    RevisionControlService,
)
from openprogram.execution.effects import EffectClassification, EffectStatus
from openprogram.execution.model import CommandStatus
from openprogram.execution._schema import initialize_schema
from openprogram.execution._schema import SCHEMA_VERSION
from openprogram.execution.store import ExecutionConflict, ExecutionStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _frontier() -> dict[str, object]:
    return {
        "step_id": "first", "action_id": "action:first",
        "contract_hash": _hash("contract:first"), "branch_path": ["root"],
        "input_schema_hash": _hash("input:first"), "output_schema_hash": _hash("output:first"),
        "dependency_hash": _hash("dependency:first"), "effect_contract_hash": _hash("effect:first"),
    }


def _published_manifest(store, source, checkpoint, frontier):
    revisions = RevisionControlService(store)
    artifact = revisions.put_artifact(
        kind="program_artifact",
        content={"entrypoint": "workflow.fork", "source_hash": _hash("fork")},
    )
    draft = revisions.create_draft(
        project_binding={"project_id": "project", "worktree_id": "worktree", "root_identity": "root", "source_commit": _hash("commit")},
        source_execution_id=source.execution_id, base_revision_id=source.revision_id,
        source_checkpoint_id=checkpoint.checkpoint_id,
        changes=({"kind": "program_artifact", "target": "workflow", "before_hash": _hash("before"), "after_ref": artifact.artifact_ref, "rationale": "fork"},),
        frontier_mapping=({"old_step_id": "first", "new_step_id": "first", "relation": "preserved", "old_contract_hash": frontier["contract_hash"], "new_contract_hash": frontier["contract_hash"]},),
        requested_by={"subject": "author"},
    )
    validation = revisions.validate_draft(draft_id=draft.draft_id, expected_draft_version=draft.draft_version)
    approval = revisions.approve_draft(draft_id=draft.draft_id, expected_draft_version=draft.draft_version, validation_id=validation.validation_id, actor={"subject": "reviewer"}, policy_version="policy-v1")
    return revisions.publish_draft(draft_id=draft.draft_id, expected_draft_version=draft.draft_version, validation_id=validation.validation_id, approval_id=approval.approval_id, actor={"subject": "publisher"})


def _fork(service, store, source, checkpoint, frontier, *, command_id="fork"):
    manifest = _published_manifest(store, source, checkpoint, frontier)
    return service.request_fork(
        command_id=command_id, execution_id=source.execution_id,
        expected_version=source.status_version, actor={"subject": "owner"},
        manifest_id=manifest.manifest_id, checkpoint_id=checkpoint.checkpoint_id,
        proof_hash=manifest.proof_hash,
    )


def _source(
    tmp_path,
    *,
    status: ExecutionStatus = ExecutionStatus.PAUSED,
    frontier=True,
    unresolved: EffectStatus | None = None,
):
    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "workflow"})
    source = store.admit_execution(
        execution_id="source",
        run_id="run",
        session_id="session",
        revision_id=revision.revision_id,
        input_ref="blob:source-input",
        input_hash="source-input-hash",
        entrypoint="workflow",
        trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:source-config",
        user_message_id="msg-user",
        assistant_message_id="msg-assistant",
        capabilities=CapabilitySet(fork=True, retry=True, pause=True, safe_point_kinds=("after",)),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(source.execution_id, expected_version=1, owner_id="owner", ttl_seconds=30)
    active, running = attempts.activate(leased.attempt_id, generation=1, expected_execution_version=reserved.status_version)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    completed = (_frontier(),) if frontier else None
    receipt_ref = store.put_state_blob(source.execution_id, '{"receipt":"first"}')["ref"]
    receipts = ({
        "effect_id": "effect-first", "frontier_step_id": "first", "action_id": "action:first",
        "outcome": "committed", "receipt_ref": receipt_ref,
    },)
    if status is ExecutionStatus.PAUSED:
        paused = __import__("asyncio").run(service.request_pause(
            command_id="pause", execution_id=source.execution_id,
            expected_version=running.status_version, actor={},
        ))
        completion = service.arrive_safe_point(
            attempt_id=active.attempt_id, generation=1, command_id="pause",
            expected_execution_version=paused.execution.status_version,
            fragment=CheckpointFragment(
                safe_point_kind="after", frontier=({"step_id": "first"},),
                completed_frontier=completed,
                state_refs={},
                effect_receipts=receipts,
            ),
        )
        checkpoint, source = completion.checkpoint, completion.execution
    else:
        checkpoint, running = service.checkpoints.publish(
            source.execution_id,
            expected_version=running.status_version,
            revision_id=source.revision_id,
            parent_checkpoint_id=None,
            frontier=({"step_id": "first"},),
            completed_frontier=completed,
            state_refs={}, completed_actions=(), effect_receipts=receipts, child_frontier={},
            pending_command_ids=(), created_by_attempt_id=active.attempt_id,
        )
        if unresolved is not None:
            effect = service.effects.register(
                effect_id="unresolved",
                execution_id=source.execution_id,
                attempt_id=active.attempt_id,
                action_id="send",
                classification=EffectClassification.NONREPEATABLE,
                idempotency_key=None,
                metadata={},
            )
            effect = service.effects.mark_dispatched(
                effect.effect_id, expected_status=EffectStatus.PLANNED
            )
            if unresolved is EffectStatus.UNCERTAIN:
                service.effects.mark_uncertain(
                    effect.effect_id, expected_status=EffectStatus.DISPATCHED
                )
        ended, source = attempts.finish(
            active.attempt_id, generation=1, expected_execution_version=running.status_version,
            target=status, outcome=status.value,
        )
    return store, attempts, service, source, checkpoint


def test_fork_validates_prefix_and_persists_a_queued_child(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    result = _fork(service, store, source, checkpoint, _frontier())
    assert result.command.result_json["child_execution_id"] == result.child.execution_id
    assert result.child.status is ExecutionStatus.QUEUED
    assert result.child.parent_execution_id == source.execution_id
    assert result.child.source_checkpoint_id == checkpoint.checkpoint_id
    assert result.child.checkpoint_head_id is None
    assert result.child.capabilities == source.capabilities
    assert result.execution == source


def test_fork_requires_published_manifest_and_structured_frontier(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path, frontier=False)
    with pytest.raises(ExecutionConflict) as missing:
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={"subject": "owner"},
            checkpoint_id=checkpoint.checkpoint_id, manifest_id="manifest_missing",
            proof_hash=_hash("proof"),
        )
    assert missing.value.code == "revision_manifest_not_found"


def test_retry_allows_legacy_frontier_and_uses_same_revision(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path, status=ExecutionStatus.FAILED, frontier=False)
    result = service.request_retry(
        command_id="retry", execution_id=source.execution_id,
        expected_version=source.status_version, actor={},
    )
    assert result.revision == store.get_revision(source.revision_id)
    assert result.child.revision_id == source.revision_id
    assert result.child.source_checkpoint_id == checkpoint.checkpoint_id


@pytest.mark.parametrize("branch_kind", ["fork", "retry"])
def test_branch_children_inherit_immutable_input_for_ui_projection(tmp_path, branch_kind):
    from openprogram.execution.outbox import ProjectionDispatcher
    from openprogram.execution.projections import (
        ExecutionProjectionReadModel,
        projection_handlers,
    )

    status = ExecutionStatus.PAUSED if branch_kind == "fork" else ExecutionStatus.FAILED
    store, _attempts, service, source, checkpoint = _source(
        tmp_path, status=status, frontier=branch_kind == "fork"
    )
    if branch_kind == "fork":
        result = _fork(service, store, source, checkpoint, _frontier(), command_id="fork-input")
    else:
        result = service.request_retry(
            command_id="retry-input",
            execution_id=source.execution_id,
            expected_version=source.status_version,
            actor={},
        )

    source_input = store.get_execution_input(source.execution_id)
    child_input = store.get_execution_input(result.child.execution_id)
    assert source_input is not None
    assert child_input is not None
    assert child_input.execution_id == result.child.execution_id
    assert child_input.input_ref == source_input.input_ref
    assert child_input.user_message_id == "msg-user"
    assert child_input.assistant_message_id == "msg-assistant"

    dispatcher = ProjectionDispatcher(store, projection_handlers(store))
    dispatcher.drain(owner_id="projection-worker")
    projection = ExecutionProjectionReadModel(store).get_current(
        "ui", result.child.execution_id
    )
    assert projection is not None
    assert projection.payload["input"] == {
        "entrypoint": "workflow",
        "user_message_id": "msg-user",
        "assistant_message_id": "msg-assistant",
    }


def test_retry_command_idempotency_returns_the_same_child(tmp_path):
    store, attempts, service, source, checkpoint = _source(
        tmp_path, status=ExecutionStatus.FAILED, frontier=False
    )
    first = service.request_retry(
        command_id="retry", execution_id=source.execution_id,
        expected_version=source.status_version, actor={},
    )
    second = service.request_retry(
        command_id="retry", execution_id=source.execution_id,
        expected_version=source.status_version, actor={},
    )
    assert second.child == first.child
    assert second.command.result_json == first.command.result_json


@pytest.mark.parametrize("effect_status", [EffectStatus.DISPATCHED, EffectStatus.UNCERTAIN])
def test_fork_and_retry_reject_unresolved_effects(tmp_path, effect_status):
    store, attempts, service, source, checkpoint = _source(
        tmp_path, status=ExecutionStatus.FAILED, frontier=True, unresolved=effect_status
    )
    with pytest.raises(ExecutionConflict) as fork:
        _published_manifest(store, source, checkpoint, _frontier())
    assert fork.value.code == "unresolved_effect"
    with pytest.raises(ExecutionConflict) as retry:
        service.request_retry(
            command_id="retry", execution_id=source.execution_id,
            expected_version=source.status_version, actor={},
        )
    assert retry.value.code == "unresolved_effect"
    assert store.get_command("fork") is None
    assert store.get_command("retry") is None


def test_first_child_activation_uses_source_checkpoint_and_starts_a_new_chain(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    branch = _fork(service, store, source, checkpoint, _frontier())
    leased, reserved = attempts.lease(
        branch.child.execution_id, expected_version=branch.child.status_version,
        owner_id="child-owner", ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=1,
        expected_execution_version=reserved.status_version,
    )
    seen = []
    delivered, issue = asyncio.run(service._activate(
        active, None, (), activator=lambda _attempt, activation: seen.append(activation)
    ))
    assert delivered and issue is None
    assert seen[0].checkpoint.checkpoint_id == checkpoint.checkpoint_id
    child_checkpoint, child_running = service.checkpoints.publish(
        branch.child.execution_id, expected_version=running.status_version,
        revision_id=branch.child.revision_id, parent_checkpoint_id=None,
        frontier=({"step_id": "child"},),
        completed_frontier=({"step_id": "child", "contract_hash": "child-h"},),
        state_refs={}, completed_actions=(), effect_receipts=(), child_frontier={},
        pending_command_ids=(), created_by_attempt_id=active.attempt_id,
    )
    assert child_checkpoint.parent_checkpoint_id is None
    assert child_running.source_checkpoint_id == checkpoint.checkpoint_id
    assert child_running.checkpoint_head_id == child_checkpoint.checkpoint_id


def test_v2_to_v4_migration_preserves_legacy_rows_and_revision_identity(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    initialize_schema(connection)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("DROP INDEX executions_session_status")
    connection.execute("DROP INDEX executions_run_parent")
    connection.execute(
        """
        CREATE TABLE executions_v2 (
            execution_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            parent_execution_id TEXT,
            revision_id TEXT NOT NULL,
            status TEXT NOT NULL,
            status_version INTEGER NOT NULL,
            reason_code TEXT,
            current_attempt_id TEXT,
            owner_lease_json TEXT NOT NULL,
            checkpoint_head_id TEXT,
            safe_point_json TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            effect_summary_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            terminal_at REAL
        )
        """
    )
    connection.execute(
        "INSERT INTO executions_v2 SELECT execution_id, run_id, session_id, "
        "parent_execution_id, revision_id, status, status_version, reason_code, "
        "current_attempt_id, owner_lease_json, checkpoint_head_id, "
        "safe_point_json, capabilities_json, effect_summary_json, created_at, "
        "updated_at, terminal_at FROM executions"
    )
    connection.execute("DROP TABLE executions")
    connection.execute("ALTER TABLE executions_v2 RENAME TO executions")
    connection.execute("ALTER TABLE commands DROP COLUMN result_json")
    connection.execute("ALTER TABLE checkpoints DROP COLUMN completed_frontier_json")
    manifest = {"entrypoint": "legacy"}
    legacy_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    connection.execute(
        "INSERT INTO revisions VALUES (?, ?, ?, ?, ?)",
        ("legacy-revision", None, legacy_hash, json.dumps(manifest), 1.0),
    )
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()

    store = ExecutionStore(path)
    legacy = store.get_revision("legacy-revision")
    assert legacy is not None and legacy.content_hash == legacy_hash
    with sqlite3.connect(path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        table_sql = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'executions'"
        ).fetchone()[0]
        assert "CHECK(parent_execution_id IS NOT NULL OR source_checkpoint_id IS NULL)" in table_sql
        assert "REFERENCES checkpoints(checkpoint_id)" in table_sql
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute(
                "INSERT INTO executions ("
                "execution_id, run_id, session_id, parent_execution_id, "
                "source_checkpoint_id, revision_id, status, status_version, "
                "owner_lease_json, safe_point_json, capabilities_json, "
                "effect_summary_json, created_at, updated_at) VALUES ("
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "invalid-root",
                    "run-invalid",
                    "session-invalid",
                    None,
                    "checkpoint-invalid",
                    "revision-invalid",
                    "queued",
                    1,
                    "{}",
                    "{}",
                    "{}",
                    "{}",
                    1.0,
                    1.0,
                ),
            )
        names = {row[1] for row in migrated.execute("PRAGMA table_info(executions)")}
        assert "source_checkpoint_id" in names
        assert any(
            row[2] == "checkpoints" and row[4] == "checkpoint_id"
            for row in migrated.execute("PRAGMA foreign_key_list(executions)")
        )
        assert "result_json" in {row[1] for row in migrated.execute("PRAGMA table_info(commands)")}
        assert "completed_frontier_json" in {row[1] for row in migrated.execute("PRAGMA table_info(checkpoints)")}
    reused = store.create_revision(manifest=manifest, parent_revision_id=None)
    assert reused.revision_id == "legacy-revision"
    assert reused.content_hash == legacy_hash


def test_v3_migration_rebuilds_source_checkpoint_foreign_key(tmp_path):
    path = tmp_path / "v3.db"
    connection = sqlite3.connect(path)
    initialize_schema(connection)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("DROP INDEX executions_session_status")
    connection.execute("DROP INDEX executions_run_parent")
    connection.execute(
        """
        CREATE TABLE executions_v3 (
            execution_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            parent_execution_id TEXT,
            source_checkpoint_id TEXT,
            revision_id TEXT NOT NULL,
            status TEXT NOT NULL,
            status_version INTEGER NOT NULL,
            reason_code TEXT,
            current_attempt_id TEXT,
            owner_lease_json TEXT NOT NULL,
            checkpoint_head_id TEXT,
            safe_point_json TEXT NOT NULL,
            capabilities_json TEXT NOT NULL,
            effect_summary_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            terminal_at REAL
        )
        """
    )
    connection.execute(
        "INSERT INTO executions_v3 SELECT * FROM executions"
    )
    connection.execute("DROP TABLE executions")
    connection.execute("ALTER TABLE executions_v3 RENAME TO executions")
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()

    ExecutionStore(path)

    with sqlite3.connect(path) as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        foreign_keys = migrated.execute(
            "PRAGMA foreign_key_list(executions)"
        ).fetchall()
        assert any(
            row[2] == "checkpoints"
            and row[3] == "source_checkpoint_id"
            and row[4] == "checkpoint_id"
            for row in foreign_keys
        )
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []


def test_branch_command_is_idempotent_but_distinct_commands_create_distinct_children(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    manifest = _published_manifest(store, source, checkpoint, _frontier())
    args = dict(execution_id=source.execution_id, expected_version=source.status_version, actor={"subject": "owner"}, checkpoint_id=checkpoint.checkpoint_id, manifest_id=manifest.manifest_id, proof_hash=manifest.proof_hash)
    one = service.request_fork(command_id="one", **args)
    repeat = service.request_fork(command_id="one", **args)
    two = service.request_fork(command_id="two", **args)
    assert repeat.child == one.child
    assert two.child.execution_id != one.child.execution_id


def test_fork_transaction_rolls_back_command_and_child(tmp_path, monkeypatch):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    manifest = _published_manifest(store, source, checkpoint, _frontier())
    original = store._insert_execution
    def fail(connection, record):
        if record.execution_id != source.execution_id:
            raise RuntimeError("fault")
        return original(connection, record)
    monkeypatch.setattr(store, "_insert_execution", fail)
    with pytest.raises(RuntimeError):
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={"subject": "owner"}, checkpoint_id=checkpoint.checkpoint_id,
            manifest_id=manifest.manifest_id, proof_hash=manifest.proof_hash,
        )
    assert store.get_command("fork") is None
    assert store.get_execution(source.execution_id) == source
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_fork_rolls_back_when_child_insert_fails(tmp_path, monkeypatch):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    manifest = _published_manifest(store, source, checkpoint, _frontier())
    original = store._insert_execution
    def fail_child(connection, record):
        if record.execution_id != source.execution_id:
            raise RuntimeError("fault")
        return original(connection, record)
    monkeypatch.setattr(store, "_insert_execution", fail_child)
    with pytest.raises(RuntimeError):
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={"subject": "owner"}, checkpoint_id=checkpoint.checkpoint_id,
            manifest_id=manifest.manifest_id, proof_hash=manifest.proof_hash,
        )
    assert store.get_command("fork") is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_fork_rolls_back_when_final_command_transition_fails(tmp_path, monkeypatch):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    manifest = _published_manifest(store, source, checkpoint, _frontier())
    original = store._transition_command
    def fail_final(connection, command_id, **kwargs):
        if command_id == "fork" and kwargs.get("target") is CommandStatus.APPLIED:
            raise RuntimeError("fault")
        return original(connection, command_id, **kwargs)
    monkeypatch.setattr(store, "_transition_command", fail_final)
    with pytest.raises(RuntimeError):
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={"subject": "owner"}, checkpoint_id=checkpoint.checkpoint_id,
            manifest_id=manifest.manifest_id, proof_hash=manifest.proof_hash,
        )
    assert store.get_command("fork") is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
