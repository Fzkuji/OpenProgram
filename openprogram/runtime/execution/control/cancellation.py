"""Execution control cancellation operations on the owning service."""
from __future__ import annotations
import asyncio
from threading import Timer
from typing import Any, Mapping
from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionRecord, ExecutionStatus, TERMINAL_COMMAND_STATUSES, TERMINAL_EXECUTION_STATUSES
from ..store import ExecutionConflict

from .shared import (
    ControlDispatch,
    off_loop,
    ProjectionRecoveryRequired,
    _CANCEL_SUPERSEDES,
    _PAUSE_SUPERSEDES,
    _log,
)


class CancellationOperations:
    def _remember_cancel_delivery(
        self, execution_id: str, command_id: str,
    ) -> None:
        with self._cancel_delivery_lock:
            self._delivered_cancel_commands.add(command_id)
            self._cancel_delivery_by_execution.setdefault(
                execution_id, set(),
            ).add(command_id)


    def _forget_cancel_delivery(
        self, execution_id: str, command_id: str | None = None,
    ) -> None:
        """Release the transient dedupe marker after cancellation settles."""
        with self._cancel_delivery_lock:
            if command_id is None:
                command_ids = self._cancel_delivery_by_execution.pop(
                    execution_id, set(),
                )
                self._delivered_cancel_commands.difference_update(command_ids)
                for pending_id in command_ids:
                    timer = self._cancel_escalations.pop(pending_id, None)
                    if timer is not None:
                        timer.cancel()
                return
            self._delivered_cancel_commands.discard(command_id)
            timer = self._cancel_escalations.pop(command_id, None)
            if timer is not None:
                timer.cancel()
            command_ids = self._cancel_delivery_by_execution.get(execution_id)
            if command_ids is None:
                return
            command_ids.discard(command_id)
            if not command_ids:
                self._cancel_delivery_by_execution.pop(execution_id, None)


    def _schedule_cancel_escalation(
        self, command: ControlCommand, execution: ExecutionRecord,
    ) -> None:
        attempt_id = execution.current_attempt_id
        generation = execution.owner_lease.get("generation")
        if attempt_id is None or not isinstance(generation, int):
            return
        with self._cancel_delivery_lock:
            if command.command_id in self._cancel_escalations:
                return
            timer = Timer(
                self.cancel_grace_seconds,
                self._escalate_cancel,
                args=(
                    command.command_id,
                    execution.execution_id,
                    attempt_id,
                    generation,
                    str(command.payload.get("reason_code") or "cancelled"),
                ),
            )
            timer.daemon = True
            self._cancel_escalations[command.command_id] = timer
            timer.start()


    def _escalate_cancel(
        self,
        command_id: str,
        execution_id: str,
        attempt_id: str,
        generation: int,
        reason: str,
    ) -> None:
        with self._cancel_delivery_lock:
            self._cancel_escalations.pop(command_id, None)
        execution = self.executions.get_execution(execution_id)
        if execution is None or (
            execution.status is not ExecutionStatus.CANCELLING
            or execution.current_attempt_id != attempt_id
            or execution.owner_lease.get("generation") != generation
        ):
            return
        try:
            receipt = asyncio.run(self.terminate_attempt(
                execution_id=execution_id,
                attempt_id=attempt_id,
                generation=generation,
                reason=reason,
            ))
            if not receipt.terminated:
                return
            self.recover_owner_loss(
                execution_id,
                attempt_id=attempt_id,
                generation=generation,
            )
        except Exception:
            _log.exception("cancel escalation failed for %s", execution_id)


    def _prune_cancel_delivery(self, execution_id: str) -> None:
        """Drop markers whose persisted commands are already terminal."""
        with self._cancel_delivery_lock:
            command_ids = tuple(
                self._cancel_delivery_by_execution.get(execution_id, ()),
            )
        for command_id in command_ids:
            command = self.executions.get_command(command_id)
            if command is None or command.status in TERMINAL_COMMAND_STATUSES:
                self._forget_cancel_delivery(execution_id, command_id)


    def _reconcile_terminal_cancel(
        self, execution: ExecutionRecord, connection=None,
    ) -> None:
        """Finish applying cancel commands after a terminal execution CAS."""
        if connection is None:
            commands = self.executions.list_commands(
                execution.execution_id,
                statuses=(CommandStatus.APPLYING,),
                kinds=(CommandKind.CANCEL,),
            )
        else:
            rows = connection.rows(
                "commands",
                order=("submitted_at", "command_id"),
                execution_id=execution.execution_id,
                kind=CommandKind.CANCEL.value,
                status=CommandStatus.APPLYING.value,
            )
            commands = [self.executions._command(row) for row in rows]
        if not commands:
            return
        if execution.status is ExecutionStatus.CANCELLED:
            target = CommandStatus.APPLIED
            rejection_code = None
            receipt = {"recovered": "terminal_execution"}
        else:
            target = CommandStatus.REJECTED
            rejection_code = "execution_terminal"
            receipt = {"recovered": "terminal_execution"}
        for command in commands:
            if connection is None:
                self.executions.transition_command(
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=target,
                    result_version=execution.status_version,
                    rejection_code=rejection_code,
                    receipt=receipt,
                )
            else:
                self.executions._transition_command(
                    connection,
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=target,
                    result_version=execution.status_version,
                    rejection_code=rejection_code,
                    receipt=receipt,
                )


    def reconcile_terminal_cancel(self, execution: ExecutionRecord) -> None:
        """Durably settle cancel commands after a terminal execution write."""
        if execution.status not in TERMINAL_EXECUTION_STATUSES:
            return
        self._reconcile_terminal_cancel(execution)
        self._forget_cancel_delivery(execution.execution_id)


    async def request_pause(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
    ) -> ControlDispatch:
        # The pause is written in a worker thread (the execution's records
        # live under its conversation's lock); only the owner's signal is
        # awaited here.
        command, execution, delivered_now = await off_loop(
            execution_id, self._accept_pause,
            command_id=command_id, execution_id=execution_id,
            expected_version=expected_version, actor=actor,
        )
        if delivered_now:
            return ControlDispatch(
                command=command, execution=execution, delivered=False
            )
        return await self._dispatch(command, execution, operation="pause")


    def _accept_pause(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        actor: Mapping[str, Any],
    ) -> tuple[ControlCommand, ExecutionRecord, bool]:
        """Write a pause: ``(command, execution, settled)``, where ``settled``
        means nothing is left to tell an owner (a replay, or a pause that
        took effect at once)."""
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
            supersede_kinds=_PAUSE_SUPERSEDES,
            supersede_code="superseded_by_pause",
            apply_command=immediate,
        )
        if duplicate:
            return command, execution, True
        if execution.status is ExecutionStatus.PAUSED:
            self._observe_paused(execution)
            return command, execution, True
        return command, execution, False


    async def request_cancel(
        self,
        *,
        command_id: str,
        execution_id: str,
        actor: Mapping[str, Any],
        reason_code: str,
        expected_version: int | None = None,
    ) -> ControlDispatch:
        """Stop ``execution_id``, whichever state it moved to.

        The one stop every surface takes. A stop names its execution, not
        a version; ``expected_version`` is the version the caller last saw,
        logged when it moved and never compared. The answer depends only on
        the state: a running execution moves to ``cancelling`` and its
        owner is told; one already ``cancelling`` returns the stop in force
        and tells its owner if no earlier delivery reached it
        (:meth:`deliver_pending_cancel`); one that has ended returns its
        final state with ``already_terminal``. What the execution waits on
        is the caller's to stop (``run_control.stop_with_descendants``).

        Every write (the command, the transition, the cancelled reply in
        the conversation) runs in a worker thread, in the order it always
        had; the owner's signal is awaited on the caller's loop, so a held
        conversation delays this stop and nothing else.
        """
        command, execution, outcome = await off_loop(
            execution_id, self._accept_cancel,
            command_id=command_id, execution_id=execution_id, actor=actor,
            reason_code=reason_code, expected_version=expected_version,
        )
        if outcome == "terminal":
            return ControlDispatch(
                command=None, execution=execution, delivered=False,
                issue_code="already_terminal",
            )
        if outcome == "replayed" and execution.status in TERMINAL_EXECUTION_STATUSES:
            # The same stop again (an internal stop reuses its id), after
            # the execution ended: this call ended nothing.
            await off_loop(execution_id, self.reconcile_terminal_cancel, execution)
            return ControlDispatch(
                command=command, execution=execution, delivered=False,
                issue_code="already_terminal",
            )
        if outcome != "accepted":
            # The stop in force reaches its current owner unless that owner
            # already took it.
            redelivered = await self.deliver_pending_cancel(
                execution_id=execution_id,
                attempt_id=execution.current_attempt_id,
                generation=execution.owner_lease.get("generation"),
            )
            return redelivered or ControlDispatch(
                command=command, execution=execution, delivered=False,
            )
        if execution.current_attempt_id is None:
            return await off_loop(
                execution_id, self._cancel_without_owner, command, execution, reason_code,
            )
        return await self._deliver_cancel_once(command, execution)


    async def _deliver_cancel_once(
        self, command: ControlCommand, execution: ExecutionRecord,
    ) -> ControlDispatch:
        """Hand a cancel to its owner unless it has it or is being handed it.

        The claim is taken and dropped under the delivery lock, never held
        across the owner's await: stops of one execution are written in
        worker threads and arrive here interleaved, and a thread lock held
        over an await would make those threads wait on the loop."""
        command_id = command.command_id
        with self._cancel_delivery_lock:
            if (
                command_id in self._delivered_cancel_commands
                or command_id in self._cancel_deliveries_in_flight
            ):
                return ControlDispatch(
                    command=command, execution=execution, delivered=False,
                    issue_code="already_delivered",
                )
            self._cancel_deliveries_in_flight.add(command_id)
        try:
            dispatch = await self._dispatch(command, execution, operation="cancel")
            if dispatch.delivered:
                self._remember_cancel_delivery(execution.execution_id, command_id)
                self._schedule_cancel_escalation(command, execution)
            return dispatch
        finally:
            with self._cancel_delivery_lock:
                self._cancel_deliveries_in_flight.discard(command_id)


    def _accept_cancel(
        self,
        *,
        command_id: str,
        execution_id: str,
        actor: Mapping[str, Any],
        reason_code: str,
        expected_version: int | None,
    ):
        """Write the stop: ``(command, execution, outcome)`` of ``accept_cancel``."""
        current = self.executions.get_execution(execution_id)
        if current is None:
            raise ExecutionConflict("not_found", f"execution not found: {execution_id}")
        if expected_version is not None and expected_version != current.status_version:
            _log.info(
                "cancel.version_moved execution=%s seen=%s current=%s",
                execution_id, expected_version, current.status_version,
            )

        def close_waits(connection, _execution) -> None:
            from ..waits import DurableWaitStore

            DurableWaitStore(self.executions).cancel_execution_in_transaction(
                connection, execution_id
            )

        terminal_candidate = (
            current.status is ExecutionStatus.QUEUED
            and current.current_attempt_id is None
            and not self.effects.list_unresolved(execution_id)
            and self.executions.get_command(command_id) is None
        )
        if terminal_candidate and self._terminal_preparer is not None:
            # A queued Job ends at once, so its dispatch is fenced first, for
            # this exact snapshot. Should the snapshot move before the stop
            # is written, the fence is handed to recovery, which settles it.
            try:
                prepared = self._terminal_preparer(current, command_id)
                if prepared is False:
                    raise RuntimeError("terminal dispatch barrier unavailable")
            except Exception as exc:
                self._record_terminal_recovery(current, command_id)
                raise ProjectionRecoveryRequired(execution_id) from exc
            try:
                return self.executions.accept_cancel(
                    command_id=command_id, execution_id=execution_id,
                    actor=actor, reason_code=reason_code,
                    supersede_kinds=_CANCEL_SUPERSEDES,
                    after_transition=close_waits,
                    at_version=current.status_version,
                )
            except Exception as exc:
                self._record_terminal_recovery(current, command_id)
                raise ProjectionRecoveryRequired(execution_id) from exc
        return self.executions.accept_cancel(
            command_id=command_id, execution_id=execution_id,
            actor=actor, reason_code=reason_code,
            supersede_kinds=_CANCEL_SUPERSEDES,
            after_transition=close_waits,
        )


    def _cancel_without_owner(
        self, command: ControlCommand, execution: ExecutionRecord, reason_code: str,
    ) -> ControlDispatch:
        """End an accepted stop of an execution no attempt runs: it is
        cancelled here, and its cancelled reply written to the conversation."""
        execution = self.executions.transition_execution(
            execution.execution_id,
            expected_version=execution.status_version,
            target=ExecutionStatus.CANCELLED,
            reason_code=reason_code,
        )
        try:
            self._observe_terminal(execution)
        except Exception as exc:
            try:
                command = self.executions.transition_command(
                    command.command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.REJECTED,
                    result_version=execution.status_version,
                    rejection_code=ProjectionRecoveryRequired.code,
                    receipt={"error": str(exc)},
                )
            except Exception:
                _log.exception(
                    "failed to persist projection recovery command state for %s",
                    execution.execution_id,
                )
            raise ProjectionRecoveryRequired(
                execution.execution_id,
            ) from exc
        command = self._mark_applied(command, execution)
        return ControlDispatch(
            command=command, execution=execution, delivered=False
        )


    def _pending_cancel(
        self, execution_id: str,
    ) -> tuple[ExecutionRecord, ControlCommand | None] | None:
        """The execution and its stop in force, read in a worker thread;
        ``None`` when the execution is gone or has ended (its stops settled)."""
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            return None
        if execution.status in TERMINAL_EXECUTION_STATUSES:
            self._reconcile_terminal_cancel(execution)
            self._forget_cancel_delivery(execution_id)
            return None
        self._prune_cancel_delivery(execution_id)
        pending = self.executions.list_commands(
            execution_id,
            statuses=(CommandStatus.APPLYING,),
            kinds=(CommandKind.CANCEL,),
        )
        return execution, (pending[0] if pending else None)


    async def deliver_pending_cancel(
        self,
        *,
        execution_id: str,
        attempt_id: str | None,
        generation: int | None,
    ) -> ControlDispatch | None:
        """Deliver a persisted cancel command to this exact local owner.

        Delivery happens once per owner: an owner that took the command
        answers ``already_delivered`` without being called (its handle may
        be released while its ending is still being written). A delivery
        that did not reach it, and an owner that took over since (recovery
        forgets the earlier delivery), get the command now.
        """
        found = await off_loop(execution_id, self._pending_cancel, execution_id)
        if found is None:
            return None
        execution, command = found
        if command is None or (
            command.kind is not CommandKind.CANCEL
            or command.status is not CommandStatus.APPLYING
        ):
            if (
                execution.status in TERMINAL_EXECUTION_STATUSES
                or command is not None and command.status in TERMINAL_COMMAND_STATUSES
            ):
                self._forget_cancel_delivery(
                    execution_id,
                    command.command_id if command is not None else None,
                )
            return None
        if (
            execution.current_attempt_id != attempt_id
            or execution.owner_lease.get("generation") != generation
        ):
            return ControlDispatch(
                command=command,
                execution=execution,
                delivered=False,
                issue_code="stale_owner",
            )
        return await self._deliver_cancel_once(command, execution)
