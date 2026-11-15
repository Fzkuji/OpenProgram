"""Execution persistence: commands."""
from __future__ import annotations

import hashlib

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
from .run_tables import TableHandle


def _cancel_fingerprint(
    execution_id: str, reason_code: str, actor: Mapping[str, Any],
) -> str:
    """A cancel's request identity. It names no version: a stop does not
    depend on one."""
    return _fingerprint({
        "execution_id": execution_id,
        "kind": CommandKind.CANCEL.value,
        "payload": {"reason_code": reason_code},
        "actor": dict(actor),
    })


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
        from ..job_input import JobAgentInputV1
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
        after_transition: Callable[[TableHandle, ExecutionRecord], None] | None = None,
    ) -> tuple[ControlCommand, ExecutionRecord, bool]:
        with self._transaction() as connection:
            existing_row = connection.get("commands", command_id)
            if existing_row is not None:
                command, _ = self._accept_command(
                    connection,
                    command_id=command_id,
                    execution_id=execution_id,
                    expected_version=expected_version,
                    kind=kind,
                    payload=payload,
                    actor=actor,
                )
                execution = self._require_execution(connection, execution_id)
                return command, execution, True
            command, execution = self._accept_with_transition(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=expected_version,
                kind=kind,
                target=target,
                payload=payload,
                actor=actor,
                reason_code=reason_code,
                supersede_kinds=supersede_kinds,
                supersede_code=supersede_code,
                apply_command=apply_command,
                after_transition=after_transition,
            )
            return command, execution, False


    def _accept_with_transition(
        self,
        connection: TableHandle,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        target: ExecutionStatus,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
        reason_code: str | None,
        supersede_kinds: Collection[CommandKind],
        supersede_code: str,
        apply_command: bool = False,
        after_transition: Callable[[TableHandle, ExecutionRecord], None] | None = None,
        fingerprint: str | None = None,
    ) -> tuple[ControlCommand, ExecutionRecord]:
        """Write a new command and the execution transition it causes."""
        command, _ = self._accept_command(
            connection,
            command_id=command_id,
            execution_id=execution_id,
            expected_version=expected_version,
            kind=kind,
            payload=payload,
            actor=actor,
            fingerprint=fingerprint,
        )
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
            rows = connection.rows(
                "commands",
                execution_id=execution_id,
                status=(CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value),
                kind=tuple(superseded.value for superseded in supersede_kinds),
                where=lambda row: row["command_id"] != command_id,
            )
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
        return command, execution


    def accept_cancel(
        self,
        *,
        command_id: str,
        execution_id: str,
        actor: Mapping[str, Any],
        reason_code: str,
        supersede_kinds: Collection[CommandKind] = (),
        after_transition: Callable[[TableHandle, ExecutionRecord], None] | None = None,
        at_version: int | None = None,
    ) -> tuple[ControlCommand | None, ExecutionRecord, str]:
        """Take a stop of ``execution_id`` at whatever version it holds now.

        A stop names its execution, not a version: the caller's intent is
        that the run ends, whichever state it moved to since it last looked.
        One transaction reads the execution and answers by its state:

        ``"accepted"``  a new command, the execution moved to ``cancelling``;
        ``"replayed"``  this command id was taken before (same request);
        ``"cancelling"`` an earlier stop is in force: its command comes back;
        ``"terminal"``  the execution has ended; there is no command.

        ``at_version`` is only for a caller that prepared something for one
        exact snapshot (a Job's dispatch fence); it refuses a moved version.
        """
        with self._transaction() as connection:
            existing_row = connection.get("commands", command_id)
            if existing_row is not None:
                # The same id stopping the same execution is the same stop,
                # whoever repeats it and whatever reason it gives now (a
                # parent's stop and the run's own limit reach one child).
                if (
                    existing_row["execution_id"] != execution_id
                    or existing_row["kind"] != CommandKind.CANCEL.value
                ):
                    raise CommandConflict(
                        "idempotency_collision",
                        f"command_id was already used for a different request: {command_id}",
                    )
                return (
                    self._command(existing_row),
                    self._require_execution(connection, execution_id),
                    "replayed",
                )
            execution = self._require_execution(connection, execution_id)
            if execution.status in TERMINAL_EXECUTION_STATUSES:
                return None, execution, "terminal"
            if execution.status is ExecutionStatus.CANCELLING:
                row = connection.first(
                    "commands",
                    order=("submitted_at", "command_id"),
                    execution_id=execution_id,
                    kind=CommandKind.CANCEL.value,
                    status=CommandStatus.APPLYING.value,
                )
                if row is not None:
                    return self._command(row), execution, "cancelling"
            if at_version is not None and execution.status_version != at_version:
                raise ExecutionConflict(
                    "stale_version",
                    f"expected execution version {at_version}, "
                    f"found {execution.status_version}",
                )
            command, execution = self._accept_with_transition(
                connection,
                command_id=command_id,
                execution_id=execution_id,
                expected_version=execution.status_version,
                kind=CommandKind.CANCEL,
                target=ExecutionStatus.CANCELLING,
                payload={"reason_code": reason_code},
                actor=actor,
                reason_code=reason_code,
                supersede_kinds=supersede_kinds,
                supersede_code="superseded_by_cancel",
                after_transition=after_transition,
                fingerprint=_cancel_fingerprint(execution_id, reason_code, actor),
            )
            return command, execution, "accepted"


    def get_command(self, command_id: str) -> ControlCommand | None:
        with closing(self._connect()) as connection:
            return self._get_command(connection, command_id)


    def withdraw_steer(self, command_id: str) -> tuple[ControlCommand | None, bool]:
        """Take back a steer that no safe point has started applying.

        Returns the command and whether it is withdrawn. Only an ACCEPTED
        steer whose message has not reached the turn can be taken back; it
        closes as REJECTED with ``withdrawn``. One that is applying, applied
        or already written into the conversation stays as it is, and the
        caller reports that the message was delivered.
        """
        with self._transaction() as connection:
            command = self._get_command(connection, command_id)
            if command is None or command.kind is not CommandKind.STEER:
                return None, False
            if command.status is not CommandStatus.ACCEPTED:
                return command, (
                    command.status is CommandStatus.REJECTED
                    and command.rejection_code == "withdrawn"
                )
            execution = self._require_execution(connection, command.execution_id)
            # A delivery whose receipt failed to commit left the message in
            # the conversation; the next safe point or the turn's end
            # records it as applied.
            if self._steer_delivered(connection, execution, command_id)[1]:
                return command, False
            return self._transition_command(
                connection, command_id,
                expected_status=CommandStatus.ACCEPTED,
                target=CommandStatus.REJECTED,
                rejection_code="withdrawn",
            ), True

    def _steer_delivered(
        self, connection: TableHandle, execution: ExecutionRecord, command_id: str,
    ) -> tuple[str, bool]:
        """The steer's user message id and whether the turn's branch holds it."""
        from openprogram.runtime.context.session_db import default_db

        message_id = hashlib.sha256(
            f"{execution.session_id}:{command_id}".encode()
        ).hexdigest()[:24]
        turn_input = connection.get("execution_inputs", execution.execution_id)
        return message_id, bool(turn_input) and default_db().has_persisted_ancestor(
            execution.session_id, message_id, str(turn_input["assistant_message_id"]),
        )


    def list_commands(
        self,
        execution_id: str,
        *,
        statuses: Collection[CommandStatus] = (),
        kinds: Collection[CommandKind] = (),
    ) -> list[ControlCommand]:
        filters: dict[str, Any] = {}
        if statuses:
            filters["status"] = [status.value for status in statuses]
        if kinds:
            filters["kind"] = [kind.value for kind in kinds]
        with closing(self._connect()) as connection:
            return [self._command(row) for row in connection.rows(
                "commands", execution_id=execution_id, order=("submitted_at", "command_id"), **filters,
            )]


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
        connection: TableHandle,
        *,
        command_id: str,
        execution_id: str,
        expected_version: int,
        kind: CommandKind,
        payload: Mapping[str, Any],
        actor: Mapping[str, Any],
        allow_queued_initial_step: bool = False,
        fingerprint: str | None = None,
    ) -> tuple[ControlCommand, bool]:
        if fingerprint is None:
            fingerprint = _fingerprint(
                {
                    "execution_id": execution_id,
                    "expected_version": expected_version,
                    "kind": kind.value,
                    "payload": dict(payload),
                    "actor": dict(actor),
                }
            )
        existing_row = connection.get("commands", command_id)
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
            if connection.exists(
                "commands",
                execution_id=execution_id,
                kind=(CommandKind.CONTINUE.value, CommandKind.STEP.value),
                status=(CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value),
            ):
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
        connection.insert("commands", {
            "command_id": command.command_id,
            "execution_id": command.execution_id,
            "expected_version": command.expected_version,
            "kind": command.kind.value,
            "payload_json": _json(command.payload),
            "actor_json": _json(command.actor),
            "fingerprint": fingerprint,
            "status": command.status.value,
            "submitted_at": command.submitted_at,
            "updated_at": command.updated_at,
            "result_version": command.result_version,
            "rejection_code": command.rejection_code,
            "result_json": _json(command.result_json),
        })
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
        connection: TableHandle,
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
        changes: dict[str, Any] = {
            "status": target.value,
            "status_version": new_version,
            "reason_code": reason_code,
            "updated_at": now,
            "terminal_at": terminal_at,
        }
        if clear_owner:
            changes.update(current_attempt_id=None, owner_lease_json="{}")
        updated = connection.update(
            "executions", changes, execution_id=execution_id, status_version=expected_version,
        )
        if updated != 1:
            raise ExecutionConflict("stale_version", "execution changed concurrently")
        if target in TERMINAL_EXECUTION_STATUSES or (
            target is ExecutionStatus.RECONCILIATION_REQUIRED and clear_owner
        ):
            # Admission and owner closure share this transaction. A steer
            # arriving after the last safe point must get a definitive receipt,
            # including when unresolved effects end the attempt for reconciliation.
            pending_steers = connection.rows(
                "commands",
                execution_id=execution_id,
                kind=CommandKind.STEER.value,
                status=(CommandStatus.ACCEPTED.value, CommandStatus.APPLYING.value),
            ) if connection.exists("execution_agent_turn_inputs", execution_id=execution_id) else []
            for steer in pending_steers:
                status = CommandStatus(steer["status"])
                # Agent messages live in the session Git store. Recover a
                # completed branch-linked write whose receipt failed (including
                # a rolled-back ACCEPTED -> APPLYING transition on resume).
                # Delivery holds this same transaction lock around the write.
                message_id, delivered = self._steer_delivered(
                    connection, current, str(steer["command_id"]),
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
        connection: TableHandle,
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
        connection.update(
            "commands",
            {
                "status": target.value,
                "updated_at": now,
                "result_version": result_version,
                "rejection_code": rejection_code,
                "result_json": _json(result_json or {}),
            },
            command_id=command_id,
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

