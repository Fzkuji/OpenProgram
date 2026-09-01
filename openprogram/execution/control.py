"""Durable-intent-first coordination for execution control commands."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping

from openprogram.paths import get_active_profile

from .attempts import AttemptConflict, AttemptRecord, AttemptStore
from .checkpoints import (
    CheckpointFragment,
    CheckpointManifest,
    ExecutionCheckpointStore,
)
from .driver import DriverAck, DriverRegistry, DriverRegistryConflict
from .effects import EffectRecord, EffectStatus, EffectStore
from .model import (
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionRecord,
    ExecutionStatus,
    TERMINAL_EXECUTION_STATUSES,
)
from .store import ExecutionConflict, ExecutionStore, default_store


_default_control_services: dict[str, RuntimeControlService] = {}
_default_control_services_lock = RLock()


def default_control_service() -> RuntimeControlService:
    """Return the canonical control service for the active profile."""
    profile = get_active_profile() or "default"
    with _default_control_services_lock:
        service = _default_control_services.get(profile)
        if service is None:
            executions = default_store()
            service = RuntimeControlService(
                executions,
                AttemptStore(executions),
                DriverRegistry(),
            )
            _default_control_services[profile] = service
        return service


_CANCEL_SUPERSEDES = (
    CommandKind.PAUSE,
    CommandKind.CONTINUE,
    CommandKind.STEP,
    CommandKind.STEER,
    CommandKind.FORK,
    CommandKind.RETRY,
)


@dataclass(frozen=True)
class ControlDispatch:
    command: ControlCommand
    execution: ExecutionRecord
    delivered: bool
    ack: DriverAck | None = None
    issue_code: str | None = None


@dataclass(frozen=True)
class SafePointCompletion:
    command: ControlCommand
    execution: ExecutionRecord
    attempt: AttemptRecord
    checkpoint: CheckpointManifest


@dataclass(frozen=True)
class AttemptCompletion:
    execution: ExecutionRecord
    attempt: AttemptRecord
    command: ControlCommand | None = None


@dataclass(frozen=True)
class ReconciliationCompletion:
    effect: EffectRecord
    execution: ExecutionRecord
    command: ControlCommand | None = None


@dataclass(frozen=True)
class RecoveryCompletion:
    execution: ExecutionRecord
    attempt: AttemptRecord | None = None
    command: ControlCommand | None = None


class RuntimeControlService:
    """The sole coordinator for canonical commands and live driver signals."""

    def __init__(
        self,
        executions: ExecutionStore,
        attempts: AttemptStore,
        registry: DriverRegistry,
    ) -> None:
        self.executions = executions
        self.attempts = attempts
        self.registry = registry
        self.effects = EffectStore(executions)
        self.checkpoints = ExecutionCheckpointStore(executions)

    async def request_pause(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
    ) -> ControlDispatch:
        current = self.executions.get_execution(execution_id)
        if current is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        immediate = (
            current.status is ExecutionStatus.QUEUED
            and current.current_attempt_id is None
        )
        command, execution, duplicate = self.executions.accept_command_with_transition(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=CommandKind.PAUSE,
            target=(ExecutionStatus.PAUSED if immediate else ExecutionStatus.PAUSING),
            payload={},
            actor=actor,
        )
        if duplicate:
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        if execution.status is ExecutionStatus.PAUSED:
            command = self._mark_applied(command, execution)
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        return await self._dispatch(command, execution, operation="pause")

    async def request_cancel(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
        reason_code: str,
    ) -> ControlDispatch:
        command, execution, duplicate = self.executions.accept_command_with_transition(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=CommandKind.CANCEL,
            target=ExecutionStatus.CANCELLING,
            payload={"reason_code": reason_code},
            actor=actor,
            reason_code=reason_code,
            supersede_kinds=_CANCEL_SUPERSEDES,
            supersede_code="superseded_by_cancel",
        )
        if duplicate:
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        if execution.current_attempt_id is None and not self.effects.list_unresolved(
            execution.execution_id
        ):
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.CANCELLED,
                reason_code=reason_code,
            )
            command = self._mark_applied(command, execution)
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        return await self._dispatch(command, execution, operation="cancel")

    def arrive_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        if attempt.generation != generation:
            raise AttemptConflict(
                "stale_generation",
                f"expected attempt generation {generation}, found {attempt.generation}",
            )
        execution = self.executions.get_execution(attempt.execution_id)
        if execution is None:
            raise AttemptConflict("execution_not_found", "attempt execution is missing")
        command = self.executions.get_command(command_id)
        if (
            command is None
            or command.execution_id != execution.execution_id
            or command.kind is not CommandKind.PAUSE
            or command.status is not CommandStatus.APPLYING
        ):
            raise AttemptConflict(
                "command_mismatch",
                "safe point does not match an applying pause command",
            )
        if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
            raise AttemptConflict(
                "unsupported_safe_point",
                "driver reported a safe point not declared by the execution",
            )
        pending = tuple(
            dict.fromkeys((*fragment.pending_command_ids, command.command_id))
        )
        checkpoint, checkpointed = self.checkpoints.publish(
            execution.execution_id,
            expected_version=expected_execution_version,
            revision_id=execution.revision_id,
            parent_checkpoint_id=execution.checkpoint_head_id,
            frontier=fragment.frontier,
            state_refs=fragment.state_refs,
            completed_actions=fragment.completed_actions,
            effect_receipts=fragment.effect_receipts,
            child_frontier=fragment.child_frontier,
            pending_command_ids=pending,
            created_by_attempt_id=attempt_id,
        )
        ended, paused = self.attempts.finish(
            attempt_id,
            generation=generation,
            expected_execution_version=checkpointed.status_version,
            target=ExecutionStatus.PAUSED,
            outcome="paused_at_safe_point",
        )
        command = self._mark_applied(command, paused)
        self.registry.unbind(
            paused.execution_id,
            attempt_id=attempt_id,
            generation=generation,
        )
        return SafePointCompletion(
            command=command,
            execution=paused,
            attempt=ended,
            checkpoint=checkpoint,
        )

    def finish_attempt(
        self,
        *,
        attempt_id: str,
        generation: int,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        command_id: str | None = None,
        reason_code: str | None = None,
    ) -> AttemptCompletion:
        if target not in TERMINAL_EXECUTION_STATUSES:
            raise AttemptConflict(
                "invalid_outcome",
                "finish_attempt requires a terminal execution target",
            )
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        command = self.executions.get_command(command_id) if command_id else None
        if command_id is not None and (
            command is None
            or command.execution_id != attempt.execution_id
            or command.status is not CommandStatus.APPLYING
        ):
            raise AttemptConflict(
                "command_mismatch",
                "attempt outcome does not match an applying command",
            )
        unresolved = self.effects.list_unresolved(attempt.execution_id)
        actual_target = (
            ExecutionStatus.RECONCILIATION_REQUIRED if unresolved else target
        )
        ended, execution = self.attempts.finish(
            attempt_id,
            generation=generation,
            expected_execution_version=expected_execution_version,
            target=actual_target,
            outcome=("reconciliation_required" if unresolved else outcome),
            reason_code=("effect_reconciliation" if unresolved else reason_code),
        )
        self.registry.unbind(
            execution.execution_id,
            attempt_id=attempt_id,
            generation=generation,
        )
        if command is not None and execution.status in TERMINAL_EXECUTION_STATUSES:
            command = self._mark_applied(command, execution)
        return AttemptCompletion(
            execution=execution,
            attempt=ended,
            command=command,
        )

    def recover_owner_loss(self, execution_id: str) -> RecoveryCompletion:
        """Durably finalize work whose physical owner is known to be gone."""
        with self.executions._transaction() as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if execution.status not in {
                ExecutionStatus.QUEUED,
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.PAUSED,
                ExecutionStatus.CANCELLING,
            }:
                return RecoveryCompletion(execution=execution)
            if (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is None
            ):
                return RecoveryCompletion(execution=execution)

            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution_id,),
            ).fetchone() is not None
            command_kind: CommandKind | None = None
            apply_command = False
            reject_command = False
            if execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}:
                target = execution.status
                reason_code = "owner_lost_before_activation"
                outcome = "owner_lost_before_activation"
            elif execution.status is ExecutionStatus.RUNNING:
                target = (
                    ExecutionStatus.RECONCILIATION_REQUIRED
                    if unresolved
                    else ExecutionStatus.INTERRUPTED
                )
                reason_code = "effect_reconciliation" if unresolved else "owner_lost"
                outcome = "reconciliation_required" if unresolved else "owner_lost"
            elif execution.status is ExecutionStatus.PAUSING:
                command_kind = CommandKind.PAUSE
                if unresolved:
                    target = ExecutionStatus.RECONCILIATION_REQUIRED
                    reason_code = "effect_reconciliation"
                    outcome = "reconciliation_required"
                elif execution.checkpoint_head_id is not None:
                    target = ExecutionStatus.PAUSED
                    reason_code = "owner_lost_after_checkpoint"
                    outcome = "owner_lost_after_checkpoint"
                    apply_command = True
                else:
                    target = ExecutionStatus.INTERRUPTED
                    reason_code = "owner_lost_before_checkpoint"
                    outcome = "owner_lost_before_checkpoint"
                    reject_command = True
            else:
                command_kind = CommandKind.CANCEL
                if unresolved:
                    target = ExecutionStatus.RECONCILIATION_REQUIRED
                    reason_code = "effect_reconciliation"
                    outcome = "reconciliation_required"
                else:
                    target = ExecutionStatus.CANCELLED
                    reason_code = execution.reason_code or "owner_lost"
                    outcome = "owner_lost_during_cancel"
                    apply_command = True

            recovered = self.executions._transition_execution(
                connection,
                execution_id,
                expected_version=execution.status_version,
                target=target,
                reason_code=reason_code,
                clear_owner=True,
            )
            attempt = None
            if execution.current_attempt_id is not None:
                attempt = self.attempts._require(
                    connection, execution.current_attempt_id
                )
                if attempt.execution_id != execution_id:
                    raise AttemptConflict(
                        "attempt_mismatch",
                        "execution current attempt belongs to a different execution",
                    )
                attempt = self.attempts._end_for_owner_loss(
                    connection, attempt, outcome=outcome
                )
                self.executions._append_event(
                    connection,
                    execution_id=execution_id,
                    execution_version=recovered.status_version,
                    kind="attempt.ended",
                    payload={"attempt": attempt.to_dict()},
                    created_at=attempt.updated_at,
                )

            command = self._applying_command(
                connection, execution_id, command_kind
            )
            if command is not None and apply_command:
                command = self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=recovered.status_version,
                )
            elif command is not None and reject_command:
                command = self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=recovered.status_version,
                    rejection_code="owner_lost_before_checkpoint",
                )

        if attempt is not None:
            self.registry.unbind(
                execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        return RecoveryCompletion(
            execution=recovered,
            attempt=attempt,
            command=command,
        )

    def recover_startup(self) -> tuple[RecoveryCompletion, ...]:
        """Recover nonterminal executions whose previous owner is gone."""
        recoveries = []
        for execution in self.executions.list_nonterminal():
            if execution.status in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.CANCELLING,
            } or (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is not None
            ):
                recoveries.append(self.recover_owner_loss(execution.execution_id))
        return tuple(recoveries)

    def resolve_effect(
        self,
        *,
        effect_id: str,
        expected_status: EffectStatus,
        outcome: EffectStatus,
        receipt: Mapping[str, Any],
    ) -> ReconciliationCompletion:
        effect = self.effects.resolve(
            effect_id,
            expected_status=expected_status,
            outcome=outcome,
            receipt=receipt,
        )
        execution = self.executions.get_execution(effect.execution_id)
        if execution is None:
            raise AttemptConflict("execution_not_found", "effect execution is missing")
        if (
            execution.status is not ExecutionStatus.RECONCILIATION_REQUIRED
            or self.effects.list_unresolved(execution.execution_id)
        ):
            return ReconciliationCompletion(effect=effect, execution=execution)
        commands = self.executions.list_commands(
            execution.execution_id,
            statuses=(CommandStatus.APPLYING,),
            kinds=(CommandKind.CANCEL, CommandKind.PAUSE),
        )
        command = commands[0] if commands else None
        if command is None or command.kind is CommandKind.PAUSE:
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.PAUSED,
                reason_code="effects_reconciled",
            )
            if command is not None:
                command = self._mark_applied(command, execution)
        else:
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.CANCELLING,
                reason_code="effects_reconciled",
            )
            execution = self.executions.transition_execution(
                execution.execution_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.CANCELLED,
                reason_code="cancelled_after_reconciliation",
            )
            command = self._mark_applied(command, execution)
        return ReconciliationCompletion(
            effect=effect,
            execution=execution,
            command=command,
        )

    def _mark_applied(
        self,
        command: ControlCommand,
        execution: ExecutionRecord,
    ) -> ControlCommand:
        if command.status is CommandStatus.APPLIED:
            return command
        return self.executions.transition_command(
            command.command_id,
            expected_status=CommandStatus.APPLYING,
            target=CommandStatus.APPLIED,
            result_version=execution.status_version,
        )

    def _applying_command(
        self,
        connection,
        execution_id: str,
        kind: CommandKind | None,
    ) -> ControlCommand | None:
        if kind is None:
            return None
        row = connection.execute(
            "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
            "AND status = ? ORDER BY submitted_at, command_id LIMIT 1",
            (execution_id, kind.value, CommandStatus.APPLYING.value),
        ).fetchone()
        return self.executions._command(row) if row is not None else None

    async def _dispatch(
        self,
        command: ControlCommand,
        execution: ExecutionRecord,
        *,
        operation: str,
    ) -> ControlDispatch:
        attempt_id = execution.current_attempt_id
        generation = execution.owner_lease.get("generation")
        if attempt_id is None or not isinstance(generation, int):
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="owner_not_active",
            )
        try:
            binding = self.registry.resolve(
                execution.execution_id,
                attempt_id=attempt_id,
                generation=generation,
            )
        except DriverRegistryConflict as exc:
            issue = "owner_not_local" if exc.code == "not_found" else exc.code
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code=issue,
            )
        try:
            if operation == "pause":
                ack = await binding.driver.request_pause(
                    binding.handle, command.command_id
                )
            else:
                ack = await binding.driver.request_cancel(
                    binding.handle, command.command_id
                )
        except Exception:
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="driver_error",
            )
        if ack.command_id != command.command_id or ack.attempt_id != attempt_id:
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="invalid_ack",
            )
        return ControlDispatch(
            command=command,
            execution=execution,
            delivered=True,
            ack=ack,
        )
