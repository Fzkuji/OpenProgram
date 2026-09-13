"""Execution persistence: projections."""
from __future__ import annotations




import sqlite3

import time

import uuid


from contextlib import closing



from typing import Any, Collection, Mapping


from .._schema import PROJECTION_KINDS

from ..model import _json


from .shared import (
    ExecutionConflict,
    ProjectionConflict,
    RESOURCE_INTENT_KINDS,
)

class ProjectionsOperations:
    def get_projection_outbox(self, outbox_id: str):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            return self._projection_outbox(row) if row is not None else None


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
        resulting row is the durable hand-off between the two SQLite files.
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
                existing = connection.execute(
                    "SELECT * FROM execution_resource_intents WHERE execution_id = ? AND idempotency_key = ?",
                    (execution_id, key),
                ).fetchone()
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
                intent_id = f"resource-intent-{uuid.uuid4().hex}"
                connection.execute(
                    "INSERT INTO execution_resource_intents (intent_id, execution_id, kind, idempotency_key, fingerprint, admission_id, attempt_id, generation, resource_lease_generation, payload_json, state, claim_owner, claim_expires_at, attempts, result_json, last_error, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, 0, '{}', NULL, ?, ?, NULL)",
                    (
                        intent_id, execution_id, kind, key, fingerprint,
                        intent.get("admission_id"), intent.get("attempt_id"),
                        intent.get("generation"), intent.get("resource_lease_generation"),
                        encoded, now, now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
                ).fetchone()
                assert row is not None
                persisted = self._resource_intent(row)
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
            rows = connection.execute(
                "SELECT * FROM execution_resource_intents WHERE kind IN (?, ?, ?, ?) ORDER BY created_at, intent_id",
                tuple(pairs),
            ).fetchall()
            for row in rows:
                kind = str(row["kind"])
                prefix, separator, suffix = str(row["idempotency_key"]).partition(":")
                if prefix not in {"execution", "resource"} or not separator:
                    continue
                counterpart_key = f"{'resource' if prefix == 'execution' else 'execution'}:{suffix}"
                counterpart = connection.execute(
                    "SELECT 1 FROM execution_resource_intents WHERE execution_id = ? AND idempotency_key = ?",
                    (row["execution_id"], counterpart_key),
                ).fetchone()
                if counterpart is not None:
                    continue
                execution = self._require_execution(connection, str(row["execution_id"]))
                now = time.time()
                intent_id = f"resource-intent-{uuid.uuid4().hex}"
                counterpart_kind = pairs[kind]
                connection.execute(
                    "INSERT INTO execution_resource_intents (intent_id, execution_id, kind, idempotency_key, fingerprint, admission_id, attempt_id, generation, resource_lease_generation, payload_json, state, claim_owner, claim_expires_at, attempts, result_json, last_error, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, 0, '{}', NULL, ?, ?, NULL)",
                    (
                        intent_id, row["execution_id"], counterpart_kind,
                        counterpart_key, row["fingerprint"], row["admission_id"],
                        row["attempt_id"], row["generation"],
                        row["resource_lease_generation"], row["payload_json"], now, now,
                    ),
                )
                persisted = self._resource_intent(connection.execute(
                    "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
                ).fetchone())
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
        query = "SELECT * FROM execution_resource_intents WHERE 1 = 1"
        values: list[Any] = []
        if execution_id is not None:
            query += " AND execution_id = ?"
            values.append(execution_id)
        if states:
            query += " AND state IN (" + ",".join("?" for _ in states) + ")"
            values.extend(states)
        query += " ORDER BY created_at, intent_id"
        if limit is not None:
            if limit <= 0:
                raise ExecutionConflict("invalid_limit", "limit must be positive")
            query += " LIMIT ?"
            values.append(limit)
        with closing(self._connect()) as connection:
            return [self._resource_intent(row) for row in connection.execute(query, values)]


    def claim_resource_intents(
        self, *, owner_id: str, limit: int = 100, lease_ttl_seconds: float = 30.0, now: float | None = None,
    ) -> list[dict[str, Any]]:
        if not owner_id or limit <= 0 or lease_ttl_seconds <= 0:
            raise ExecutionConflict("invalid_resource_claim", "owner_id, limit and lease_ttl_seconds must be positive")
        current = time.time() if now is None else now
        with self._transaction() as connection:
            connection.execute(
                "UPDATE execution_resource_intents SET state = 'pending', claim_owner = NULL, claim_expires_at = NULL, updated_at = ? WHERE state = 'claimed' AND claim_expires_at <= ?",
                (current, current),
            )
            rows = connection.execute(
                "SELECT intent_id FROM execution_resource_intents WHERE state = 'pending' ORDER BY created_at, intent_id LIMIT ?", (limit,)
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                changed = connection.execute(
                    "UPDATE execution_resource_intents SET state = 'claimed', claim_owner = ?, claim_expires_at = ?, attempts = attempts + 1, updated_at = ? WHERE intent_id = ? AND state = 'pending'",
                    (owner_id, current + lease_ttl_seconds, current, row["intent_id"]),
                ).rowcount
                if changed:
                    claimed_row = connection.execute(
                        "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (row["intent_id"],)
                    ).fetchone()
                    assert claimed_row is not None
                    claimed.append(self._resource_intent(claimed_row))
            return claimed


    def complete_resource_intent(
        self, intent_id: str, *, owner_id: str, result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                return None
            if row["state"] == "applied":
                return self._resource_intent(row)
            if row["state"] != "claimed" or row["claim_owner"] != owner_id:
                return None
            connection.execute(
                "UPDATE execution_resource_intents SET state = 'applied', claim_owner = NULL, claim_expires_at = NULL, result_json = ?, updated_at = ?, completed_at = ? WHERE intent_id = ?",
                (_json(dict(result or {})), now, now, intent_id),
            )
            return self._resource_intent(connection.execute(
                "SELECT * FROM execution_resource_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone())


    def retry_resource_intent(self, intent_id: str, *, owner_id: str, error: str) -> bool:
        with self._transaction() as connection:
            return connection.execute(
                "UPDATE execution_resource_intents SET state = 'pending', claim_owner = NULL, claim_expires_at = NULL, last_error = ?, updated_at = ? WHERE intent_id = ? AND state = 'claimed' AND claim_owner = ?",
                (error[:1024], time.time(), intent_id, owner_id),
            ).rowcount == 1


    def list_projection_outbox(
        self,
        *,
        execution_id: str | None = None,
        states: Collection[str] = (),
        limit: int | None = None,
    ):
        query = "SELECT * FROM execution_projection_outbox WHERE 1 = 1"
        values: list[Any] = []
        if execution_id is not None:
            query += " AND execution_id = ?"
            values.append(execution_id)
        if states:
            query += " AND state IN (" + ",".join("?" for _ in states) + ")"
            values.extend(
                getattr(state, "value", state)
                for state in states
            )
        query += " ORDER BY event_sequence, projection_kind"
        if limit is not None:
            if limit <= 0:
                raise ProjectionConflict("invalid_limit", "limit must be positive")
            query += " LIMIT ?"
            values.append(limit)
        with closing(self._connect()) as connection:
            return [
                self._projection_outbox(row)
                for row in connection.execute(query, values)
            ]


    def claim_projection_outbox(
        self,
        *,
        owner_id: str,
        limit: int = 100,
        lease_ttl_seconds: float = 30.0,
        allowed_kinds: Collection[str] | None = None,
    ):
        from ..outbox import ProjectionOutboxState

        if not owner_id or limit <= 0 or lease_ttl_seconds <= 0:
            raise ProjectionConflict(
                "invalid_claim", "owner_id, positive limit and lease are required"
            )
        kinds = (
            tuple(getattr(kind, "value", kind) for kind in allowed_kinds)
            if allowed_kinds is not None
            else PROJECTION_KINDS
        )
        if any(kind not in PROJECTION_KINDS for kind in kinds):
            raise ProjectionConflict("invalid_projection_kind", "unknown projection kind")
        if allowed_kinds is not None and not kinds:
            return []
        now = time.time()
        with self._transaction() as connection:
            self._reclaim_projection_outbox_in_transaction(connection, now=now)
            kind_placeholders = ",".join("?" for _ in kinds)
            rows = connection.execute(
                "SELECT * FROM execution_projection_outbox "
                "WHERE state = ? AND available_at <= ? "
                f"AND projection_kind IN ({kind_placeholders}) "
                "ORDER BY event_sequence, projection_kind LIMIT ?",
                (ProjectionOutboxState.PENDING.value, now, *kinds, limit),
            ).fetchall()
            expires = now + lease_ttl_seconds
            claimed = []
            for row in rows:
                updated = connection.execute(
                    "UPDATE execution_projection_outbox SET state = ?, "
                    "claim_owner = ?, claim_expires_at = ?, attempts = attempts + 1 "
                    "WHERE outbox_id = ? AND state = ?",
                    (
                        ProjectionOutboxState.CLAIMED.value,
                        owner_id,
                        expires,
                        row["outbox_id"],
                        ProjectionOutboxState.PENDING.value,
                    ),
                )
                if updated.rowcount == 1:
                    claimed_row = connection.execute(
                        "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                        (row["outbox_id"],),
                    ).fetchone()
                    claimed.append(self._projection_outbox(claimed_row))
            return claimed


    def ack_projection_outbox(self, outbox_id: str, *, owner_id: str):
        from ..outbox import ProjectionOutboxState

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise ProjectionConflict("not_found", f"outbox item not found: {outbox_id}")
            if row["state"] == ProjectionOutboxState.DELIVERED.value:
                return self._projection_outbox(row)
            now = time.time()
            updated = connection.execute(
                "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
                "claim_expires_at = NULL, delivered_at = ?, last_error = NULL "
                "WHERE outbox_id = ? AND state = ? AND claim_owner = ? "
                "AND claim_expires_at IS NOT NULL AND claim_expires_at > ?",
                (
                    ProjectionOutboxState.DELIVERED.value,
                    now,
                    outbox_id,
                    ProjectionOutboxState.CLAIMED.value,
                    owner_id,
                    now,
                ),
            )
            if updated.rowcount != 1:
                latest = connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                assert latest is not None
                self._raise_projection_claim_conflict(latest, owner_id, now)
            return self._projection_outbox(
                connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )


    def fail_projection_outbox(
        self,
        outbox_id: str,
        *,
        owner_id: str,
        error: str,
        retry_at: float | None = None,
    ):
        from ..outbox import ProjectionOutboxState

        if not error:
            raise ProjectionConflict("invalid_error", "projection failure must include an error")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
            if row is None:
                raise ProjectionConflict("not_found", f"outbox item not found: {outbox_id}")
            now = time.time()
            updated = connection.execute(
                "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
                "claim_expires_at = NULL, available_at = ?, last_error = ? "
                "WHERE outbox_id = ? AND state = 'claimed' AND claim_owner = ? "
                "AND claim_expires_at IS NOT NULL AND claim_expires_at > ?",
                (
                    ProjectionOutboxState.PENDING.value,
                    now if retry_at is None else retry_at,
                    error[:2000],
                    outbox_id,
                    owner_id,
                    now,
                ),
            )
            if updated.rowcount != 1:
                latest = connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
                assert latest is not None
                self._raise_projection_claim_conflict(latest, owner_id, now)
            return self._projection_outbox(
                connection.execute(
                    "SELECT * FROM execution_projection_outbox WHERE outbox_id = ?",
                    (outbox_id,),
                ).fetchone()
            )


    def release_projection_outbox(
        self, outbox_ids: Collection[str], *, owner_id: str
    ) -> int:
        """Return this owner's unprocessed live claims to pending without retry cost."""
        from ..outbox import ProjectionOutboxState

        ids = tuple(dict.fromkeys(outbox_ids))
        if not owner_id:
            raise ProjectionConflict("invalid_claim", "owner_id is required")
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        now = time.time()
        with self._transaction() as connection:
            updated = connection.execute(
                "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
                "claim_expires_at = NULL WHERE state = ? AND claim_owner = ? "
                "AND claim_expires_at IS NOT NULL AND claim_expires_at > ? "
                f"AND outbox_id IN ({placeholders})",
                (
                    ProjectionOutboxState.PENDING.value,
                    ProjectionOutboxState.CLAIMED.value,
                    owner_id,
                    now,
                    *ids,
                ),
            )
            return updated.rowcount


    def reclaim_projection_outbox(self, *, now: float | None = None) -> int:
        with self._transaction() as connection:
            return self._reclaim_projection_outbox_in_transaction(
                connection, now=time.time() if now is None else now
            )


    @staticmethod
    def _reclaim_projection_outbox_in_transaction(
        connection: sqlite3.Connection, *, now: float
    ) -> int:
        from ..outbox import ProjectionOutboxState

        updated = connection.execute(
            "UPDATE execution_projection_outbox SET state = ?, claim_owner = NULL, "
            "claim_expires_at = NULL WHERE state = ? AND claim_expires_at IS NOT NULL "
            "AND claim_expires_at <= ?",
            (
                ProjectionOutboxState.PENDING.value,
                ProjectionOutboxState.CLAIMED.value,
                now,
            ),
        )
        return updated.rowcount


    @staticmethod
    def _raise_projection_claim_conflict(
        row: sqlite3.Row, owner_id: str, now: float
    ) -> None:
        from ..outbox import ProjectionOutboxState

        if row["state"] != ProjectionOutboxState.CLAIMED.value:
            raise ProjectionConflict("invalid_state", f"outbox item is {row['state']}")
        if not owner_id or row["claim_owner"] != owner_id:
            raise ProjectionConflict("claim_owner_mismatch", "outbox claim belongs to another owner")
        if row["claim_expires_at"] is None or float(row["claim_expires_at"]) <= now:
            raise ProjectionConflict("claim_expired", "outbox claim has expired")

