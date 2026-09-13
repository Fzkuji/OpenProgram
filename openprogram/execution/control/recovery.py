"""Execution control recovery operations on the owning service."""
from __future__ import annotations
import json
import time
from typing import Mapping
from ..attempts import AttemptConflict, AttemptStatus
from ..effects import EffectStatus
from ..model import CommandKind, CommandStatus, ExecutionStatus, TERMINAL_EXECUTION_STATUSES
from ..store import ExecutionConflict, _json

from .shared import (
    RecoveryCompletion,
)


class RecoveryOperations:
    def recover_owner_loss(
        self,
        execution_id: str,
        *,
        attempt_id: str | None = None,
        generation: int | None = None,
        only_if_abandoned: bool = False,
    ) -> RecoveryCompletion:
        """Durably finalize work whose physical owner is known to be gone.

        A physical owner reports its own loss with the exact attempt identity.
        The check is performed inside the same write transaction as recovery,
        so a late report from an older owner cannot recover a newer attempt.
        Startup recovery omits the identity because it is the authority that
        discovers abandoned owners.
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
                owner_attempt = (self.attempts._require(connection, execution.current_attempt_id)
                                 if execution.current_attempt_id else None)
                if process_owner_may_be_alive(
                    execution.owner_lease,
                    lease_expires_at=owner_attempt.lease_expires_at if owner_attempt else None,
                ):
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

            restart_pending = False
            agent_input = self.executions.get_agent_turn_input(execution_id)
            if (
                execution.status is ExecutionStatus.RUNNING
                and isinstance(agent_input, Mapping)
                and agent_input.get("kind") == "chat"
            ):
                # A provider request is an execution-local uncertainty.  It
                # has no external tool effect and can be safely classified as
                # not committed before restarting from the same admission.
                # Any tool effect remains unresolved and therefore blocks
                # automatic continuation below.
                rows = connection.execute(
                    "SELECT * FROM effects WHERE execution_id = ?",
                    (execution_id,),
                ).fetchall()
                unresolved_rows = [
                    row for row in rows
                    if row["status"] in {EffectStatus.DISPATCHED.value, EffectStatus.UNCERTAIN.value}
                ]
                kinds = []
                for row in rows:
                    try:
                        metadata = json.loads(row["metadata_json"])
                        kinds.append(str(metadata.get("kind") or "") if isinstance(metadata, Mapping) else "")
                    except (TypeError, ValueError):
                        kinds.append("")
                committed_tool = any(
                    not kind.startswith("provider.")
                    and row["status"] in {
                        EffectStatus.COMMITTED.value,
                        EffectStatus.NOT_COMMITTED.value,
                        EffectStatus.COMPENSATED.value,
                    }
                    for row, kind in zip(rows, kinds)
                )
                # Earlier releases could finish a tool without advancing
                # the continuation cursor. Event order, rather than wall
                # time, proves that the cursor covers every committed tool.
                cursor_covers_tools = not committed_tool
                if execution.checkpoint_head_id is not None:
                    published = connection.execute(
                        "SELECT MAX(sequence) FROM execution_events "
                        "WHERE execution_id = ? AND kind = 'checkpoint.published' "
                        "AND json_extract(payload_json, '$.checkpoint.checkpoint_id') = ?",
                        (execution_id, execution.checkpoint_head_id),
                    ).fetchone()[0]
                    if published is not None:
                        uncovered = connection.execute(
                            "SELECT 1 FROM execution_events WHERE execution_id = ? "
                            "AND sequence > ? AND kind IN ('effect.committed', 'effect.not_committed', 'effect.compensated') "
                            "AND COALESCE(json_extract(payload_json, '$.effect.metadata.kind'), '') NOT LIKE 'provider.%' LIMIT 1",
                            (execution_id, published),
                        ).fetchone()
                        cursor_covers_tools = uncovered is None
                provider_only = unresolved_rows and all(
                    kinds[index] == "provider.before"
                    for index, row in enumerate(rows)
                    if row in unresolved_rows
                ) and cursor_covers_tools
                if provider_only:
                    now = time.time()
                    for row in unresolved_rows:
                        receipt = {"outcome": "not_committed", "reason": "provider_request_interrupted"}
                        connection.execute(
                            "UPDATE effects SET status = ?, receipt_json = ?, updated_at = ?, resolved_at = ? WHERE effect_id = ?",
                            (EffectStatus.NOT_COMMITTED.value, _json(receipt), now, now, row["effect_id"]),
                        )
                        effect = self.effects._require(connection, str(row["effect_id"]))
                        self.effects._append_event(connection, execution.status_version, effect, now)
                    unresolved_rows = []
                control_pending = connection.execute(
                    "SELECT 1 FROM commands WHERE execution_id = ? AND kind IN (?, ?) "
                    "AND status IN (?, ?) LIMIT 1",
                    (execution_id, CommandKind.PAUSE.value, CommandKind.CANCEL.value,
                     CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value),
                ).fetchone() is not None
                request = agent_input.get("request") if isinstance(agent_input, Mapping) else {}
                interaction = request.get("interaction") if isinstance(request, Mapping) else None
                restart_pending = (
                    not unresolved_rows
                    and not control_pending
                    and interaction not in {"spawn", "merge"}
                    and cursor_covers_tools
                )

            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1",
                (execution_id,),
            ).fetchone() is not None
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
                restart_checkpoint = None if unresolved else crash_checkpoint(self, connection, execution)
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
                from ..restart import record_intent, window_seconds
                prior = self.attempts._require(connection, execution.current_attempt_id)
                record_intent(self.executions, connection, recovered,
                              interrupted_at=max(prior.updated_at, execution.updated_at),
                              seconds=window_seconds())
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

            command = None
            if running_commands:
                rows = connection.execute(
                    "SELECT * FROM commands WHERE execution_id = ? "
                    "AND status IN (?, ?) ORDER BY submitted_at, command_id",
                    (
                        execution_id,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                    ),
                ).fetchall()
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
        if attempt is not None:
            self.registry.unbind(
                execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        if recovered.status in TERMINAL_EXECUTION_STATUSES:
            self._forget_cancel_delivery(execution_id)
        return RecoveryCompletion(
            execution=recovered,
            attempt=attempt,
            command=command,
        )


    def recover_startup(self) -> tuple[RecoveryCompletion, ...]:
        """Recover nonterminal executions whose previous owner is gone.

        An admitted Agent execution is normally activated immediately. If a
        process stops after admission but before an attempt is leased, startup
        terminalizes that record instead of leaving it queued forever.
        """
        self.replay_finish_repairs()
        # A stalled repair is a durable, explicit recovery item. Startup may
        # retry it once through the same fenced path; if it remains blocked,
        # its marker prevents generic owner-loss recovery from changing the
        # desired outcome.
        self.replay_finish_repairs(include_stalled=True, due_only=True)
        stalled_repairs = set()
        repair_cursor = None
        while True:
            repair_page = self.executions.list_finish_repairs(
                limit=256,
                include_stalled=True,
                after=repair_cursor,
            )
            if not repair_page:
                break
            for repair in repair_page:
                repair_cursor = (
                    float(repair["created_at"]),
                    str(repair["execution_id"]),
                    str(repair["attempt_id"]),
                    int(repair["generation"]),
                )
                if repair.get("reason_code") == "finish_repair_stalled":
                    stalled_repairs.add(str(repair["execution_id"]))
        recoveries = []
        for execution in self.executions.list_nonterminal():
            from ..process_owner import process_owner_may_be_alive
            owner_attempt = (self.attempts.get(execution.current_attempt_id)
                             if execution.current_attempt_id else None)
            if process_owner_may_be_alive(
                execution.owner_lease,
                lease_expires_at=owner_attempt.lease_expires_at if owner_attempt else None,
            ):
                continue
            if (
                execution.execution_id in stalled_repairs
                or execution.reason_code == "finish_repair_capacity_migration"
            ):
                # A bounded in-process retry explicitly dead-lettered this
                # finish. Keep its owner state visible for manual reconcile;
                # startup owner-loss recovery must not overwrite the repair.
                continue
            if execution.status in {
                ExecutionStatus.RUNNING,
                ExecutionStatus.PAUSING,
                ExecutionStatus.CANCELLING,
            } or (
                execution.status in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}
                and execution.current_attempt_id is not None
            ):
                recovery = self.recover_owner_loss(
                    execution.execution_id, only_if_abandoned=True,
                )
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
                        request = admission.get("request", {})
                        restart = (
                            admission.get("kind") == "chat"
                            and request.get("interaction") not in {"spawn", "merge"}
                        )
                        recovered = self.executions._transition_execution(
                            connection, execution.execution_id,
                            expected_version=execution.status_version,
                            target=ExecutionStatus.PAUSED if restart else ExecutionStatus.FAILED,
                            reason_code="restart_pending" if restart else "owner_lost_before_activation",
                        )
                        if restart:
                            from ..restart import record_intent, window_seconds
                            record_intent(self.executions, connection, recovered,
                                          interrupted_at=current.updated_at, seconds=window_seconds())
                except (AttemptConflict, ExecutionConflict):
                    recovered = self.executions.get_execution(execution.execution_id)
                    if recovered is None:
                        continue
                recoveries.append(RecoveryCompletion(execution=recovered))
        return tuple(recoveries)


    def _replay_expired_finish_repair(
        self, execution_id: str, attempt_id: str, generation: int,
    ) -> bool:
        """Consume a durable completion after its exact owner's lease expires.

        This recovery transaction cannot dispatch work or revive a lease. It
        re-reads the persisted intent and fences the abandoned attempt while
        preserving cancellation and unresolved-effect authority.
        """
        with self.executions._transaction() as connection:
            repair = connection.execute(
                "SELECT * FROM execution_finish_repairs "
                "WHERE execution_id = ? AND attempt_id = ? AND generation = ?",
                (execution_id, attempt_id, generation),
            ).fetchone()
            if repair is None:
                raise AttemptConflict("not_found", "durable finish intent is missing")
            attempt = self.attempts._require(connection, attempt_id)
            self.attempts._validate_generation(attempt, generation)
            self.attempts._validate_not_fenced(attempt)
            if attempt.execution_id != execution_id or attempt.status is not AttemptStatus.ACTIVE:
                raise AttemptConflict("stale_owner", "finish intent has no active owner")
            if attempt.lease_expires_at > self.attempts._clock():
                return False
            execution = self.executions._require_execution(connection, execution_id)
            self.attempts._validate_owner(execution, attempt, execution.status_version)
            target = ExecutionStatus(repair["target"])
            if target not in TERMINAL_EXECUTION_STATUSES:
                raise AttemptConflict("invalid_outcome", "finish intent must be terminal")
            outcome, reason_code = repair["outcome"], repair["reason_code"]
            command = None
            if execution.status is ExecutionStatus.CANCELLING:
                command = self._applying_command(connection, execution_id, CommandKind.CANCEL)
                if command is None:
                    raise AttemptConflict("command_mismatch", "cancellation has no applying command")
                target, outcome = ExecutionStatus.CANCELLED, "cancelled"
                reason_code = execution.reason_code or "cancelled"
            elif repair["command_id"]:
                command = self.executions._get_command(connection, repair["command_id"])
                if (command is None or command.execution_id != execution_id
                        or command.status is not CommandStatus.APPLYING):
                    raise AttemptConflict("command_mismatch", "finish intent command is not applying")
            unresolved = connection.execute(
                "SELECT 1 FROM effects WHERE execution_id = ? "
                "AND status IN ('dispatched', 'uncertain') LIMIT 1", (execution_id,),
            ).fetchone() is not None
            if unresolved:
                target, outcome = ExecutionStatus.RECONCILIATION_REQUIRED, "reconciliation_required"
                reason_code = "effect_reconciliation"
            elif reason_code == "finish_repair_stalled":
                reason_code = None
            completed = self.executions._transition_execution(
                connection, execution_id, expected_version=execution.status_version,
                target=target, reason_code=reason_code, clear_owner=True,
            )
            ended = self.attempts._end_for_owner_loss(connection, attempt, outcome=outcome)
            self.executions._append_event(
                connection, execution_id=execution_id,
                execution_version=completed.status_version, kind="attempt.ended",
                payload={"attempt": ended.to_dict()}, created_at=ended.updated_at,
            )
            if command is not None and completed.status in TERMINAL_EXECUTION_STATUSES:
                self.executions._transition_command(
                    connection, command.command_id, expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED, result_version=completed.status_version,
                )
        self.registry.unbind(execution_id, attempt_id=attempt_id, generation=generation)
        if completed.status in TERMINAL_EXECUTION_STATUSES:
            self._forget_cancel_delivery(execution_id)
        return True


    def replay_finish_repairs(
        self, *, include_stalled: bool = False, due_only: bool = False,
    ) -> int:
        """Replay durable Agent finish intents with current fencing state."""
        repaired = 0
        cursor = None
        while True:
            repairs = self.executions.list_finish_repairs(
                limit=256,
                include_stalled=include_stalled,
                after=cursor,
                due_before=time.time() if due_only else None,
            )
            if not repairs:
                break
            for repair in repairs:
                cursor = (
                    float(repair["created_at"]),
                    str(repair["execution_id"]),
                    str(repair["attempt_id"]),
                    int(repair["generation"]),
                )
                if (
                    repair.get("reason_code") == "finish_repair_stalled"
                    and not include_stalled
                ):
                    continue
                execution_id = str(repair["execution_id"])
                attempt_id = str(repair["attempt_id"])
                generation = int(repair["generation"])
                execution = self.executions.get_execution(execution_id)
                attempt = self.attempts.get(attempt_id)
                if (
                    execution is None
                    or attempt is None
                    or execution.status in TERMINAL_EXECUTION_STATUSES
                    or execution.current_attempt_id != attempt_id
                    or execution.owner_lease.get("generation") != generation
                    or attempt.generation != generation
                    or attempt.status is not AttemptStatus.ACTIVE
                ):
                    self.executions.delete_finish_repair(
                        execution_id, attempt_id, generation,
                    )
                    repaired += 1
                    continue
                try:
                    target = ExecutionStatus(str(repair["target"]))
                except ValueError:
                    self.executions.delete_finish_repair(
                        execution_id, attempt_id, generation,
                    )
                    repaired += 1
                    continue
                outcome = str(repair["outcome"])
                reason_code = repair.get("reason_code")
                command_id = repair.get("command_id")
                if execution.status is ExecutionStatus.CANCELLING:
                    target = ExecutionStatus.CANCELLED
                    outcome = "cancelled"
                    reason_code = execution.reason_code or "cancelled"
                    applying_cancels = self.executions.list_commands(
                        execution_id,
                        statuses=(CommandStatus.APPLYING,),
                        kinds=(CommandKind.CANCEL,),
                    )
                    command_id = (
                        applying_cancels[0].command_id
                        if applying_cancels
                        else None
                    )
                    if command_id is None:
                        # A cancelling execution with an active attempt must
                        # be completed through its applying cancel command.
                        continue
                    self.executions.upsert_finish_repair(
                        execution_id=execution_id,
                        attempt_id=attempt_id,
                        generation=generation,
                        expected_version=execution.status_version,
                        target=target.value,
                        outcome=outcome,
                        reason_code=reason_code,
                        command_id=command_id,
                    )
                try:
                    if not self._replay_expired_finish_repair(execution_id, attempt_id, generation):
                        self.finish_attempt(
                            attempt_id=attempt_id,
                            generation=generation,
                            expected_execution_version=execution.status_version,
                            target=target,
                            outcome=outcome,
                            command_id=(str(command_id) if command_id else None),
                            reason_code=reason_code,
                        )
                except Exception:
                    retry_count = int(repair.get("retry_count") or 0) + 1
                    try:
                        self.executions.defer_finish_repair(
                            execution_id,
                            attempt_id,
                            generation,
                            retry_count=retry_count,
                            next_attempt_at=time.time() + min(
                                3600.0, 30.0 * (2 ** min(retry_count, 7))
                            ),
                        )
                    except Exception:
                        continue
                    continue
                self.executions.delete_finish_repair(
                    execution_id, attempt_id, generation,
                )
                repaired += 1
        return repaired

