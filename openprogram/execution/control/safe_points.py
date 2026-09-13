"""Execution control safe points operations on the owning service."""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Mapping
from ..attempts import AttemptConflict
from ..checkpoints import CheckpointFragment
from ..model import CommandKind, CommandStatus, ExecutionStatus, TERMINAL_EXECUTION_STATUSES, _thaw_json

from .shared import (
    AttemptCompletion,
    SafePointCompletion,
)


class SafePointsOperations:
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
        if command is None:
            raise AttemptConflict("command_not_found", f"safe point command not found: {command_id}")
        if command.kind not in {
            CommandKind.STEP,
            CommandKind.PAUSE,
            CommandKind.STEER,
        }:
            raise AttemptConflict(
                "unsupported_command",
                f"safe point cannot consume {command.kind.value}",
            )
        cancelled = self._arrive_superseded_safe_point(
            attempt_id=attempt_id,
            generation=generation,
            command_id=command_id,
        )
        if cancelled is not None:
            return cancelled
        if command.kind is CommandKind.STEP:
            return self._arrive_step_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=command_id,
                expected_execution_version=expected_execution_version,
                fragment=fragment,
            )
        if command is not None and command.kind is CommandKind.PAUSE:
            return self._arrive_pause_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=command_id,
                expected_execution_version=expected_execution_version,
                fragment=fragment,
            )
        if command is not None and command.kind is CommandKind.STEER:
            return self._arrive_steer_safe_point(
                attempt_id=attempt_id,
                generation=generation,
                command_id=command_id,
                expected_execution_version=expected_execution_version,
                fragment=fragment,
            )
        raise AttemptConflict(
            "unsupported_command",
            f"safe point cannot consume {command.kind.value}",
        )


    def _arrive_superseded_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
    ) -> SafePointCompletion | None:
        """Reconcile a late lower-priority report against cancellation."""
        completion = None
        unbind = False
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is None:
                raise AttemptConflict("command_not_found", f"safe point command not found: {command_id}")
            if command.execution_id != execution.execution_id:
                raise AttemptConflict(
                    "command_mismatch",
                    "safe point command belongs to another execution",
                )
            cancel = self._applying_command(connection, execution.execution_id, CommandKind.CANCEL)
            if cancel is None:
                if not (
                    execution.status is ExecutionStatus.CANCELLED
                    and command.status in {
                        CommandStatus.APPLIED,
                        CommandStatus.REJECTED,
                    }
                ):
                    return None
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if execution.status is not ExecutionStatus.CANCELLING:
                raise AttemptConflict(
                    "invalid_state",
                    f"applying cancel cannot finish execution in {execution.status.value}",
                )
            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution.execution_id,),
            ).fetchone() is not None
            target = (
                ExecutionStatus.RECONCILIATION_REQUIRED
                if unresolved
                else ExecutionStatus.CANCELLED
            )
            outcome = "reconciliation_required" if unresolved else "cancelled_at_safe_point"
            reason_code = "effect_reconciliation" if unresolved else cancel.payload.get("reason_code")
            ended, cancelled = self.attempts._finish_in_transaction(
                connection,
                attempt_id,
                generation=generation,
                expected_execution_version=execution.status_version,
                target=target,
                outcome=outcome,
                reason_code=reason_code,
            )
            command = self.executions._get_command(connection, command_id)
            if command is None:
                raise AttemptConflict("command_not_found", f"safe point command not found: {command_id}")
            if command.status in {CommandStatus.ACCEPTED, CommandStatus.APPLYING}:
                command = self.executions._transition_command(
                    connection,
                    command_id,
                    expected_status=command.status,
                    target=CommandStatus.REJECTED,
                    result_version=cancelled.status_version,
                    rejection_code="superseded_by_cancel",
                )
            if not unresolved:
                cancel = self.executions._transition_command(
                    connection,
                    cancel.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=cancelled.status_version,
                )
            checkpoint = (
                self.checkpoints._get(connection, cancelled.checkpoint_head_id)
                if cancelled.checkpoint_head_id
                else None
            )
            completion = SafePointCompletion(
                command=command,
                execution=cancelled,
                attempt=ended,
                checkpoint=checkpoint,
            )
            unbind = True
        if unbind:
            self.registry.unbind(
                execution.execution_id,
                attempt_id=attempt_id,
                generation=generation,
            )
        return completion


    def _arrive_pause_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        """Commit checkpoint, steering receipts, and pause in one transaction."""
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is not None and command.execution_id != execution.execution_id:
                raise AttemptConflict("command_mismatch", "safe point command belongs to another execution")
            if command is not None and command.status is CommandStatus.APPLIED:
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if (
                execution.status_version != expected_execution_version
                or command is None
                or command.execution_id != execution.execution_id
                or command.kind is not CommandKind.PAUSE
                or command.status is not CommandStatus.APPLYING
            ):
                raise AttemptConflict("command_mismatch", "safe point does not match an applying pause command")
            if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                raise AttemptConflict("unsupported_safe_point", "driver reported an undeclared safe point")
            steering_commands = [
                self.executions._command(row)
                for row in connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution.execution_id,
                        CommandKind.STEER.value,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
            ]
            state_refs = dict(fragment.state_refs)
            if steering_commands:
                steering = list(state_refs.get("steering", ()))
                steering.extend(
                    {"command_id": item.command_id, "payload": dict(item.payload)}
                    for item in steering_commands
                )
                state_refs["steering"] = steering
            steering_ids = {item.command_id for item in steering_commands}
            pending = tuple(
                item
                for item in dict.fromkeys(fragment.pending_command_ids)
                if item != command_id and item not in steering_ids
            )
            checkpoint, checkpointed = self.checkpoints._publish_in_transaction(
                connection,
                execution_id=execution.execution_id,
                expected_version=expected_execution_version,
                revision_id=execution.revision_id,
                parent_checkpoint_id=execution.checkpoint_head_id,
                frontier=fragment.frontier,
                completed_frontier=fragment.completed_frontier,
                state_refs=state_refs,
                completed_actions=fragment.completed_actions,
                effect_receipts=fragment.effect_receipts,
                child_frontier=fragment.child_frontier,
                pending_command_ids=pending,
                created_by_attempt_id=attempt_id,
            )
            ended, paused = self.attempts._finish_in_transaction(
                connection,
                attempt_id,
                generation=generation,
                expected_execution_version=checkpointed.status_version,
                target=ExecutionStatus.PAUSED,
                outcome="paused_at_safe_point",
            )
            safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {
                "kind": fragment.safe_point_kind
            }
            receipt = {"checkpoint_id": checkpoint.checkpoint_id, "safe_point": safe_point}
            command = self.executions._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.APPLYING,
                target=CommandStatus.APPLIED,
                result_version=paused.status_version,
                receipt=receipt,
            )
            applied = [command]
            for steer in steering_commands:
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection,
                        steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                steer = self.executions._transition_command(
                    connection,
                    steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied.append(steer)
        self.registry.unbind(
            execution.execution_id,
            attempt_id=attempt_id,
            generation=generation,
        )
        return SafePointCompletion(
            command=command,
            execution=paused,
            attempt=ended,
            checkpoint=checkpoint,
            applied_commands=tuple(applied),
        )


    def _arrive_steer_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        """Apply running steering at a safe point, with optional pause closeout."""
        unbind = False
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is None or command.execution_id != execution.execution_id:
                raise AttemptConflict("command_mismatch", "safe point does not match this execution")
            if command.status is CommandStatus.APPLIED:
                self.attempts._validate_lease(attempt, self.attempts._clock())
                self.attempts._validate_owner(
                    execution, attempt, expected_execution_version
                )
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if command.kind is not CommandKind.STEER or command.status not in {
                CommandStatus.ACCEPTED,
                CommandStatus.APPLYING,
            }:
                raise AttemptConflict("command_mismatch", "safe point does not match an unfinished steer command")
            if execution.status_version != expected_execution_version:
                raise AttemptConflict(
                    "stale_version",
                    f"expected execution version {expected_execution_version}, found {execution.status_version}",
                )
            if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                raise AttemptConflict("unsupported_safe_point", "driver reported an undeclared safe point")
            if self._applying_command(connection, execution.execution_id, CommandKind.CANCEL) is not None:
                raise AttemptConflict("superseded_by_cancel", "cancel has priority over steer")
            if self._applying_command(connection, execution.execution_id, CommandKind.STEP) is not None:
                raise AttemptConflict("superseded_by_step", "step has priority over steer")
            pause = self._applying_command(connection, execution.execution_id, CommandKind.PAUSE)
            if execution.status is ExecutionStatus.PAUSING and pause is None:
                raise AttemptConflict("invalid_state", "pausing execution has no applying pause command")
            if execution.status not in {ExecutionStatus.RUNNING, ExecutionStatus.PAUSING}:
                raise AttemptConflict("invalid_state", f"steer safe point arrived while execution is {execution.status.value}")
            steering_commands = [
                self.executions._command(row)
                for row in connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution.execution_id,
                        CommandKind.STEER.value,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
            ]
            state_refs = dict(fragment.state_refs)
            steering = list(state_refs.get("steering", ()))
            steering.extend(
                {"command_id": item.command_id, "payload": dict(item.payload)}
                for item in steering_commands
            )
            state_refs["steering"] = steering
            steering_ids = {item.command_id for item in steering_commands}
            pending = tuple(
                item
                for item in dict.fromkeys(fragment.pending_command_ids)
                if item not in steering_ids
            )
            checkpoint, checkpointed = self.checkpoints._publish_in_transaction(
                connection,
                execution_id=execution.execution_id,
                expected_version=expected_execution_version,
                revision_id=execution.revision_id,
                parent_checkpoint_id=execution.checkpoint_head_id,
                frontier=fragment.frontier,
                completed_frontier=fragment.completed_frontier,
                state_refs=state_refs,
                completed_actions=fragment.completed_actions,
                effect_receipts=fragment.effect_receipts,
                child_frontier=fragment.child_frontier,
                pending_command_ids=pending,
                created_by_attempt_id=attempt_id,
            )
            current_attempt = attempt
            result_execution = checkpointed
            if pause is not None:
                current_attempt, result_execution = self.attempts._finish_in_transaction(
                    connection,
                    attempt_id,
                    generation=generation,
                    expected_execution_version=checkpointed.status_version,
                    target=ExecutionStatus.PAUSED,
                    outcome="paused_at_safe_point",
                )
                unbind = True
            safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {"kind": fragment.safe_point_kind}
            receipt = {"checkpoint_id": checkpoint.checkpoint_id, "safe_point": safe_point}
            applied = []
            if pause is not None:
                applied.append(
                    self.executions._transition_command(
                        connection,
                        pause.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.APPLIED,
                        result_version=result_execution.status_version,
                        receipt=receipt,
                    )
                )
            for steer in steering_commands:
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection,
                        steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                steer = self.executions._transition_command(
                    connection,
                    steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=result_execution.status_version,
                    receipt=receipt,
                )
                applied.append(steer)
            command = next(item for item in applied if item.command_id == command_id)
        if unbind:
            self.registry.unbind(execution.execution_id, attempt_id=attempt_id, generation=generation)
        return SafePointCompletion(
            command=command,
            execution=result_execution,
            attempt=current_attempt,
            checkpoint=checkpoint,
            applied_commands=tuple(applied),
        )


    def arrive_step_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment | None = None,
        safe_point_kind: str = "control.step",
        frontier: tuple[Mapping[str, Any], ...] = (),
        state_refs: Mapping[str, Any] | None = None,
        managed_action: Mapping[str, Any] | None = None,
        control_step: Mapping[str, Any] | None = None,
    ) -> SafePointCompletion:
        """Report one step unit and atomically return the execution to paused."""
        if fragment is None:
            fragment = CheckpointFragment(
                safe_point_kind=safe_point_kind,
                frontier=frontier or ({"safe_point": safe_point_kind},),
                state_refs=state_refs or {},
                managed_action=managed_action,
                control_step=control_step,
            )
        elif managed_action is not None or control_step is not None:
            fragment = replace(
                fragment,
                managed_action=managed_action,
                control_step=control_step,
            )
        return self._arrive_step_safe_point(
            attempt_id=attempt_id,
            generation=generation,
            command_id=command_id,
            expected_execution_version=expected_execution_version,
            fragment=fragment,
        )


    def _arrive_step_safe_point(
        self,
        *,
        attempt_id: str,
        generation: int,
        command_id: str,
        expected_execution_version: int,
        fragment: CheckpointFragment,
    ) -> SafePointCompletion:
        if (fragment.managed_action is None) == (fragment.control_step is None):
            raise AttemptConflict(
                "invalid_step_unit",
                "step safe point must report exactly one managed_action or control_step",
            )
        if fragment.safe_point_kind == "" or fragment.safe_point_kind is None:
            raise AttemptConflict("invalid_safe_point", "safe_point_kind is required")
        with self.executions._transaction() as connection:
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            command = self.executions._get_command(connection, command_id)
            if command is not None and command.execution_id != execution.execution_id:
                raise AttemptConflict("command_mismatch", "safe point command belongs to another execution")
            if command is not None and command.status is CommandStatus.APPLIED:
                checkpoint = (
                    self.checkpoints._get(connection, execution.checkpoint_head_id)
                    if execution.checkpoint_head_id
                    else None
                )
                return SafePointCompletion(
                    command=command,
                    execution=execution,
                    attempt=attempt,
                    checkpoint=checkpoint,
                )
            if execution.status_version != expected_execution_version:
                raise AttemptConflict(
                    "stale_version",
                    f"expected execution version {expected_execution_version}, found {execution.status_version}",
                )
            if (
                command is None
                or command.execution_id != execution.execution_id
                or command.kind is not CommandKind.STEP
                or command.status is not CommandStatus.APPLYING
            ):
                raise AttemptConflict("command_mismatch", "safe point does not match an applying step command")
            if fragment.safe_point_kind not in execution.capabilities.safe_point_kinds:
                raise AttemptConflict("unsupported_safe_point", "driver reported an undeclared safe point")

            pause = self._applying_command(connection, execution.execution_id, CommandKind.PAUSE)

            checkpoint_version = expected_execution_version
            if execution.status is ExecutionStatus.RUNNING:
                execution = self.executions._transition_execution(
                    connection,
                    execution.execution_id,
                    expected_version=expected_execution_version,
                    target=ExecutionStatus.PAUSING,
                    reason_code="step_at_safe_point",
                )
                checkpoint_version = execution.status_version
            elif execution.status is not ExecutionStatus.PAUSING:
                raise AttemptConflict(
                    "invalid_state",
                    f"step safe point arrived while execution is {execution.status.value}",
                )

            state_refs = dict(fragment.state_refs)
            steering = list(state_refs.get("steering", ()))
            steering_commands = connection.execute(
                "SELECT * FROM commands WHERE execution_id = ? AND kind = ? "
                "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                (
                    execution.execution_id,
                    CommandKind.STEER.value,
                    CommandStatus.ACCEPTED.value,
                    CommandStatus.APPLYING.value,
                ),
            ).fetchall()
            for row in steering_commands:
                steer = self.executions._command(row)
                steering.append({"command_id": steer.command_id, "payload": dict(steer.payload)})
            if steering_commands:
                state_refs["steering"] = steering
            unit = fragment.managed_action or fragment.control_step
            completed = tuple(fragment.completed_actions) + ({"managed_action": dict(unit)} if fragment.managed_action is not None else {"control_step": dict(unit)},)
            pending = tuple(
                command_id
                for command_id in fragment.pending_command_ids
                if command_id != command.command_id
                and command_id not in {str(row["command_id"]) for row in steering_commands}
            )
            # The checkpoint and all command/attempt lifecycle writes below are
            # committed together; a crash cannot expose a half-applied step.
            checkpoint, checkpointed = self.checkpoints._publish_in_transaction(
                connection,
                execution_id=execution.execution_id,
                expected_version=checkpoint_version,
                revision_id=execution.revision_id,
                parent_checkpoint_id=execution.checkpoint_head_id,
                frontier=fragment.frontier,
                completed_frontier=fragment.completed_frontier,
                state_refs=state_refs,
                completed_actions=completed,
                effect_receipts=fragment.effect_receipts,
                child_frontier=fragment.child_frontier,
                pending_command_ids=pending,
                created_by_attempt_id=attempt_id,
            )
            ended, paused = self.attempts._finish_in_transaction(
                connection,
                attempt_id,
                generation=generation,
                expected_execution_version=checkpointed.status_version,
                target=ExecutionStatus.PAUSED,
                outcome="step_at_safe_point",
            )
            safe_point = _thaw_json(checkpoint.frontier[-1]) if checkpoint.frontier else {"kind": fragment.safe_point_kind}
            receipt = {"checkpoint_id": checkpoint.checkpoint_id, "safe_point": safe_point}
            if pause is not None:
                command = self.executions._transition_command(
                    connection,
                    command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=paused.status_version,
                    rejection_code="superseded_by_pause",
                )
                applied = []
                pause = self.executions._transition_command(
                    connection,
                    pause.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied.append(pause)
            else:
                command = self.executions._transition_command(
                    connection,
                    command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied = [command]
            for row in steering_commands:
                steer = self.executions._command(row)
                if steer.status is CommandStatus.ACCEPTED:
                    steer = self.executions._transition_command(
                        connection,
                        steer.command_id,
                        expected_status=CommandStatus.ACCEPTED,
                        target=CommandStatus.APPLYING,
                    )
                steer = self.executions._transition_command(
                    connection,
                    steer.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=paused.status_version,
                    receipt=receipt,
                )
                applied.append(steer)
        self.registry.unbind(execution.execution_id, attempt_id=attempt_id, generation=generation)
        return SafePointCompletion(
            command=command,
            execution=paused,
            attempt=ended,
            checkpoint=checkpoint,
            applied_commands=tuple(applied),
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
        try:
            if command is not None and execution.status in TERMINAL_EXECUTION_STATUSES:
                command = self._mark_applied(command, execution)
        finally:
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                self._forget_cancel_delivery(execution.execution_id)
        return AttemptCompletion(
            execution=execution,
            attempt=ended,
            command=command,
        )

