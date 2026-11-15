"""Execution control recovery operations on the owning service."""
from __future__ import annotations
import json
import logging
import threading
import time
from typing import Mapping
from .. import hooks
from ..attempts import AttemptConflict, AttemptStatus
from .. import restart as restart_policy
from ..restart import journal_head, publish_position
from ..model import CommandKind, CommandStatus, ExecutionStatus, TERMINAL_EXECUTION_STATUSES, _json
from ..store import ExecutionConflict

from .shared import (
    RecoveryCompletion,
)


_log = logging.getLogger(__name__)

#: Seconds between looks at whether another process still runs a turn.
_OTHER_PROCESS_CHECK_SECONDS = 2.0


class RecoveryOperations:
    def close_interrupted_chat(self, execution_id: str, *, expected_version: int):
        """Close only the dead frame. Unknown effects stay in the durable ledger."""
        from ..chat_recovery import recovery_state
        with self.executions._transaction() as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if execution.status_version != expected_version:
                raise ExecutionConflict("stale_version", "Interrupted chat changed")
            state = recovery_state(self.executions, execution, allow_cancel=True)
            if not state["can_start_new_turn"]:
                raise ExecutionConflict("recovery_unavailable", str(state["recovery_reason"]))
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                return execution
            if execution.status is ExecutionStatus.CANCELLING:
                execution = self.executions._transition_execution(
                    connection, execution_id, expected_version=execution.status_version,
                    target=ExecutionStatus.RECONCILIATION_REQUIRED, reason_code="effect_reconciliation")
            closed = self.executions._transition_execution(
                connection, execution_id, expected_version=execution.status_version,
                target=ExecutionStatus.INTERRUPTED, reason_code="restart_new_turn",
                clear_owner=True,
            )
            self._reconcile_terminal_cancel(closed, connection)
            for row in connection.rows(
                "commands", execution_id=execution_id, kind=CommandKind.CANCEL.value,
                status=CommandStatus.ACCEPTED.value,
            ):
                self.executions._transition_command(connection, row["command_id"],
                    expected_status=CommandStatus.ACCEPTED, target=CommandStatus.REJECTED,
                    result_version=closed.status_version, rejection_code="execution_terminal")
            return closed

    def recover_owner_loss(
        self,
        execution_id: str,
        *,
        attempt_id: str | None = None,
        generation: int | None = None,
        only_if_abandoned: bool = False,
        continuable: bool = True,
        never_bound: bool = False,
    ) -> RecoveryCompletion:
        """Durably finalize work whose physical owner is known to be gone.

        A physical owner reports its own loss with the exact attempt identity.
        The check is performed inside the same write transaction as recovery,
        so a late report from an older owner cannot recover a newer attempt.
        Startup recovery omits the identity because it is the authority that
        discovers abandoned owners.

        A turn lost to a restart may be left paused for the next Worker to
        continue. ``continuable=False`` is for an owner that is gone while the
        Worker goes on (a scheduled run's process that was killed): such a
        turn ends instead, and nothing is recorded for a restart to pick up.

        ``never_bound`` is for an owner a cancel reached before any driver was
        bound to run its turn: the attempt ends ``cancelled_during_activation``,
        so whoever reads it later knows the turn never ran.
        """
        if (attempt_id is None) != (generation is None):
            raise AttemptConflict(
                "invalid_owner",
                "attempt_id and generation must be supplied together",
            )
        with self.executions._transaction() as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if only_if_abandoned:
                from ..process_owner import process_owner_may_be_alive
                if process_owner_may_be_alive(execution.owner_lease):
                    return RecoveryCompletion(execution=execution)
            if attempt_id is not None and (
                execution.current_attempt_id != attempt_id
                or execution.owner_lease.get("generation") != generation
                ):
                raise AttemptConflict(
                    "stale_owner",
                    "owner-loss report does not match the current execution owner",
                )
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                self._reconcile_terminal_cancel(execution, connection)
                self._forget_cancel_delivery(execution_id)
                return RecoveryCompletion(execution=execution)
            if (
                execution.status is ExecutionStatus.PAUSED
                and execution.current_attempt_id is None
            ):
                command = self._applying_command(
                    connection, execution_id, CommandKind.PAUSE
                )
                if command is not None:
                    command = self.executions._transition_command(
                        connection,
                        command.command_id,
                        expected_status=CommandStatus.APPLYING,
                        target=CommandStatus.APPLIED,
                        result_version=execution.status_version,
                    )
                return RecoveryCompletion(execution=execution, command=command)
            if execution.status not in {
                ExecutionStatus.QUEUED,
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.PAUSED,
                ExecutionStatus.CANCELLING,
            }:
                self._forget_cancel_delivery(execution_id)
                return RecoveryCompletion(execution=execution)
            if (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is None
            ):
                return RecoveryCompletion(execution=execution)

            unresolved = self.executions._first_unresolved_effect(connection, execution_id) is not None
            journal = journal_head(self, connection, execution)
            has_decision = journal is not None and journal[0].has_decision
            restart_pending = False
            restart_new_turn = False
            agent_input = (self.executions.get_agent_turn_input(execution_id)
                           or self.executions.get_job_agent_input(execution_id))
            is_job = isinstance(agent_input, Mapping) and agent_input.get("kind") == "job_agent"
            if (
                continuable
                and execution.status is ExecutionStatus.RUNNING
                and isinstance(agent_input, Mapping)
                and agent_input.get("kind") in {"chat", "job_agent"}
                and (not is_job or only_if_abandoned)
            ):
                control_pending = connection.exists(
                    "commands", execution_id=execution_id,
                    kind=[CommandKind.PAUSE.value, CommandKind.CANCEL.value],
                    status=[CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value],
                )
                request = agent_input.get("turn_request" if is_job else "request", {})
                from openprogram.runtime.execution.slash_actions import slash_action_of
                interaction = request.get("interaction") if isinstance(request, Mapping) else None
                slash_action = slash_action_of(dict(request)) if isinstance(request, Mapping) else None
                # A turn with no finished model decision has done nothing yet,
                # so it starts over from what the user sent.
                restart_pending = (
                    not has_decision
                    and not unresolved
                    and not control_pending
                    and (is_job or slash_action is None)
                )
                restart_new_turn = (not is_job and not restart_pending and not control_pending
                                    and slash_action is None
                                    and interaction in {None, "interactive"}
                                    and request.get("source") in {"web", "tui", "acp"})

            command_kind: CommandKind | None = None
            apply_command = False
            reject_command = False
            running_commands = False
            restart_checkpoint = None
            if execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}:
                target = execution.status
                reason_code = "owner_lost_before_activation"
                outcome = "owner_lost_before_activation"
                if execution.status is ExecutionStatus.PAUSED:
                    command_kind = CommandKind.PAUSE
                    apply_command = True
            elif execution.status is ExecutionStatus.RUNNING:
                from ..restart import crash_checkpoint
                restart_checkpoint = (None if unresolved or not continuable
                                      else crash_checkpoint(self, connection, execution, journal))
                execution = self.executions._require_execution(connection, execution_id)
                running_commands = True
                target = (
                    ExecutionStatus.PAUSED
                    if restart_pending
                    else
                    ExecutionStatus.RECONCILIATION_REQUIRED
                    if unresolved
                    else ExecutionStatus.INTERRUPTED
                )
                reason_code = (
                    "restart_pending" if restart_pending
                    else "effect_reconciliation" if unresolved else "owner_lost"
                )
                outcome = (
                    "restart_pending" if restart_pending
                    else "reconciliation_required" if unresolved else "owner_lost"
                )
                if restart_checkpoint is not None:
                    target = ExecutionStatus.PAUSED
                    reason_code = outcome = "restart_recoverable"
            elif execution.status is ExecutionStatus.PAUSING:
                command_kind = CommandKind.PAUSE
                if unresolved:
                    target = ExecutionStatus.RECONCILIATION_REQUIRED
                    reason_code = "effect_reconciliation"
                    outcome = "reconciliation_required"
                elif execution.checkpoint_head_id is not None or (
                    has_decision and publish_position(self, connection, execution, journal, restart_policy.window_seconds())
                ):
                    execution = self.executions._require_execution(connection, execution_id)
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
                # The producer has stopped under a durable cancel intent.
                # Unknown external effects stay unresolved, just as they do
                # in finish_attempt(CANCELLED); they do not keep it running.
                target = ExecutionStatus.CANCELLED
                reason_code = execution.reason_code or "cancelled"
                outcome = "cancelled_during_activation" if never_bound else "owner_lost_during_cancel"
                apply_command = True

            if restart_checkpoint is not None:
                execution = self.executions._transition_execution(
                    connection, execution_id, expected_version=execution.status_version,
                    target=ExecutionStatus.PAUSING, reason_code="restart_recoverable",
                )
            recovered = self.executions._transition_execution(
                connection,
                execution_id,
                expected_version=execution.status_version,
                target=target,
                reason_code=reason_code,
                clear_owner=True,
            )
            if restart_checkpoint is not None:
                from ..restart import record_intent
                record_intent(self.executions, connection, recovered,
                              interrupted_at=restart_checkpoint[0], seconds=restart_checkpoint[1])
            elif restart_pending and recovered.status is ExecutionStatus.PAUSED:
                from ..restart import record_intent
                prior = self.attempts._require(connection, execution.current_attempt_id)
                record_intent(self.executions, connection, recovered,
                              interrupted_at=max(prior.updated_at, execution.updated_at),
                              seconds=restart_policy.window_seconds())
            elif restart_new_turn:
                from ..restart import record_intent
                prior = self.attempts._require(connection, execution.current_attempt_id)
                record_intent(self.executions, connection, recovered,
                              interrupted_at=max(prior.updated_at, execution.updated_at),
                              seconds=restart_policy.window_seconds(), mode="new_turn")
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
                last_active_at = attempt.updated_at
                attempt = self.attempts._end_for_owner_loss(
                    connection, attempt, outcome=outcome
                )
                self.executions._append_event(
                    connection,
                    execution_id=execution_id,
                    execution_version=recovered.status_version,
                    kind="attempt.ended",
                    payload={"attempt": attempt.to_dict(), "last_active_at": last_active_at},
                    created_at=attempt.updated_at,
                )

            command = None
            if running_commands:
                rows = connection.rows(
                    "commands",
                    order=("submitted_at", "command_id"),
                    execution_id=execution_id,
                    status=[CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value],
                )
                for row in rows:
                    recovered_command = self.executions._transition_command(
                        connection,
                        str(row["command_id"]),
                        expected_status=CommandStatus(row["status"]),
                        target=CommandStatus.REJECTED,
                        result_version=recovered.status_version,
                        rejection_code=(
                            "effect_reconciliation" if unresolved else "owner_lost"
                        ),
                    )
                    if command is None or recovered_command.kind is CommandKind.STEP:
                        command = recovered_command
            else:
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
            # A cooperative worker shutdown can die before its pause reaches
            # a safe point. Preserve that exact restart intent, not a user pause.
            if (command is not None and command.kind is CommandKind.PAUSE
                    and recovered.status in {ExecutionStatus.RECONCILIATION_REQUIRED, ExecutionStatus.INTERRUPTED}
                    and isinstance(agent_input, Mapping) and agent_input.get("kind") == "chat"
                    and agent_input.get("request", {}).get("source") in {"web", "tui", "acp"}
                    and agent_input.get("request", {}).get("interaction") in {None, "interactive"}):
                pause_command_id = command.command_id
                row = connection.first(
                    "execution_events",
                    where=lambda event: json.loads(event["payload_json"]).get("pause_command_id") == pause_command_id,
                    order="-sequence",
                    execution_id=execution_id,
                    kind="execution.restart.requested",
                )
                if row is not None:
                    if command.status is CommandStatus.APPLYING:
                        command = self.executions._transition_command(connection, command.command_id,
                            expected_status=CommandStatus.APPLYING, target=CommandStatus.REJECTED,
                            result_version=recovered.status_version, rejection_code="restart_new_turn")
                    data = json.loads(row["payload_json"])
                    self.executions._append_event(connection, execution_id=execution_id,
                        execution_version=recovered.status_version, kind="execution.restart.requested",
                        payload=dict(data, mode="new_turn", expected_version=recovered.status_version,
                                     pause_command_id=None), created_at=time.time())
                    self.executions._append_event(connection, execution_id=execution_id,
                        execution_version=recovered.status_version, kind="execution.restart.settled",
                        payload=dict(request_sequence=row["sequence"], outcome="pause_interrupted"), created_at=time.time())
        if attempt is not None:
            self.registry.unbind(
                execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        if recovered.status in TERMINAL_EXECUTION_STATUSES:
            self._forget_cancel_delivery(execution_id)
            if recovered.status is ExecutionStatus.CANCELLED:
                self._observe_terminal(recovered)
        return RecoveryCompletion(
            execution=recovered,
            attempt=attempt,
            command=command,
        )


    def _take_recorded_ending(self, execution_id: str) -> RecoveryCompletion | None:
        """Close a turn that ended in its own process and was written over afterwards.

        The Worker could pause a scheduled run's turn from a copy of its row
        loaded before that turn finished. The later line in the log then says
        pausing, while the attempt it names has ended. A terminal state has no
        way out, so the line that recorded it is the true one.
        """
        with self.executions._transaction() as connection:
            current = self.executions._require_execution(connection, execution_id)
            attempt = (connection.get("attempts", current.current_attempt_id)
                       if current.current_attempt_id else None)
            if attempt is None or attempt["status"] != AttemptStatus.ENDED.value:
                return None
            ending = self.executions.tables.recorded_ending(current.session_id, execution_id)
            if ending is None:
                return None
            version = current.status_version + 1
            connection.update("executions", {**ending, "status_version": version}, execution_id=execution_id)
            for row in connection.rows(
                "commands", execution_id=execution_id,
                status=[CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value],
            ):
                self.executions._transition_command(
                    connection, row["command_id"], expected_status=CommandStatus(row["status"]),
                    target=CommandStatus.REJECTED, result_version=version, rejection_code="execution_terminal",
                )
            return RecoveryCompletion(execution=self.executions._require_execution(connection, execution_id))

    def recover_startup(self) -> tuple[RecoveryCompletion, ...]:
        """Recover nonterminal executions whose previous owner is gone.

        An admitted Agent execution is normally activated immediately. If a
        process stops after admission but before an attempt is leased, startup
        terminalizes that record instead of leaving it queued forever.
        """
        recoveries = []
        still_running = []
        for execution in self.executions.list_nonterminal():
            from ..process_owner import owned_by_another_process, process_owner_may_be_alive
            if process_owner_may_be_alive(execution.owner_lease):
                if owned_by_another_process(execution.owner_lease):
                    still_running.append(execution)
                continue
            if execution.status in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.CANCELLING,
            } or (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is not None
            ):
                try:
                    recovery = (self._take_recorded_ending(execution.execution_id)
                                or self.recover_owner_loss(execution.execution_id, only_if_abandoned=True))
                except Exception:  # noqa: BLE001 - one run must not keep the Worker from starting
                    _log.exception("could not recover execution %s", execution.execution_id)
                    continue
                if recovery.attempt is not None or recovery.execution.status in TERMINAL_EXECUTION_STATUSES:
                    recoveries.append(recovery)
            elif (
                execution.status is ExecutionStatus.QUEUED
                and execution.current_attempt_id is None
                and self.executions.get_agent_turn_input(execution.execution_id) is not None
            ):
                try:
                    with self.executions._transaction() as connection:
                        current = self.executions._require_execution(connection, execution.execution_id)
                        if process_owner_may_be_alive(current.owner_lease):
                            continue
                        admission = self.executions.get_agent_turn_input(execution.execution_id)
                        from openprogram.runtime.execution.slash_actions import slash_action_of
                        request = admission.get("request", {})
                        restart = (
                            admission.get("kind") == "chat"
                            and slash_action_of(dict(request)) is None
                        )
                        recovered = self.executions._transition_execution(
                            connection, execution.execution_id,
                            expected_version=execution.status_version,
                            target=ExecutionStatus.PAUSED if restart else ExecutionStatus.FAILED,
                            reason_code="restart_pending" if restart else "owner_lost_before_activation",
                        )
                        if restart:
                            from ..restart import record_intent
                            record_intent(self.executions, connection, recovered,
                                          interrupted_at=current.updated_at, seconds=restart_policy.window_seconds())
                except (AttemptConflict, ExecutionConflict):
                    recovered = self.executions.get_execution(execution.execution_id)
                    if recovered is None:
                        continue
                recoveries.append(RecoveryCompletion(execution=recovered))
        if still_running:
            threading.Thread(
                target=self._take_up_when_finished, args=(still_running,),
                name="other-process-turns", daemon=True,
            ).start()
        return tuple(recoveries)

    def _take_up_when_finished(self, executions: list) -> None:
        """Read how turns that outlived the previous Worker ended, once they have.

        Their process is not a child of this one, so nothing else says when it
        ends, and this process would show them running until its own next start.
        """
        from ..process_owner import process_owner_may_be_alive

        while executions:
            time.sleep(_OTHER_PROCESS_CHECK_SECONDS)
            ended = [item for item in executions if not process_owner_may_be_alive(item.owner_lease)]
            executions = [item for item in executions if item not in ended]
            for session_id in {item.session_id for item in ended}:
                try:
                    self.executions.tables.take_up_other_process_turns(session_id)
                except Exception:  # noqa: BLE001 - the next start reads the log anyway
                    _log.warning("finished turns in %s not read back", session_id, exc_info=True)
