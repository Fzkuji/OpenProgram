"""Resource governance: finalization."""
from __future__ import annotations
import json
import time
from typing import Any, Callable, Mapping
from openprogram.agent.job.types import JobStatus, is_terminal
from .limits import (
    TERMINAL_FIELD_NAMES,
    _CANONICAL_PROJECTION_OWNER,
)


class FinalizationOperations:
    def finalize_stopping_job(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Finalize this exact stopping claim."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("stopping",),
            use_admission_reason=True,
            mutate=mutate,
        )


    def abandon_stopping_job(
        self, job_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        """Revoke a failed stopping claim so reconciliation can finish it."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions
                   SET owner_instance_id = NULL,
                       lease_generation = lease_generation + 1,
                       lease_expires_at = NULL
                   WHERE job_id = ? AND state = 'stopping'
                     AND owner_instance_id = ? AND lease_generation = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )""",
                (job_id, owner_instance_id, lease_generation),
            ).rowcount == 1


    def request_stop(self, job_id: str, reason_code: str) -> None:
        with self.ledger.immediate() as conn:
            conn.execute(
                """UPDATE job_admissions
                   SET state = CASE
                       WHEN state = 'live' THEN 'stopping'
                       WHEN state = 'queued'
                            AND borrowed_parent_job_id IS NOT NULL
                            AND owner_instance_id IS NOT NULL THEN state
                       WHEN state IN ('preparing','queued') THEN 'released'
                       ELSE state END,
                       terminal_blocked = CASE
                           WHEN state IN ('preparing','queued') THEN 0
                           ELSE terminal_blocked END,
                       terminal_block_command_id = CASE
                           WHEN state IN ('preparing','queued') THEN NULL
                           ELSE terminal_block_command_id END,
                       terminal_block_phase = CASE
                           WHEN state IN ('preparing','queued') THEN NULL
                           ELSE terminal_block_phase END,
                       terminal_block_expires_at = CASE
                           WHEN state IN ('preparing','queued') THEN NULL
                           ELSE terminal_block_expires_at END,
                       terminal_block_prior_dispatch_ready = CASE
                           WHEN state IN ('preparing','queued') THEN NULL
                           ELSE terminal_block_prior_dispatch_ready END,
                       reason_code = ?,
                       released_at = CASE
                           WHEN state IN ('preparing','queued')
                                AND NOT (
                                    state = 'queued'
                                    AND borrowed_parent_job_id IS NOT NULL
                                    AND owner_instance_id IS NOT NULL
                                ) THEN ? ELSE released_at END
                   WHERE job_id = ?
                     AND state IN ('preparing','queued','live','stopping')""",
                (reason_code, time.time(), job_id),
            )


    def release_job(
        self,
        job_id: str,
        reason_code: str | None = None,
        *,
        owner_instance_id: str | None = None,
        lease_generation: int | None = None,
    ) -> bool:
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions
                   SET state = 'released', queue_state = 'released', terminal_blocked = 0,
                       terminal_block_command_id = NULL,
                       terminal_block_phase = NULL,
                       terminal_block_expires_at = NULL,
                       terminal_block_prior_dispatch_ready = NULL,
                       released_at = ?, lease_expires_at = NULL,
                       resume_parent_msg_id = NULL,
                       reason_code = COALESCE(?, reason_code)
                   WHERE job_id = ? AND state != 'released'
                     AND (state IN ('preparing','queued')
                          OR (owner_instance_id = ? AND lease_generation = ?))
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )""",
                (
                    time.time(), reason_code, job_id,
                    owner_instance_id, lease_generation,
                ),
            ).rowcount == 1


    @staticmethod
    def _terminal_fields_json(terminal_fields: Mapping[str, Any]) -> str:
        fields = dict(terminal_fields)
        if fields.keys() != TERMINAL_FIELD_NAMES:
            raise ValueError("terminal_fields must contain only terminal Job fields")
        try:
            status = JobStatus(fields["status"])
        except (TypeError, ValueError) as exc:
            raise ValueError("terminal_fields status must be terminal") from exc
        if not is_terminal(status):
            raise ValueError("terminal_fields status must be terminal")
        fields["status"] = status.value
        for name in TERMINAL_FIELD_NAMES - {"status"}:
            if fields[name] is not None and not isinstance(fields[name], str):
                raise ValueError(f"terminal_fields {name} must be a string or null")
        return json.dumps(
            {"version": 1, "fields": fields},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


    @classmethod
    def _terminal_fields(cls, fields_json: str) -> dict[str, Any]:
        try:
            payload = json.loads(fields_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid terminal fields JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.keys() != {"version", "fields"}
            or payload["version"] != 1
            or not isinstance(payload["fields"], dict)
            or cls._terminal_fields_json(payload["fields"]) != fields_json
        ):
            raise ValueError("invalid terminal fields JSON")
        return payload["fields"]


    def _stage_finalization(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        eligible_states: tuple[str, ...],
        borrowed_parent_job_id: str | None = None,
        require_parent_fence: bool = False,
        use_admission_reason: bool = False,
    ) -> tuple[str, str, dict[str, Any]] | None:
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                """SELECT session_id, state, borrowed_parent_job_id, reason_code
                   FROM job_admissions
                   WHERE job_id = ? AND owner_instance_id = ?
                     AND lease_generation = ?""",
                (job_id, owner_instance_id, lease_generation),
            ).fetchone()
            if (
                admission is None
                or admission["state"] not in eligible_states
                or admission["borrowed_parent_job_id"] != borrowed_parent_job_id
            ):
                return None
            if require_parent_fence:
                parent = conn.execute(
                    """SELECT 1 FROM job_admissions
                       WHERE job_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id = ? AND lease_generation = ?""",
                    (
                        borrowed_parent_job_id, owner_instance_id,
                        lease_generation,
                    ),
                ).fetchone()
                if parent is None:
                    return None
            fields = dict(terminal_fields)
            if use_admission_reason and admission["reason_code"] is not None:
                fields["reason_code"] = admission["reason_code"]
            fields_json = self._terminal_fields_json(fields)
            existing = conn.execute(
                """SELECT owner_instance_id, lease_generation, fields_json, state
                   FROM job_finalizations WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["owner_instance_id"] != owner_instance_id
                    or existing["lease_generation"] != lease_generation
                    or existing["fields_json"] != fields_json
                ):
                    return None
                if existing["state"] == "pending":
                    # Someone else already staged this intent and owns the
                    # terminal write; a pending intent left by a crash is
                    # completed by reconcile, never by a second writer.
                    return None
                return str(existing["state"]), fields_json, fields
            conn.execute(
                """INSERT INTO job_finalizations (
                       job_id, session_id, owner_instance_id, lease_generation,
                       fields_json, state, created_at
                   ) VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    job_id, admission["session_id"], owner_instance_id,
                    lease_generation, fields_json, time.time(),
                ),
            )
            return "pending", fields_json, fields


    def _complete_finalization(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        fields_json: str,
        reason_code: str | None,
        eligible_states: tuple[str, ...],
    ) -> bool:
        with self.ledger.immediate() as conn:
            intent = conn.execute(
                """SELECT state FROM job_finalizations
                   WHERE job_id = ? AND owner_instance_id = ?
                     AND lease_generation = ? AND fields_json = ?""",
                (job_id, owner_instance_id, lease_generation, fields_json),
            ).fetchone()
            if intent is None:
                return False
            if intent["state"] == "completed":
                return True
            placeholders = ",".join("?" for _ in eligible_states)
            changed = conn.execute(
                f"""UPDATE job_admissions
                    SET state = 'released', terminal_blocked = 0,
                        terminal_block_command_id = NULL,
                        terminal_block_phase = NULL,
                        terminal_block_expires_at = NULL,
                        terminal_block_prior_dispatch_ready = NULL,
                        released_at = ?, lease_expires_at = NULL,
                        resume_parent_msg_id = NULL,
                        reason_code = COALESCE(?, reason_code)
                    WHERE job_id = ? AND owner_instance_id = ?
                      AND lease_generation = ? AND state IN ({placeholders})""",
                (
                    time.time(), reason_code, job_id, owner_instance_id,
                    lease_generation, *eligible_states,
                ),
            ).rowcount
            if changed != 1:
                return False
            conn.execute(
                """UPDATE job_finalizations
                   SET state = 'completed', completed_at = ?
                   WHERE job_id = ? AND state = 'pending'""",
                (time.time(), job_id),
            )
            return True


    def enqueue_terminal_projection(
        self, job_id: str, terminal_fields: Mapping[str, Any],
    ) -> bool:
        """Durably queue a canonical terminal projection for retry.

        This ownerless intent is used only when the JobStore write is
        temporarily unavailable.  It preserves the exact terminal fields;
        reconciliation performs the projection and admission release later.
        """
        fields_json = self._terminal_fields_json(terminal_fields)
        with self.ledger.immediate() as conn:
            admission = conn.execute(
                "SELECT session_id, lease_generation FROM job_admissions "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if admission is None:
                return False
            # The dispatch fence and the projection intent must commit
            # together.  This closes the queued-admission race with
            # claim_next while the canonical database remains separate.
            conn.execute(
                """UPDATE job_admissions
                   SET dispatch_ready = 0, terminal_blocked = 1,
                       terminal_block_phase = COALESCE(
                           terminal_block_phase, 'projection'
                       ),
                       terminal_block_expires_at = COALESCE(
                           terminal_block_expires_at, ?
                       ),
                       terminal_block_prior_dispatch_ready = COALESCE(
                           terminal_block_prior_dispatch_ready, dispatch_ready
                       )
                   WHERE job_id = ? AND state != 'released'""",
                (time.time() + 30.0, job_id),
            )
            existing = conn.execute(
                "SELECT fields_json, state FROM job_finalizations "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if existing is not None:
                return existing[0] == fields_json
            conn.execute(
                """INSERT INTO job_finalizations (
                       job_id, session_id, owner_instance_id, lease_generation,
                       fields_json, state, created_at
                   ) VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    job_id, admission["session_id"],
                    _CANONICAL_PROJECTION_OWNER,
                    admission["lease_generation"], fields_json, time.time(),
                ),
            )
        return True


    def block_dispatch(
        self,
        job_id: str,
        *,
        command_id: str | None = None,
        phase: str = "recovery",
    ) -> bool:
        """Fence an admission before canonical terminal transition."""
        if phase not in {"prepared", "recovery", "projection"}:
            raise ValueError("invalid terminal barrier phase")
        now = time.time()
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions
                   SET dispatch_ready = 0, terminal_blocked = 1,
                       terminal_block_command_id = COALESCE(
                           terminal_block_command_id, ?
                       ),
                       terminal_block_phase = CASE
                           WHEN terminal_blocked = 1
                           THEN COALESCE(terminal_block_phase, ?)
                           ELSE ? END,
                       terminal_block_expires_at = CASE
                           WHEN terminal_blocked = 1
                           THEN COALESCE(terminal_block_expires_at, ?)
                           ELSE ? END,
                       terminal_block_prior_dispatch_ready = COALESCE(
                           terminal_block_prior_dispatch_ready,
                           CASE WHEN terminal_blocked = 1 THEN 0
                                ELSE dispatch_ready END
                       )
                   WHERE job_id = ? AND state != 'released'""",
                (
                    command_id, phase, phase, now + 30.0, now + 30.0, job_id,
                ),
            ).rowcount == 1


    def mark_terminal_dispatch_recovery(
        self, job_id: str, *, command_id: str,
    ) -> bool:
        """Record that the canonical cancel CAS failed after preparation."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions
                   SET terminal_block_phase = 'recovery',
                       terminal_block_expires_at = ?
                   WHERE job_id = ? AND terminal_blocked = 1
                     AND terminal_block_command_id = ?""",
                (time.time() + 30.0, job_id, command_id),
            ).rowcount == 1


    def unblock_terminal_dispatch(
        self,
        job_id: str,
        *,
        expected_state: str = "queued",
        expected_owner_instance_id: str | None = None,
        expected_lease_generation: int | None = None,
    ) -> bool:
        """Restore a queued admission under an exact recovery fence."""
        owner_clause = (
            "owner_instance_id IS NULL"
            if expected_owner_instance_id is None
            else "owner_instance_id = ?"
        )
        generation_clause = (
            ""
            if expected_lease_generation is None
            else " AND lease_generation = ?"
        )
        params: list[Any] = [job_id, expected_state]
        if expected_owner_instance_id is not None:
            params.append(expected_owner_instance_id)
        if expected_lease_generation is not None:
            params.append(expected_lease_generation)
        params.append(time.time())
        with self.ledger.immediate() as conn:
            return conn.execute(
                f"""UPDATE job_admissions
                   SET dispatch_ready = COALESCE(
                           terminal_block_prior_dispatch_ready, dispatch_ready
                       ),
                       terminal_blocked = 0,
                       terminal_block_command_id = NULL,
                       terminal_block_phase = NULL,
                       terminal_block_expires_at = NULL,
                       terminal_block_prior_dispatch_ready = NULL
                   WHERE job_id = ? AND state = ?
                     AND terminal_blocked = 1
                     AND {owner_clause}{generation_clause}
                     AND (
                         terminal_block_phase = 'recovery'
                         OR (
                             terminal_block_phase = 'prepared'
                             AND terminal_block_expires_at <= ?
                         )
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )""",
                params,
            ).rowcount == 1


    def restore_terminal_dispatch_claim(
        self,
        job_id: str,
        *,
        expected_state: str,
        expected_owner_instance_id: str | None,
        expected_lease_generation: int,
        target_state: str,
        owner_instance_id: str | None = None,
        lease_generation: int | None = None,
        reason_code: str | None = None,
    ) -> bool:
        """Reconcile a pre-cancel fence to a canonical owner/state exactly."""
        if target_state not in {"live", "stopping", "released"}:
            raise ValueError("target_state must be live, stopping, or released")
        if target_state != "released" and (
            owner_instance_id is None or lease_generation is None
        ):
            raise ValueError("an active target requires an owner fence")
        owner_clause = (
            "owner_instance_id IS NULL"
            if expected_owner_instance_id is None
            else "owner_instance_id = ?"
        )
        params: list[Any] = [job_id, expected_state]
        if expected_owner_instance_id is not None:
            params.append(expected_owner_instance_id)
        params.append(expected_lease_generation)
        params.append(time.time())
        with self.ledger.immediate() as conn:
            if target_state == "released":
                sql = f"""UPDATE job_admissions
                           SET state = 'released', terminal_blocked = 0,
                               dispatch_ready = 0, owner_instance_id = NULL,
                               lease_expires_at = NULL, released_at = ?,
                               resume_parent_msg_id = NULL,
                               terminal_block_command_id = NULL,
                               terminal_block_phase = NULL,
                               terminal_block_expires_at = NULL,
                               terminal_block_prior_dispatch_ready = NULL,
                               reason_code = COALESCE(?, reason_code)
                           WHERE job_id = ? AND state = ?
                             AND terminal_blocked = 1
                             AND {owner_clause}
                             AND lease_generation = ?
                             AND (
                                 terminal_block_phase = 'recovery'
                                 OR (
                                     terminal_block_phase = 'prepared'
                                     AND terminal_block_expires_at <= ?
                                 )
                             )
                             AND NOT EXISTS (
                                 SELECT 1 FROM job_finalizations
                                 WHERE job_finalizations.job_id = job_admissions.job_id
                                   AND job_finalizations.state = 'pending'
                             )"""
                release_params = [time.time(), reason_code, *params]
                return conn.execute(sql, release_params).rowcount == 1
            now = time.time()
            sql = f"""UPDATE job_admissions
                       SET state = ?, terminal_blocked = 0,
                           dispatch_ready = 0, owner_instance_id = ?,
                           lease_generation = ?, started_at = COALESCE(started_at, ?),
                           last_activity_at = ?, lease_expires_at = ?,
                           terminal_block_command_id = NULL,
                           terminal_block_phase = NULL,
                           terminal_block_expires_at = NULL,
                           terminal_block_prior_dispatch_ready = NULL,
                           reason_code = COALESCE(?, reason_code)
                       WHERE job_id = ? AND state = ?
                         AND terminal_blocked = 1
                         AND {owner_clause}
                         AND lease_generation = ?
                         AND (
                             terminal_block_phase = 'recovery'
                             OR (
                                 terminal_block_phase = 'prepared'
                                 AND terminal_block_expires_at <= ?
                             )
                         )
                         AND NOT EXISTS (
                             SELECT 1 FROM job_finalizations
                             WHERE job_finalizations.job_id = job_admissions.job_id
                               AND job_finalizations.state = 'pending'
                         )"""
            active_params = [
                target_state, owner_instance_id, lease_generation, now, now,
                now + 30.0, reason_code, *params,
            ]
            return conn.execute(sql, active_params).rowcount == 1


    def _complete_ownerless_projection(
        self, job_id: str, *, lease_generation: int,
        fields_json: str, reason_code: str | None,
    ) -> bool:
        """Complete an ownerless projection intent under its admission fence."""
        with self.ledger.immediate() as conn:
            intent = conn.execute(
                """SELECT state FROM job_finalizations
                   WHERE job_id = ? AND owner_instance_id = ?
                     AND lease_generation = ? AND fields_json = ?""",
                (
                    job_id, _CANONICAL_PROJECTION_OWNER,
                    lease_generation, fields_json,
                ),
            ).fetchone()
            if intent is None:
                return False
            if intent["state"] == "completed":
                return True
            admission = conn.execute(
                "SELECT state, lease_generation FROM job_admissions "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if admission is None:
                return False
            if admission["state"] != "released":
                changed = conn.execute(
                    """UPDATE job_admissions
                           SET state = 'released', terminal_blocked = 0,
                               terminal_block_command_id = NULL,
                               terminal_block_phase = NULL,
                               terminal_block_expires_at = NULL,
                               terminal_block_prior_dispatch_ready = NULL,
                               released_at = ?,
                           lease_expires_at = NULL,
                           resume_parent_msg_id = NULL,
                           reason_code = COALESCE(?, reason_code)
                       WHERE job_id = ?
                         AND state IN ('preparing','queued','live','stopping')
                         AND lease_generation = ?""",
                    (time.time(), reason_code, job_id, lease_generation),
                ).rowcount
                if changed != 1:
                    return False
            changed = conn.execute(
                """UPDATE job_finalizations
                   SET state = 'completed', completed_at = ?
                   WHERE job_id = ? AND owner_instance_id = ?
                     AND lease_generation = ? AND fields_json = ?
                     AND state = 'pending'""",
                (
                    time.time(), job_id, _CANONICAL_PROJECTION_OWNER,
                    lease_generation, fields_json,
                ),
            ).rowcount
            return changed == 1


    def pending_finalization(self, job_id: str):
        """Return the immutable pending terminal intent, if one exists."""
        with self.ledger.read() as conn:
            row = conn.execute(
                """SELECT job_id, session_id, owner_instance_id, lease_generation,
                          fields_json, state
                   FROM job_finalizations
                   WHERE job_id = ? AND state = 'pending'""",
                (job_id,),
            ).fetchone()
        return None if row is None else tuple(row)


    def pending_finalizations(self) -> list[tuple[str, str, str, int, str, str]]:
        """Return all pending terminal intents for projection recovery."""
        with self.ledger.read() as conn:
            rows = conn.execute(
                """SELECT job_id, session_id, owner_instance_id,
                          lease_generation, fields_json, state
                   FROM job_finalizations
                   WHERE state = 'pending'
                   ORDER BY created_at"""
            ).fetchall()
        return [tuple(row) for row in rows]


    def admission_fence(self, job_id: str) -> tuple[str | None, int] | None:
        """Return the current admission owner fence for a projection."""
        with self.ledger.read() as conn:
            row = conn.execute(
                """SELECT owner_instance_id, lease_generation
                   FROM job_admissions WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
        return None if row is None else (row[0], int(row[1]))


    def admission_state(
        self, job_id: str,
    ) -> tuple[str, str | None, int, bool, str | None, float | None, str | None, int | None] | None:
        """Return state and fence fields needed for canonical recovery."""
        with self.ledger.read() as conn:
            row = conn.execute(
                """SELECT state, owner_instance_id, lease_generation,
                          terminal_blocked, terminal_block_phase,
                          terminal_block_expires_at, terminal_block_command_id,
                          terminal_block_prior_dispatch_ready
                   FROM job_admissions WHERE job_id = ?""",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return (
            str(row[0]), row[1], int(row[2]), bool(row[3]), row[4],
            row[5], row[6], row[7],
        )


    def complete_pending_finalization(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        fields_json: str,
        reason_code: str | None,
    ) -> bool:
        """Release exactly the admission fenced by a pending intent."""
        if owner_instance_id == _CANONICAL_PROJECTION_OWNER:
            return self._complete_ownerless_projection(
                job_id,
                lease_generation=lease_generation,
                fields_json=fields_json,
                reason_code=reason_code,
            )
        return self._complete_finalization(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            fields_json=fields_json,
            reason_code=reason_code,
            eligible_states=('preparing', 'queued', 'live', 'stopping'),
        )


    def _finalize_with_intent(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        eligible_states: tuple[str, ...],
        mutate: Callable[[dict[str, Any]], Any],
        borrowed_parent_job_id: str | None = None,
        require_parent_fence: bool = False,
        use_admission_reason: bool = False,
    ) -> bool:
        staged = self._stage_finalization(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=eligible_states,
            borrowed_parent_job_id=borrowed_parent_job_id,
            require_parent_fence=require_parent_fence,
            use_admission_reason=use_admission_reason,
        )
        if staged is None:
            return False
        intent_state, fields_json, fields = staged
        if intent_state == "completed":
            return True
        mutate(fields)
        return self._complete_finalization(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            fields_json=fields_json,
            reason_code=fields["reason_code"],
            eligible_states=eligible_states,
        )


    def finalize_job(
        self,
        job_id: str,
        reason_code: str | None,
        *,
        owner_instance_id: str,
        lease_generation: int,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Persist terminal intent, write JobStore, then release this lease."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("live", "stopping"),
            mutate=mutate,
        )

