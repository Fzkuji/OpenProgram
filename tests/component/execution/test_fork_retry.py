from __future__ import annotations

import pytest

from openprogram.execution import (
    AttemptStore,
    CapabilitySet,
    CheckpointFragment,
    DriverRegistry,
    ExecutionStatus,
    RuntimeControlService,
)
from openprogram.execution.store import ExecutionConflict, ExecutionStore


def _source(tmp_path, *, status: ExecutionStatus = ExecutionStatus.PAUSED, frontier=True):
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
    original = store._create_revision_in_transaction
    def fail(*args, **kwargs):
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
    assert store.get_revision("rev_" + "0" * 32) is None
