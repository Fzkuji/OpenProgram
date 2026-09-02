"""Execution-owned durable question and approval waits.

Wait records are canonical state in the execution SQLite store.  Local
events may wake a currently running Python thread, but never decide whether a
question is open, answered, declined, expired, or cancelled.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .model import CommandKind, CommandStatus, ControlCommand, ExecutionStatus
from .store import ExecutionConflict, ExecutionStore, _json


class WaitStatus(str, Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_TERMINAL_WAIT_STATUSES = frozenset({
    WaitStatus.RESOLVED, WaitStatus.DECLINED, WaitStatus.EXPIRED, WaitStatus.CANCELLED,
})
_WAIT_KINDS = frozenset({"ask", "confirm", "approval", "form", "ask_many"})
_MAX_WAIT_REQUEST_BYTES = 256 * 1024
_MAX_WAIT_ANSWER_BYTES = 64 * 1024


@dataclass(frozen=True)
class WaitRecord:
    wait_id: str
    execution_id: str
    attempt_id: str
    generation: int
    checkpoint_id: str | None
    kind: str
    request_ref: str
    request_hash: str
    policy_snapshot_ref: str
    status: WaitStatus
    claim_generation: int
    claim_owner: str | None
    claim_expires_at: float | None
    answer_ref: str | None
    outcome: str | None
    created_at: float
    expires_at: float
    resolved_at: float | None
    updated_at: float
    request: Mapping[str, Any]
    policy_snapshot: Mapping[str, Any]
    answer: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "wait_id": self.wait_id, "execution_id": self.execution_id,
            "attempt_id": self.attempt_id, "generation": self.generation,
            "checkpoint_id": self.checkpoint_id, "kind": self.kind,
            "request_ref": self.request_ref, "request_hash": self.request_hash,
            "policy_snapshot_ref": self.policy_snapshot_ref,
            "status": self.status.value, "claim_generation": self.claim_generation,
            "claim_owner": self.claim_owner, "claim_expires_at": self.claim_expires_at,
            "answer_ref": self.answer_ref, "outcome": self.outcome,
            "created_at": self.created_at, "expires_at": self.expires_at,
            "resolved_at": self.resolved_at, "updated_at": self.updated_at,
            "request": dict(self.request), "policy_snapshot": dict(self.policy_snapshot),
            "answer": self.answer,
        }


class DurableWaitStore:
    """One wait authority for questions and approvals."""

    def __init__(self, executions: ExecutionStore) -> None:
        self.executions = executions

    @staticmethod
    def _encode(value: Mapping[str, Any] | Any, *, limit: int, field: str) -> bytes:
        try:
            encoded = _json(value).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ExecutionConflict("invalid_wait", f"{field} must be JSON") from exc
        if len(encoded) > limit:
            raise ExecutionConflict("wait_payload_too_large", f"{field} exceeds its size limit")
        return encoded

    def open_wait(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        kind: str,
        request: Mapping[str, Any],
        policy_snapshot: Mapping[str, Any],
        expires_at: float,
        checkpoint_id: str | None = None,
        wait_id: str | None = None,
    ) -> WaitRecord:
        with self.executions._transaction() as connection:
            wait_id = self._open_in_transaction(
                connection,
                execution_id=execution_id,
                attempt_id=attempt_id,
                generation=generation,
                kind=kind,
                request=request,
                policy_snapshot=policy_snapshot,
                expires_at=expires_at,
                checkpoint_id=checkpoint_id,
                wait_id=wait_id,
            )
        record = self.get_wait(wait_id)
        assert record is not None
        return record

    def _open_in_transaction(
        self,
        connection,
        *,
        execution_id: str,
        attempt_id: str,
        generation: int,
        kind: str,
        request: Mapping[str, Any],
        policy_snapshot: Mapping[str, Any],
        expires_at: float,
        checkpoint_id: str | None = None,
        wait_id: str | None = None,
    ) -> str:
        """Write one open wait while the caller holds the execution transaction.

        The public ``open_wait`` method and a safe-point handoff use the
        same validation and write path.  The latter publishes its checkpoint,
        wait, execution state, and ended attempt in one SQLite transaction.
        """
        if kind not in _WAIT_KINDS:
            raise ExecutionConflict("invalid_wait_kind", "unsupported wait kind")
        if not execution_id or not attempt_id or type(generation) is not int:
            raise ExecutionConflict("invalid_wait", "execution, attempt, and generation are required")
        if type(expires_at) not in {int, float}:
            raise ExecutionConflict("invalid_wait", "wait expiry must be numeric")
        request_bytes = self._encode(dict(request), limit=_MAX_WAIT_REQUEST_BYTES, field="request")
        policy_bytes = self._encode(dict(policy_snapshot), limit=_MAX_WAIT_REQUEST_BYTES, field="policy snapshot")
        now = time.time()
        if expires_at <= now:
            raise ExecutionConflict("invalid_wait_expiry", "wait expiry must be in the future")
        wait_id = wait_id or f"wait_{uuid.uuid4().hex}"
        execution = self.executions._require_execution(connection, execution_id)
        if execution.status in {
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED, ExecutionStatus.INTERRUPTED,
        }:
            raise ExecutionConflict("terminal", "terminal execution cannot open a wait")
        if (
            execution.current_attempt_id != attempt_id
            or execution.owner_lease.get("generation") != generation
        ):
            raise ExecutionConflict("stale_attempt", "wait owner is not the current attempt")
        attempt = connection.execute(
            "SELECT execution_id, generation, status FROM attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if attempt is None or attempt["execution_id"] != execution_id or int(attempt["generation"]) != generation or attempt["status"] != "active":
            raise ExecutionConflict("stale_attempt", "wait attempt is no longer active")
        if checkpoint_id is not None:
            checkpoint = connection.execute(
                "SELECT execution_id FROM checkpoints WHERE checkpoint_id = ?", (checkpoint_id,)
            ).fetchone()
            if checkpoint is None or checkpoint["execution_id"] != execution_id:
                raise ExecutionConflict("invalid_checkpoint", "wait checkpoint belongs to another execution")
        request_blob = self.executions._put_state_blob_in_transaction(
            connection, execution_id=execution_id, payload=request_bytes,
            media_type="application/json", schema_version=1,
        )
        policy_blob = self.executions._put_state_blob_in_transaction(
            connection, execution_id=execution_id, payload=policy_bytes,
            media_type="application/json", schema_version=1,
        )
        try:
            connection.execute(
                "INSERT INTO execution_waits (wait_id, execution_id, attempt_id, generation, checkpoint_id, kind, request_ref, request_hash, policy_snapshot_ref, status, claim_generation, claim_owner, claim_expires_at, answer_ref, outcome, created_at, expires_at, resolved_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, NULL, NULL, NULL, NULL, ?, ?, NULL, ?)",
                (wait_id, execution_id, attempt_id, generation, checkpoint_id, kind,
                 request_blob["ref"], hashlib.sha256(request_bytes).hexdigest(),
                 policy_blob["ref"], now, float(expires_at), now),
            )
        except Exception as exc:
            raise ExecutionConflict("wait_exists", f"wait already exists: {wait_id}") from exc
        for ref, name in ((request_blob["ref"], "request"), (policy_blob["ref"], "policy")):
            connection.execute(
                "INSERT OR IGNORE INTO execution_state_blob_refs (execution_id, ref, name, reference_kind, reference_id, created_at) VALUES (?, ?, ?, 'wait', ?, ?)",
                (execution_id, ref, name, wait_id, now),
            )
        self.executions._append_event(
            connection, execution_id=execution_id, execution_version=execution.status_version,
            kind="execution.wait.opened", payload={"wait_id": wait_id, "kind": kind, "expires_at": float(expires_at)}, created_at=now,
        )
        return wait_id

    def _decode_ref(self, execution_id: str, ref: str | None) -> Any:
        if not ref:
            return None
        value = self.executions.get_state_blob(execution_id, ref)
        if value is None:
            raise ExecutionConflict("wait_payload_missing", "wait payload reference is missing")
        try:
            return json.loads(bytes(value["payload"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutionConflict("wait_payload_invalid", "wait payload is invalid") from exc

    def _record(self, row) -> WaitRecord:
        request = self._decode_ref(str(row["execution_id"]), str(row["request_ref"]))
        policy = self._decode_ref(str(row["execution_id"]), str(row["policy_snapshot_ref"]))
        answer = self._decode_ref(str(row["execution_id"]), row["answer_ref"])
        if not isinstance(request, Mapping) or not isinstance(policy, Mapping):
            raise ExecutionConflict("wait_payload_invalid", "wait request and policy must be objects")
        return WaitRecord(
            wait_id=str(row["wait_id"]), execution_id=str(row["execution_id"]),
            attempt_id=str(row["attempt_id"]), generation=int(row["generation"]),
            checkpoint_id=row["checkpoint_id"], kind=str(row["kind"]),
            request_ref=str(row["request_ref"]), request_hash=str(row["request_hash"]),
            policy_snapshot_ref=str(row["policy_snapshot_ref"]), status=WaitStatus(str(row["status"])),
            claim_generation=int(row["claim_generation"]), claim_owner=row["claim_owner"],
            claim_expires_at=float(row["claim_expires_at"]) if row["claim_expires_at"] is not None else None,
            answer_ref=row["answer_ref"], outcome=row["outcome"],
            created_at=float(row["created_at"]), expires_at=float(row["expires_at"]),
            resolved_at=float(row["resolved_at"]) if row["resolved_at"] is not None else None,
            updated_at=float(row["updated_at"]), request=dict(request), policy_snapshot=dict(policy), answer=answer,
        )

    def get_wait(self, wait_id: str) -> WaitRecord | None:
        with self.executions._connect() as connection:
            row = connection.execute("SELECT * FROM execution_waits WHERE wait_id = ?", (wait_id,)).fetchone()
        return self._record(row) if row is not None else None

    def list_open(self, *, session_id: str | None = None, execution_id: str | None = None) -> list[WaitRecord]:
        clauses = ["w.status IN ('open', 'claimed')"]
        values: list[Any] = []
        if session_id is not None:
            clauses.append("e.session_id = ?")
            values.append(session_id)
        if execution_id is not None:
            clauses.append("w.execution_id = ?")
            values.append(execution_id)
        with self.executions._connect() as connection:
            rows = connection.execute(
                "SELECT w.* FROM execution_waits AS w JOIN executions AS e ON e.execution_id = w.execution_id WHERE "
                + " AND ".join(clauses) + " ORDER BY w.created_at, w.wait_id", values,
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_outcomes(self) -> list[WaitRecord]:
        """Return durable non-cancel outcomes for recovery scheduling."""
        with self.executions._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_waits WHERE status IN ('resolved', 'declined', 'expired') "
                "ORDER BY resolved_at, wait_id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def claim(self, wait_id: str, *, generation: int, owner_id: str, lease_ttl_seconds: float, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        if not owner_id or lease_ttl_seconds <= 0:
            raise ExecutionConflict("invalid_wait_claim", "owner and positive lease are required")
        with self.executions._transaction() as connection:
            row = connection.execute("SELECT * FROM execution_waits WHERE wait_id = ?", (wait_id,)).fetchone()
            if row is None:
                return False
            if row["status"] != WaitStatus.OPEN.value or int(row["claim_generation"]) != generation or float(row["expires_at"]) <= current:
                return False
            changed = connection.execute(
                "UPDATE execution_waits SET status = 'claimed', claim_owner = ?, claim_expires_at = ?, updated_at = ? WHERE wait_id = ? AND status = 'open' AND claim_generation = ?",
                (owner_id, current + lease_ttl_seconds, current, wait_id, generation),
            ).rowcount
            return changed == 1

    def reclaim_expired_claims(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self.executions._transaction() as connection:
            result = connection.execute(
                "UPDATE execution_waits SET status = 'open', claim_generation = claim_generation + 1, claim_owner = NULL, claim_expires_at = NULL, updated_at = ? WHERE status = 'claimed' AND claim_expires_at IS NOT NULL AND claim_expires_at <= ? AND expires_at > ?",
                (current, current, current),
            )
            return int(result.rowcount)

    def reclaim_orphaned_claims(self, *, now: float | None = None) -> int:
        """Return claims whose original execution owner is no longer live.

        Startup fences stale attempts before invoking this method.  A claim is
        therefore retained only while its exact attempt/generation remains the
        current active owner with an unexpired owner lease.
        """
        current = time.time() if now is None else float(now)
        with self.executions._transaction() as connection:
            result = connection.execute(
                "UPDATE execution_waits SET status = 'open', "
                "claim_generation = claim_generation + 1, claim_owner = NULL, "
                "claim_expires_at = NULL, updated_at = ? "
                "WHERE status = 'claimed' AND expires_at > ? AND NOT EXISTS ("
                "SELECT 1 FROM attempts AS a JOIN executions AS e "
                "ON e.execution_id = a.execution_id "
                "WHERE a.attempt_id = execution_waits.attempt_id "
                "AND a.execution_id = execution_waits.execution_id "
                "AND a.generation = execution_waits.generation "
                "AND a.status = 'active' "
                "AND e.current_attempt_id = a.attempt_id "
                "AND json_extract(e.owner_lease_json, '$.generation') = a.generation "
                "AND a.lease_expires_at > ?)",
                (current, current, current),
            )
            return int(result.rowcount)

    def expire_due(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self.executions._transaction() as connection:
            rows = connection.execute(
                "SELECT wait_id, execution_id FROM execution_waits WHERE status IN ('open', 'claimed') AND expires_at <= ?", (current,)
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE execution_waits SET status = 'expired', claim_owner = NULL, claim_expires_at = NULL, outcome = 'timeout', resolved_at = ?, updated_at = ? WHERE wait_id = ? AND status IN ('open', 'claimed')",
                    (current, current, row["wait_id"]),
                )
                execution = self.executions._require_execution(connection, row["execution_id"])
                self.executions._append_event(connection, execution_id=row["execution_id"], execution_version=execution.status_version, kind="execution.wait.expired", payload={"wait_id": row["wait_id"]}, created_at=current)
            return len(rows)

    def cancel_execution(self, execution_id: str) -> int:
        with self.executions._transaction() as connection:
            return self.cancel_execution_in_transaction(connection, execution_id)

    def cancel_execution_in_transaction(self, connection, execution_id: str) -> int:
        """Cancel unresolved waits in the caller's canonical transaction."""
        now = time.time()
        rows = connection.execute(
            "SELECT wait_id FROM execution_waits WHERE execution_id = ? AND status IN ('open', 'claimed')", (execution_id,)
        ).fetchall()
        if not rows:
            return 0
        connection.execute(
            "UPDATE execution_waits SET status = 'cancelled', claim_owner = NULL, claim_expires_at = NULL, outcome = 'cancelled', resolved_at = ?, updated_at = ? WHERE execution_id = ? AND status IN ('open', 'claimed')",
            (now, now, execution_id),
        )
        execution = self.executions._require_execution(connection, execution_id)
        for row in rows:
            self.executions._append_event(
                connection, execution_id=execution_id,
                execution_version=execution.status_version,
                kind="execution.wait.cancelled", payload={"wait_id": row["wait_id"]},
                created_at=now,
            )
        return len(rows)

    def resolve_with_command(
        self,
        *, command_id: str, execution_id: str, expected_version: int,
        actor: Mapping[str, Any], kind: CommandKind, wait_id: str,
        generation: int, answer: Any = None,
    ) -> tuple[ControlCommand, WaitRecord, bool]:
        if kind not in {CommandKind.WAIT_ANSWER, CommandKind.WAIT_DECLINE}:
            raise ExecutionConflict("invalid_wait_command", "unsupported wait command")
        if not wait_id or type(generation) is not int:
            raise ExecutionConflict("invalid_wait", "wait id and generation are required")
        payload: dict[str, Any] = {"wait_id": wait_id, "generation": generation}
        if kind is CommandKind.WAIT_ANSWER:
            payload["answer"] = answer
            answer_bytes = self._encode(answer, limit=_MAX_WAIT_ANSWER_BYTES, field="answer")
        else:
            if answer is not None:
                payload["reason"] = answer
            answer_bytes = self._encode(answer, limit=_MAX_WAIT_ANSWER_BYTES, field="decline reason") if answer is not None else None
        now = time.time()
        resolved_duplicate = False
        result_command: ControlCommand | None = None
        with self.executions._transaction() as connection:
            command, duplicate = self.executions._accept_command(
                connection, command_id=command_id, execution_id=execution_id,
                expected_version=expected_version, kind=kind, payload=payload, actor=actor,
            )
            row = connection.execute("SELECT * FROM execution_waits WHERE wait_id = ?", (wait_id,)).fetchone()
            if row is None or row["execution_id"] != execution_id:
                raise ExecutionConflict("wait_not_found", "wait does not belong to execution")
            if duplicate:
                resolved_duplicate = True
                result_command = command
            else:
                if float(row["expires_at"]) <= now:
                    connection.execute(
                        "UPDATE execution_waits SET status = 'expired', outcome = 'timeout', claim_owner = NULL, claim_expires_at = NULL, resolved_at = ?, updated_at = ? WHERE wait_id = ? AND status IN ('open', 'claimed')",
                        (now, now, wait_id),
                    )
                    result_command = self.executions._transition_command(connection, command_id, expected_status=CommandStatus.ACCEPTED, target=CommandStatus.REJECTED, result_version=expected_version, rejection_code="wait_expired")
                elif row["status"] != WaitStatus.OPEN.value or int(row["claim_generation"]) != generation:
                    raise ExecutionConflict("wait_generation", "wait is not open at this generation")
                else:
                    self.executions._transition_command(connection, command_id, expected_status=CommandStatus.ACCEPTED, target=CommandStatus.APPLYING)
                    answer_ref = None
                    if answer_bytes is not None:
                        blob = self.executions._put_state_blob_in_transaction(connection, execution_id=execution_id, payload=answer_bytes, media_type="application/json", schema_version=1)
                        answer_ref = blob["ref"]
                        connection.execute(
                            "INSERT OR IGNORE INTO execution_state_blob_refs (execution_id, ref, name, reference_kind, reference_id, created_at) VALUES (?, ?, 'answer', 'wait', ?, ?)",
                            (execution_id, answer_ref, wait_id, now),
                        )
                    status = WaitStatus.RESOLVED if kind is CommandKind.WAIT_ANSWER else WaitStatus.DECLINED
                    outcome = "answered" if kind is CommandKind.WAIT_ANSWER else "declined"
                    connection.execute(
                        "UPDATE execution_waits SET status = ?, claim_owner = ?, claim_expires_at = NULL, answer_ref = ?, outcome = ?, resolved_at = ?, updated_at = ? WHERE wait_id = ? AND status = 'open' AND claim_generation = ?",
                        (status.value, f"command:{command_id}", answer_ref, outcome, now, now, wait_id, generation),
                    )
                    execution = self.executions._require_execution(connection, execution_id)
                    self.executions._append_event(connection, execution_id=execution_id, execution_version=execution.status_version, command_id=command_id, kind=f"execution.wait.{outcome}", payload={"wait_id": wait_id, "generation": generation, "outcome": outcome}, created_at=now)
                    result_command = self.executions._transition_command(connection, command_id, expected_status=CommandStatus.APPLYING, target=CommandStatus.APPLIED, result_version=execution.status_version, receipt={"wait_id": wait_id, "outcome": outcome})
        record = self.get_wait(wait_id)
        assert record is not None and result_command is not None
        return result_command, record, resolved_duplicate


__all__ = ["DurableWaitStore", "WaitRecord", "WaitStatus"]
