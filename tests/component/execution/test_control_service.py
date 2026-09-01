from __future__ import annotations

import asyncio

from openprogram.execution.attempts import AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import (
    DriverAck,
    DriverBinding,
    DriverRegistry,
)
from openprogram.execution.model import (
    CapabilitySet,
    CommandStatus,
    ExecutionStatus,
)
from openprogram.execution.store import ExecutionStore


class RecordingDriver:
    def __init__(self, executions: ExecutionStore, *, fail: bool = False):
        self.executions = executions
        self.fail = fail
        self.observed: list[tuple[str, ExecutionStatus, CommandStatus]] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(pause=True)

    async def request_pause(self, handle, command_id: str) -> DriverAck:
        execution = self.executions.get_execution(handle["execution_id"])
        command = self.executions.get_command(command_id)
        assert execution is not None and command is not None
        self.observed.append(("pause", execution.status, command.status))
        if self.fail:
            raise RuntimeError("driver unavailable")
        return DriverAck(command_id=command_id, attempt_id=handle["attempt_id"])

    async def request_cancel(self, handle, command_id: str) -> DriverAck:
        execution = self.executions.get_execution(handle["execution_id"])
        command = self.executions.get_command(command_id)
        assert execution is not None and command is not None
        self.observed.append(("cancel", execution.status, command.status))
        if self.fail:
            raise RuntimeError("driver unavailable")
        return DriverAck(command_id=command_id, attempt_id=handle["attempt_id"])


def _execution(tmp_path, *, active: bool):
    executions = ExecutionStore(tmp_path / "executions.db")
    revision = executions.create_revision(manifest={"entrypoint": "chat"})
    execution = executions.create_execution(
        execution_id="exec_1",
        run_id="run_1",
        session_id="session_1",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("action.after",),
            state_schema_version=1,
        ),
    )
    if not active:
        return executions, AttemptStore(executions), execution, None
    attempts = AttemptStore(executions)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="worker_1",
        ttl_seconds=30,
        attempt_id="attempt_1",
    )
    active_attempt, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return executions, attempts, running, active_attempt


def _bind(registry, execution, attempt, driver) -> None:
    registry.bind(
        DriverBinding(
            execution_id=execution.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=driver,
            handle={
                "execution_id": execution.execution_id,
                "attempt_id": attempt.attempt_id,
            },
        )
    )


def test_pause_intent_is_durable_before_driver_notification(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    registry = DriverRegistry()
    driver = RecordingDriver(executions)
    _bind(registry, execution, attempt, driver)
    service = RuntimeControlService(executions, attempts, registry)

    result = asyncio.run(
        service.request_pause(
            command_id="command_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    assert result.execution.status is ExecutionStatus.PAUSING
    assert result.command.status is CommandStatus.APPLYING
    assert result.delivered
    assert result.issue_code is None
    assert driver.observed == [
        ("pause", ExecutionStatus.PAUSING, CommandStatus.APPLYING)
    ]


def test_pause_without_local_owner_remains_durable_and_recoverable(tmp_path) -> None:
    executions, attempts, execution, _ = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    result = asyncio.run(
        service.request_pause(
            command_id="command_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    assert result.execution.status is ExecutionStatus.PAUSING
    assert result.command.status is CommandStatus.APPLYING
    assert not result.delivered
    assert result.issue_code == "owner_not_local"


def test_queued_pause_and_cancel_finish_without_a_live_driver(tmp_path) -> None:
    executions, attempts, execution, _ = _execution(tmp_path, active=False)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    paused = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )
    assert paused.execution.status is ExecutionStatus.PAUSED
    assert paused.command.status is CommandStatus.APPLIED

    cancelled = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=paused.execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )
    assert cancelled.execution.status is ExecutionStatus.CANCELLED
    assert cancelled.command.status is CommandStatus.APPLIED


def test_cancel_supersedes_an_applying_pause(tmp_path) -> None:
    executions, attempts, execution, _ = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )
    cancelling = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=pausing.execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    pause_command = executions.get_command("pause_1")
    assert pause_command is not None
    assert pause_command.status is CommandStatus.REJECTED
    assert pause_command.rejection_code == "superseded_by_cancel"
    assert cancelling.execution.status is ExecutionStatus.CANCELLING
    assert cancelling.command.status is CommandStatus.APPLYING


def test_driver_failure_never_reverses_a_committed_cancel_intent(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    registry = DriverRegistry()
    driver = RecordingDriver(executions, fail=True)
    _bind(registry, execution, attempt, driver)
    service = RuntimeControlService(executions, attempts, registry)

    result = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    assert result.execution.status is ExecutionStatus.CANCELLING
    assert result.command.status is CommandStatus.APPLYING
    assert not result.delivered
    assert result.issue_code == "driver_error"
    assert executions.get_execution(execution.execution_id) == result.execution


def test_pause_completes_only_after_checkpoint_and_attempt_finalization(
    tmp_path,
) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    registry = DriverRegistry()
    driver = RecordingDriver(executions)
    _bind(registry, execution, attempt, driver)
    service = RuntimeControlService(executions, attempts, registry)
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    completed = service.arrive_safe_point(
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        command_id="pause_1",
        expected_execution_version=pausing.execution.status_version,
        fragment=CheckpointFragment(
            safe_point_kind="action.after",
            frontier=({"kind": "action.after", "step_id": "tool_1"},),
            state_refs={"conversation": "blob:conversation-1"},
        ),
    )

    assert completed.execution.status is ExecutionStatus.PAUSED
    assert completed.execution.current_attempt_id is None
    assert completed.execution.checkpoint_head_id == completed.checkpoint.checkpoint_id
    assert completed.command.status is CommandStatus.APPLIED
    assert completed.attempt.outcome == "paused_at_safe_point"
    assert not registry.unbind(
        execution.execution_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
    )
