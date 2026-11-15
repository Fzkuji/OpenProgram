"""Resource governance: recovery."""
from __future__ import annotations
import time
from typing import Any, Callable
from openprogram.runtime.jobs.types import Job, is_terminal
from openprogram.runtime.jobs.resource_governance.contracts import (
    ReconcileResult,
)
from openprogram.runtime.jobs.resource_governance.limits import (
    _CANONICAL_PROJECTION_OWNER,
    _NO_TERMINAL_BLOCK,
)


class RecoveryOperations:
    def reconcile(
        self,
        *,
        job_lookup: Callable[[str, str], Job | None],
        write_terminal: Callable[[str, str, dict[str, Any]], Any] | None = None,
        mark_worker_lost: Callable[[str, str], Any],
        owner_is_alive: Callable[[str], bool],
        now: float | None = None,
    ) -> ReconcileResult:
        """Reconcile durable admissions without spanning job-store I/O."""
        current_time = time.time() if now is None else now
        pending = self._read().rows("job_finalizations", state="pending", order="created_at")
        pending_job_ids = {str(row["job_id"]) for row in pending}
        finalization_conflicts = 0
        completed_pending: list[tuple[str, str]] = []
        for intent in pending:
            job = job_lookup(intent["session_id"], intent["job_id"])
            if job is None:
                continue
            try:
                fields = self._terminal_fields(intent["fields_json"])
            except ValueError:
                continue
            if not is_terminal(job.status):
                if write_terminal is None:
                    continue
                try:
                    write_terminal(intent["session_id"], intent["job_id"], fields)
                except Exception:
                    continue
                job = job_lookup(intent["session_id"], intent["job_id"])
                if job is None or not is_terminal(job.status):
                    continue
            actual_fields = {
                "status": job.status.value,
                "head_id": job.head_id,
                "result_text": job.result_text,
                "error": job.error,
                "reason_code": job.reason_code,
            }
            if actual_fields != fields:
                finalization_conflicts += 1
                continue
            if intent["owner_instance_id"] == _CANONICAL_PROJECTION_OWNER:
                completed = self._complete_ownerless_projection(
                    intent["job_id"],
                    lease_generation=intent["lease_generation"],
                    fields_json=intent["fields_json"],
                    reason_code=fields["reason_code"],
                )
                if completed:
                    completed_pending.append((intent["job_id"], intent["session_id"]))
                continue
            completed = self._complete_finalization(
                intent["job_id"],
                owner_instance_id=intent["owner_instance_id"],
                lease_generation=intent["lease_generation"],
                fields_json=intent["fields_json"],
                reason_code=fields["reason_code"],
                eligible_states=("live", "stopping", "queued"),
            )
            if completed:
                completed_pending.append((intent["job_id"], intent["session_id"]))
        rows = self._read().rows(
            "job_admissions", where=lambda row: row["state"] != "released", order="admitted_seq",
        )
        finalized = rolled_back = released_missing = released_lost = 0
        worker_lost: list[tuple[str, str]] = []
        for row in rows:
            if row["job_id"] in pending_job_ids:
                continue
            state = row["state"]
            job = job_lookup(row["session_id"], row["job_id"])
            if state == "preparing":
                if job is not None and job.admission_id == row["admission_id"]:
                    with self._write() as tables:
                        changed = tables.update(
                            "job_admissions", {"state": "queued"},
                            admission_id=row["admission_id"], state="preparing",
                        )
                    finalized += int(changed == 1)
                else:
                    with self._write() as tables:
                        deleted = tables.delete(
                            "job_admissions", admission_id=row["admission_id"], state="preparing",
                        )
                        if deleted:
                            tables.delete("budget_scopes", budget_scope_id=row["budget_scope_id"])
                    rolled_back += int(deleted == 1)
                continue
            if state == "queued":
                if job is None:
                    with self._write() as tables:
                        changed = tables.update(
                            "job_admissions",
                            {
                                "state": "released", **_NO_TERMINAL_BLOCK,
                                "reason_code": "error.job_missing",
                                "resume_parent_msg_id": None,
                                "released_at": current_time,
                            },
                            admission_id=row["admission_id"], state="queued",
                        )
                    released_missing += int(changed == 1)
                elif is_terminal(job.status):
                    with self._write() as tables:
                        changed = tables.update(
                            "job_admissions",
                            {
                                "state": "released", **_NO_TERMINAL_BLOCK,
                                "released_at": current_time,
                                "resume_parent_msg_id": None,
                                "reason_code": lambda item: (
                                    job.reason_code
                                    if job.reason_code is not None
                                    else item["reason_code"]
                                ),
                            },
                            admission_id=row["admission_id"], state="queued",
                        )
                    released_missing += int(changed == 1)
                continue
            owner = row["owner_instance_id"]
            if owner and owner_is_alive(owner):
                continue
            with self._write() as tables:
                fenced = tables.update(
                    "job_admissions",
                    {
                        "state": "stopping", "reason_code": "error.worker_lost",
                        "owner_instance_id": None,
                        "lease_generation": lambda item: item["lease_generation"] + 1,
                    },
                    admission_id=row["admission_id"], state=("live", "stopping"),
                    owner_instance_id=owner, lease_generation=row["lease_generation"],
                )
            if fenced != 1:
                continue
            try:
                mark_worker_lost(row["session_id"], row["job_id"])
            except Exception:
                continue
            with self._write() as tables:
                changed = tables.update(
                    "job_admissions",
                    {
                        "state": "released", **_NO_TERMINAL_BLOCK,
                        "released_at": current_time,
                        "resume_parent_msg_id": None,
                    },
                    admission_id=row["admission_id"], state="stopping",
                    owner_instance_id=None, lease_generation=row["lease_generation"] + 1,
                    reason_code="error.worker_lost",
                )
            released_lost += int(changed == 1)
            if changed == 1:
                worker_lost.append((str(row["job_id"]), str(row["session_id"])))
        return ReconcileResult(
            finalized_preparing=finalized,
            rolled_back_preparing=rolled_back,
            released_missing=released_missing,
            released_worker_lost=released_lost,
            finalization_conflicts=finalization_conflicts,
            completed_pending=tuple(completed_pending),
            worker_lost=tuple(worker_lost),
        )

