"""Resource governance: recovery."""
from __future__ import annotations
import time
from typing import Any, Callable
from openprogram.agent.job.types import Job, is_terminal
from .contracts import (
    ReconcileResult,
)
from .limits import (
    _CANONICAL_PROJECTION_OWNER,
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
        with self.ledger.read() as conn:
            pending = conn.execute(
                """SELECT job_id, session_id, owner_instance_id, lease_generation,
                          fields_json
                   FROM job_finalizations WHERE state = 'pending'
                   ORDER BY created_at"""
            ).fetchall()
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
        with self.ledger.read() as conn:
            rows = conn.execute(
                """SELECT admission_id, job_id, session_id, budget_scope_id,
                          state, owner_instance_id, lease_generation, lease_expires_at
                   FROM job_admissions WHERE state != 'released'
                   ORDER BY admitted_seq"""
            ).fetchall()
        finalized = rolled_back = released_missing = released_lost = 0
        worker_lost: list[tuple[str, str]] = []
        for row in rows:
            if row["job_id"] in pending_job_ids:
                continue
            state = row["state"]
            job = job_lookup(row["session_id"], row["job_id"])
            if state == "preparing":
                if job is not None and job.admission_id == row["admission_id"]:
                    with self.ledger.immediate() as conn:
                        changed = conn.execute(
                            """UPDATE job_admissions SET state = 'queued'
                               WHERE admission_id = ? AND state = 'preparing'""",
                            (row["admission_id"],),
                        ).rowcount
                    finalized += int(changed == 1)
                else:
                    with self.ledger.immediate() as conn:
                        deleted = conn.execute(
                            """DELETE FROM job_admissions
                               WHERE admission_id = ? AND state = 'preparing'""",
                            (row["admission_id"],),
                        ).rowcount
                        if deleted:
                            conn.execute(
                                "DELETE FROM budget_scopes WHERE budget_scope_id = ?",
                                (row["budget_scope_id"],),
                            )
                    rolled_back += int(deleted == 1)
                continue
            if state == "queued":
                if job is None:
                    with self.ledger.immediate() as conn:
                        changed = conn.execute(
                            """UPDATE job_admissions
                               SET state = 'released', terminal_blocked = 0,
                                   terminal_block_command_id = NULL,
                                   terminal_block_phase = NULL,
                                   terminal_block_expires_at = NULL,
                                   terminal_block_prior_dispatch_ready = NULL,
                                   reason_code = 'error.job_missing',
                                   resume_parent_msg_id = NULL,
                                   released_at = ?
                               WHERE admission_id = ? AND state = 'queued'""",
                            (current_time, row["admission_id"]),
                        ).rowcount
                    released_missing += int(changed == 1)
                elif is_terminal(job.status):
                    with self.ledger.immediate() as conn:
                        changed = conn.execute(
                            """UPDATE job_admissions
                               SET state = 'released', terminal_blocked = 0,
                                   terminal_block_command_id = NULL,
                                   terminal_block_phase = NULL,
                                   terminal_block_expires_at = NULL,
                                   terminal_block_prior_dispatch_ready = NULL,
                                   released_at = ?,
                                   resume_parent_msg_id = NULL,
                                   reason_code = COALESCE(?, reason_code)
                               WHERE admission_id = ? AND state = 'queued'""",
                            (
                                current_time, job.reason_code,
                                row["admission_id"],
                            ),
                        ).rowcount
                    released_missing += int(changed == 1)
                continue
            lease = row["lease_expires_at"]
            if lease is not None and float(lease) > current_time:
                continue
            owner = row["owner_instance_id"]
            if owner and owner_is_alive(owner):
                continue
            with self.ledger.immediate() as conn:
                fenced = conn.execute(
                    """UPDATE job_admissions
                       SET state = 'stopping', reason_code = 'error.worker_lost',
                           owner_instance_id = NULL,
                           lease_generation = lease_generation + 1,
                           lease_expires_at = NULL
                       WHERE admission_id = ? AND state IN ('live','stopping')
                         AND owner_instance_id IS ?
                         AND lease_generation = ?
                         AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                    (
                        row["admission_id"], owner,
                        row["lease_generation"], current_time,
                    ),
                ).rowcount
            if fenced != 1:
                continue
            try:
                mark_worker_lost(row["session_id"], row["job_id"])
            except Exception:
                continue
            with self.ledger.immediate() as conn:
                changed = conn.execute(
                    """UPDATE job_admissions
                       SET state = 'released', terminal_blocked = 0,
                       terminal_block_command_id = NULL,
                       terminal_block_phase = NULL,
                       terminal_block_expires_at = NULL,
                       terminal_block_prior_dispatch_ready = NULL,
                           released_at = ?, lease_expires_at = NULL,
                           resume_parent_msg_id = NULL
                       WHERE admission_id = ? AND state = 'stopping'
                         AND owner_instance_id IS NULL
                         AND lease_generation = ?
                         AND reason_code = 'error.worker_lost'""",
                    (
                        current_time, row["admission_id"],
                        row["lease_generation"] + 1,
                    ),
                ).rowcount
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

