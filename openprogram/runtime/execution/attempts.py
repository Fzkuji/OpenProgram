"""Activations of an execution inside the process that owns it."""

from __future__ import annotations

import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .model import ExecutionRecord, ExecutionStatus
from .store import ExecutionStore
from .store.run_tables import RowConflict, TableHandle
from .model import _json

FAILURE_TEXT_CHARS = 4000
# A provider error can echo megabytes. Scrubbing a little past the kept length
# keeps a secret cut at the end of the kept text from escaping its pattern.
_SCRUBBED_FAILURE_CHARS = 4 * FAILURE_TEXT_CHARS


def stored_failure_text(text: str | None) -> str | None:
    """The readable failure as an attempt keeps it: scrubbed, then bounded."""
    if not text or not text.strip():
        return None
    from openprogram.operations.diagnostics.redaction import redact_text
    from .audit import redact_audit_payload

    return redact_audit_payload({"failure_text": redact_text(text[:_SCRUBBED_FAILURE_CHARS])[:FAILURE_TEXT_CHARS]})["failure_text"]


class AttemptStatus(str, Enum):
    LEASED = "leased"
    ACTIVE = "active"
    CHECKPOINTING = "checkpointing"
    ENDED = "ended"


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    execution_id: str
    generation: int
    status: AttemptStatus
    owner_id: str
    leased_at: float
    updated_at: float
    activated_at: float | None = None
    ended_at: float | None = None
    outcome: str | None = None
    fenced: bool = False
    # Read only by the Activity events endpoint; to_dict feeds event payloads.
    failure_text: str | None = None

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "generation": self.generation,
            "status": self.status.value,
            "owner_id": self.owner_id,
            "leased_at": self.leased_at,
            "activated_at": self.activated_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "fenced": self.fenced,
        }


class AttemptConflict(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class AttemptStore:
    """Own attempt generations and fence stale physical owners."""

    def __init__(
        self,
        executions: ExecutionStore,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.executions = executions
        self._clock = clock

    @contextmanager
    def _admission_transaction(self, session_id: str) -> Iterator[TableHandle]:
        from openprogram.state.paths import get_state_dir
        from openprogram.state.store.session.migration import session_hold_active
        from openprogram.state.store.session.session_lock import session_interprocess_lock

        with session_interprocess_lock(session_id, timeout=5.0):
            if session_hold_active(Path(get_state_dir()) / "projects", session_id):
                raise AttemptConflict(
                    "session_migration",
                    "attempt admission is paused while session migration runs",
                )
            with self.executions._transaction() as connection:
                yield connection

    def _attempt_session_id(self, attempt_id: str) -> str:
        with closing(self.executions._connect()) as connection:
            row = connection.get("attempts", attempt_id)
        if row is None:
            raise AttemptConflict("attempt_not_found", f"attempt does not exist: {attempt_id}")
        execution = self.executions.get_execution(str(row["execution_id"]))
        if execution is None:
            raise AttemptConflict("execution_not_found", "attempt execution does not exist")
        return execution.session_id

    def lease(
        self,
        execution_id: str,
        *,
        expected_version: int,
        owner_id: str,
        attempt_id: str | None = None,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        if not owner_id:
            raise AttemptConflict("invalid_lease", "owner_id is required")
        attempt_id = attempt_id or f"attempt_{uuid.uuid4().hex}"
        execution = self.executions.get_execution(execution_id)
        if execution is None:
            raise AttemptConflict("execution_not_found", "execution does not exist")
        with self._admission_transaction(execution.session_id) as connection:
            execution = self.executions._require_execution(connection, execution_id)
            if execution.status_version != expected_version:
                raise AttemptConflict(
                    "stale_version",
                    f"expected execution version {expected_version}, "
                    f"found {execution.status_version}",
                )
            if execution.status not in {ExecutionStatus.QUEUED, ExecutionStatus.PAUSED}:
                raise AttemptConflict(
                    "invalid_state",
                    f"cannot lease attempt while execution is {execution.status.value}",
                )
            if execution.current_attempt_id is not None:
                raise AttemptConflict(
                    "owner_exists", "execution already has a current attempt"
                )
            generation = self._next_generation(connection, execution_id)
            now = self._clock()
            lease = dict(execution.owner_lease)
            if "process_owner" in lease:
                from .process_owner import current_process_owner
                lease["process_owner"] = current_process_owner()
            lease.update({"owner_id": owner_id, "generation": generation})
            record = AttemptRecord(
                attempt_id=attempt_id,
                execution_id=execution_id,
                generation=generation,
                status=AttemptStatus.LEASED,
                owner_id=owner_id,
                leased_at=now,
                updated_at=now,
            )
            try:
                self._insert(connection, record)
            except RowConflict as exc:
                raise AttemptConflict(
                    "attempt_exists", f"attempt already exists: {attempt_id}"
                ) from exc
            updated = connection.update(
                "executions",
                {
                    "status_version": expected_version + 1,
                    "current_attempt_id": attempt_id,
                    "owner_lease_json": _json(lease),
                    "updated_at": now,
                },
                execution_id=execution_id,
                status_version=expected_version,
            )
            if updated != 1:
                raise AttemptConflict("stale_version", "execution changed concurrently")
            execution = self.executions._require_execution(connection, execution_id)
            self.executions._append_event(
                connection,
                execution_id=execution_id,
                execution_version=execution.status_version,
                kind="execution.updated",
                payload={"record": execution.to_dict()},
                created_at=now,
            )
            self.executions._append_event(
                connection,
                execution_id=execution_id,
                execution_version=execution.status_version,
                kind="attempt.leased",
                payload={"attempt": record.to_dict()},
                created_at=now,
            )
            return record, execution

    def set_process_owner(self, attempt_id: str, *, generation: int, active: bool) -> None:
        """Attach or drop process evidence for one exact physical attempt."""
        with self.executions._transaction() as connection:
            attempt = self._require(connection, attempt_id)
            self._validate_generation(attempt, generation)
            execution = self.executions._require_execution(connection, attempt.execution_id)
            if (execution.current_attempt_id != attempt_id
                    or execution.owner_lease.get("generation") != generation):
                if active:
                    raise AttemptConflict("stale_owner", "execution owner changed")
                return
            lease = dict(execution.owner_lease)
            if active:
                from .process_owner import current_process_owner
                lease["process_owner"] = current_process_owner()
            else:
                lease.pop("process_owner", None)
            connection.update(
                "executions",
                {"owner_lease_json": _json(lease)},
                execution_id=execution.execution_id,
            )

    def activate(
        self,
        attempt_id: str,
        *,
        generation: int,
        expected_execution_version: int,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        with self._admission_transaction(self._attempt_session_id(attempt_id)) as connection:
            attempt = self._require(connection, attempt_id)
            self._validate_generation(attempt, generation)
            if attempt.status is not AttemptStatus.LEASED:
                raise AttemptConflict(
                    "invalid_state", f"attempt is already {attempt.status.value}"
                )
            now = self._clock()
            execution = self.executions._require_execution(
                connection, attempt.execution_id
            )
            self._validate_owner(execution, attempt, expected_execution_version)
            running = self.executions._transition_execution(
                connection,
                attempt.execution_id,
                expected_version=expected_execution_version,
                target=ExecutionStatus.RUNNING,
                reason_code=None,
            )
            connection.update(
                "attempts",
                {"status": AttemptStatus.ACTIVE.value, "activated_at": now, "updated_at": now},
                attempt_id=attempt_id,
            )
            active = self._require(connection, attempt_id)
            self.executions._append_event(
                connection,
                execution_id=attempt.execution_id,
                execution_version=running.status_version,
                kind="attempt.active",
                payload={"attempt": active.to_dict()},
                created_at=now,
            )
            return active, running

    def ensure_held(self, attempt_id: str, *, generation: int) -> AttemptRecord:
        """Refuse an owner that recovery replaced or that has already ended."""
        attempt = self.get(attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        self._validate_generation(attempt, generation)
        self._validate_not_fenced(attempt)
        if attempt.status is AttemptStatus.ENDED:
            raise AttemptConflict("terminal", "attempt has ended")
        return attempt

    def finish(
        self,
        attempt_id: str,
        *,
        generation: int,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        reason_code: str | None = None,
        failure_text: str | None = None,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        with self.executions._transaction() as connection:
            attempt = self._require(connection, attempt_id)
            self._validate_generation(attempt, generation)
            if attempt.status is AttemptStatus.ENDED:
                self._validate_not_fenced(attempt)
                raise AttemptConflict("terminal", "attempt has ended")
            now = self._clock()
            execution = self.executions._require_execution(
                connection, attempt.execution_id
            )
            self._validate_owner(execution, attempt, expected_execution_version)
            finished = self.executions._transition_execution(
                connection,
                attempt.execution_id,
                expected_version=expected_execution_version,
                target=target,
                reason_code=reason_code,
                clear_owner=True,
            )
            connection.update(
                "attempts",
                {"status": AttemptStatus.ENDED.value, "outcome": outcome, "ended_at": now, "updated_at": now,
                 "failure_text": stored_failure_text(failure_text)},
                attempt_id=attempt_id,
            )
            ended = self._require(connection, attempt_id)
            self.executions._append_event(
                connection,
                execution_id=attempt.execution_id,
                execution_version=finished.status_version,
                kind="attempt.ended",
                payload={"attempt": ended.to_dict()},
                created_at=now,
            )
            return ended, finished

    def _finish_in_transaction(
        self,
        connection: TableHandle,
        attempt_id: str,
        *,
        generation: int,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        reason_code: str | None = None,
        failure_text: str | None = None,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        """Finish an active attempt under a caller-owned transaction."""
        attempt = self._require(connection, attempt_id)
        self._validate_generation(attempt, generation)
        if attempt.status is AttemptStatus.ENDED:
            self._validate_not_fenced(attempt)
            raise AttemptConflict("terminal", "attempt has ended")
        now = self._clock()
        execution = self.executions._require_execution(connection, attempt.execution_id)
        self._validate_owner(execution, attempt, expected_execution_version)
        finished = self.executions._transition_execution(
            connection,
            attempt.execution_id,
            expected_version=expected_execution_version,
            target=target,
            reason_code=reason_code,
            clear_owner=True,
        )
        connection.update(
            "attempts",
            {"status": AttemptStatus.ENDED.value, "outcome": outcome, "ended_at": now, "updated_at": now,
             "failure_text": stored_failure_text(failure_text)},
            attempt_id=attempt_id,
        )
        ended = self._require(connection, attempt_id)
        self.executions._append_event(
            connection,
            execution_id=attempt.execution_id,
            execution_version=finished.status_version,
            kind="attempt.ended",
            payload={"attempt": ended.to_dict()},
            created_at=now,
        )
        return ended, finished

    def get(self, attempt_id: str) -> AttemptRecord | None:
        with closing(self.executions._connect()) as connection:
            return self._get(connection, attempt_id)

    def list_for_execution(self, execution_id: str) -> list[AttemptRecord]:
        """Every attempt of one execution, oldest generation first."""
        self.executions.tables.ensure_execution(execution_id)
        with closing(self.executions._connect()) as connection:
            rows = connection.rows("attempts", execution_id=execution_id, order="generation")
        return [self._record(row) for row in rows]

    def _end_for_owner_loss(
        self,
        connection: TableHandle,
        attempt: AttemptRecord,
        *,
        outcome: str,
    ) -> AttemptRecord:
        """Fence an attempt inside its execution recovery transaction."""
        if attempt.status is AttemptStatus.ENDED:
            return attempt
        now = self._clock()
        connection.update(
            "attempts",
            {
                "status": AttemptStatus.ENDED.value,
                "fenced": 1,
                "outcome": outcome,
                "ended_at": now,
                "updated_at": now,
            },
            attempt_id=attempt.attempt_id,
        )
        return self._require(connection, attempt.attempt_id)

    @staticmethod
    def _validate_generation(attempt: AttemptRecord, generation: int) -> None:
        if attempt.generation != generation:
            raise AttemptConflict(
                "stale_generation",
                f"expected attempt generation {generation}, found {attempt.generation}",
            )

    @staticmethod
    def _validate_held(attempt: AttemptRecord) -> None:
        """Refuse an owner that recovery fenced or that has ended."""
        if attempt.status is AttemptStatus.ENDED:
            raise AttemptConflict("stale_owner", "attempt owner was recovered")

    @staticmethod
    def _validate_not_fenced(attempt: AttemptRecord) -> None:
        if attempt.fenced:
            raise AttemptConflict("stale_owner", "attempt owner was recovered")

    @staticmethod
    def _validate_owner(
        execution: ExecutionRecord,
        attempt: AttemptRecord,
        expected_version: int,
    ) -> None:
        if execution.status_version != expected_version:
            raise AttemptConflict(
                "stale_version",
                f"expected execution version {expected_version}, "
                f"found {execution.status_version}",
            )
        lease = dict(execution.owner_lease)
        if (
            execution.current_attempt_id != attempt.attempt_id
            or lease.get("owner_id") != attempt.owner_id
            or lease.get("generation") != attempt.generation
        ):
            raise AttemptConflict("stale_owner", "attempt no longer owns execution")

    @staticmethod
    def _next_generation(connection: TableHandle, execution_id: str) -> int:
        attempts = connection.rows("attempts", execution_id=execution_id)
        return max((int(attempt["generation"]) for attempt in attempts), default=0) + 1

    @staticmethod
    def _insert(connection: TableHandle, attempt: AttemptRecord) -> None:
        connection.insert(
            "attempts",
            {
                "attempt_id": attempt.attempt_id,
                "execution_id": attempt.execution_id,
                "generation": attempt.generation,
                "status": attempt.status.value,
                "owner_id": attempt.owner_id,
                "leased_at": attempt.leased_at,
                "activated_at": attempt.activated_at,
                "updated_at": attempt.updated_at,
                "ended_at": attempt.ended_at,
                "outcome": attempt.outcome,
                "fenced": int(attempt.fenced),
            },
        )

    @classmethod
    def _get(
        cls, connection: TableHandle, attempt_id: str
    ) -> AttemptRecord | None:
        row = connection.get("attempts", attempt_id)
        return cls._record(row) if row is not None else None

    @classmethod
    def _require(cls, connection: TableHandle, attempt_id: str) -> AttemptRecord:
        attempt = cls._get(connection, attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        return attempt

    @staticmethod
    def _record(row: Mapping[str, Any]) -> AttemptRecord:
        return AttemptRecord(
            attempt_id=str(row["attempt_id"]),
            execution_id=str(row["execution_id"]),
            generation=int(row["generation"]),
            status=AttemptStatus(row["status"]),
            owner_id=str(row["owner_id"]),
            leased_at=float(row["leased_at"]),
            activated_at=(
                float(row["activated_at"]) if row["activated_at"] is not None else None
            ),
            updated_at=float(row["updated_at"]),
            ended_at=(float(row["ended_at"]) if row["ended_at"] is not None else None),
            outcome=row["outcome"],
            fenced=bool(row["fenced"]),
            failure_text=row.get("failure_text"),
        )
