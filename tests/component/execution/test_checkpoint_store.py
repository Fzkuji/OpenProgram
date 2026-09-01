from __future__ import annotations

import time

import pytest

from openprogram.execution.attempts import AttemptStore
from openprogram.execution.checkpoints import (
    CheckpointConflict,
    ExecutionCheckpointStore,
)
from openprogram.execution.effects import EffectClassification, EffectStore
from openprogram.execution.model import CapabilitySet
from openprogram.execution.store import ExecutionStore


def _execution(tmp_path):
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "workflow"})
    execution = executions.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            fork=True,
            safe_point_kinds=("workflow.step.after",),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=30,
        attempt_id="attempt_1",
    )
    _, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return executions, running


def test_checkpoint_publish_is_content_addressed_and_updates_head_atomically(
    tmp_path,
) -> None:
    executions, execution = _execution(tmp_path)
    checkpoints = ExecutionCheckpointStore(executions)

    checkpoint, updated = checkpoints.publish(
        execution.execution_id,
        expected_version=execution.status_version,
        revision_id=execution.revision_id,
        parent_checkpoint_id=None,
        frontier=({"step_id": "collect", "phase": "after", "branch_id": "root"},),
        state_refs={"program": "blob:program-1"},
        completed_actions=(
            {"action_id": "action_1", "input_hash": "abc", "result_ref": "blob:r1"},
        ),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id="attempt_1",
    )

    assert checkpoint.checkpoint_id.startswith("ckpt_")
    assert updated.checkpoint_head_id == checkpoint.checkpoint_id
    assert updated.status_version == execution.status_version + 1
    assert updated.safe_point["step_id"] == "collect"
    assert checkpoints.get(checkpoint.checkpoint_id) == checkpoint
    assert executions.rebuild_execution(execution.execution_id) == updated


def test_checkpoint_publish_is_idempotent_for_identical_content(tmp_path) -> None:
    executions, execution = _execution(tmp_path)
    checkpoints = ExecutionCheckpointStore(executions)
    values = {
        "execution_id": execution.execution_id,
        "expected_version": execution.status_version,
        "revision_id": execution.revision_id,
        "parent_checkpoint_id": None,
        "frontier": ({"step_id": "start", "phase": "after"},),
        "state_refs": {},
        "completed_actions": (),
        "effect_receipts": (),
        "child_frontier": {},
        "pending_command_ids": (),
        "created_by_attempt_id": "attempt_1",
    }
    first, updated = checkpoints.publish(**values)
    second, duplicate_execution = checkpoints.publish(**values)
    assert second == first
    assert duplicate_execution == updated


def test_checkpoint_requires_current_parent_revision_and_version(tmp_path) -> None:
    executions, execution = _execution(tmp_path)
    checkpoints = ExecutionCheckpointStore(executions)
    first, updated = checkpoints.publish(
        execution.execution_id,
        expected_version=execution.status_version,
        revision_id=execution.revision_id,
        parent_checkpoint_id=None,
        frontier=({"step_id": "start", "phase": "after"},),
        state_refs={},
        completed_actions=(),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id="attempt_1",
    )

    with pytest.raises(CheckpointConflict) as stale:
        checkpoints.publish(
            execution.execution_id,
            expected_version=execution.status_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=first.checkpoint_id,
            frontier=({"step_id": "next", "phase": "after"},),
            state_refs={},
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id="attempt_1",
        )
    assert stale.value.code == "stale_version"

    with pytest.raises(CheckpointConflict) as wrong_parent:
        checkpoints.publish(
            execution.execution_id,
            expected_version=updated.status_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=None,
            frontier=({"step_id": "next", "phase": "after"},),
            state_refs={},
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id="attempt_1",
        )
    assert wrong_parent.value.code == "parent_mismatch"

    with pytest.raises(CheckpointConflict) as wrong_revision:
        checkpoints.publish(
            execution.execution_id,
            expected_version=updated.status_version,
            revision_id="rev_wrong",
            parent_checkpoint_id=first.checkpoint_id,
            frontier=({"step_id": "next", "phase": "after"},),
            state_refs={},
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id="attempt_1",
        )
    assert wrong_revision.value.code == "revision_mismatch"


def test_checkpoint_refuses_unresolved_external_effect(tmp_path) -> None:
    executions, execution = _execution(tmp_path)
    effects = EffectStore(executions)
    planned = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id="attempt_1",
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    effects.mark_dispatched(planned.effect_id, expected_status=planned.status)

    with pytest.raises(CheckpointConflict) as unresolved:
        ExecutionCheckpointStore(executions).publish(
            execution.execution_id,
            expected_version=execution.status_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=None,
            frontier=({"step_id": "send", "phase": "after"},),
            state_refs={},
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id="attempt_1",
        )
    assert unresolved.value.code == "unresolved_effect"


def test_expired_attempt_cannot_publish_checkpoint(tmp_path) -> None:
    executions, execution = _execution(tmp_path)
    checkpoints = ExecutionCheckpointStore(
        executions,
        clock=lambda: time.time() + 60,
    )

    with pytest.raises(CheckpointConflict) as stale:
        checkpoints.publish(
            execution.execution_id,
            expected_version=execution.status_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=None,
            frontier=({"step_id": "start", "phase": "after"},),
            state_refs={},
            completed_actions=(),
            effect_receipts=(),
            child_frontier={},
            pending_command_ids=(),
            created_by_attempt_id="attempt_1",
        )
    assert stale.value.code == "stale_attempt"
