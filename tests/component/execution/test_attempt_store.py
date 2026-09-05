from __future__ import annotations

import pytest

from openprogram.execution.attempts import (
    AttemptConflict,
    AttemptStatus,
    AttemptStore,
)
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.execution.store import ExecutionStore


class Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _execution(tmp_path):
    execution_store = ExecutionStore(tmp_path / "executions.db")
    revision = execution_store.create_revision(manifest={"entrypoint": "chat"})
    execution = execution_store.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("action.before", "action.after"),
            state_schema_version=1,
        ),
    )
    return execution_store, execution


def test_attempt_lease_and_activation_are_fenced_and_update_execution(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    clock = Clock()
    attempts = AttemptStore(execution_store, clock=clock)

    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=30,
        attempt_id="attempt_1",
    )
    assert leased.status is AttemptStatus.LEASED
    assert leased.generation == 1
    assert leased.lease_expires_at == 130
    assert reserved.status is ExecutionStatus.QUEUED
    assert reserved.current_attempt_id == leased.attempt_id
    assert reserved.owner_lease["generation"] == 1

    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    assert active.status is AttemptStatus.ACTIVE
    assert running.status is ExecutionStatus.RUNNING
    assert running.current_attempt_id == active.attempt_id


def test_attempt_heartbeat_extends_only_the_attempt_lease(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    clock = Clock()
    attempts = AttemptStore(execution_store, clock=clock)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=10,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    version_before = running.status_version

    clock.now = 105
    renewed = attempts.heartbeat(
        active.attempt_id,
        generation=active.generation,
        ttl_seconds=20,
    )
    assert renewed.lease_expires_at == 125
    assert (
        execution_store.get_execution(running.execution_id).status_version
        == version_before
    )


def test_expired_or_stale_attempt_cannot_activate_or_heartbeat(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    clock = Clock()
    attempts = AttemptStore(execution_store, clock=clock)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=5,
    )

    clock.now = 106
    with pytest.raises(AttemptConflict) as expired:
        attempts.activate(
            leased.attempt_id,
            generation=leased.generation,
            expected_execution_version=reserved.status_version,
        )
    assert expired.value.code == "lease_expired"

    with pytest.raises(AttemptConflict) as stale:
        attempts.heartbeat(
            leased.attempt_id,
            generation=leased.generation + 1,
            ttl_seconds=5,
        )
    assert stale.value.code == "stale_generation"


def test_expired_active_attempt_cannot_heartbeat_or_finish(tmp_path) -> None:
    execution_store, execution = _execution(tmp_path)
    clock = Clock()
    attempts = AttemptStore(execution_store, clock=clock)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=5,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    clock.now = 106
    before_execution = execution_store.get_execution(execution.execution_id)

    with pytest.raises(AttemptConflict) as heartbeat:
        attempts.heartbeat(
            active.attempt_id,
            generation=active.generation,
            ttl_seconds=5,
        )
    assert heartbeat.value.code == "lease_expired"

    with pytest.raises(AttemptConflict) as finish:
        attempts.finish(
            active.attempt_id,
            generation=active.generation,
            expected_execution_version=running.status_version,
            target=ExecutionStatus.COMPLETED,
            outcome="result_committed",
        )
    assert finish.value.code == "lease_expired"

    after_execution = execution_store.get_execution(execution.execution_id)
    after_attempt = attempts.get(active.attempt_id)
    assert after_execution == before_execution
    assert after_execution is not None
    assert after_execution.current_attempt_id == active.attempt_id
    assert after_attempt is not None
    assert after_attempt.status is AttemptStatus.ACTIVE
    assert after_attempt.lease_expires_at == 105


def test_finish_attempt_changes_execution_and_clears_owner_lease_atomically(
    tmp_path,
) -> None:
    execution_store, execution = _execution(tmp_path)
    attempts = AttemptStore(execution_store, clock=Clock())
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )

    ended, completed = attempts.finish(
        active.attempt_id,
        generation=active.generation,
        expected_execution_version=running.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="result_committed",
    )
    assert ended.status is AttemptStatus.ENDED
    assert ended.outcome == "result_committed"
    assert completed.status is ExecutionStatus.COMPLETED
    assert completed.current_attempt_id is None
    assert completed.owner_lease == {}

    with pytest.raises(AttemptConflict) as terminal:
        attempts.heartbeat(
            ended.attempt_id,
            generation=ended.generation,
            ttl_seconds=10,
        )
    assert terminal.value.code == "terminal"

    with pytest.raises(AttemptConflict) as repeated_finish:
        attempts.finish(
            ended.attempt_id,
            generation=ended.generation,
            expected_execution_version=completed.status_version,
            target=ExecutionStatus.COMPLETED,
            outcome="result_committed",
        )
    assert repeated_finish.value.code == "terminal"
