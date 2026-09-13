"""Execution control activation operations on the owning service."""
from __future__ import annotations
import inspect
from typing import Any, Mapping
from ..attempts import AttemptConflict, AttemptRecord
from ..checkpoints import CheckpointManifest
from ..driver import ActivationInput, DriverBinding, DriverRegistryConflict
from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionRecord, ExecutionStatus, TERMINAL_EXECUTION_STATUSES

from .shared import (
    Activator,
)


class ActivationOperations:
    def _finish_activation(
        self,
        attempt: AttemptRecord | None,
        command: ControlCommand,
        *,
        delivered: bool,
        issue_code: str | None,
    ) -> tuple[ControlCommand, ExecutionRecord]:
        """Close the activation race while holding the execution write lock."""
        if attempt is None:
            raise AttemptConflict("activation_failed", "activation has no attempt")
        reason = issue_code or "activation_failed"
        unbind = False
        with self.executions._transaction() as connection:
            current_attempt = self.attempts._require(connection, attempt.attempt_id)
            self.attempts._validate_generation(current_attempt, attempt.generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            current_command = self.executions._get_command(connection, command.command_id)
            if current_command is None:
                raise AttemptConflict("activation_failed", "activation command disappeared")
            if current_command.execution_id != execution.execution_id:
                raise AttemptConflict(
                    "command_mismatch",
                    "activation command belongs to another execution",
                )
            if current_command.status is CommandStatus.APPLIED:
                return current_command, execution
            if (
                current_command.status is CommandStatus.REJECTED
                and execution.status in TERMINAL_EXECUTION_STATUSES
            ):
                return current_command, execution
            if current_command.status is CommandStatus.APPLYING and delivered:
                if current_command.kind is CommandKind.CONTINUE:
                    current_command = self.executions._transition_command(
                        connection,
                        current_command.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.APPLIED,
                        result_version=execution.status_version,
                    )
                return current_command, execution

            cancel = self._applying_command(
                connection, execution.execution_id, CommandKind.CANCEL
            )
            pause = self._applying_command(
                connection, execution.execution_id, CommandKind.PAUSE
            )
            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution.execution_id,),
            ).fetchone() is not None
            if cancel is not None:
                target = (
                    ExecutionStatus.RECONCILIATION_REQUIRED
                    if unresolved
                    else ExecutionStatus.CANCELLED
                )
                target_reason = "effect_reconciliation" if unresolved else "cancelled_during_activation"
                outcome = "reconciliation_required" if unresolved else "cancelled_during_activation"
            elif pause is not None:
                target = ExecutionStatus.PAUSED
                target_reason = "pause_during_activation"
                outcome = "pause_during_activation"
            else:
                target = ExecutionStatus.PAUSED
                target_reason = reason
                outcome = reason

            if execution.status is ExecutionStatus.RUNNING and target is ExecutionStatus.PAUSED:
                pausing = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=execution.status_version,
                    target=ExecutionStatus.PAUSING,
                    reason_code=target_reason,
                )
                execution = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=pausing.status_version,
                    target=ExecutionStatus.PAUSED,
                    reason_code=target_reason,
                    clear_owner=True,
                )
            elif execution.status in {
                ExecutionStatus.PAUSING,
                ExecutionStatus.CANCELLING,
            }:
                execution = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=execution.status_version,
                    target=target,
                    reason_code=target_reason,
                    clear_owner=True,
                )
            else:
                raise AttemptConflict(
                    "activation_failed",
                    f"cannot settle activation while execution is {execution.status.value}",
                )
            ended = self.attempts._end_for_owner_loss(
                connection, current_attempt, outcome=outcome
            )
            self.executions._append_event(
                connection,
                execution_id=execution.execution_id,
                execution_version=execution.status_version,
                kind="attempt.ended",
                payload={"attempt": ended.to_dict()},
                created_at=ended.updated_at,
            )
            if current_command.status is CommandStatus.APPLYING:
                current_command = self.executions._transition_command(
                    connection,
                    current_command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=execution.status_version,
                    rejection_code=reason,
                )
            if cancel is not None and not unresolved:
                self.executions._transition_command(
                    connection,
                    cancel.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=execution.status_version,
                )
            elif pause is not None:
                self.executions._transition_command(
                    connection,
                    pause.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=execution.status_version,
                )
            unbind = True
        if unbind:
            self.registry.unbind(
                attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        return current_command, execution


    async def _activate(
        self,
        attempt: AttemptRecord | None,
        checkpoint: CheckpointManifest | None,
        steer_inputs: tuple[Mapping[str, Any], ...],
        *,
        activator: Activator | None,
        driver: Any | None = None,
    ) -> tuple[bool, str | None]:
        if attempt is None:
            return False, None
        if checkpoint is None and attempt.execution_id:
            child_execution = self.executions.get_execution(attempt.execution_id)
            if child_execution is not None:
                checkpoint_id = (
                    child_execution.checkpoint_head_id
                    or child_execution.source_checkpoint_id
                )
                if checkpoint_id is not None:
                    checkpoint = self.checkpoints.get(checkpoint_id)
        callback = activator or self.activator
        if callback is None and driver is not None:
            callback = driver.activate
        if callback is None:
            return True, None
        try:
            result = callback(
                attempt,
                ActivationInput(checkpoint=checkpoint, steer_inputs=steer_inputs),
            )
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, DriverBinding):
                if (
                    result.execution_id != attempt.execution_id
                    or result.attempt_id != attempt.attempt_id
                    or result.generation != attempt.generation
                ):
                    raise DriverRegistryConflict(
                        "invalid_binding",
                        "activation returned a binding for a different attempt",
                    )
                self._bind_driver(result)
            elif isinstance(result, tuple) and len(result) == 2:
                driver, handle = result
                self._bind_driver(
                    DriverBinding(
                        execution_id=attempt.execution_id,
                        attempt_id=attempt.attempt_id,
                        generation=attempt.generation,
                        driver=driver,
                        handle=handle,
                    )
                )
            elif driver is not None:
                self._bind_driver(
                    DriverBinding(
                        execution_id=attempt.execution_id,
                        attempt_id=attempt.attempt_id,
                        generation=attempt.generation,
                        driver=driver,
                        handle=result,
                    )
                )
            return True, None
        except Exception as exc:
            issue = getattr(exc, "code", None)
            return False, issue if issue == "continuation_contract_mismatch" else "activation_failed"


    def _bind_driver(self, binding: DriverBinding[Any]) -> None:
        """Commit driver-local activation only after durable registry fencing."""
        committed = getattr(binding.driver, "activation_committed", None)
        try:
            self.registry.bind(
                binding,
                on_bound=(
                    (lambda: committed(binding))
                    if callable(committed)
                    else None
                ),
            )
        except Exception:
            aborted = getattr(binding.driver, "activation_aborted", None)
            if callable(aborted):
                aborted(binding)
            raise


    def _durable_owner(self, execution_id: str) -> tuple[str, int] | None:
        execution = self.executions.get_execution(execution_id)
        if execution is None or execution.current_attempt_id is None:
            return None
        generation = execution.owner_lease.get("generation")
        if not isinstance(generation, int):
            return None
        return execution.current_attempt_id, generation

