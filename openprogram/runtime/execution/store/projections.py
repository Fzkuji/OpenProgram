"""Execution persistence: projections."""
from __future__ import annotations

import time

import uuid


from contextlib import closing



from typing import Any, Collection, Mapping


from ..model import _json


from .shared import (
    ExecutionConflict,
    RESOURCE_INTENT_KINDS,
)

class ProjectionsOperations:
    def enqueue_resource_intent(
        self,
        execution_id: str,
        *,
        kind: str,
        idempotency_key: str,
        fingerprint: str,
        admission_id: str | None = None,
        attempt_id: str | None = None,
        generation: int | None = None,
        resource_lease_generation: int | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one execution-owned cross-authority intent.

        This transaction deliberately does not call ResourceGovernor.  The
        resulting row is the durable hand-off between the two authorities.
        """
        return self.enqueue_resource_intents(
            execution_id,
            intents=({
                "kind": kind,
                "idempotency_key": idempotency_key,
                "fingerprint": fingerprint,
                "admission_id": admission_id,
                "attempt_id": attempt_id,
                "generation": generation,
                "resource_lease_generation": resource_lease_generation,
                "payload": payload or {},
            },),
        )[0]


    def enqueue_resource_intents(
        self,
        execution_id: str,
        *,
        intents: Collection[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Atomically persist one complete cross-authority intent set.

        A ResourceSaga operation is only recoverable when both of its
        execution and resource records exist.  This method is deliberately
        all-or-nothing: retries return the original rows, while a partial
        legacy row is completed in the same transaction as its counterpart.
        """
        values = [dict(intent) for intent in intents]
        if not values:
            raise ExecutionConflict("invalid_resource_intent", "at least one resource intent is required")
        keys: set[str] = set()
        for intent in values:
            kind = intent.get("kind")
            key = intent.get("idempotency_key")
            fingerprint = intent.get("fingerprint")
            if kind not in RESOURCE_INTENT_KINDS:
                raise ExecutionConflict("invalid_resource_intent", f"unsupported resource intent: {kind}")
            if not isinstance(key, str) or not key or not isinstance(fingerprint, str) or not fingerprint:
                raise ExecutionConflict("invalid_resource_intent", "idempotency_key and fingerprint are required")
            if key in keys:
                raise ExecutionConflict("invalid_resource_intent", "resource intent keys must be unique")
            keys.add(key)
        with self._transaction() as connection:
            execution = self._require_execution(connection, execution_id)
            result: list[dict[str, Any]] = []
            for intent in values:
                kind = str(intent["kind"])
                key = str(intent["idempotency_key"])
                fingerprint = str(intent["fingerprint"])
                encoded = _json(dict(intent.get("payload") or {}))
                existing = connection.first(
                    "execution_resource_intents", execution_id=execution_id, idempotency_key=key,
                )
                if existing is not None:
                    if (
                        existing["kind"] != kind
                        or existing["fingerprint"] != fingerprint
                        or existing["payload_json"] != encoded
                    ):
                        raise ExecutionConflict("resource_intent_collision", "resource idempotency key was reused with different content")
                    result.append(self._resource_intent(existing))
                    continue
                now = time.time()
                persisted = self._resource_intent(connection.insert("execution_resource_intents", {
                    "intent_id": f"resource-intent-{uuid.uuid4().hex}",
                    "execution_id": execution_id,
                    "kind": kind,
                    "idempotency_key": key,
                    "fingerprint": fingerprint,
                    "admission_id": intent.get("admission_id"),
                    "attempt_id": intent.get("attempt_id"),
                    "generation": intent.get("generation"),
                    "resource_lease_generation": intent.get("resource_lease_generation"),
                    "payload_json": encoded,
                    "state": "pending",
                    "created_at": now,
                    "updated_at": now,
                }))
                self._append_event(
                    connection,
                    execution_id=execution_id,
                    execution_version=execution.status_version,
                    kind=kind,
                    payload={"intent": persisted},
                    created_at=now,
                )
                result.append(persisted)
            return result


    def repair_resource_claim_release_pairs(self) -> int:
        """Complete one-sided claim/release records left by older writers."""
        pairs = {
            "execution.claim.intent": "resource.claim.intent",
            "resource.claim.intent": "execution.claim.intent",
            "execution.release.intent": "resource.release.intent",
            "resource.release.intent": "execution.release.intent",
        }
        repaired = 0
        with self._transaction() as connection:
            rows = connection.rows(
                "execution_resource_intents", kind=tuple(pairs), order=("created_at", "intent_id"),
            )
            for row in rows:
                kind = str(row["kind"])
                prefix, separator, suffix = str(row["idempotency_key"]).partition(":")
                if prefix not in {"execution", "resource"} or not separator:
                    continue
                counterpart_key = f"{'resource' if prefix == 'execution' else 'execution'}:{suffix}"
                if connection.exists(
                    "execution_resource_intents",
                    execution_id=row["execution_id"], idempotency_key=counterpart_key,
                ):
                    continue
                execution = self._require_execution(connection, str(row["execution_id"]))
                now = time.time()
                counterpart_kind = pairs[kind]
                persisted = self._resource_intent(connection.insert("execution_resource_intents", {
                    "intent_id": f"resource-intent-{uuid.uuid4().hex}",
                    "execution_id": row["execution_id"],
                    "kind": counterpart_kind,
                    "idempotency_key": counterpart_key,
                    "fingerprint": row["fingerprint"],
                    "admission_id": row["admission_id"],
                    "attempt_id": row["attempt_id"],
                    "generation": row["generation"],
                    "resource_lease_generation": row["resource_lease_generation"],
                    "payload_json": row["payload_json"],
                    "state": "pending",
                    "created_at": now,
                    "updated_at": now,
                }))
                self._append_event(
                    connection,
                    execution_id=str(row["execution_id"]),
                    execution_version=execution.status_version,
                    kind=counterpart_kind,
                    payload={"intent": persisted, "repaired": True},
                    created_at=now,
                )
                repaired += 1
        return repaired


    def list_resource_intents(
        self, *, execution_id: str | None = None, states: Collection[str] = (), limit: int | None = None,
    ) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        if execution_id is not None:
            filters["execution_id"] = execution_id
        if states:
            filters["state"] = list(states)
        if limit is not None and limit <= 0:
            raise ExecutionConflict("invalid_limit", "limit must be positive")
        with closing(self._connect()) as connection:
            return [self._resource_intent(row) for row in connection.rows(
                "execution_resource_intents", order=("created_at", "intent_id"), limit=limit, **filters,
            )]


    def claim_resource_intents(
        self, *, owner_id: str, limit: int = 100, lease_ttl_seconds: float = 30.0, now: float | None = None,
    ) -> list[dict[str, Any]]:
        if not owner_id or limit <= 0 or lease_ttl_seconds <= 0:
            raise ExecutionConflict("invalid_resource_claim", "owner_id, limit and lease_ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._transaction() as connection:
            connection.update(
                "execution_resource_intents",
                {"state": "pending", "claim_owner": None, "claim_expires_at": None, "updated_at": current},
                state="claimed",
                where=lambda row: row["claim_expires_at"] is not None and row["claim_expires_at"] <= current,
            )
            rows = connection.rows(
                "execution_resource_intents", state="pending", order=("created_at", "intent_id"), limit=limit,
            )
            claimed: list[dict[str, Any]] = []
            for row in rows:
                changed = connection.update(
                    "execution_resource_intents",
                    {
                        "state": "claimed",
                        "claim_owner": owner_id,
                        "claim_expires_at": current + lease_ttl_seconds,
                        "attempts": lambda stored: stored["attempts"] + 1,
                        "updated_at": current,
                    },
                    intent_id=row["intent_id"],
                    state="pending",
                )
                if changed:
                    claimed_row = connection.get("execution_resource_intents", row["intent_id"])
                    assert claimed_row is not None
                    claimed.append(self._resource_intent(claimed_row))
            return claimed


    def complete_resource_intent(
        self, intent_id: str, *, owner_id: str, result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._transaction() as connection:
            row = connection.get("execution_resource_intents", intent_id)
            if row is None:
                return None
            if row["state"] == "applied":
                return self._resource_intent(row)
            if row["state"] != "claimed" or row["claim_owner"] != owner_id:
                return None
            connection.update(
                "execution_resource_intents",
                {
                    "state": "applied",
                    "claim_owner": None,
                    "claim_expires_at": None,
                    "result_json": _json(dict(result or {})),
                    "updated_at": now,
                    "completed_at": now,
                },
                intent_id=intent_id,
            )
            return self._resource_intent(connection.get("execution_resource_intents", intent_id))


    def retry_resource_intent(self, intent_id: str, *, owner_id: str, error: str) -> bool:
        with self._transaction() as connection:
            return connection.update(
                "execution_resource_intents",
                {
                    "state": "pending",
                    "claim_owner": None,
                    "claim_expires_at": None,
                    "last_error": error[:1024],
                    "updated_at": time.time(),
                },
                intent_id=intent_id,
                state="claimed",
                claim_owner=owner_id,
            ) == 1
