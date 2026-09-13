"""Resource governance: dispatch."""
from __future__ import annotations
import os
import time
from typing import Any, Callable
from openprogram.agent.job.types import Job
from .contracts import (
    AdmissionDecision,
    DispatchClaim,
)


class DispatchOperations:
    @staticmethod
    def _owner_holds_worker_lock(owner_instance_id: str) -> bool:
        """Validate that this caller owns the profile's singleton worker lock."""
        owner_pid = os.getpid()
        if owner_instance_id.startswith("worker_"):
            try:
                owner_pid = int(owner_instance_id.split("_", 2)[1])
            except (IndexError, ValueError):
                return False
            if owner_pid != os.getpid():
                return False
        try:
            from openprogram.worker.lock import is_held_by
            return is_held_by(owner_pid)
        except Exception:
            return False


    def try_start(self, job_id: str, *, owner_instance_id: str) -> bool:
        """Atomically exchange queued capacity for live capacity."""
        if not self._owner_holds_worker_lock(owner_instance_id):
            return False
        with self.ledger.immediate() as conn:
            row, resolved = self._resolved_for_admission(conn, job_id)
            if row is None or resolved is None:
                return False
            admission = conn.execute(
                """SELECT state, owner_instance_id FROM job_admissions
                   WHERE job_id = ?""", (job_id,),
            ).fetchone()
            if admission["state"] == "live":
                return admission["owner_instance_id"] == owner_instance_id
            if admission["state"] != "queued":
                return False
            capacity = self._capacity(conn, row["session_id"], resolved)
            live = capacity["session_live"]
            global_live = conn.execute(
                "SELECT COUNT(*) FROM job_admissions WHERE state IN ('live','stopping')"
            ).fetchone()[0]
            if global_live >= resolved.scheduler_capacity:
                return False
            if live["limit"] is not None and live["used"] >= live["limit"]:
                return False
            now = time.time()
            changed = conn.execute(
                """UPDATE job_admissions
                   SET state = 'live', owner_instance_id = ?, started_at = ?,
                       last_activity_at = ?, lease_expires_at = ?,
                       lease_generation = lease_generation + 1
                   WHERE job_id = ? AND state = 'queued'
                     AND dispatch_ready = 1 AND terminal_blocked = 0
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )""",
                (owner_instance_id, now, now, now + 30.0, job_id),
            ).rowcount
            return changed == 1


    def has_live_jobs(self) -> bool:
        """Include claims acquired before canonical activation in shutdown checks."""
        with self.ledger.read() as connection:
            return connection.execute(
                "SELECT 1 FROM job_admissions WHERE state IN ('live', 'stopping') LIMIT 1"
            ).fetchone() is not None


    def claim_next(
        self,
        *,
        owner_instance_id: str,
        excluded_sessions: set[str] | None = None,
        only_job_id: str | None = None,
    ) -> DispatchClaim | None:
        """Claim the globally oldest queued job whose session is eligible."""
        if not self._owner_holds_worker_lock(owner_instance_id):
            return None
        excluded_sessions = excluded_sessions or set()
        with self.ledger.immediate() as conn:
            queued = conn.execute(
                """SELECT job_id, session_id FROM job_admissions
                   WHERE state = 'queued' AND dispatch_ready = 1
                     AND terminal_blocked = 0
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )
                   ORDER BY admitted_seq"""
            ).fetchall()
            for candidate in queued:
                if only_job_id is not None and candidate["job_id"] != only_job_id:
                    continue
                if candidate["session_id"] in excluded_sessions:
                    continue
                row, resolved = self._resolved_for_admission(
                    conn, candidate["job_id"],
                )
                if row is None or resolved is None:
                    continue
                global_live = conn.execute(
                    """SELECT COUNT(*) FROM job_admissions
                       WHERE state IN ('live','stopping')"""
                ).fetchone()[0]
                if global_live >= resolved.scheduler_capacity:
                    return None
                capacity = self._capacity(conn, candidate["session_id"], resolved)
                live = capacity["session_live"]
                if live["limit"] is not None and live["used"] >= live["limit"]:
                    continue
                now = time.time()
                changed = conn.execute(
                    """UPDATE job_admissions
                       SET state = 'live', owner_instance_id = ?, started_at = ?,
                           last_activity_at = ?, lease_expires_at = ?,
                           lease_generation = lease_generation + 1
                       WHERE job_id = ? AND state = 'queued'
                         AND dispatch_ready = 1 AND terminal_blocked = 0""",
                    (
                        owner_instance_id, now, now, now + 30.0,
                        candidate["job_id"],
                    ),
                ).rowcount
                if changed == 1:
                    generation = conn.execute(
                        "SELECT lease_generation FROM job_admissions WHERE job_id = ?",
                        (candidate["job_id"],),
                    ).fetchone()[0]
                    return DispatchClaim(
                        candidate["job_id"], candidate["session_id"], generation,
                    )
            return None


    def reserve_admission(
        self,
        job: Job,
        *,
        persist: Callable[[Job], Any],
        creates_agent: bool = True,
        caller_session_id: str | None = None,
        caller_turn_id: str | None = None,
        dispatch_ready: bool = True,
    ) -> AdmissionDecision:
        """Idempotently consume an execution admission intent in this ledger."""
        return self.admit_job(
            job,
            persist=persist,
            creates_agent=creates_agent,
            caller_session_id=caller_session_id,
            caller_turn_id=caller_turn_id,
            dispatch_ready=dispatch_ready,
        )


    def queue_resume(
        self,
        job_id: str,
        *,
        admission_id: str,
        command_id: str,
        paused: bool = True,
    ) -> bool:
        """Idempotently put an existing admission back in the claim queue."""
        queue_state = "paused_waiting_claim" if paused else "queued_resume"
        with self.ledger.immediate() as conn:
            row = conn.execute(
                "SELECT state, queue_state, resume_command_id FROM job_admissions WHERE job_id = ? AND admission_id = ?",
                (job_id, admission_id),
            ).fetchone()
            if row is None:
                return False
            if row["state"] == "live":
                return row["resume_command_id"] == command_id
            if row["state"] == "queued" and row["resume_command_id"] not in (None, command_id):
                return False
            return conn.execute(
                "UPDATE job_admissions SET state = 'queued', queue_state = ?, resume_command_id = ?, dispatch_ready = 1, owner_instance_id = NULL, lease_expires_at = NULL WHERE job_id = ? AND admission_id = ? AND state IN ('queued', 'released')",
                (queue_state, command_id, job_id, admission_id),
            ).rowcount == 1


    def reclaim_paused(self, job_id: str, *, admission_id: str, command_id: str) -> bool:
        """Public name for the paused continuation claim queue transition."""
        return self.queue_resume(
            job_id, admission_id=admission_id, command_id=command_id, paused=True,
        )


    def claim_execution(
        self,
        job_id: str,
        *,
        owner_instance_id: str,
        admission_id: str,
        command_id: str | None = None,
    ) -> DispatchClaim | None:
        """Claim exactly one admission, returning an existing same-owner claim."""
        with self.ledger.read() as conn:
            row = conn.execute(
                "SELECT session_id, state, owner_instance_id, lease_generation, admission_id FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None or row["admission_id"] != admission_id:
            return None
        if row["state"] == "live" and row["owner_instance_id"] == owner_instance_id:
            return DispatchClaim(job_id, str(row["session_id"]), int(row["lease_generation"]))
        claim = self.claim_next(owner_instance_id=owner_instance_id, only_job_id=job_id)
        if claim is None:
            return None
        with self.ledger.immediate() as conn:
            conn.execute(
                "UPDATE job_admissions SET queue_state = 'live', resume_command_id = ? WHERE job_id = ? AND owner_instance_id = ? AND lease_generation = ?",
                (command_id, job_id, owner_instance_id, claim.lease_generation),
            )
        return claim


    def release_execution(
        self,
        job_id: str,
        reason_code: str,
        *,
        admission_id: str,
        owner_instance_id: str | None = None,
        resource_lease_generation: int | None = None,
    ) -> bool:
        """Fenced, idempotent resource release for a canonical execution."""
        with self.ledger.read() as conn:
            row = conn.execute(
                "SELECT state, admission_id, owner_instance_id, lease_generation FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None or row["admission_id"] != admission_id:
            return False
        if row["state"] == "released":
            return True
        return self.release_job(
            job_id,
            reason_code,
            owner_instance_id=owner_instance_id,
            lease_generation=resource_lease_generation,
        )


    def stage_deferred_resume(
        self,
        job_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Persist the mutable target head under the original admission fence."""
        with self.ledger.immediate() as conn:
            row = conn.execute(
                """SELECT resume_parent_msg_id FROM job_admissions
                   WHERE job_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND borrowed_parent_job_id IS NULL""",
                (job_id, admission_id),
            ).fetchone()
            if row is None:
                return False
            staged = row["resume_parent_msg_id"]
            if staged is not None and staged != parent_msg_id:
                return False
            conn.execute(
                """UPDATE job_admissions SET resume_parent_msg_id = ?
                   WHERE job_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0""",
                (parent_msg_id, job_id, admission_id),
            )
            return True


    def mark_dispatch_ready(
        self,
        job_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Publish a staged deferred Job under its immutable admission fence."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions
                   SET dispatch_ready = 1
                   WHERE job_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND terminal_blocked = 0
                     AND resume_parent_msg_id = ?
                     AND borrowed_parent_job_id IS NULL""",
                (job_id, admission_id, parent_msg_id),
            ).rowcount == 1


    def continuation_parent_msg_id(self, job_id: str) -> str | None:
        """Return the durable deferred-resume target for canonical input."""
        with self.ledger.read() as conn:
            row = conn.execute(
                "SELECT resume_parent_msg_id FROM job_admissions "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return row[0] if row is not None else None


    def admission_exists(self, job_id: str) -> bool:
        """Check whether the resource ledger owns an admission for a Job."""
        with self.ledger.read() as conn:
            return conn.execute(
                "SELECT 1 FROM job_admissions WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone() is not None


    def budget_scope_id(self, job_id: str) -> str | None:
        """Return the authoritative budget scope for a canonical Job."""
        with self.ledger.read() as conn:
            row = conn.execute(
                "SELECT budget_scope_id FROM job_admissions WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return str(row[0]) if row is not None and row[0] else None


    def canonical_limits(self, job_id: str) -> dict[str, int | float | None]:
        """Read the active Job budget from the authoritative ledger row."""
        with self.ledger.read() as conn:
            rows = conn.execute(
                """WITH RECURSIVE ancestors AS (
                       SELECT b.*, 0 AS depth
                       FROM budget_scopes b
                       JOIN job_admissions a
                         ON a.budget_scope_id = b.budget_scope_id
                       WHERE a.job_id = ?
                       UNION ALL
                       SELECT parent.*, ancestors.depth + 1
                       FROM budget_scopes parent
                       JOIN ancestors
                         ON ancestors.parent_scope_id = parent.budget_scope_id
                   )
                   SELECT max_total_tokens, max_cost_microusd,
                          max_runtime_seconds, idle_timeout_seconds
                   FROM ancestors ORDER BY depth""",
                (job_id,),
            ).fetchall()
        if not rows:
            return {}
        values = [
            next((row[index] for row in rows if row[index] is not None), None)
            for index in range(4)
        ]
        return {
            "max_total_tokens": values[0],
            "max_cost_microusd": values[1],
            "max_runtime_seconds": values[2],
            "idle_timeout_seconds": values[3],
        }


    def publish_accepted_job(
        self,
        job_id: str,
        *,
        admission_id: str,
    ) -> bool:
        """Make a newly admitted Job claimable after caller side effects.

        Unlike ``mark_dispatch_ready``, this path has no mutable resume head
        to stage.  Its fence is the immutable admission id plus the queued,
        undispatched state.
        """
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions SET dispatch_ready = 1
                   WHERE job_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND terminal_blocked = 0
                     AND resume_parent_msg_id IS NULL
                     AND borrowed_parent_job_id IS NULL""",
                (job_id, admission_id),
            ).rowcount == 1


    def reset_deferred_resume(
        self,
        job_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Undo a staged head whose JobStore update did not survive."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions SET resume_parent_msg_id = NULL
                   WHERE job_id = ? AND admission_id = ?
                     AND state = 'queued' AND dispatch_ready = 0
                     AND resume_parent_msg_id = ?""",
                (job_id, admission_id, parent_msg_id),
            ).rowcount == 1


    def pending_deferred_resumes(self) -> list[tuple[str, str, str, str]]:
        """Return staged resume publications left incomplete by a crash."""
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT job_id, session_id, admission_id, resume_parent_msg_id
                   FROM job_admissions
                   WHERE state = 'queued' AND dispatch_ready = 0
                     AND resume_parent_msg_id IS NOT NULL
                     AND borrowed_parent_job_id IS NULL
                   ORDER BY admitted_seq"""
            ).fetchall()
        return [
            (
                str(row["job_id"]), str(row["session_id"]),
                str(row["admission_id"]), str(row["resume_parent_msg_id"]),
            )
            for row in rows
        ]


    def deferred_dispatches(self) -> list[tuple[str, str]]:
        """Return admitted Jobs waiting for their inbox intent."""
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT job_id, session_id FROM job_admissions
                   WHERE state = 'queued' AND dispatch_ready = 0
                     AND borrowed_parent_job_id IS NULL
                     AND resume_parent_msg_id IS NULL
                   ORDER BY admitted_seq"""
            ).fetchall()
        return [(str(row["job_id"]), str(row["session_id"])) for row in rows]


    def renew_lease(
        self, job_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        with self.ledger.immediate() as conn:
            now = time.time()
            return conn.execute(
                """UPDATE job_admissions SET lease_expires_at = ?
                   WHERE job_id = ? AND owner_instance_id = ?
                     AND lease_generation = ?
                     AND state IN ('live','stopping')""",
                (now + 30.0, job_id, owner_instance_id, lease_generation),
            ).rowcount == 1


    def requeue_job(
        self, job_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        """Return a claimed job to queued when another turn owns its session."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions
                   SET state = 'queued', queue_state = 'queued', owner_instance_id = NULL,
                       lease_expires_at = NULL, started_at = NULL,
                       last_activity_at = NULL
                   WHERE job_id = ? AND state = 'live'
                     AND owner_instance_id = ? AND lease_generation = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )""",
                (job_id, owner_instance_id, lease_generation),
            ).rowcount == 1

