"""Resource governance: borrowed."""
from __future__ import annotations
import time
from typing import Any, Callable, Mapping


class BorrowedOperations:
    def release_borrowed_job(
        self,
        job_id: str,
        *,
        parent_job_id: str,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
    ) -> bool:
        """Release a child only while its borrowed parent fence is current."""
        with self.ledger.immediate() as conn:
            parent = conn.execute(
                """SELECT 1 FROM job_admissions
                   WHERE job_id = ? AND state IN ('live','stopping')
                     AND owner_instance_id = ? AND lease_generation = ?""",
                (parent_job_id, owner_instance_id, lease_generation),
            ).fetchone()
            if parent is None:
                return False
            return conn.execute(
                """UPDATE job_admissions
                   SET state = 'released', terminal_blocked = 0,
                       terminal_block_command_id = NULL,
                       terminal_block_phase = NULL,
                       terminal_block_expires_at = NULL,
                       terminal_block_prior_dispatch_ready = NULL,
                       released_at = ?, reason_code = ?,
                       lease_expires_at = NULL, resume_parent_msg_id = NULL
                   WHERE job_id = ? AND state = 'queued'
                     AND borrowed_parent_job_id = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = job_admissions.job_id
                           AND job_finalizations.state = 'pending'
                     )""",
                (time.time(), reason_code, job_id, parent_job_id),
            ).rowcount == 1


    def start_borrowed_job(
        self,
        job_id: str,
        *,
        parent_job_id: str,
        owner_instance_id: str,
        lease_generation: int,
    ) -> bool:
        """Fence a borrowed child's runtime without consuming live capacity."""
        with self.ledger.immediate() as conn:
            parent = conn.execute(
                """SELECT 1 FROM job_admissions
                   WHERE job_id = ? AND state IN ('live','stopping')
                     AND owner_instance_id = ? AND lease_generation = ?""",
                (parent_job_id, owner_instance_id, lease_generation),
            ).fetchone()
            if parent is None:
                return False
            now = time.time()
            return conn.execute(
                """UPDATE job_admissions
                   SET owner_instance_id = ?, lease_generation = ?,
                       started_at = ?, last_activity_at = ?, lease_expires_at = ?
                   WHERE job_id = ? AND state = 'queued'
                     AND dispatch_ready = 0
                     AND terminal_blocked = 0
                     AND borrowed_parent_job_id = ?
                     AND owner_instance_id IS NULL""",
                (
                    owner_instance_id, lease_generation, now, now, now + 30.0,
                    job_id, parent_job_id,
                ),
            ).rowcount == 1


    def renew_borrowed_lease(
        self,
        job_id: str,
        *,
        parent_job_id: str,
        owner_instance_id: str,
        lease_generation: int,
    ) -> bool:
        """Renew the child entry only while the borrowed parent fence is live."""
        with self.ledger.immediate() as conn:
            return conn.execute(
                """UPDATE job_admissions AS child SET lease_expires_at = ?
                   WHERE child.job_id = ? AND child.state = 'queued'
                     AND child.borrowed_parent_job_id = ?
                     AND child.owner_instance_id = ?
                     AND child.lease_generation = ?
                     AND EXISTS (
                         SELECT 1 FROM job_admissions AS parent
                         WHERE parent.job_id = child.borrowed_parent_job_id
                           AND parent.state IN ('live','stopping')
                           AND parent.owner_instance_id = ?
                           AND parent.lease_generation = ?
                     )""",
                (
                    time.time() + 30.0, job_id, parent_job_id,
                    owner_instance_id, lease_generation,
                    owner_instance_id, lease_generation,
                ),
            ).rowcount == 1


    def finalize_borrowed_job(
        self,
        job_id: str,
        *,
        parent_job_id: str,
        owner_instance_id: str,
        lease_generation: int,
        reason_code: str,
        terminal_fields: Mapping[str, Any],
        mutate: Callable[[dict[str, Any]], Any],
    ) -> bool:
        """Finalize a borrowed child under its exact parent fence."""
        if terminal_fields.get("reason_code") != reason_code:
            raise ValueError("terminal_fields reason_code must match reason_code")
        return self._finalize_with_intent(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            terminal_fields=terminal_fields,
            eligible_states=("queued",),
            borrowed_parent_job_id=parent_job_id,
            require_parent_fence=True,
            mutate=mutate,
        )


    def release_orphaned_borrowed_jobs(self) -> list[tuple[str, str]]:
        """Release borrowed children whose parent no longer owns a claim."""
        with self.ledger.immediate() as conn:
            rows = conn.execute(
                """SELECT child.job_id, child.session_id
                   FROM job_admissions AS child
                   LEFT JOIN job_admissions AS parent
                     ON parent.job_id = child.borrowed_parent_job_id
                   WHERE child.state = 'queued'
                     AND child.borrowed_parent_job_id IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM job_finalizations
                         WHERE job_finalizations.job_id = child.job_id
                           AND job_finalizations.state = 'pending'
                     )
                     AND (parent.job_id IS NULL
                          OR parent.state NOT IN ('live','stopping'))"""
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE job_admissions
                       SET state = 'released', terminal_blocked = 0,
                           terminal_block_command_id = NULL,
                           terminal_block_phase = NULL,
                           terminal_block_expires_at = NULL,
                           terminal_block_prior_dispatch_ready = NULL,
                           released_at = ?,
                           resume_parent_msg_id = NULL,
                           reason_code = 'error.borrowed_parent_lost'
                       WHERE job_id = ? AND state = 'queued'
                         AND NOT EXISTS (
                             SELECT 1 FROM job_finalizations
                             WHERE job_finalizations.job_id = job_admissions.job_id
                               AND job_finalizations.state = 'pending'
                         )""",
                    (time.time(), row["job_id"]),
                )
        return [(str(row["job_id"]), str(row["session_id"])) for row in rows]

