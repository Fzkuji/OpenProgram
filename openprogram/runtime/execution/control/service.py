"""Execution control service operations on the owning service."""
from __future__ import annotations
from threading import RLock, Timer
from typing import Any, Callable, Mapping
from ..attempts import AttemptConflict, AttemptStore
from ..checkpoints import ExecutionCheckpointStore
from ..driver import DriverRegistry, DriverRegistryConflict
from ..effects import EffectStatus, EffectStore
from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionRecord, ExecutionStatus, TERMINAL_EXECUTION_STATUSES
from ..store import CommandConflict, ExecutionStore
from ..state_blobs import ExecutionStateBlobStore

from .shared import (
    Activator,
    ControlDispatch,
    ReconciliationCompletion,
    WaitSuspension,
    _log,
    off_loop,
)

from .waits import WaitsOperations
from .cancellation import CancellationOperations
from .branches import BranchesOperations
from .activation import ActivationOperations
from .safe_points import SafePointsOperations
from .recovery import RecoveryOperations


class RuntimeControlService(WaitsOperations, CancellationOperations, BranchesOperations, ActivationOperations, SafePointsOperations, RecoveryOperations):
    """The sole coordinator for canonical commands and live driver signals."""

    def __init__(
        self,
        executions: ExecutionStore,
        attempts: AttemptStore,
        registry: DriverRegistry,
        *,
        activator: Activator | None = None,
        owner_id: str = "control-service",
        cancel_grace_seconds: float = 30.0,
    ) -> None:
        self.executions = executions
        self.attempts = attempts
        self.registry = registry
        self.registry.set_owner_resolver(self._durable_owner)
        self.effects = EffectStore(executions)
        self.state_blobs = ExecutionStateBlobStore(executions)
        self.checkpoints = ExecutionCheckpointStore(executions)
        self.activator = activator
        self.owner_id = owner_id
        self.cancel_grace_seconds = max(0.0, float(cancel_grace_seconds))
        self._terminal_observer: Callable[[ExecutionRecord], object] | None = None
        self._pause_observer: Callable[[ExecutionRecord], object] | None = None
        self._wait_suspension_observer: Callable[[WaitSuspension], object] | None = None
        self._wait_resume_scheduler: Callable[[Any, ExecutionRecord], object] | None = None
        self._terminal_preparer: Callable[..., object] | None = None
        self._terminal_recovery: Callable[..., object] | None = None
        self._cancel_delivery_lock = RLock()
        self._delivered_cancel_commands: set[str] = set()
        # Cancels being handed to their owner right now: a second stop
        # arriving meanwhile must not hand the same command over again.
        self._cancel_deliveries_in_flight: set[str] = set()
        self._cancel_delivery_by_execution: dict[str, set[str]] = {}
        self._cancel_escalations: dict[str, Timer] = {}


    def set_terminal_observer(
        self, observer: Callable[[ExecutionRecord], object] | None,
    ) -> None:
        """Attach a transport-neutral projection/release observer."""
        self._terminal_observer = observer


    def set_pause_observer(
        self, observer: Callable[[ExecutionRecord], object] | None,
    ) -> None:
        """Attach the canonical paused-state resource observer."""
        self._pause_observer = observer


    def set_wait_suspension_observer(
        self, observer: Callable[[WaitSuspension], object] | None,
    ) -> None:
        """Attach the cross-authority resource release for a wait handoff."""
        self._wait_suspension_observer = observer


    def set_wait_resume_scheduler(
        self, scheduler: Callable[[Any, ExecutionRecord], object] | None,
    ) -> None:
        """Attach Job resource re-admission before a wait continuation."""
        self._wait_resume_scheduler = scheduler


    def set_terminal_preparer(
        self, preparer: Callable[..., object] | None,
    ) -> None:
        """Attach a pre-terminal barrier for external resource projections."""
        self._terminal_preparer = preparer


    def set_terminal_recovery(
        self, recovery: Callable[..., object] | None,
    ) -> None:
        """Attach recovery recording for a failed canonical CAS."""
        self._terminal_recovery = recovery


    def _observe_terminal(self, execution: ExecutionRecord) -> None:
        if execution.status not in TERMINAL_EXECUTION_STATUSES:
            return
        observer = self._terminal_observer
        if execution.status is ExecutionStatus.CANCELLED:
            try:
                from ..event_effects import ExecutionEventEffects
                ExecutionEventEffects(self.executions).project_cancelled_assistant(execution)
            except Exception:
                # The event effect retries this; a display failure must not
                # reject a cancellation that is already written.
                _log.exception("cancelled chat projection deferred for %s", execution.execution_id)
        if observer is not None:
            # Resource-release failures remain visible to control callers.
            observer(execution)


    def _observe_paused(self, execution: ExecutionRecord) -> None:
        if execution.status is not ExecutionStatus.PAUSED:
            return
        observer = self._pause_observer
        if observer is not None:
            try:
                observer(execution)
            except Exception:
                # The canonical pause is already committed.  Startup and the
                # runner's periodic repair resubmit its idempotent release;
                # do not turn a completed safe point into a second execution.
                _log.exception(
                    "paused-state observer failed for %s",
                    execution.execution_id,
                )


    def _record_terminal_recovery(
        self, execution: ExecutionRecord, command_id: str,
    ) -> None:
        recovery = self._terminal_recovery
        if recovery is None:
            return
        try:
            recovery(execution, command_id)
        except Exception:
            _log.exception(
                "failed to persist terminal barrier recovery for %s",
                execution.execution_id,
            )


    async def terminate_attempt(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        reason: str,
    ):
        """Invoke exact driver's termination hook after cancellation grace."""
        execution = self.executions.get_execution(execution_id)
        if execution is None or (
            execution.current_attempt_id != attempt_id
            or execution.owner_lease.get("generation") != generation
        ):
            raise AttemptConflict("stale_owner", "termination owner is stale")
        binding = self.registry.resolve(
            execution_id, attempt_id=attempt_id, generation=generation,
        )
        return await binding.driver.terminate(binding.handle, reason)


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
        *,
        receipt: Mapping[str, Any] | None = None,
    ) -> ControlCommand:
        if command.execution_id != execution.execution_id:
            raise AttemptConflict(
                "command_mismatch",
                "command belongs to another execution",
            )
        if command.status is CommandStatus.APPLIED:
            if command.kind is CommandKind.CANCEL:
                self._forget_cancel_delivery(
                    command.execution_id, command.command_id,
                )
            return command
        try:
            applied = self.executions.transition_command(
                command.command_id,
                expected_status=CommandStatus.APPLYING,
                target=CommandStatus.APPLIED,
                result_version=execution.status_version,
                receipt=receipt,
            )
        except CommandConflict:
            # Terminal reconciliation may have completed this same command
            # after the caller read its APPLYING snapshot.
            applied = self.executions.get_command(command.command_id)
            if (
                applied is None
                or applied.execution_id != execution.execution_id
                or applied.status is not CommandStatus.APPLIED
                or applied.result_version != execution.status_version
            ):
                raise
        if command.kind is CommandKind.CANCEL:
            self._forget_cancel_delivery(
                command.execution_id, command.command_id,
            )
        return applied


    def _applying_command(
        self,
        connection,
        execution_id: str,
        kind: CommandKind | None,
    ) -> ControlCommand | None:
        if kind is None:
            return None
        row = connection.first(
            "commands",
            order=("submitted_at", "command_id"),
            execution_id=execution_id,
            kind=kind.value,
            status=CommandStatus.APPLYING.value,
        )
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
            if issue == "owner_not_local" and operation == "cancel":
                # A turn running in a process of its own (a scheduled run)
                # follows its execution's file and stops itself from there.
                from .stop_requests import forward

                try:
                    await off_loop(execution.execution_id, forward, self.executions, execution, "cancel")
                except Exception:
                    _log.warning("stop of %s not passed to its process", execution.execution_id, exc_info=True)
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
