"""JobRunner borrowed operations."""
from __future__ import annotations
from . import shared


class BorrowedOperations:
    def can_borrow_current_claim(self, session_id: str) -> bool:
        return self._current_borrowable_claim(session_id) is not None


    def _current_borrowable_claim(
        self, session_id: str,
    ) -> tuple[str, str, int, str] | None:
        inherited = shared._borrowed_claim.get()
        if inherited is not None:
            return inherited if inherited[3] == session_id else None
        if shared._current_job_runner.get() is not self:
            return None
        parent_job_id = shared._current_job_id.get()
        if not parent_job_id:
            return None
        parent = shared._store_load(session_id, parent_job_id)
        if parent is None or parent.parent_session_id != session_id:
            return None
        with self._lock:
            info = self._jobs.get(parent_job_id)
            generation = info.get("lease_generation") if info else None
        if generation is None:
            fence = self._governor.admission_fence(parent_job_id)
            if fence is not None and fence[0] == self._instance_id:
                generation = fence[1]
        if generation is None:
            return None
        return (
            parent_job_id, self._instance_id, int(generation), session_id,
        )


    def _run_borrowed_job(
        self,
        job: shared.Job,
        claim: tuple[str, str, int, str],
    ) -> None:
        """Execute a sync child inline under its same-session parent fence."""
        self._run_borrowed_canonical(job, claim)


    def _run_borrowed_canonical(
        self,
        job: shared.Job,
        claim: tuple[str, str, int, str],
    ) -> None:
        """Run a borrowed child through the same canonical Agent owner."""
        parent_job_id, owner_instance_id, lease_generation, session_id = claim
        if not self._governor.start_borrowed_job(
            job.id,
            parent_job_id=parent_job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
        ):
            raise RuntimeError(f"borrowed job {job.id!r} lost its parent fence")
        started_monotonic = self._monotonic()
        time_limits = self._borrowed_time_limits(job, started_monotonic)
        cancel_ev = shared.threading.Event()
        done_ev = shared.threading.Event()
        activated = self._activate_canonical_execution(job.id, cancel_ev)
        if activated is None:
            self._governor.release_borrowed_job(
                job.id,
                parent_job_id=parent_job_id,
                owner_instance_id=owner_instance_id,
                lease_generation=lease_generation,
                reason_code="error.activation_failed",
            )
            raise RuntimeError(f"borrowed job {job.id!r} could not activate")
        attempt, _running, binding = activated
        with self._lock:
            self._jobs[job.id] = {
                "event": cancel_ev,
                "future": None,
                "session_id": session_id,
                "context": shared.contextvars.copy_context(),
                "started_monotonic": started_monotonic,
                "last_activity_monotonic": started_monotonic,
                "time_limits": time_limits,
                "lease_generation": lease_generation,
                "budget_cancelled": False,
                "borrowed_parent_job_id": parent_job_id,
                "attempt_id": attempt.attempt_id,
                "attempt_generation": attempt.generation,
                "driver": binding.driver,
            }
            self._done_events[job.id] = done_ev
        lease_stop = shared.threading.Event()
        lease_thread = shared.threading.Thread(
            target=self._renew_borrowed_lease,
            args=(
                job.id,
                parent_job_id,
                lease_generation,
                lease_stop,
                attempt.attempt_id,
                attempt.generation,
            ),
            daemon=True,
            name=f"op-job-borrowed-lease-{job.id}",
        )
        lease_thread.start()
        result = None
        try:
            result = binding.handle.done.result()
        finally:
            lease_stop.set()
            lease_thread.join(timeout=1.0)
            execution = self._execution_store.get_execution(job.id)
            if execution is not None and execution.status.value in {
                "completed", "cancelled", "failed", "interrupted",
            }:
                projected_status = {
                    "completed": shared.JobStatus.COMPLETED,
                    "cancelled": shared.JobStatus.CANCELLED,
                    "failed": shared.JobStatus.ERRORED,
                    "interrupted": shared.JobStatus.ERRORED,
                }[execution.status.value]
                self._project_canonical_terminal(
                    execution,
                    terminal_fields=shared._terminal_fields(
                        projected_status,
                        execution.reason_code or projected_status.value,
                        head_id=getattr(result, "head_id", None),
                        result_text=getattr(result, "final_text", None),
                        error=getattr(result, "error", None),
                    ),
                    admission_owner_instance_id=owner_instance_id,
                    admission_lease_generation=lease_generation,
                )
            else:
                self._governor.release_borrowed_job(
                    job.id,
                    parent_job_id=parent_job_id,
                    owner_instance_id=owner_instance_id,
                    lease_generation=lease_generation,
                    reason_code="error.borrowed_owner_lost",
                )
            self._wake_done(job.id)
            with self._lock:
                self._jobs.pop(job.id, None)
                self._done_events.pop(job.id, None)


    def _borrowed_time_limits(
        self, job: shared.Job, started_monotonic: float,
    ) -> tuple[float | None, float | None]:
        """Apply configured child ceilings and ancestors' remaining runtime."""
        runtime_limit, idle_limit = self._governor.job_time_limits(job.id)
        current_id = job.parent_job_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            with self._lock:
                ancestor = self._jobs.get(current_id)
                snapshot = dict(ancestor) if ancestor is not None else None
            if snapshot is not None and snapshot.get("started_monotonic") is not None:
                ancestor_runtime = snapshot.get("time_limits", (None, None))[0]
                if ancestor_runtime is not None:
                    remaining = max(
                        0.0,
                        float(ancestor_runtime) - (
                            started_monotonic - snapshot["started_monotonic"]
                        ),
                    )
                    runtime_limit = (
                        remaining if runtime_limit is None
                        else min(float(runtime_limit), remaining)
                    )
            current = self.get_job(current_id)
            current_id = current.parent_job_id if current is not None else None
        return runtime_limit, idle_limit

