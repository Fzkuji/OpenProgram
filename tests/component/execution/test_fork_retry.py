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
)
from openprogram.execution.effects import EffectClassification, EffectStatus
from openprogram.execution.model import CommandStatus
from openprogram.execution._schema import initialize_schema
from openprogram.execution.store import ExecutionConflict, ExecutionStore


def _source(
    tmp_path,
    *,
    status: ExecutionStatus = ExecutionStatus.PAUSED,
    frontier=True,
    unresolved: EffectStatus | None = None,
):
    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "workflow"})
    source = store.create_execution(
        execution_id="source",
        run_id="run",
        session_id="session",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(fork=True, retry=True, pause=True, safe_point_kinds=("after",)),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(source.execution_id, expected_version=1, owner_id="owner", ttl_seconds=30)
    active, running = attempts.activate(leased.attempt_id, generation=1, expected_execution_version=reserved.status_version)
    service = RuntimeControlService(store, attempts, DriverRegistry())
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
                completed_frontier=((({"step_id": "first", "contract_hash": "h"},)) if frontier else None),
                state_refs={},
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
            completed_frontier=((({"step_id": "first", "contract_hash": "h"},)) if frontier else None),
            state_refs={}, completed_actions=(), effect_receipts=(), child_frontier={},
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
    result = service.request_fork(
        command_id="fork",
        execution_id=source.execution_id,
        expected_version=source.status_version,
        actor={"subject": "owner"},
        checkpoint_id=checkpoint.checkpoint_id,
        revision_manifest={"entrypoint": "edited"},
        compatible_prefix=[{"step_id": "first", "contract_hash": "h"}],
    )
    assert result.command.result_json["child_execution_id"] == result.child.execution_id
    assert result.child.status is ExecutionStatus.QUEUED
    assert result.child.parent_execution_id == source.execution_id
    assert result.child.source_checkpoint_id == checkpoint.checkpoint_id
    assert result.child.checkpoint_head_id is None
    assert result.child.capabilities == source.capabilities
    assert result.execution == source


@pytest.mark.parametrize("bad_prefix", [
    [{"step_id": "first", "contract_hash": "h"}, {"step_id": "first", "contract_hash": "h2"}],
    [{"step_id": "z", "contract_hash": "h"}, {"step_id": "a", "contract_hash": "h"}],
    [{"step_id": "first", "contract_hash": "different"}],
])
def test_fork_rejects_incomplete_duplicate_unsorted_or_mismatched_prefix(tmp_path, bad_prefix):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    with pytest.raises(ExecutionConflict):
        service.request_fork(
            command_id="fork",
            execution_id=source.execution_id,
            expected_version=source.status_version,
            actor={}, checkpoint_id=checkpoint.checkpoint_id,
            revision_manifest={"entrypoint": "edited"}, compatible_prefix=bad_prefix,
        )
    assert store.get_command("fork") is None


def test_fork_requires_nonlegacy_completed_frontier_and_own_checkpoint(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path, frontier=False)
    with pytest.raises(ExecutionConflict) as missing:
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={},
            checkpoint_id=checkpoint.checkpoint_id, revision_manifest={"x": 1},
            compatible_prefix=[],
        )
    assert missing.value.code == "checkpoint_frontier_required"


def test_retry_allows_legacy_frontier_and_uses_same_revision(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path, status=ExecutionStatus.FAILED, frontier=False)
    result = service.request_retry(
        command_id="retry", execution_id=source.execution_id,
        expected_version=source.status_version, actor={},
    )
    assert result.revision == store.get_revision(source.revision_id)
    assert result.child.revision_id == source.revision_id
    assert result.child.source_checkpoint_id == checkpoint.checkpoint_id


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
        tmp_path, status=ExecutionStatus.FAILED, frontier=False, unresolved=effect_status
    )
    with pytest.raises(ExecutionConflict) as fork:
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={},
            checkpoint_id=checkpoint.checkpoint_id, revision_manifest={"x": 1},
            compatible_prefix=[],
        )
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
    branch = service.request_fork(
        command_id="fork", execution_id=source.execution_id,
        expected_version=source.status_version, actor={},
        checkpoint_id=checkpoint.checkpoint_id, revision_manifest={"x": 1},
        compatible_prefix=[{"step_id": "first", "contract_hash": "h"}],
    )
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


def test_v2_to_v3_migration_preserves_legacy_rows_and_revision_identity(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    initialize_schema(connection)
    connection.execute("ALTER TABLE executions DROP COLUMN source_checkpoint_id")
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
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 3
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


def test_branch_command_is_idempotent_but_distinct_commands_create_distinct_children(tmp_path):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    args = dict(execution_id=source.execution_id, expected_version=source.status_version, actor={}, checkpoint_id=checkpoint.checkpoint_id, revision_manifest={"x": 1}, compatible_prefix=[{"step_id": "first", "contract_hash": "h"}])
    one = service.request_fork(command_id="one", **args)
    repeat = service.request_fork(command_id="one", **args)
    two = service.request_fork(command_id="two", **args)
    assert repeat.child == one.child
    assert two.child.execution_id != one.child.execution_id


def test_fork_transaction_rolls_back_command_revision_and_child(tmp_path, monkeypatch):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    with closing(store._connect()) as connection:
        before_revisions = {
            row[0] for row in connection.execute("SELECT revision_id FROM revisions")
        }
    original = store._create_revision_in_transaction
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fault")
    monkeypatch.setattr(store, "_create_revision_in_transaction", fail)
    with pytest.raises(RuntimeError):
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={}, checkpoint_id=checkpoint.checkpoint_id,
            revision_manifest={"x": 1}, compatible_prefix=[{"step_id": "first", "contract_hash": "h"}],
        )
    assert store.get_command("fork") is None
    assert store.get_execution(source.execution_id) == source
    with sqlite3.connect(store.path) as connection:
        assert {row[0] for row in connection.execute("SELECT revision_id FROM revisions")} == before_revisions
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_fork_rolls_back_when_child_insert_fails(tmp_path, monkeypatch):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    original = store._insert_execution
    def fail_child(connection, record):
        if record.execution_id != source.execution_id:
            raise RuntimeError("fault")
        return original(connection, record)
    monkeypatch.setattr(store, "_insert_execution", fail_child)
    with pytest.raises(RuntimeError):
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={}, checkpoint_id=checkpoint.checkpoint_id,
            revision_manifest={"x": 1}, compatible_prefix=[{"step_id": "first", "contract_hash": "h"}],
        )
    assert store.get_command("fork") is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_fork_rolls_back_when_final_command_transition_fails(tmp_path, monkeypatch):
    store, attempts, service, source, checkpoint = _source(tmp_path)
    original = store._transition_command
    def fail_final(connection, command_id, **kwargs):
        if command_id == "fork" and kwargs.get("target") is CommandStatus.APPLIED:
            raise RuntimeError("fault")
        return original(connection, command_id, **kwargs)
    monkeypatch.setattr(store, "_transition_command", fail_final)
    with pytest.raises(RuntimeError):
        service.request_fork(
            command_id="fork", execution_id=source.execution_id,
            expected_version=source.status_version, actor={}, checkpoint_id=checkpoint.checkpoint_id,
            revision_manifest={"x": 1}, compatible_prefix=[{"step_id": "first", "contract_hash": "h"}],
        )
    assert store.get_command("fork") is None
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1
