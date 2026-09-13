"""Execution persistence: commands."""
from __future__ import annotations

import hashlib



import sqlite3

import time



from contextlib import closing



from typing import Any, Callable, Collection, Mapping



from ..model import CommandKind, CommandStatus, ControlCommand, ExecutionRecord, ExecutionStatus, TERMINAL_COMMAND_STATUSES, TERMINAL_EXECUTION_STATUSES, _json

from ..state_machine import InvalidCommand, validate_command, validate_transition

from .shared import (
    CommandConflict,
    ExecutionConflict,
    _fingerprint,
)

class CommandsOperations:
    def accept_command(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
    ) -> ControlCommand:
        with self._transaction() as connection:
            command, _ = self._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload=payload,
                actor=actor,
            )
            return command


    def accept_initial_job_step(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
    ) -> ControlCommand:
        """Accept the Job-only queued initial-step exception.

        Generic executions still reject ``step`` while queued.  A Job is
        identified by its immutable, strict ``JobAgentInputV1`` record before
        this exception is admitted; a resource claimant later creates the
        attempt that consumes the one provider decision.
        """
        from openprogram.agent.job.input import JobAgentInputV1
        raw_input = self.get_job_agent_input(execution_id)
        if raw_input is None:
            raise InvalidCommand("invalid_state", "initial step requires a Job execution")
        JobAgentInputV1.parse(raw_input)
        with self._transaction() as connection:
            command, _ = self._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=CommandKind.STEP,
                payload=payload,
                actor=actor,
                allow_queued_initial_step=True,
            )
            return command


    def accept_command_with_transition(
        self,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        target: ExecutionStatus,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
        reason_code: str | None = None,
        supersede_kinds: Collection[CommandKind] = (),
        supersede_code: str = "superseded",
        apply_command: bool = False,
        after_transition: Callable[[sqlite3.Connection, ExecutionRecord], None] | None = None,
    ) -> tuple[ControlCommand, ExecutionRecord, bool]:
        with self._transaction() as connection:
            command, duplicate = self._accept_command(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                payload=payload,
                actor=actor,
            )
            if duplicate:
                execution = self._require_execution(connection, execution_id)
                return command, execution, True
            execution = self._transition_execution(
                connection,
                execution_id,
                expected_version=expected_version,
                target=target,
                reason_code=reason_code,
            )
            if after_transition is not None:
                after_transition(connection, execution)
            if supersede_kinds:
                values = tuple(kind.value for kind in supersede_kinds)
                placeholders = ",".join("?" for _ in values)
                rows = connection.execute(
                    "SELECT command_id, status FROM commands "
                    "WHERE execution_id = ? AND command_id != ? "
                    "AND status IN (?, ?) "
                    f"AND kind IN ({placeholders})",
                    (
                        execution_id,
                        command_id,
                        CommandStatus.ACCEPTED.value,
                        CommandStatus.APPLYING.value,
                        *values,
                    ),
                ).fetchall()
                for row in rows:
                    self._transition_command(
                        connection,
                        str(row["command_id"]),
                        expected_status=CommandStatus(row["status"]),
                        target=CommandStatus.REJECTED,
                        result_version=execution.status_version,
                        rejection_code=supersede_code,
                    )
            command = self._transition_command(
                connection,
                command_id,
                expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.APPLYING,
            )
            if apply_command:
                command = self._transition_command(
                    connection,
                    command_id,
                    expected_status=CommandStatus.APPLYING,
                    target=CommandStatus.APPLIED,
                    result_version=execution.status_version,
                )
            return command, execution, False


    def get_command(self, command_id: str) -> ControlCommand | None:
        with closing(self._connect()) as connection:
            return self._get_command(connection, command_id)


    def list_commands(
        self,
        execution_id: str,
        *,
        statuses: Collection[CommandStatus] = (),
        kinds: Collection[CommandKind] = (),
    ) -> list[ControlCommand]:
        query = "SELECT * FROM commands WHERE execution_id = ?"
        values: list[Any] = [execution_id]
        if statuses:
            status_values = tuple(status.value for status in statuses)
            query += " AND status IN (" + ",".join("?" for _ in status_values) + ")"
            values.extend(status_values)
        if kinds:
            kind_values = tuple(kind.value for kind in kinds)
            query += " AND kind IN (" + ",".join("?" for _ in kind_values) + ")"
            values.extend(kind_values)
        query += " ORDER BY submitted_at, command_id"
        with closing(self._connect()) as connection:
            return [self._command(row) for row in connection.execute(query, values)]


    def transition_command(
        self,
        command_id: str,
        *,
        expected_status: CommandStatus,
        target: CommandStatus,
        result_version: int | None = None,
        rejection_code: str | None = None,
        receipt: Mapping[str, Any] | None = None,
    ) -> ControlCommand:
        with self._transaction() as connection:
            return self._transition_command(
                connection,
                command_id,
                expected_status=expected_status,
                target=target,
                result_version=result_version,
                rejection_code=rejection_code,
                receipt=receipt,
            )


    def _accept_command(
        self,
        connection: sqlite3.Connection,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
        allow_queued_initial_step: bool = False,
    ) -> tuple[ControlCommand, bool]:
        fingerprint = _fingerprint(
            {
                "execution_id": execution_id,
                "expected_version": expected_version,
                "kind": kind.value,
                "payload": dict(payload),
                "actor": dict(actor),
            }
        )
        existing_row = connection.execute(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if existing_row is not None:
            if existing_row["fingerprint"] != fingerprint:
                raise CommandConflict(
                    "idempotency_collision",
                    f"command_id was already used for a different request: {command_id}",
                )
            return self._command(existing_row), True

        execution = self._require_execution(connection, execution_id)
        if execution.status_version != expected_version:
            raise ExecutionConflict(
                "stale_version",
                f"expected execution version {expected_version}, "
                f"found {execution.status_version}",
            )
        if not (
            allow_queued_initial_step
            and kind is CommandKind.STEP
            and execution.status is ExecutionStatus.QUEUED
            and execution.capabilities.step
        ):
            validate_command(kind, execution.status, execution.capabilities)
        if kind in {CommandKind.CONTINUE, CommandKind.STEP}:
            pending = connection.execute(
                "SELECT command_id FROM commands WHERE execution_id = ? "
                "AND kind IN (?, ?) AND status IN (?, ?) LIMIT 1",
                (
                    execution_id,
                    CommandKind.CONTINUE.value,
                    CommandKind.STEP.value,
                    CommandStatus.ACCEPTED.value,
                    CommandStatus.APPLYING.value,
                ),
            ).fetchone()
            if pending is not None:
                raise CommandConflict(
                    "continuation_pending",
                    "a continue or step command already owns this execution version",
                )
        now = time.time()
        command = ControlCommand(
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=kind,
            payload=dict(payload),
            actor=dict(actor),
            status=CommandStatus.ACCEPTED,
            submitted_at=now,
            updated_at=now,
        )
        connection.execute(
            "INSERT INTO commands "
            "(command_id, execution_id, expected_version, kind, payload_json, "
            "actor_json, fingerprint, status, submitted_at, updated_at, "
            "result_version, rejection_code, result_json) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command.command_id,
                command.execution_id,
                command.expected_version,
                command.kind.value,
                _json(command.payload),
                _json(command.actor),
                fingerprint,
                command.status.value,
                command.submitted_at,
                command.updated_at,
                command.result_version,
                command.rejection_code,
                _json(command.result_json),
            ),
        )
        self._append_event(
            connection,
            execution_id=execution_id,
            execution_version=execution.status_version,
            command_id=command_id,
            kind="command.accepted",
            payload={"command": command.to_dict()},
            created_at=now,
        )
        self._append_audit_event(
            connection,
            execution=execution,
            actor=command.actor,
            action=command.kind.value,
            result=command.status.value,
            surface=str(command.actor.get("surface") or "runtime"),
            payload=command.payload,
            command_id=command.command_id,
            draft_id=None,
            wait_id=(command.payload.get("wait_id") if isinstance(command.payload, Mapping) else None),
            correlation_id=command.command_id,
            source_version=execution.status_version,
            checkpoint_id=execution.checkpoint_head_id,
            reason_code=None,
            evidence_refs=(),
            project_binding=self._project_binding(execution),
        )
        return command, False


    def _transition_execution(
        self,
        connection: sqlite3.Connection,
        execution_id: str,
        *,
        expected_version: int,
        target: ExecutionStatus,
        reason_code: str | None,
        clear_owner: bool = False,
    ) -> ExecutionRecord:
        current = self._require_execution(connection, execution_id)
        if current.status_version != expected_version:
            raise ExecutionConflict(
                "stale_version",
                f"expected execution version {expected_version}, "
                f"found {current.status_version}",
            )
        if current.status in TERMINAL_EXECUTION_STATUSES:
            raise ExecutionConflict(
                "terminal", f"execution is already {current.status.value}"
            )
        if current.status is not target or not clear_owner:
            validate_transition(current.status, target)
        now = time.time()
        terminal_at = now if target in TERMINAL_EXECUTION_STATUSES else None
        new_version = expected_version + 1
        if clear_owner:
            updated = connection.execute(
                "UPDATE executions SET status = ?, status_version = ?, "
                "reason_code = ?, updated_at = ?, terminal_at = ?, "
                "current_attempt_id = NULL, owner_lease_json = '{}' "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    target.value,
                    new_version,
                    reason_code,
                    now,
                    terminal_at,
                    execution_id,
                    expected_version,
                ),
            )
        else:
            updated = connection.execute(
                "UPDATE executions SET status = ?, status_version = ?, "
                "reason_code = ?, updated_at = ?, terminal_at = ? "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    target.value,
                    new_version,
                    reason_code,
                    now,
                    terminal_at,
                    execution_id,
                    expected_version,
                ),
            )
        if updated.rowcount != 1:
            raise ExecutionConflict("stale_version", "execution changed concurrently")
        if target in TERMINAL_EXECUTION_STATUSES or (
            target is ExecutionStatus.RECONCILIATION_REQUIRED and clear_owner
        ):
            # Admission and owner closure share this transaction. A steer
            # arriving after the last safe point must get a definitive receipt,
            # including when unresolved effects end the attempt for reconciliation.
            pending_steers = connection.execute(
                "SELECT command_id, status FROM commands WHERE execution_id = ? "
                "AND kind = ? AND status IN (?, ?) "
                "AND EXISTS (SELECT 1 FROM execution_agent_turn_inputs "
                "WHERE execution_id = commands.execution_id)",
                (execution_id, CommandKind.STEER.value,
                 CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value),
            ).fetchall()
            for steer in pending_steers:
                status = CommandStatus(steer["status"])
                message_id = hashlib.sha256(
                    f"{current.session_id}:{steer['command_id']}".encode()
                ).hexdigest()[:24]
                # Agent messages live in the session Git store. Recover a
                # completed branch-linked write whose SQL receipt failed (including
                # a rolled-back ACCEPTED -> APPLYING transition on resume).
                # Delivery holds this same transaction lock around the write.
                from openprogram.agent.session_db import default_db
                turn_input = connection.execute(
                    "SELECT assistant_message_id FROM execution_inputs WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                delivered = bool(turn_input) and default_db().has_persisted_ancestor(
                    current.session_id, message_id, str(turn_input["assistant_message_id"]),
                )
                if delivered and status is CommandStatus.ACCEPTED:
                    self._transition_command(
                        connection, str(steer["command_id"]),
                        expected_status=status, target=CommandStatus.APPLYING,
                    )
                    status = CommandStatus.APPLYING
                self._transition_command(
                    connection, str(steer["command_id"]),
                    expected_status=status,
                    target=CommandStatus.APPLIED if delivered else CommandStatus.REJECTED,
                    result_version=new_version,
                    rejection_code=None if delivered else "execution_finished",
                    receipt={"user_message_id": message_id} if delivered else None,
                )
        if target in TERMINAL_EXECUTION_STATUSES:
            connection.execute(
                "DELETE FROM execution_finish_repair_slots WHERE execution_id = ?",
                (execution_id,),
            )
        record = self._require_execution(connection, execution_id)
        self._append_event(
            connection,
            execution_id=execution_id,
            execution_version=record.status_version,
            kind="execution.updated",
            payload={"record": record.to_dict()},
            created_at=now,
        )
        return record


    def _transition_command(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        *,
        expected_status: CommandStatus,
        target: CommandStatus,
        result_version: int | None = None,
        rejection_code: str | None = None,
        receipt: Mapping[str, Any] | None = None,
        result_json: Mapping[str, Any] | None = None,
    ) -> ControlCommand:
        current = self._get_command(connection, command_id)
        if current is None:
            raise CommandConflict("not_found", f"command not found: {command_id}")
        if current.status in TERMINAL_COMMAND_STATUSES:
            raise CommandConflict(
                "terminal", f"command is already {current.status.value}"
            )
        if current.status is not expected_status:
            raise CommandConflict(
                "stale_status",
                f"expected command status {expected_status.value}, "
                f"found {current.status.value}",
            )
        allowed = {
            CommandStatus.ACCEPTED: {
                CommandStatus.APPLYING,
                CommandStatus.REJECTED,
            },
            CommandStatus.APPLYING: {
                CommandStatus.APPLIED,
                CommandStatus.REJECTED,
            },
        }
        if target not in allowed[current.status]:
            raise CommandConflict(
                "invalid_transition",
                f"invalid command transition: {current.status.value} -> {target.value}",
            )
        now = time.time()
        connection.execute(
            "UPDATE commands SET status = ?, updated_at = ?, "
            "result_version = ?, rejection_code = ?, result_json = ? WHERE command_id = ?",
            (
                target.value,
                now,
                result_version,
                rejection_code,
                _json(result_json or {}),
                command_id,
            ),
        )
        command = self._get_command(connection, command_id)
        assert command is not None
        payload: dict[str, Any] = {"command": command.to_dict()}
        if receipt is not None:
            payload["receipt"] = dict(receipt)
        if result_json is not None:
            payload["result"] = dict(result_json)
        self._append_event(
            connection,
            execution_id=command.execution_id,
            execution_version=result_version,
            command_id=command_id,
            kind=f"command.{target.value}",
            payload=payload,
            created_at=now,
        )
        execution = self._require_execution(connection, command.execution_id)
        self._append_audit_event(
            connection,
            execution=execution,
            actor=command.actor,
            action=command.kind.value,
            result=command.status.value,
            surface=str(command.actor.get("surface") or "runtime"),
            payload=command.payload,
            command_id=command.command_id,
            draft_id=None,
            wait_id=(command.payload.get("wait_id") if isinstance(command.payload, Mapping) else None),
            correlation_id=command.command_id,
            source_version=result_version,
            checkpoint_id=execution.checkpoint_head_id,
            reason_code=rejection_code,
            evidence_refs=(),
            project_binding=self._project_binding(execution),
        )
        return command

