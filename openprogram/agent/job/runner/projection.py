"""JobRunner projection operations."""
from __future__ import annotations
from . import shared


class ProjectionOperations:
    def _project_canonical_terminal(
        self, execution, *, terminal_fields: dict[str, shared.Any] | None = None,
        admission_owner_instance_id: str | None = None,
        admission_lease_generation: int | None = None,
    ) -> None:
        from openprogram.execution import ExecutionStatus

        status_map = {
            ExecutionStatus.COMPLETED: shared.JobStatus.COMPLETED,
            ExecutionStatus.CANCELLED: shared.JobStatus.CANCELLED,
            ExecutionStatus.FAILED: shared.JobStatus.ERRORED,
            ExecutionStatus.INTERRUPTED: shared.JobStatus.ERRORED,
        }
        status = status_map.get(execution.status)
        if status is None:
            return
        # Startup recovery also visits foreground Agent executions. Their
        # validated immutable input distinguishes them from resource-admitted
        # Jobs; a missing or corrupt Job input must still fail closed below.
        if self._execution_store.get_agent_turn_input(execution.execution_id) is not None:
            return
        # A durable finalization intent is the sole source of terminal
        # projection fields.  In particular, recovery must not replace a
        # pending result/error/head with fields reconstructed from the
        # canonical execution row.
        pending_lookup = getattr(self._governor, "pending_finalization", None)
        pending = pending_lookup(execution.execution_id) if pending_lookup else None
        if pending is not None:
            self._project_pending_canonical_terminal(pending)
            return
        job = shared._store_load(execution.session_id, execution.execution_id)
        if (
            job is not None
            and not shared.is_terminal(job.status)
            and terminal_fields is None
            and status is shared.JobStatus.COMPLETED
            and self._local_owner_will_project_completed(execution.execution_id)
        ):
            # Dispatcher inserts _jobs/_done_events before the pool future
            # exists and before _wait_for_canonical_driver stamps result_text.
            # Reconstructing COMPLETED here would freeze an empty absorbing row.
            return
        if job is not None and not shared.is_terminal(job.status):
            projection_fields = terminal_fields or shared._terminal_fields(
                status, execution.reason_code or status.value,
            )
            try:
                shared._store_write_terminal(
                    execution.session_id,
                    execution.execution_id,
                    projection_fields,
                )
            except ValueError:
                pass
            except Exception:
                enqueue = getattr(
                    self._governor, "enqueue_terminal_projection", None,
                )
                persisted = False
                if enqueue is not None:
                    try:
                        persisted = bool(enqueue(
                            execution.execution_id, projection_fields,
                        ))
                    except Exception:
                        shared._log.exception(
                            "failed to persist terminal projection intent for %s",
                            execution.execution_id,
                        )
                if not persisted:
                    block_dispatch = getattr(self._governor, "block_dispatch", None)
                    if block_dispatch is not None:
                        block_dispatch(
                            execution.execution_id,
                            phase="projection",
                        )
                    raise RuntimeError(
                        "terminal projection recovery required",
                    )
                # The ledger intent is durable; the next reconciliation pass
                # retries the JobStore write before releasing admission.
                self._dispatch_wake.set()
                return
        canonical_job = self._canonical_job(execution.execution_id)
        if canonical_job is None or not canonical_job.admission_id:
            raise RuntimeError("terminal Job projection has no immutable admission")
        if admission_owner_instance_id is None or admission_lease_generation is None:
            admission_fence = self._governor.admission_fence(
                execution.execution_id,
            )
            if admission_fence is not None:
                admission_owner_instance_id, admission_lease_generation = (
                    admission_fence
                )
        self._resource_saga.request_release(
            execution.execution_id,
            admission_id=canonical_job.admission_id,
            reason_code=execution.reason_code or status.value,
            attempt_id=execution.current_attempt_id,
            generation=execution.owner_lease.get("generation"),
            resource_lease_generation=admission_lease_generation,
            terminal_version=execution.status_version,
        )
        self._resource_saga.reconcile()
        if status is shared.JobStatus.CANCELLED and canonical_job.worktree_id:
            from openprogram.worktree.manager import get_manager
            from openprogram.worktree.types import WorktreeStatus

            manager = get_manager()
            worktree = manager.get_worktree(canonical_job.worktree_id)
            if worktree is not None and worktree.status not in {
                WorktreeStatus.DISCARDED,
                WorktreeStatus.KEPT,
                WorktreeStatus.MERGED,
            }:
                manager.discard_worktree(canonical_job.worktree_id, force=True)
        clear_resume = getattr(self._governor, "clear_resume_parent_msg_id", None)
        if clear_resume is not None:
            clear_resume(execution.execution_id)


    def _prepare_canonical_terminal(
        self, execution, command_id: str | None = None,
    ) -> bool:
        """Block queued admission before canonical cancellation can terminalize."""
        block_dispatch = getattr(self._governor, "block_dispatch", None)
        if block_dispatch is None:
            return False
        return bool(block_dispatch(
            execution.execution_id,
            command_id=command_id,
            phase="prepared",
        ))


    def _recover_canonical_terminal_prepare(
        self, execution, command_id: str,
    ) -> bool:
        mark_recovery = getattr(
            self._governor, "mark_terminal_dispatch_recovery", None,
        )
        if mark_recovery is None:
            return False
        return bool(mark_recovery(execution.execution_id, command_id=command_id))


    def _project_pending_canonical_terminal(self, intent) -> bool:
        """Replay one durable terminal intent and then release its exact fence."""
        (
            job_id, session_id, owner_instance_id,
            lease_generation, fields_json, _state,
        ) = intent
        try:
            fields = self._governor._terminal_fields(fields_json)
        except ValueError:
            shared._log.error("invalid terminal projection intent for %s", intent)
            return False
        job = shared._store_load(session_id, job_id)
        if job is None:
            return False
        if not shared.is_terminal(job.status):
            try:
                shared._store_write_terminal(session_id, job_id, fields)
            except ValueError:
                pass
            except Exception:
                return False
            job = shared._store_load(session_id, job_id)
        if job is None or not shared.is_terminal(job.status):
            return False
        actual_fields = {
            "status": job.status.value,
            "head_id": job.head_id,
            "result_text": job.result_text,
            "error": job.error,
            "reason_code": job.reason_code,
        }
        if actual_fields != fields:
            return False
        complete = getattr(self._governor, "complete_pending_finalization", None)
        if complete is None or not complete(
            job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
            fields_json=fields_json,
            reason_code=fields["reason_code"],
        ):
            return False
        clear_resume = getattr(self._governor, "clear_resume_parent_msg_id", None)
        if clear_resume is not None:
            clear_resume(job_id)
        return True


    def _local_owner_will_project_completed(self, job_id: str) -> bool:
        """True while this runner still owns the worker that stamps result_text.

        The dispatcher writes ``_jobs`` and ``_done_events`` before activating
        the canonical driver and before ``_pool.submit(_run_one)``, so ``future``
        may still be None when the driver is already terminal.
        ``_wait_for_canonical_driver`` pops both maps only after it attempts
        the field-bearing projection.
        """
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None or job_id not in self._done_events:
                return False
            future = entry.get("future")
            return future is None or not future.done()


    def _project_existing_canonical_terminals(self) -> list[tuple[str, str]]:
        """Close projection gaps after canonical finish before a crash."""
        completed: list[tuple[str, str]] = []
        pending_ids: set[str] = set()
        pending_list = getattr(self._governor, "pending_finalizations", None)
        if pending_list is not None:
            for intent in pending_list():
                job_id = str(intent[0])
                pending_ids.add(job_id)
                if self._project_pending_canonical_terminal(intent):
                    completed.append((job_id, str(intent[1])))
        for job in self.list_jobs():
            if job.id in pending_ids:
                continue
            execution = self._execution_store.get_execution(job.id)
            if execution is not None and execution.status.value in {
                "completed", "cancelled", "failed", "interrupted",
            }:
                try:
                    self._execution_control.reconcile_terminal_cancel(execution)
                except Exception:
                    shared._log.exception(
                        "failed to reconcile terminal cancel command for %s",
                        execution.execution_id,
                    )
                fence = getattr(self._governor, "admission_fence", None)
                owner_generation = fence(job.id) if fence is not None else None
                self._project_canonical_terminal(
                    execution,
                    admission_owner_instance_id=(
                        owner_generation[0] if owner_generation is not None else None
                    ),
                    admission_lease_generation=(
                        owner_generation[1] if owner_generation is not None else None
                    ),
                )
        return completed


    def _reconcile_terminal_dispatch_barriers(self) -> None:
        """Reconcile pre-cancel fences with the canonical execution owner."""
        unblock = getattr(self._governor, "unblock_terminal_dispatch", None)
        if unblock is None:
            return
        admission_state = getattr(self._governor, "admission_state", None)
        restore = getattr(
            self._governor, "restore_terminal_dispatch_claim", None,
        )
        for execution in self._execution_store.list_nonterminal():
            if admission_state is None:
                if (
                    execution.status.value == "queued"
                    and execution.current_attempt_id is None
                ):
                    try:
                        unblock(execution.execution_id)
                    except Exception:
                        shared._log.exception(
                            "failed to reconcile terminal dispatch barrier for %s",
                            execution.execution_id,
                        )
                continue
            try:
                admission = admission_state(execution.execution_id)
                if admission is None or not admission[3]:
                    continue
                state, owner, lease_generation, _blocked, *_barrier = admission
                if (
                    execution.status.value == "queued"
                    and execution.current_attempt_id is None
                ):
                    unblock(
                        execution.execution_id,
                        expected_state=state,
                        expected_owner_instance_id=owner,
                        expected_lease_generation=lease_generation,
                    )
                    continue
                if restore is None:
                    continue
                canonical_owner = execution.owner_lease.get("owner_id")
                canonical_generation = execution.owner_lease.get("generation")
                if (
                    execution.current_attempt_id is not None
                    and isinstance(canonical_owner, str)
                    and isinstance(canonical_generation, int)
                ):
                    restore(
                        execution.execution_id,
                        expected_state=state,
                        expected_owner_instance_id=owner,
                        expected_lease_generation=lease_generation,
                        target_state=(
                            "stopping"
                            if execution.status.value == "cancelling"
                            else "live"
                        ),
                        owner_instance_id=canonical_owner,
                        lease_generation=canonical_generation,
                        reason_code=execution.reason_code,
                    )
                elif execution.status.value in {
                    "paused", "cancelling", "reconciliation_required",
                }:
                    restore(
                        execution.execution_id,
                        expected_state=state,
                        expected_owner_instance_id=owner,
                        expected_lease_generation=lease_generation,
                        target_state="released",
                        reason_code=execution.reason_code,
                    )
            except Exception:
                shared._log.exception(
                    "failed to reconcile terminal dispatch barrier for %s",
                    execution.execution_id,
                )

