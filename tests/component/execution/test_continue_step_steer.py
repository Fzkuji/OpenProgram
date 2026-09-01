from __future__ import annotations

import asyncio

from openprogram.execution.attempts import AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet, CommandStatus, ExecutionStatus
from openprogram.execution.store import ExecutionStore


def _paused(tmp_path):
    store = ExecutionStore(tmp_path / "execution.db")
    revision = store.create_revision(manifest={"entrypoint": "fake"})
    execution = store.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            steer=True,
            safe_point_kinds=("action.after", "control.step"),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker",
        ttl_seconds=30,
        attempt_id="attempt_1",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    checkpoints = RuntimeControlService(store, attempts, DriverRegistry()).checkpoints
    checkpoint, running = checkpoints.publish(
        running.execution_id,
        expected_version=running.status_version,
        revision_id=running.revision_id,
        parent_checkpoint_id=None,
        frontier=({"step_id": "start", "phase": "after"},),
        state_refs={"program": {"cursor": 0}},
        completed_actions=(),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id=active.attempt_id,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=running.execution_id,
            expected_version=running.status_version,
            actor={"surface": "test"},
        )
    )
    paused = service.arrive_safe_point(
        attempt_id=active.attempt_id,
        generation=active.generation,
        command_id="pause_1",
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"step_id": "start", "phase": "after"},),
            state_refs={"program": {"cursor": 0}},
        ),
    ).execution
    assert paused.status is ExecutionStatus.PAUSED
    assert paused.checkpoint_head_id is not None
    return store, attempts, service, paused


def test_continue_reuses_execution_and_creates_one_new_attempt(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    result = asyncio.run(
        service.request_continue(
            command_id="continue_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    assert result.execution.status is ExecutionStatus.RUNNING
    assert result.command.status is CommandStatus.APPLIED
    assert result.execution.revision_id == paused.revision_id
    assert result.execution.current_attempt_id is not None
    assert len(store.list_commands(paused.execution_id)) == 2
    current = attempts.get(result.execution.current_attempt_id)
    assert current is not None and current.generation == 2


def test_step_is_one_permit_and_atomically_pauses_with_receipt(tmp_path):
    store, attempts, service, paused = _paused(tmp_path)
    steer = service.request_steer(
        command_id="steer_1",
        execution_id=paused.execution_id,
        expected_version=paused.status_version,
        actor={"surface": "test"},
        payload={"message": "use source A"},
    )
    assert steer.command.status is CommandStatus.ACCEPTED
    result = asyncio.run(
        service.request_step(
            command_id="step_1",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    assert result.command.status is CommandStatus.APPLYING
    completed = service.arrive_step_safe_point(
        attempt_id=result.execution.current_attempt_id,
        generation=result.execution.owner_lease["generation"],
        command_id="step_1",
        expected_execution_version=result.execution.status_version,
        safe_point_kind="control.step",
        frontier=({"step_id": "next", "phase": "after"},),
        state_refs={"program": {"cursor": 1}},
        managed_action={"action_id": "action_1"},
    )
    assert completed.execution.status is ExecutionStatus.PAUSED
    assert completed.attempt.status.value == "ended"
    assert completed.command.status is CommandStatus.APPLIED
    events = store.list_events(paused.execution_id)
    applied = [event for event in events if event.kind == "command.applied"]
    receipt = next(event.payload["receipt"] for event in applied if event.command_id == "step_1")
    assert receipt["checkpoint_id"] == completed.checkpoint.checkpoint_id
    assert receipt["safe_point"]["step_id"] == "next"
    assert store.get_command("steer_1").status is CommandStatus.APPLIED
    assert completed.checkpoint.state_refs["steering"][0]["payload"]["message"] == "use source A"
