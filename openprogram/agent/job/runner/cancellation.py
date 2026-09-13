"""JobRunner cancellation operations."""
from __future__ import annotations
from . import shared


class CancellationOperations:
    def cancel_execution(self, job_id: str, *, reason: shared.Optional[str] = None) -> shared.Optional[shared.Job]:
        return self._cancel_cascade(
            job_id, reason=reason, root_reason_code="cancel.user",
        )


    def _request_canonical_cancel(
        self, job_id: str, reason_code: str,
    ) -> None:
        """Persist one exact execution.cancel before worker signalling."""
        execution = self._execution_store.get_execution(job_id)
        if execution is None:
            raise RuntimeError(f"canonical execution {job_id!r} is missing")
        if execution.status.value in {
            "completed", "failed", "cancelled", "interrupted",
        }:
            return
        if execution.status.value == "cancelling":
            return
        try:
            dispatch = self._run_control(
                self._execution_control.request_cancel(
                    command_id=f"execution-cancel:{job_id}",
                    execution_id=job_id,
                    expected_version=execution.status_version,
                    actor={
                        "source": "job_runner",
                        "owner_id": self._instance_id,
                    },
                    reason_code=reason_code,
                )
            )
            if getattr(dispatch.execution.status, "value", None) in {
                "cancelled", "failed", "interrupted",
            }:
                self._project_canonical_terminal(dispatch.execution)
        except Exception:
            shared._log.exception("failed to persist canonical cancel for job %s", job_id)
            raise


    def _consume_pending_canonical_cancel(
        self,
        job_id: str,
        attempt_id: str,
        generation: int,
        cancel_event: shared.threading.Event,
    ) -> bool:
        """Consume a durable cancel command in the owning worker process."""
        try:
            dispatch = self._run_control(
                self._execution_control.deliver_pending_cancel(
                    execution_id=job_id,
                    attempt_id=attempt_id,
                    generation=generation,
                )
            )
        except Exception:
            shared._log.exception("failed to consume canonical cancel for %s", job_id)
            return False
        if dispatch is None or not dispatch.delivered:
            # In particular, owner_not_local is not a delivery confirmation.
            return False
        cancel_event.set()
        return True


    @staticmethod
    def _run_control(awaitable):
        """Run a control coroutine from sync code, including an event loop thread."""
        try:
            shared.asyncio.get_running_loop()
        except RuntimeError:
            return shared.asyncio.run(awaitable)
        result: list[shared.Any] = []
        failure: list[BaseException] = []

        def run() -> None:
            try:
                result.append(shared.asyncio.run(awaitable))
            except BaseException as exc:  # noqa: BLE001
                failure.append(exc)

        thread = shared.threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join()
        if failure:
            raise failure[0]
        return result[0] if result else None


    def _cancel_cascade(
        self,
        job_id: str,
        *,
        reason: shared.Optional[str],
        root_reason_code: str,
    ) -> shared.Optional[shared.Job]:
        """Cancel ``job_id`` and every descendant job on its
        ``parent_job_id`` chain (cascading cancel). Returns the
        (post-update) Job entity for ``job_id``, or None if not found.

        Descendants are collected breadth-first over the persisted job
        entities with a visited-set guard (a cycle in parent_job_id
        would otherwise loop forever). Pending/queued descendants flip
        straight to cancelled; running ones go through the same
        per-job cancel path as the root.

        Descendants are cancelled BEFORE the root. Cancelling the root
        makes its worker drop out, which frees a pool slot, and a queued
        descendant gets picked up in that slot — running a job that the
        cascade was about to cancel. Signalling descendants first means
        the worker finds an already-terminal entity and bails without
        calling ``run_agent_turn``.
        """
        # Unknown root: return None without touching anything, same as
        # before. The lookup is free for a job on the pool.
        if (
            self._find_session_for_job(job_id) is None
            and self._execution_store.get_execution(job_id) is None
        ):
            return None
        cascade_reason = reason or f"parent job {job_id} cancelled"
        for child in self._descendant_jobs(job_id):
            if shared.is_terminal(child.status):
                continue
            try:
                self._cancel_single(
                    child.id, reason=cascade_reason, reason_code="cancel.parent",
                )
            except Exception:
                pass
        return self._cancel_single(
            job_id, reason=reason, reason_code=root_reason_code,
        )


    def _descendant_jobs(self, root_job_id: str) -> list[shared.Job]:
        """All jobs reachable from ``root_job_id`` via parent_job_id,
        breadth-first, cycle-safe. Terminal ancestors are still
        traversed — a completed child may have spawned a grandchild
        that is still running."""
        children: dict[str, list[shared.Job]] = {}
        for t in self.list_jobs():
            if t.parent_job_id:
                children.setdefault(t.parent_job_id, []).append(t)
        out: list[shared.Job] = []
        seen = {root_job_id}
        queue = [root_job_id]
        while queue:
            cur = queue.pop(0)
            for child in children.get(cur, []):
                if child.id in seen:
                    continue
                seen.add(child.id)
                out.append(child)
                queue.append(child.id)
        return out


    def _cancel_single(
        self,
        job_id: str,
        *,
        reason: shared.Optional[str] = None,
        reason_code: str = "cancel.user",
    ) -> shared.Optional[shared.Job]:
        """Cancel one job, no cascade. Returns the (post-update)
        Job entity, or None if not found.

        Effect:

          * asks the unified execution service to persist the exact reason
            before signalling; an in-process copy preserves it if persistence
            is temporarily unavailable
          * sets the job's cancel event (worker drops out on next
            cooperative checkpoint)
          * delivers the canonical driver cancellation signal to the exact
            attempt-bound worker
          * if the job is still in pending/queued, flips to cancelled
            immediately (no worker pickup yet, nothing to wait for)
          * if running, retains the resource and attempt fences until the
            worker reports its terminal outcome
        """
        with self._lock:
            info = self._jobs.get(job_id)
        if not info:
            # Maybe loaded from disk-only state — try to find session.
            cur = self._find_session_for_job(job_id)
            canonical = self._execution_store.get_execution(job_id)
            if cur is None and canonical is None:
                return None
            session_id = cur or canonical.session_id
            info = None
        else:
            session_id = info["session_id"]
        cur_job = shared._store_load(session_id, job_id) or self._canonical_job(job_id)
        if cur_job is None:
            return None
        canonical_state = self._execution_store.get_execution(job_id)
        if canonical_state is None:
            # A JobStore-only row is not executable after the canonical
            # cutover.  Close it explicitly so it cannot remain queued or be
            # resurrected by cancellation, and release any leftover ledger
            # admission.  There is no legacy execution fallback.
            try:
                updated = shared._store_update_status(
                    session_id,
                    job_id,
                    shared.JobStatus.ERRORED,
                    error="canonical execution admission is missing",
                    reason_code="error.canonical_missing",
                )
            except ValueError:
                updated = shared._store_load(session_id, job_id)
            if updated is not None and shared.is_terminal(updated.status):
                self._governor.release_job(
                    job_id, updated.reason_code or "error.canonical_missing",
                )
                self._broadcast_job_status(updated)
                self._wake_done(job_id)
                self._update_attach_card(updated, error_text=updated.error)
                shared._broadcast_session_reload(session_id, reason="job_errored")
            return updated
        if canonical_state is not None and canonical_state.status.value in {
            "completed", "failed", "cancelled", "interrupted",
        }:
            if canonical_state.status.value == "cancelled":
                return cur_job
            return cur_job
        if shared.is_terminal(cur_job.status):
            return cur_job
        canonical_queued = (
            canonical_state is not None
            and canonical_state.status.value == "queued"
        )
        if canonical_queued and cur_job.status in (shared.JobStatus.PENDING, shared.JobStatus.QUEUED):
            self._request_canonical_cancel(job_id, reason_code)
            canonical_reason = self._canonical_cancel_reason(job_id)
            effective_reason = canonical_reason or reason_code
            try:
                from openprogram.agent import inbox
                inbox.discard_job(session_id, job_id)
                inbox.discard_tracked_job(job_id)
            except Exception:
                pass
            try:
                updated = shared._store_update_status(
                    session_id, job_id, shared.JobStatus.CANCELLED,
                    cancel_requested_at=shared.time.time(),
                    error=reason or (
                        "withdrawn before delivery"
                        if info is None else "cancelled before pickup"
                    ),
                    reason_code=effective_reason,
                )
            except ValueError:
                updated = shared._store_load(session_id, job_id)
            if updated is not None and shared.is_terminal(updated.status):
                if updated.status != shared.JobStatus.CANCELLED:
                    return updated
                try:
                    self._governor.request_stop(
                        job_id, effective_reason,
                    )
                except Exception:
                    shared._log.exception(
                        "failed to stop resource admission for job %s", job_id,
                    )
                if info is not None:
                    info["event"].set()
                self._broadcast_job_status(updated)
                self._wake_done(job_id)
                self._update_attach_card(updated)
                shared._broadcast_session_reload(session_id, reason="job_cancelled")
                with self._lock:
                    current_info = self._jobs.get(job_id)
                    if (
                        current_info is not None
                        and current_info.get("future") is None
                    ):
                        self._jobs.pop(job_id, None)
                        self._done_events.pop(job_id, None)
                self._dispatch_wake.set()
                return updated
            if updated is None:
                return None
            # The dispatcher won the pending -> running race. Continue through
            # the running path so the exact reason is durable before its token
            # is published.
            cur_job = updated
            with self._lock:
                info = self._jobs.get(job_id) or info

        # The canonical command is the authority.  The projection is only
        # read for the legacy Job DTO returned by this API.
        self._request_canonical_cancel(job_id, reason_code)
        canonical = self._execution_store.get_execution(job_id)
        persisted_reason = self._canonical_cancel_reason(job_id)
        with self._lock:
            live_info = self._jobs.get(job_id)
            if live_info is not None:
                info = live_info
                effective_reason = live_info.setdefault(
                    "cancel_reason_code", persisted_reason or reason_code,
                )
            else:
                effective_reason = persisted_reason or reason_code

        latest = shared._store_load(session_id, job_id)
        if latest is not None and shared.is_terminal(latest.status):
            if latest.status == shared.JobStatus.CANCELLED:
                try:
                    self._governor.request_stop(
                        job_id, effective_reason,
                    )
                except Exception:
                    shared._log.exception(
                        "failed to stop resource admission for job %s", job_id,
                    )
            return latest
        if info is not None:
            info["event"].set()

        # The first durable cancellation intent wins. A concurrent user,
        # parent, or budget cancellation may arrive after another reason was
        # already persisted; never overwrite that earlier decision in the
        # resource ledger.
        canonical_reason = self._canonical_cancel_reason(job_id)
        effective_reason = canonical_reason or effective_reason
        if latest is not None and not shared.is_terminal(latest.status):
            latest = shared._store_update_status(
                session_id,
                job_id,
                latest.status,
                expected_status=latest.status,
                cancel_requested_at=shared.time.time(),
                reason_code=effective_reason,
            ) or latest
            self._broadcast_job_status(latest)
        try:
            self._governor.request_stop(job_id, effective_reason)
        except Exception:
            shared._log.exception(
                "failed to stop resource admission for job %s", job_id,
            )
        if latest is not None:
            cur_job = latest

        return shared._store_load(session_id, job_id) or cur_job


    def _canonical_cancel_reason(self, job_id: str) -> str | None:
        command = self._execution_store.get_command(f"execution-cancel:{job_id}")
        if command is not None and command.kind.value == "execution.cancel":
            reason = command.payload.get("reason_code")
            if isinstance(reason, str) and reason:
                return reason
        execution = self._execution_store.get_execution(job_id)
        if execution is not None and execution.reason_code:
            return execution.reason_code
        return None


    def _cancel_reason_for_finalization(
        self, job_id: str, _projected_reason: str | None = None,
    ) -> str:
        canonical_reason = self._canonical_cancel_reason(job_id)
        if canonical_reason:
            return canonical_reason
        with self._lock:
            info = self._jobs.get(job_id)
            in_memory_reason = (
                info.get("cancel_reason_code") if info is not None else None
            )
        return in_memory_reason or "cancel.user"

