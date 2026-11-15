"""Resource governance: dispatch."""
from __future__ import annotations
import os
import time
from typing import Any, Callable
from openprogram.runtime.jobs.types import Job
from openprogram.runtime.jobs.resource_governance.contracts import (
    AdmissionDecision,
    DispatchClaim,
)
from openprogram.runtime.jobs.resource_governance.tables import RunTables, load_job_sessions
from openprogram.runtime.jobs.resource_governance.usage import _job_scope_chain
from openprogram.runtime.execution.store.ownership import owns_store_schema


def _next_generation(row: dict[str, Any]) -> int:
    return row["lease_generation"] + 1


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
            from openprogram.operations.worker.lock import is_held_by
            return is_held_by(owner_pid)
        except Exception:
            return False


    def try_start(self, job_id: str, *, owner_instance_id: str) -> bool:
        """Atomically exchange queued capacity for live capacity."""
        if not self._owner_holds_worker_lock(owner_instance_id):
            return False
        with self._write() as tables:
            admission, resolved = self._resolved_for_admission(tables, job_id)
            if admission is None or resolved is None:
                return False
            if admission["state"] == "live":
                return admission["owner_instance_id"] == owner_instance_id
            if admission["state"] != "queued":
                return False
            capacity = self._capacity(tables, admission["session_id"], resolved)
            live = capacity["session_live"]
            global_live = tables.count("job_admissions", state=("live", "stopping"))
            if global_live >= resolved.scheduler_capacity:
                return False
            if live["limit"] is not None and live["used"] >= live["limit"]:
                return False
            if self._pending_finalization(tables, job_id):
                return False
            now = time.time()
            return tables.update(
                "job_admissions",
                {
                    "state": "live", "owner_instance_id": owner_instance_id,
                    "started_at": now, "last_activity_at": now,
                    "lease_generation": _next_generation,
                },
                job_id=job_id, state="queued", dispatch_ready=1, terminal_blocked=0,
            ) == 1


    def has_live_jobs(self) -> bool:
        """Include claims acquired before canonical activation in shutdown checks."""
        run_tables = self.ledger.run_tables()
        if owns_store_schema(self.ledger._path()):  # noqa: SLF001
            run_tables = self._tables()
        else:
            # Another process owns the rows; only its logs say what they are now.
            run_tables = RunTables(run_tables.log)
            load_job_sessions(run_tables)
        return run_tables.handle.exists("job_admissions", state=("live", "stopping"))


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
        with self._write() as tables:
            pending = {
                row["job_id"] for row in tables.rows("job_finalizations", state="pending")
            }
            queued = [
                row for row in tables.rows(
                    "job_admissions", state="queued", dispatch_ready=1, terminal_blocked=0,
                    order="admitted_seq",
                )
                if row["job_id"] not in pending
            ]
            for candidate in queued:
                if only_job_id is not None and candidate["job_id"] != only_job_id:
                    continue
                if candidate["session_id"] in excluded_sessions:
                    continue
                row, resolved = self._resolved_for_admission(
                    tables, candidate["job_id"],
                )
                if row is None or resolved is None:
                    continue
                global_live = tables.count("job_admissions", state=("live", "stopping"))
                if global_live >= resolved.scheduler_capacity:
                    return None
                capacity = self._capacity(tables, candidate["session_id"], resolved)
                live = capacity["session_live"]
                if live["limit"] is not None and live["used"] >= live["limit"]:
                    continue
                now = time.time()
                changed = tables.update(
                    "job_admissions",
                    {
                        "state": "live", "owner_instance_id": owner_instance_id,
                        "started_at": now, "last_activity_at": now,
                        "lease_generation": _next_generation,
                    },
                    job_id=candidate["job_id"], state="queued",
                    dispatch_ready=1, terminal_blocked=0,
                )
                if changed == 1:
                    generation = tables.first(
                        "job_admissions", job_id=candidate["job_id"],
                    )["lease_generation"]
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
        with self._write() as tables:
            row = self._admission(tables, job_id, admission_id=admission_id)
            if row is None:
                return False
            if row["state"] == "live":
                return row["resume_command_id"] == command_id
            if row["state"] == "queued" and row["resume_command_id"] not in (None, command_id):
                return False
            return tables.update(
                "job_admissions",
                {
                    "state": "queued", "queue_state": queue_state,
                    "resume_command_id": command_id, "dispatch_ready": 1,
                    "owner_instance_id": None,
                },
                job_id=job_id, admission_id=admission_id, state=("queued", "released"),
            ) == 1


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
        tables = self._read()
        row = self._admission(tables, job_id)
        if row is None or row["admission_id"] != admission_id:
            return None
        if row["state"] == "live" and row["owner_instance_id"] == owner_instance_id:
            return DispatchClaim(job_id, str(row["session_id"]), int(row["lease_generation"]))
        claim = self.claim_next(owner_instance_id=owner_instance_id, only_job_id=job_id)
        if claim is None:
            return None
        with self._write() as tables:
            tables.update(
                "job_admissions",
                {"queue_state": "live", "resume_command_id": command_id},
                job_id=job_id, owner_instance_id=owner_instance_id,
                lease_generation=claim.lease_generation,
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
        row = self._admission(self._read(), job_id)
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
        with self._write() as tables:
            row = self._admission(
                tables, job_id, admission_id=admission_id, state="queued",
                dispatch_ready=0, borrowed_parent_job_id=None,
            )
            if row is None:
                return False
            staged = row["resume_parent_msg_id"]
            if staged is not None and staged != parent_msg_id:
                return False
            tables.update(
                "job_admissions", {"resume_parent_msg_id": parent_msg_id},
                job_id=job_id, admission_id=admission_id, state="queued", dispatch_ready=0,
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
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            return tables.update(
                "job_admissions", {"dispatch_ready": 1},
                job_id=job_id, admission_id=admission_id, state="queued",
                dispatch_ready=0, terminal_blocked=0,
                resume_parent_msg_id=parent_msg_id, borrowed_parent_job_id=None,
            ) == 1


    def continuation_parent_msg_id(self, job_id: str) -> str | None:
        """Return the durable deferred-resume target for canonical input."""
        row = self._admission(self._read(), job_id)
        return row["resume_parent_msg_id"] if row is not None else None


    def admission_exists(self, job_id: str) -> bool:
        """Check whether the resource ledger owns an admission for a Job."""
        return self._admission(self._read(), job_id) is not None


    def budget_scope_id(self, job_id: str) -> str | None:
        """Return the authoritative budget scope for a canonical Job."""
        row = self._admission(self._read(), job_id)
        return str(row["budget_scope_id"]) if row is not None and row["budget_scope_id"] else None


    def canonical_limits(self, job_id: str) -> dict[str, int | float | None]:
        """Read the active Job budget from the authoritative ledger row."""
        chain = _job_scope_chain(self.ledger, self._read(), job_id)
        if not chain:
            return {}
        return {
            name: next((scope[name] for scope in chain if scope[name] is not None), None)
            for name in (
                "max_total_tokens", "max_cost_microusd",
                "max_runtime_seconds", "idle_timeout_seconds",
            )
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
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            return tables.update(
                "job_admissions", {"dispatch_ready": 1},
                job_id=job_id, admission_id=admission_id, state="queued",
                dispatch_ready=0, terminal_blocked=0,
                resume_parent_msg_id=None, borrowed_parent_job_id=None,
            ) == 1


    def reset_deferred_resume(
        self,
        job_id: str,
        *,
        admission_id: str,
        parent_msg_id: str,
    ) -> bool:
        """Undo a staged head whose JobStore update did not survive."""
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            return tables.update(
                "job_admissions", {"resume_parent_msg_id": None},
                job_id=job_id, admission_id=admission_id, state="queued",
                dispatch_ready=0, resume_parent_msg_id=parent_msg_id,
            ) == 1


    def pending_deferred_resumes(self) -> list[tuple[str, str, str, str]]:
        """Return staged resume publications left incomplete by a crash."""
        rows = self._read().rows(
            "job_admissions", state="queued", dispatch_ready=0, borrowed_parent_job_id=None,
            where=lambda row: row["resume_parent_msg_id"] is not None,
            order="admitted_seq",
        )
        return [
            (
                str(row["job_id"]), str(row["session_id"]),
                str(row["admission_id"]), str(row["resume_parent_msg_id"]),
            )
            for row in rows
        ]


    def dispatch_published(self, job_id: str) -> bool:
        """Whether a deferred Job has already been handed to the dispatcher."""
        row = self._admission(self._read(), job_id)
        if row is None or row["state"] == "preparing":
            return False
        return not (row["state"] == "queued" and not row["dispatch_ready"])


    def deferred_dispatches(self) -> list[tuple[str, str]]:
        """Return admitted Jobs waiting for their inbox intent."""
        rows = self._read().rows(
            "job_admissions", state="queued", dispatch_ready=0,
            borrowed_parent_job_id=None, resume_parent_msg_id=None, order="admitted_seq",
        )
        return [(str(row["job_id"]), str(row["session_id"])) for row in rows]


    def requeue_job(
        self, job_id: str, *, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        """Return a claimed job to queued when another turn owns its session."""
        with self._write() as tables:
            self.ledger.ensure_job_session(tables, job_id)
            if self._pending_finalization(tables, job_id):
                return False
            return tables.update(
                "job_admissions",
                {
                    "state": "queued", "queue_state": "queued",
                    "owner_instance_id": None, "started_at": None,
                    "last_activity_at": None,
                },
                job_id=job_id, state="live", owner_instance_id=owner_instance_id,
                lease_generation=lease_generation,
            ) == 1
