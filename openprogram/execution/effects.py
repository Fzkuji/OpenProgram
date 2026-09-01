"""Durable external-effect ledger used by checkpoint and recovery logic."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from .model import ExecutionStatus
from .store import ExecutionStore, _json


class EffectClassification(str, Enum):
    PURE = "pure"
    IDEMPOTENT = "idempotent"
    COMPENSATABLE = "compensatable"
    NONREPEATABLE = "nonrepeatable"
    UNKNOWN = "unknown"


class EffectStatus(str, Enum):
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    UNCERTAIN = "uncertain"
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    COMPENSATED = "compensated"


TERMINAL_EFFECT_STATUSES = frozenset(
    {
        EffectStatus.COMMITTED,
        EffectStatus.NOT_COMMITTED,
        EffectStatus.COMPENSATED,
    }
)
UNRESOLVED_EFFECT_STATUSES = frozenset(
    {EffectStatus.DISPATCHED, EffectStatus.UNCERTAIN}
)
EFFECT_ADMISSION_CLOSED_STATUSES = frozenset(
    {ExecutionStatus.PAUSING, ExecutionStatus.CANCELLING}
)


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    execution_id: str
    attempt_id: str
    action_id: str
    classification: EffectClassification
    idempotency_key: str | None
    metadata: Mapping[str, Any]
    status: EffectStatus
    receipt: Mapping[str, Any]
    created_at: float
    updated_at: float
    dispatched_at: float | None = None
    resolved_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "action_id": self.action_id,
            "classification": self.classification.value,
            "idempotency_key": self.idempotency_key,
            "metadata": dict(self.metadata),
            "status": self.status.value,
            "receipt": dict(self.receipt),
            "created_at": self.created_at,
            "dispatched_at": self.dispatched_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
        }


class EffectConflict(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _require_effect_admission(execution) -> None:
    if execution.status in EFFECT_ADMISSION_CLOSED_STATUSES:
        raise EffectConflict(
            "admission_closed",
            f"cannot admit an effect while execution is {execution.status.value}",
        )


class EffectStore:
    def __init__(
        self,
        executions: ExecutionStore,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.executions = executions
        self._clock = clock

    def register(
        self,
        *,
        effect_id: str,
        execution_id: str,
        attempt_id: str,
        action_id: str,
        classification: EffectClassification,
        idempotency_key: str | None,
        metadata: Mapping[str, Any],
    ) -> EffectRecord:
        identity = {
            "effect_id": effect_id,
            "execution_id": execution_id,
            "attempt_id": attempt_id,
            "action_id": action_id,
            "classification": classification.value,
            "idempotency_key": idempotency_key,
            "metadata": dict(metadata),
        }
        fingerprint = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
        with self.executions._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM effects WHERE effect_id = ?", (effect_id,)
            ).fetchone()
            if row is not None:
                if row["fingerprint"] != fingerprint:
                    raise EffectConflict(
                        "idempotency_collision",
                        f"effect_id was already used for different work: {effect_id}",
                    )
                return self._record(row)
            execution = self.executions._require_execution(connection, execution_id)
            _require_effect_admission(execution)
            now = self._clock()
            attempt = connection.execute(
                "SELECT execution_id, status, lease_expires_at FROM attempts "
                "WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if attempt is None or attempt["execution_id"] != execution_id:
                raise EffectConflict(
                    "attempt_mismatch",
                    "effect attempt does not belong to the execution",
                )
            if (
                execution.current_attempt_id != attempt_id
                or attempt["status"] != "active"
                or float(attempt["lease_expires_at"]) <= now
            ):
                raise EffectConflict(
                    "stale_attempt",
                    "only the current active attempt can register an effect",
                )
            connection.execute(
                "INSERT INTO effects "
                "(effect_id, execution_id, attempt_id, action_id, classification, "
                "idempotency_key, metadata_json, fingerprint, status, receipt_json, "
                "created_at, dispatched_at, updated_at, resolved_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    effect_id,
                    execution_id,
                    attempt_id,
                    action_id,
                    classification.value,
                    idempotency_key,
                    _json(metadata),
                    fingerprint,
                    EffectStatus.PLANNED.value,
                    "{}",
                    now,
                    None,
                    now,
                    None,
                ),
            )
            record = self._require(connection, effect_id)
            self._append_event(connection, execution.status_version, record, now)
            return record

    def mark_dispatched(
        self,
        effect_id: str,
        *,
        expected_status: EffectStatus,
    ) -> EffectRecord:
        if expected_status is not EffectStatus.PLANNED:
            raise EffectConflict(
                "invalid_transition", "only a planned effect can be dispatched"
            )
        return self._transition(
            effect_id,
            expected_status=expected_status,
            target=EffectStatus.DISPATCHED,
            require_current_attempt=True,
        )

    def mark_uncertain(
        self,
        effect_id: str,
        *,
        expected_status: EffectStatus,
    ) -> EffectRecord:
        if expected_status in TERMINAL_EFFECT_STATUSES:
            raise EffectConflict("terminal", "resolved effect cannot become uncertain")
        if expected_status is not EffectStatus.DISPATCHED:
            raise EffectConflict(
                "invalid_transition", "only a dispatched effect can become uncertain"
            )
        return self._transition(
            effect_id,
            expected_status=expected_status,
            target=EffectStatus.UNCERTAIN,
        )

    def resolve(
        self,
        effect_id: str,
        *,
        expected_status: EffectStatus,
        outcome: EffectStatus,
        receipt: Mapping[str, Any],
    ) -> EffectRecord:
        if outcome not in TERMINAL_EFFECT_STATUSES:
            raise EffectConflict(
                "invalid_outcome", "effect resolution must be a terminal outcome"
            )
        if expected_status not in UNRESOLVED_EFFECT_STATUSES:
            code = (
                "terminal"
                if expected_status in TERMINAL_EFFECT_STATUSES
                else "invalid_transition"
            )
            raise EffectConflict(code, "effect is not awaiting a resolution")
        return self._transition(
            effect_id,
            expected_status=expected_status,
            target=outcome,
            receipt=receipt,
        )

    def get(self, effect_id: str) -> EffectRecord | None:
        with closing(self.executions._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM effects WHERE effect_id = ?", (effect_id,)
            ).fetchone()
            return self._record(row) if row is not None else None

    def list_unresolved(self, execution_id: str) -> list[EffectRecord]:
        values = tuple(status.value for status in UNRESOLVED_EFFECT_STATUSES)
        with closing(self.executions._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM effects WHERE execution_id = ? "
                "AND status IN (?, ?) ORDER BY created_at, effect_id",
                (execution_id, *values),
            )
            return [self._record(row) for row in rows]

    def _transition(
        self,
        effect_id: str,
        *,
        expected_status: EffectStatus,
        target: EffectStatus,
        receipt: Mapping[str, Any] | None = None,
        require_current_attempt: bool = False,
    ) -> EffectRecord:
        with self.executions._transaction() as connection:
            current = self._require(connection, effect_id)
            if current.status in TERMINAL_EFFECT_STATUSES:
                raise EffectConflict(
                    "terminal", f"effect is already {current.status.value}"
                )
            if current.status is not expected_status:
                raise EffectConflict(
                    "stale_status",
                    f"expected effect status {expected_status.value}, "
                    f"found {current.status.value}",
                )
            now = self._clock()
            execution = None
            if target is EffectStatus.DISPATCHED:
                execution = self.executions._require_execution(
                    connection, current.execution_id
                )
                _require_effect_admission(execution)
            if require_current_attempt:
                if execution is None:
                    execution = self.executions._require_execution(
                        connection, current.execution_id
                    )
                attempt = connection.execute(
                    "SELECT status, lease_expires_at FROM attempts "
                    "WHERE attempt_id = ?",
                    (current.attempt_id,),
                ).fetchone()
                if (
                    attempt is None
                    or execution.current_attempt_id != current.attempt_id
                    or attempt["status"] != "active"
                    or float(attempt["lease_expires_at"]) <= now
                ):
                    raise EffectConflict(
                        "stale_attempt",
                        "only the current active attempt can dispatch an effect",
                    )
            dispatched_at = (
                now if target is EffectStatus.DISPATCHED else current.dispatched_at
            )
            resolved_at = now if target in TERMINAL_EFFECT_STATUSES else None
            receipt_value = dict(receipt or current.receipt)
            connection.execute(
                "UPDATE effects SET status = ?, receipt_json = ?, "
                "dispatched_at = ?, updated_at = ?, resolved_at = ? "
                "WHERE effect_id = ?",
                (
                    target.value,
                    _json(receipt_value),
                    dispatched_at,
                    now,
                    resolved_at,
                    effect_id,
                ),
            )
            updated = self._require(connection, effect_id)
            execution = self.executions._require_execution(
                connection, updated.execution_id
            )
            self._append_event(connection, execution.status_version, updated, now)
            return updated

    def _append_event(
        self,
        connection,
        execution_version: int,
        effect: EffectRecord,
        now: float,
    ) -> None:
        self.executions._append_event(
            connection,
            execution_id=effect.execution_id,
            execution_version=execution_version,
            kind=f"effect.{effect.status.value}",
            payload={"effect": effect.to_dict()},
            created_at=now,
        )

    @classmethod
    def _require(cls, connection, effect_id: str) -> EffectRecord:
        row = connection.execute(
            "SELECT * FROM effects WHERE effect_id = ?", (effect_id,)
        ).fetchone()
        if row is None:
            raise EffectConflict("not_found", f"effect not found: {effect_id}")
        return cls._record(row)

    @staticmethod
    def _record(row) -> EffectRecord:
        return EffectRecord(
            effect_id=str(row["effect_id"]),
            execution_id=str(row["execution_id"]),
            attempt_id=str(row["attempt_id"]),
            action_id=str(row["action_id"]),
            classification=EffectClassification(row["classification"]),
            idempotency_key=row["idempotency_key"],
            metadata=dict(json.loads(row["metadata_json"])),
            status=EffectStatus(row["status"]),
            receipt=dict(json.loads(row["receipt_json"])),
            created_at=float(row["created_at"]),
            dispatched_at=(
                float(row["dispatched_at"])
                if row["dispatched_at"] is not None
                else None
            ),
            updated_at=float(row["updated_at"]),
            resolved_at=(
                float(row["resolved_at"]) if row["resolved_at"] is not None else None
            ),
        )
