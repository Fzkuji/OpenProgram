"""Physical activation leases for canonical executions."""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .model import ExecutionRecord, ExecutionStatus
from .store import ExecutionStore, _json


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
    lease_expires_at: float
    leased_at: float
    updated_at: float
    activated_at: float | None = None
    ended_at: float | None = None
    outcome: str | None = None

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "generation": self.generation,
            "status": self.status.value,
            "owner_id": self.owner_id,
            "lease_expires_at": self.lease_expires_at,
            "leased_at": self.leased_at,
            "activated_at": self.activated_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
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

    def lease(
        self,
        execution_id: str,
        *,
        expected_version: int,
        owner_id: str,
        ttl_seconds: float,
        attempt_id: str | None = None,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        if not owner_id or ttl_seconds <= 0:
            raise AttemptConflict(
                "invalid_lease", "owner_id and a positive ttl_seconds are required"
            )
        attempt_id = attempt_id or f"attempt_{uuid.uuid4().hex}"
        with self.executions._transaction() as connection:
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
            generation = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) + 1 FROM attempts "
                    "WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()[0]
            )
            now = self._clock()
            record = AttemptRecord(
                attempt_id=attempt_id,
                execution_id=execution_id,
                generation=generation,
                status=AttemptStatus.LEASED,
                owner_id=owner_id,
                lease_expires_at=now + ttl_seconds,
                leased_at=now,
                updated_at=now,
            )
            try:
                self._insert(connection, record)
            except sqlite3.IntegrityError as exc:
                raise AttemptConflict(
                    "attempt_exists", f"attempt already exists: {attempt_id}"
                ) from exc
            updated = connection.execute(
                "UPDATE executions SET status_version = ?, current_attempt_id = ?, "
                "owner_lease_json = ?, updated_at = ? "
                "WHERE execution_id = ? AND status_version = ?",
                (
                    expected_version + 1,
                    attempt_id,
                    _json({"owner_id": owner_id, "generation": generation}),
                    now,
                    execution_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
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

    def activate(
        self,
        attempt_id: str,
        *,
        generation: int,
        expected_execution_version: int,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        with self.executions._transaction() as connection:
            attempt = self._require(connection, attempt_id)
            self._validate_generation(attempt, generation)
            if attempt.status is not AttemptStatus.LEASED:
                raise AttemptConflict(
                    "invalid_state", f"attempt is already {attempt.status.value}"
                )
            now = self._clock()
            self._validate_lease(attempt, now)
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
            connection.execute(
                "UPDATE attempts SET status = ?, activated_at = ?, updated_at = ? "
                "WHERE attempt_id = ?",
                (AttemptStatus.ACTIVE.value, now, now, attempt_id),
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

    def heartbeat(
        self,
        attempt_id: str,
        *,
        generation: int,
        ttl_seconds: float,
    ) -> AttemptRecord:
        if ttl_seconds <= 0:
            raise AttemptConflict("invalid_lease", "ttl_seconds must be positive")
        with self.executions._transaction() as connection:
            attempt = self._require(connection, attempt_id)
            self._validate_generation(attempt, generation)
            if attempt.status is AttemptStatus.ENDED:
                raise AttemptConflict("terminal", "attempt has ended")
            now = self._clock()
            self._validate_lease(attempt, now)
            connection.execute(
                "UPDATE attempts SET lease_expires_at = ?, updated_at = ? "
                "WHERE attempt_id = ?",
                (now + ttl_seconds, now, attempt_id),
            )
            return self._require(connection, attempt_id)

    def finish(
        self,
        attempt_id: str,
        *,
        generation: int,
        expected_execution_version: int,
        target: ExecutionStatus,
        outcome: str,
        reason_code: str | None = None,
    ) -> tuple[AttemptRecord, ExecutionRecord]:
        with self.executions._transaction() as connection:
            attempt = self._require(connection, attempt_id)
            self._validate_generation(attempt, generation)
            if attempt.status is AttemptStatus.ENDED:
                raise AttemptConflict("terminal", "attempt has ended")
            now = self._clock()
            self._validate_lease(attempt, now)
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
            connection.execute(
                "UPDATE attempts SET status = ?, outcome = ?, ended_at = ?, "
                "updated_at = ? WHERE attempt_id = ?",
                (AttemptStatus.ENDED.value, outcome, now, now, attempt_id),
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

    @staticmethod
    def _validate_generation(attempt: AttemptRecord, generation: int) -> None:
        if attempt.generation != generation:
            raise AttemptConflict(
                "stale_generation",
                f"expected attempt generation {generation}, found {attempt.generation}",
            )

    @staticmethod
    def _validate_lease(attempt: AttemptRecord, now: float) -> None:
        if attempt.lease_expires_at <= now:
            raise AttemptConflict("lease_expired", "attempt lease has expired")

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
    def _insert(connection: sqlite3.Connection, attempt: AttemptRecord) -> None:
        connection.execute(
            "INSERT INTO attempts "
            "(attempt_id, execution_id, generation, status, owner_id, "
            "lease_expires_at, leased_at, activated_at, updated_at, ended_at, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt.attempt_id,
                attempt.execution_id,
                attempt.generation,
                attempt.status.value,
                attempt.owner_id,
                attempt.lease_expires_at,
                attempt.leased_at,
                attempt.activated_at,
                attempt.updated_at,
                attempt.ended_at,
                attempt.outcome,
            ),
        )

    @classmethod
    def _get(
        cls, connection: sqlite3.Connection, attempt_id: str
    ) -> AttemptRecord | None:
        row = connection.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return cls._record(row) if row is not None else None

    @classmethod
    def _require(cls, connection: sqlite3.Connection, attempt_id: str) -> AttemptRecord:
        attempt = cls._get(connection, attempt_id)
        if attempt is None:
            raise AttemptConflict("not_found", f"attempt not found: {attempt_id}")
        return attempt

    @staticmethod
    def _record(row: sqlite3.Row) -> AttemptRecord:
        return AttemptRecord(
            attempt_id=str(row["attempt_id"]),
            execution_id=str(row["execution_id"]),
            generation=int(row["generation"]),
            status=AttemptStatus(row["status"]),
            owner_id=str(row["owner_id"]),
            lease_expires_at=float(row["lease_expires_at"]),
            leased_at=float(row["leased_at"]),
            activated_at=(
                float(row["activated_at"]) if row["activated_at"] is not None else None
            ),
            updated_at=float(row["updated_at"]),
            ended_at=(float(row["ended_at"]) if row["ended_at"] is not None else None),
            outcome=row["outcome"],
        )
