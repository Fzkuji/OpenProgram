"""Durable-intent-first coordination for execution control commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .attempts import AttemptConflict, AttemptRecord, AttemptStore
from .checkpoints import (
    CheckpointFragment,
    CheckpointManifest,
    ExecutionCheckpointStore,
)
from .driver import DriverAck, DriverRegistry, DriverRegistryConflict
from .effects import EffectStore
from .model import (
    CommandKind,
    CommandStatus,
    ControlCommand,
    ExecutionRecord,
    ExecutionStatus,
)
from .store import ExecutionConflict, ExecutionStore


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
        command, execution = self.executions.accept_command_with_transition(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=CommandKind.PAUSE,
            target=(ExecutionStatus.PAUSED if immediate else ExecutionStatus.PAUSING),
            payload={},
            actor=actor,
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
        command, execution = self.executions.accept_command_with_transition(
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
