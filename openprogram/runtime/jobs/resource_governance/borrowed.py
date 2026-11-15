"""Resource governance: borrowed."""
from __future__ import annotations
import time
from typing import Any, Callable, Mapping

from openprogram.runtime.jobs.resource_governance.limits import _NO_TERMINAL_BLOCK


class BorrowedOperations:
    def _parent_holds_claim(
        self, tables, parent_job_id: str, owner_instance_id: str, lease_generation: int,
    ) -> bool:
        return self._admission(
            tables, parent_job_id, state=("live", "stopping"),
            owner_instance_id=owner_instance_id, lease_generation=lease_generation,
        ) is not None


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
        with self._write() as tables:
            if not self._parent_holds_claim(
                tables, parent_job_id, owner_instance_id, lease_generation,
            ):
                return False
            self.ledger.ensure_job_session(tables, job_id)
            if self._pending_finalization(tables, job_id):
                return False
            return tables.update(
                "job_admissions",
                {
                    "state": "released", **_NO_TERMINAL_BLOCK,
                    "released_at": time.time(), "reason_code": reason_code,
                    "resume_parent_msg_id": None,
                },
                job_id=job_id, state="queued", borrowed_parent_job_id=parent_job_id,
            ) == 1


    def start_borrowed_job(
        self,
        job_id: str,
        *,
        parent_job_id: str,
        owner_instance_id: str,
        lease_generation: int,
    ) -> bool:
        """Fence a borrowed child's runtime without consuming live capacity."""
        with self._write() as tables:
            if not self._parent_holds_claim(
                tables, parent_job_id, owner_instance_id, lease_generation,
            ):
                return False
            self.ledger.ensure_job_session(tables, job_id)
            now = time.time()
            return tables.update(
                "job_admissions",
                {
                    "owner_instance_id": owner_instance_id,
                    "lease_generation": lease_generation,
                    "started_at": now, "last_activity_at": now,
                },
                job_id=job_id, state="queued", dispatch_ready=0, terminal_blocked=0,
                borrowed_parent_job_id=parent_job_id, owner_instance_id=None,
            ) == 1

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
        with self._write() as tables:
            pending = {
                row["job_id"] for row in tables.rows("job_finalizations", state="pending")
            }
            orphans = []
            for child in tables.rows(
                "job_admissions", state="queued",
                where=lambda row: row["borrowed_parent_job_id"] is not None,
            ):
                if child["job_id"] in pending:
                    continue
                parent = self._admission(tables, child["borrowed_parent_job_id"])
                if parent is None or parent["state"] not in ("live", "stopping"):
                    orphans.append(child)
            for child in orphans:
                tables.update(
                    "job_admissions",
                    {
                        "state": "released", **_NO_TERMINAL_BLOCK,
                        "released_at": time.time(), "resume_parent_msg_id": None,
                        "reason_code": "error.borrowed_parent_lost",
                    },
                    job_id=child["job_id"], state="queued",
                )
        return [(str(row["job_id"]), str(row["session_id"])) for row in orphans]
