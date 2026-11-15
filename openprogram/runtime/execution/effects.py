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
from .store import ExecutionStore
from .model import _json


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


#: A durable function's steps are effects whose record is the function's log.
FUNCTION_STEP_PREFIX = "function-step:"


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


def _function_step_record(execution_id: str, step) -> EffectRecord:
    settled = step.settled_at
    return EffectRecord(
        effect_id=f"{FUNCTION_STEP_PREFIX}{execution_id}:{step.action_id}",
        execution_id=execution_id, attempt_id=step.attempt_id, action_id=step.action_id,
        classification=EffectClassification.UNKNOWN, idempotency_key=None,
        metadata={"function_step": dict(step.identity), "input_hash": step.input_hash,
                  "code_hash": step.code_hash},
        status=EffectStatus(step.status), receipt=dict(step.receipt or {}),
        created_at=step.started_at, updated_at=settled or step.started_at,
        dispatched_at=step.started_at, resolved_at=settled,
    )


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


def _event_effect(effect: EffectRecord) -> dict:
    """The effect as its lifecycle event records it.

    ``metadata.payload`` (a provider effect's full message context, often
    megabytes) stays only on the ``effects`` row, which recovery reads.
    Copying it into every planned/dispatched/committed event stored the
    same context three more times per model request.
    """
    value = effect.to_dict()
    metadata = value.get("metadata")
    if isinstance(metadata, dict) and "payload" in metadata:
        value["metadata"] = {key: item for key, item in metadata.items() if key != "payload"}
    return value


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
            row = connection.get("effects", effect_id)
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
            attempt = connection.get("attempts", attempt_id)
            if attempt is None or attempt["execution_id"] != execution_id:
                raise EffectConflict(
                    "attempt_mismatch",
                    "effect attempt does not belong to the execution",
                )
            if (
                execution.current_attempt_id != attempt_id
                or attempt["status"] != "active"
            ):
                raise EffectConflict(
                    "stale_attempt",
                    "only the current active attempt can register an effect",
                )
            connection.insert(
                "effects",
                {
                    "effect_id": effect_id,
                    "execution_id": execution_id,
                    "attempt_id": attempt_id,
                    "action_id": action_id,
                    "classification": classification.value,
                    "idempotency_key": idempotency_key,
                    "metadata_json": _json(metadata),
                    "fingerprint": fingerprint,
                    "status": EffectStatus.PLANNED.value,
                    "receipt_json": "{}",
                    "created_at": now,
                    "dispatched_at": None,
                    "updated_at": now,
                    "resolved_at": None,
                },
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
        receipt: Mapping[str, Any] | None = None,
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
            receipt=receipt,
        )

    def resolve_not_started(
        self,
        effect_id: str,
        *,
        receipt: Mapping[str, Any],
        attempt_id: str | None = None,
        generation: int | None = None,
    ) -> EffectRecord:
        """Record a host-known refusal that never dispatched an external operation."""
        return self._transition(
            effect_id,
            expected_status=EffectStatus.PLANNED,
            target=EffectStatus.NOT_COMMITTED,
            receipt=receipt,
            owner_attempt_id=attempt_id,
            owner_generation=generation,
        )

    def resolve(
        self,
        effect_id: str,
        *,
        expected_status: EffectStatus,
        outcome: EffectStatus,
        receipt: Mapping[str, Any],
        attempt_id: str | None = None,
        generation: int | None = None,
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
        if effect_id.startswith(FUNCTION_STEP_PREFIX):
            return self._resolve_function_step(
                effect_id, expected_status=expected_status, outcome=outcome, receipt=receipt,
            )
        return self._transition(
            effect_id,
            expected_status=expected_status,
            target=outcome,
            receipt=receipt,
            owner_attempt_id=attempt_id,
            owner_generation=generation,
        )

    def _function_step(self, effect_id: str):
        """The function log and step behind a ``function-step:`` effect id."""
        execution_id, action_id = effect_id[len(FUNCTION_STEP_PREFIX):].rsplit(":", 1)
        log = self.executions.function_log(execution_id)
        return execution_id, log, (log.step(action_id) if log is not None else None)

    def _resolve_function_step(
        self, effect_id: str, *, expected_status: EffectStatus, outcome: EffectStatus,
        receipt: Mapping[str, Any],
    ) -> EffectRecord:
        execution_id, log, step = self._function_step(effect_id)
        if step is None:
            raise EffectConflict("not_found", f"effect not found: {effect_id}")
        if step.status != expected_status.value:
            raise EffectConflict("status_conflict", "function step changed before it was resolved")
        log.resolve_step(step.action_id, outcome.value, receipt)
        return _function_step_record(execution_id, log.step(step.action_id))

    def get(self, effect_id: str) -> EffectRecord | None:
        if effect_id.startswith(FUNCTION_STEP_PREFIX):
            execution_id, _log, step = self._function_step(effect_id)
            return None if step is None else _function_step_record(execution_id, step)
        with closing(self.executions._connect()) as connection:
            row = connection.get("effects", effect_id)
            return self._record(row) if row is not None else None

    def list_unresolved(self, execution_id: str) -> list[EffectRecord]:
        values = tuple(status.value for status in UNRESOLVED_EFFECT_STATUSES)
        with closing(self.executions._connect()) as connection:
            rows = connection.rows(
                "effects", order=("created_at", "effect_id"), execution_id=execution_id, status=values,
            )
            recorded = [self._record(row) for row in rows]
        log = self.executions.function_log(execution_id)
        steps = log.unresolved() if log is not None else ()
        return [*recorded, *(_function_step_record(execution_id, step) for step in steps)]

    def _transition(
        self,
        effect_id: str,
        *,
        expected_status: EffectStatus,
        target: EffectStatus,
        receipt: Mapping[str, Any] | None = None,
        require_current_attempt: bool = False,
        owner_attempt_id: str | None = None,
        owner_generation: int | None = None,
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
            if (owner_attempt_id is None) != (owner_generation is None):
                raise EffectConflict("invalid_owner", "effect resolution requires both attempt and generation")
            if owner_attempt_id is not None:
                execution = self.executions._require_execution(connection, current.execution_id)
                row = connection.get("attempts", owner_attempt_id)
                if (
                    current.attempt_id != owner_attempt_id
                    or execution.current_attempt_id != owner_attempt_id
                    or execution.owner_lease.get("generation") != owner_generation
                    or row is None or int(row["generation"]) != owner_generation
                    or row["status"] != "active"
                ):
                    raise EffectConflict("stale_attempt", "effect resolution owner is stale")
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
                attempt = connection.get("attempts", current.attempt_id)
                if (
                    attempt is None
                    or execution.current_attempt_id != current.attempt_id
                    or attempt["status"] != "active"
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
            try:
                encoded_receipt = _json(receipt_value)
            except (TypeError, ValueError) as exc:
                raise EffectConflict("receipt_invalid", "effect receipt must be JSON") from exc
            if len(encoded_receipt.encode("utf-8")) > 1024 * 1024:
                raise EffectConflict("receipt_too_large", "effect receipt exceeds the size limit")
            refs: set[str] = set()
            self.executions._collect_state_refs(receipt_value, refs)
            for ref in refs:
                blob = connection.get("execution_state_blobs", current.execution_id, ref)
                if blob is None:
                    raise EffectConflict("receipt_ref_invalid", "effect receipt references missing or foreign state")
                payload = bytes(blob["payload"])
                if (
                    hashlib.sha256(payload).hexdigest() != blob["sha256"]
                    or len(payload) != int(blob["byte_length"])
                    or not blob["media_type"] or int(blob["schema_version"]) < 1
                ):
                    raise EffectConflict("receipt_ref_invalid", "effect receipt references corrupt state")
            connection.update(
                "effects",
                {
                    "status": target.value,
                    "receipt_json": _json(receipt_value),
                    "dispatched_at": dispatched_at,
                    "updated_at": now,
                    "resolved_at": resolved_at,
                },
                effect_id=effect_id,
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
            payload={"effect": _event_effect(effect)},
            created_at=now,
        )

    @classmethod
    def _require(cls, connection, effect_id: str) -> EffectRecord:
        row = connection.get("effects", effect_id)
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
