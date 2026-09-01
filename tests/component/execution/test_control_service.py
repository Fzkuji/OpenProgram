from __future__ import annotations

import asyncio

import pytest

from openprogram.execution.attempts import AttemptConflict, AttemptStore
from openprogram.execution.checkpoints import CheckpointFragment
from openprogram.execution.control import RuntimeControlService
from openprogram.execution.driver import (
    DriverAck,
    DriverBinding,
    DriverRegistry,
)
from openprogram.execution.effects import (
    EffectClassification,
    EffectStatus,
    EffectStore,
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


def test_retried_pause_command_is_not_redispatched(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    registry = DriverRegistry()
    driver = RecordingDriver(executions)
    _bind(registry, execution, attempt, driver)
    service = RuntimeControlService(executions, attempts, registry)

    first = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )
    retry = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    assert retry.command == first.command
    assert retry.execution == first.execution
    assert not retry.delivered
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
    repeated_pause = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )
    assert repeated_pause.command == paused.command
    assert repeated_pause.execution == paused.execution
    assert not repeated_pause.delivered

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
    repeated_cancel = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=paused.execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )
    assert repeated_cancel.command == cancelled.command
    assert repeated_cancel.execution == cancelled.execution
    assert not repeated_cancel.delivered


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
    repeated_pause = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )
    assert repeated_pause.command == pause_command
    assert repeated_pause.execution == cancelling.execution
    assert not repeated_pause.delivered


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


def test_retried_cancel_command_is_not_redispatched(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    registry = DriverRegistry()
    driver = RecordingDriver(executions)
    _bind(registry, execution, attempt, driver)
    service = RuntimeControlService(executions, attempts, registry)

    first = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )
    retry = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    assert retry.command == first.command
    assert retry.execution == first.execution
    assert not retry.delivered
    assert driver.observed == [
        ("cancel", ExecutionStatus.CANCELLING, CommandStatus.APPLYING)
    ]


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


def test_cancel_applies_only_after_the_attempt_finishes(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    registry = DriverRegistry()
    driver = RecordingDriver(executions)
    _bind(registry, execution, attempt, driver)
    service = RuntimeControlService(executions, attempts, registry)
    cancelling = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    completed = service.finish_attempt(
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=cancelling.execution.status_version,
        target=ExecutionStatus.CANCELLED,
        outcome="cooperative_cancel",
        command_id="cancel_1",
        reason_code="user_cancelled",
    )

    assert completed.execution.status is ExecutionStatus.CANCELLED
    assert completed.command is not None
    assert completed.command.status is CommandStatus.APPLIED
    assert completed.attempt.outcome == "cooperative_cancel"
    assert registry.snapshot() == ()


def test_uncertain_effect_requires_reconciliation_before_cancel_finishes(
    tmp_path,
) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    effects = EffectStore(executions)
    planned = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    effects.mark_dispatched(planned.effect_id, expected_status=planned.status)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    cancelling = asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    awaiting = service.finish_attempt(
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=cancelling.execution.status_version,
        target=ExecutionStatus.CANCELLED,
        outcome="cooperative_cancel",
        command_id="cancel_1",
        reason_code="user_cancelled",
    )
    assert awaiting.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert awaiting.command is not None
    assert awaiting.command.status is CommandStatus.APPLYING

    reconciled = service.resolve_effect(
        effect_id="effect_1",
        expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED,
        receipt={"provider_message_id": "message_1"},
    )
    assert reconciled.execution.status is ExecutionStatus.CANCELLED
    assert reconciled.command is not None
    assert reconciled.command.status is CommandStatus.APPLIED


def test_pause_command_is_applied_after_effect_reconciliation(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    effects = EffectStore(executions)
    planned = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    effects.mark_dispatched(planned.effect_id, expected_status=planned.status)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    awaiting = service.finish_attempt(
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=pausing.execution.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
        command_id="pause_1",
    )
    assert awaiting.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert awaiting.command is not None
    assert awaiting.command.status is CommandStatus.APPLYING

    reconciled = service.resolve_effect(
        effect_id="effect_1",
        expected_status=EffectStatus.DISPATCHED,
        outcome=EffectStatus.COMMITTED,
        receipt={"provider_message_id": "message_1"},
    )
    assert reconciled.execution.status is ExecutionStatus.PAUSED
    assert reconciled.command is not None
    assert reconciled.command.command_id == "pause_1"
    assert reconciled.command.status is CommandStatus.APPLIED


def test_owner_loss_interrupts_running_attempt_and_is_idempotent(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    recovered = service.recover_owner_loss(execution.execution_id)

    assert recovered.execution.status is ExecutionStatus.INTERRUPTED
    assert recovered.execution.current_attempt_id is None
    assert recovered.attempt is not None
    assert recovered.attempt.status.value == "ended"
    assert recovered.attempt.outcome == "owner_lost"
    assert recovered.command is None

    repeated = service.recover_owner_loss(execution.execution_id)
    assert repeated.execution == recovered.execution
    assert attempts.get(attempt.attempt_id) == recovered.attempt


def test_owner_loss_fences_the_recovered_attempt_owner(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    recovered = service.recover_owner_loss(execution.execution_id)
    assert recovered.attempt is not None
    assert recovered.attempt.lease_expires_at == 0

    with pytest.raises(AttemptConflict) as heartbeat:
        attempts.heartbeat(
            attempt.attempt_id,
            generation=attempt.generation,
            ttl_seconds=30,
        )
    assert heartbeat.value.code == "stale_owner"

    with pytest.raises(AttemptConflict) as finish:
        service.finish_attempt(
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            expected_execution_version=execution.status_version,
            target=ExecutionStatus.COMPLETED,
            outcome="completed",
        )
    assert finish.value.code == "stale_owner"
    assert executions.get_execution(execution.execution_id) == recovered.execution
    assert attempts.get(attempt.attempt_id) == recovered.attempt


def test_owner_loss_pausing_with_checkpoint_applies_pause(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    _, checkpointed = service.checkpoints.publish(
        execution.execution_id,
        expected_version=execution.status_version,
        revision_id=execution.revision_id,
        parent_checkpoint_id=None,
        frontier=({"kind": "action.after", "step_id": "tool_1"},),
        state_refs={"conversation": "blob:conversation-1"},
        completed_actions=(),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id=attempt.attempt_id,
    )
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=checkpointed.status_version,
            actor={"surface": "test"},
        )
    )

    recovered = service.recover_owner_loss(execution.execution_id)

    assert pausing.execution.status is ExecutionStatus.PAUSING
    assert recovered.execution.status is ExecutionStatus.PAUSED
    assert recovered.execution.current_attempt_id is None
    assert recovered.command is not None
    assert recovered.command.status is CommandStatus.APPLIED
    assert recovered.attempt is not None
    assert recovered.attempt.outcome == "owner_lost_after_checkpoint"


def test_owner_loss_pausing_with_checkpoint_and_unresolved_effect_requires_reconciliation(
    tmp_path,
) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    _, checkpointed = service.checkpoints.publish(
        execution.execution_id,
        expected_version=execution.status_version,
        revision_id=execution.revision_id,
        parent_checkpoint_id=None,
        frontier=({"kind": "action.after", "step_id": "tool_1"},),
        state_refs={"conversation": "blob:conversation-1"},
        completed_actions=(),
        effect_receipts=(),
        child_frontier={},
        pending_command_ids=(),
        created_by_attempt_id=attempt.attempt_id,
    )
    effects = EffectStore(executions)
    effect = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    effects.mark_dispatched(effect.effect_id, expected_status=effect.status)
    pausing = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=checkpointed.status_version,
            actor={"surface": "test"},
        )
    )

    recovered = service.recover_owner_loss(execution.execution_id)

    assert pausing.execution.status is ExecutionStatus.PAUSING
    assert recovered.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert recovered.command is not None
    assert recovered.command.status is CommandStatus.APPLYING


def test_owner_loss_pausing_without_checkpoint_rejects_pause(tmp_path) -> None:
    executions, attempts, execution, _ = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    recovered = service.recover_owner_loss(execution.execution_id)

    assert recovered.execution.status is ExecutionStatus.INTERRUPTED
    assert recovered.command is not None
    assert recovered.command.status is CommandStatus.REJECTED
    assert recovered.command.rejection_code == "owner_lost_before_checkpoint"

    executions, attempts, execution, attempt = _execution(
        tmp_path / "uncertain", active=True
    )
    effects = EffectStore(executions)
    effect = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    effects.mark_dispatched(effect.effect_id, expected_status=effect.status)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )

    reconciling = service.recover_owner_loss(execution.execution_id)

    assert reconciling.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert reconciling.command is not None
    assert reconciling.command.status is CommandStatus.APPLYING


def test_owner_loss_cancelling_finishes_or_requires_reconciliation(tmp_path) -> None:
    executions, attempts, execution, attempt = _execution(tmp_path, active=True)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    cancelled = service.recover_owner_loss(execution.execution_id)

    assert cancelled.execution.status is ExecutionStatus.CANCELLED
    assert cancelled.command is not None
    assert cancelled.command.status is CommandStatus.APPLIED

    executions, attempts, execution, attempt = _execution(
        tmp_path / "uncertain", active=True
    )
    effects = EffectStore(executions)
    effect = effects.register(
        effect_id="effect_1",
        execution_id=execution.execution_id,
        attempt_id=attempt.attempt_id,
        action_id="send_message",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={},
    )
    effects.mark_dispatched(effect.effect_id, expected_status=effect.status)
    service = RuntimeControlService(executions, attempts, DriverRegistry())
    asyncio.run(
        service.request_cancel(
            command_id="cancel_1",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
            reason_code="user_cancelled",
        )
    )

    reconciling = service.recover_owner_loss(execution.execution_id)

    assert reconciling.execution.status is ExecutionStatus.RECONCILIATION_REQUIRED
    assert reconciling.command is not None
    assert reconciling.command.status is CommandStatus.APPLYING

    repeated = service.recover_owner_loss(execution.execution_id)
    assert repeated.execution == reconciling.execution


def test_owner_loss_leaves_non_owner_states_unchanged(tmp_path) -> None:
    executions, attempts, queued, _ = _execution(tmp_path, active=False)
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    recovered = service.recover_owner_loss(queued.execution_id)

    assert recovered.execution == queued
    assert recovered.attempt is None
    assert recovered.command is None

    paused = asyncio.run(
        service.request_pause(
            command_id="pause_1",
            execution_id=queued.execution_id,
            expected_version=queued.status_version,
            actor={"surface": "test"},
        )
    )
    repeated = service.recover_owner_loss(queued.execution_id)

    assert repeated.execution == paused.execution
    assert repeated.attempt is None
    assert repeated.command is None


def test_startup_recovery_scans_nonterminal_executions(tmp_path) -> None:
    executions, attempts, running, _ = _execution(tmp_path, active=True)
    queued = executions.create_execution(
        execution_id="exec_queued",
        run_id="run_queued",
        session_id="session_1",
        revision_id=running.revision_id,
    )
    service = RuntimeControlService(executions, attempts, DriverRegistry())

    recovered = service.recover_startup()

    assert [item.execution.execution_id for item in recovered] == [
        running.execution_id
    ]
    assert recovered[0].execution.status is ExecutionStatus.INTERRUPTED
    assert executions.get_execution(queued.execution_id) == queued
