"""JobRunner recovery operations."""
from __future__ import annotations
from openprogram.runtime.jobs.runner import shared


class RecoveryOperations:
    def _owner_holds_worker_lock(self, owner_instance_id: str) -> bool:
        try:
            owner_pid = int(owner_instance_id.split("_", 2)[1])
        except (IndexError, ValueError):
            return False
        try:
            from openprogram.operations.worker.lock import is_held_by
            return is_held_by(owner_pid)
        except Exception:
            return False


    def _mark_worker_lost(self, session_id: str, job_id: str) -> None:
        """Project worker loss only after canonical ownership is classified."""
        execution = self._execution_store.get_execution(job_id)
        if (
            execution is not None
            and execution.status.value in {"paused", "queued"}
            and execution.current_attempt_id is None
            and any(
                command.status.value == "accepted"
                and command.kind.value in {"execution.continue", "execution.step"}
                for command in self._execution_store.list_commands(job_id)
            )
        ):
            # A resource lease can expire after ResourceGovernor claimed it
            # but before RuntimeControlService created an attempt.  The
            # accepted canonical command still owns recovery; do not write a
            # contradictory errored Job projection.
            return
        if execution is not None and (execution.current_attempt_id is not None or execution.status.value == "paused"):
            return
        job = shared._store_load(session_id, job_id)
        if job is None or shared.is_terminal(job.status):
            return
        try:
            shared._store_update_status(
                session_id, job_id, shared.JobStatus.ERRORED,
                error="worker died before completion",
                reason_code="error.worker_lost",
            )
        except ValueError:
            return


    def _recover_unactivated_canonical_resume(self, job_id: str) -> bool:
        """Requeue a lost resource claim that never activated an attempt."""
        execution = self._execution_store.get_execution(job_id)
        job = self._canonical_job(job_id)
        if (
            execution is None
            or job is None
            or not job.admission_id
            or execution.current_attempt_id is not None
            or execution.status.value not in {"paused", "queued"}
        ):
            return False
        commands = [
            command
            for command in self._execution_store.list_commands(job_id)
            if command.kind.value in {"execution.continue", "execution.step"}
            and command.status.value in {"accepted", "applying"}
        ]
        if len(commands) != 1:
            return False
        command = commands[0]
        if command.status.value == "accepted":
            return self._governor.queue_resume(
                job_id,
                admission_id=job.admission_id,
                command_id=command.command_id,
                paused=execution.status.value == "paused",
            )
        # APPLYING without an attempt cannot be resumed safely: the command
        # crossed the canonical activation transaction but has no owner to
        # complete it.  Terminalize the canonical execution so both views
        # converge instead of leaving a permanently applying command.
        from openprogram.runtime.execution import ExecutionStatus

        try:
            failed = self._execution_store.transition_execution(
                job_id,
                expected_version=execution.status_version,
                target=ExecutionStatus.FAILED,
                reason_code="error.activation_owner_lost",
            )
        except Exception:
            return False
        self._project_canonical_terminal(failed)
        return True


    def _repair_paused_canonical_resources(self) -> None:
        """Replay a paused Job release if a post-commit observer was interrupted."""
        from openprogram.runtime.execution.model import CommandKind, CommandStatus

        for execution in self._execution_store.list_nonterminal():
            if execution.status.value != "paused":
                continue
            if self._canonical_job(execution.execution_id) is None:
                continue
            pending_resume = self._execution_store.list_commands(
                execution.execution_id,
                statuses=(CommandStatus.ACCEPTED, CommandStatus.APPLYING),
                kinds=(CommandKind.CONTINUE, CommandKind.STEP),
            )
            if pending_resume:
                continue
            try:
                self.release_paused_job_resource(
                    execution.execution_id, reason_code="pause.recovery",
                )
            except Exception:
                shared._log.exception(
                    "failed to repair paused Job resource release for %s",
                    execution.execution_id,
                )


    def _reconcile_resources(self) -> None:
        self._reconcile_execution_waits()
        # Canonical terminal state may have committed immediately before a
        # projection write failed.  Retry it before admission reconciliation;
        # failures are persisted by _project_canonical_terminal for the next
        # pass or a fresh runner.
        try:
            self._resource_saga.reconcile()
        except Exception:
            shared._log.exception("failed to reconcile execution resource intents")
        self._repair_paused_canonical_resources()
        self._reconcile_terminal_dispatch_barriers()
        projected_pending = self._project_existing_canonical_terminals()
        for job_id, session_id in projected_pending:
            job = shared._store_load(session_id, job_id)
            if job is not None and shared.is_terminal(job.status):
                self._broadcast_job_status(job)
                self._update_attach_card(job, error_text=job.error)
        try:
            result = self._governor.reconcile(
                job_lookup=lambda session_id, job_id: shared._store_load(
                    session_id, job_id,
                ),
                write_terminal=shared._store_write_terminal,
                mark_worker_lost=self._mark_worker_lost,
                owner_is_alive=self._owner_holds_worker_lock,
            )
        except Exception:
            shared._log.exception("failed to reconcile durable job resources")
            return
        for job_id, session_id in result.completed_pending:
            job = shared._store_load(session_id, job_id)
            if job is not None and shared.is_terminal(job.status):
                self._broadcast_job_status(job)
                self._update_attach_card(job, error_text=job.error)
        for job_id, session_id in result.worker_lost:
            if self._recover_unactivated_canonical_resume(job_id):
                self._dispatch_wake.set()
                continue
            # Resource reconciliation has fenced and released the legacy
            # admission, but canonical ownership must be fenced separately.
            # Recover only the exact current attempt/generation; a late
            # report from an older worker must not terminate a newer attempt
            # or allow that worker to finish the execution.
            execution = self._execution_store.get_execution(job_id)
            if (
                execution is not None
                and execution.status.value in {
                    "queued", "running", "pausing", "paused", "cancelling",
                }
                and execution.current_attempt_id is not None
            ):
                generation = execution.owner_lease.get("generation")
                if isinstance(generation, int):
                    try:
                        recovery = self._execution_control.recover_owner_loss(
                            job_id,
                            attempt_id=execution.current_attempt_id,
                            generation=generation,
                        )
                        execution = recovery.execution
                    except Exception:
                        shared._log.exception(
                            "failed to fence lost canonical owner for %s", job_id,
                        )
                if execution.status.value in {
                    "completed", "cancelled", "failed", "interrupted",
                }:
                    self._project_canonical_terminal(execution)
            job = shared._store_load(session_id, job_id)
            if job is not None and shared.is_terminal(job.status):
                self._broadcast_job_status(job)
                self._update_attach_card(job, error_text=job.error)
        try:
            self._governor.recover_provider_reservations()
        except Exception:
            shared._log.exception("failed to reconcile provider reservations")
        try:
            orphaned_borrowed = self._governor.release_orphaned_borrowed_jobs()
        except Exception:
            shared._log.exception("failed to reconcile borrowed job resources")
            orphaned_borrowed = []
        for job_id, session_id in orphaned_borrowed:
            job = shared._store_load(session_id, job_id)
            if job is None or shared.is_terminal(job.status):
                continue
            try:
                shared._store_update_status(
                    session_id, job_id, shared.JobStatus.ERRORED,
                    error="borrowed parent claim was lost",
                    reason_code="error.borrowed_parent_lost",
                )
            except ValueError:
                pass
        self._recover_deferred_resumes()
        self._recover_deferred_inboxes()
        self._recover_pending_notices()
        try:
            from openprogram.operations.self_update.delivery.delivery import deliver_pending
            deliver_pending()
        except Exception as exc:
            shared._log.warning("self-update result delivery unavailable: %s", type(exc).__name__)
        if (
            result.finalized_preparing
            or result.released_missing
            or result.released_worker_lost
        ):
            self._dispatch_wake.set()


    def _reconcile_execution_waits(self) -> None:
        """Expire and recover durable waits during the worker lifetime.

        Startup recovery handles records left by a previous process.  A
        running worker must also settle waits whose deadlines pass while it
        is serving requests, then submit the canonical continuation through
        the existing Job resource queue.
        """
        try:
            self._execution_waits.reclaim_expired_claims()
            self._execution_waits.cancel_live_waits()
            self._execution_waits.reclaim_orphaned_claims()
            expired = self._execution_waits.expire_due()
            recovered = self._recover_wait_outcomes()
            if expired or recovered:
                self._dispatch_wake.set()
        except Exception:
            shared._log.exception("failed to reconcile durable execution waits")


    def _recover_wait_outcomes(self) -> tuple[shared.Any, ...] | None:
        """Run wait recovery without nesting an event loop.

        The reconciler normally runs in its own thread, where a synchronous
        ``asyncio.run`` is correct.  A REST handler can also initialize the
        singleton runner while its event loop is active; in that case queue
        the coroutine on the existing loop and let its completion wake the
        dispatcher.  Creating the coroutine only in the selected execution
        path avoids an un-awaited coroutine when ``asyncio.run`` is invalid.
        """
        try:
            loop = shared.asyncio.get_running_loop()
        except RuntimeError:
            return shared.asyncio.run(self._execution_control.recover_wait_outcomes())

        task = loop.create_task(self._execution_control.recover_wait_outcomes())
        tasks = getattr(self, "_wait_recovery_tasks", None)
        if tasks is None:
            tasks = set()
            self._wait_recovery_tasks = tasks
        tasks.add(task)

        def _completed(done: shared.asyncio.Task) -> None:
            tasks.discard(done)
            try:
                recovered = done.result()
            except shared.asyncio.CancelledError:
                return
            except Exception:
                shared._log.exception("failed to recover durable execution waits")
                return
            if recovered:
                self._dispatch_wake.set()

        task.add_done_callback(_completed)
        return None


    def _recover_deferred_resumes(self) -> None:
        """Publish a staged resume if the Job target save was durable."""
        try:
            pending = self._governor.pending_deferred_resumes()
        except Exception:
            shared._log.exception("failed to list staged deferred resumes")
            return
        for job_id, session_id, admission_id, parent_msg_id in pending:
            job = shared._store_load(session_id, job_id)
            if job is None:
                continue
            try:
                if job.parent_msg_id == parent_msg_id:
                    self._governor.mark_dispatch_ready(
                        job_id,
                        admission_id=admission_id,
                        parent_msg_id=parent_msg_id,
                    )
                else:
                    self._governor.reset_deferred_resume(
                        job_id,
                        admission_id=admission_id,
                        parent_msg_id=parent_msg_id,
                    )
            except Exception:
                shared._log.exception(
                    "failed to recover deferred resume for job %s", job_id,
                )


    def _recover_deferred_inboxes(self) -> None:
        """Recreate inbox entries lost after a durable deferred admission."""
        try:
            deferred = self._governor.deferred_dispatches()
        except Exception:
            shared._log.exception("failed to list deferred job admissions")
            return
        from openprogram.runtime.conversation import inbox
        for job_id, session_id in deferred:
            job = shared._store_load(session_id, job_id)
            intent = job.deferred_inbox if job is not None else None
            if not isinstance(intent, dict) and job is not None:
                try:
                    if self._complete_accepted_handoff(job):
                        continue
                except Exception:
                    shared._log.exception(
                        "failed to finish the accepted hand-off of job %s", job_id,
                    )
                    continue
            if not isinstance(intent, dict):
                self._governor.request_stop(
                    job_id, "error.deferred_inbox_intent_missing",
                )
                if job is not None and not shared.is_terminal(job.status):
                    try:
                        updated = shared._store_update_status(
                            session_id, job_id, shared.JobStatus.ERRORED,
                            error="deferred inbox intent missing",
                            reason_code="error.deferred_inbox_intent_missing",
                        )
                    except Exception:
                        updated = None
                    if updated is not None:
                        self._broadcast_job_status(updated)
                        self._update_attach_card(updated)
                continue
            try:
                # Idempotent by Job id: an entry that survived is kept as is.
                inbox.enqueue(session_id, **intent, dedup=False)
            except Exception:
                shared._log.exception(
                    "failed to recover deferred inbox for job %s", job_id,
                )


    def _complete_accepted_handoff(self, job: shared.Job) -> bool:
        """Finish a spawn held for its attach card whose submitter is gone.

        The spawn was admitted held, to become dispatchable once its card is
        written. When the submitting process died in between, write the card
        if it is missing and publish the Job, so the request still runs and
        its result reaches the parent. False when the Job is not such a spawn.
        """
        from openprogram.runtime.jobs import handoff

        if not job.attach_pointer_id or not job.admission_id:
            return False
        if handoff.accept_in_progress(job):
            return True
        if not handoff.ensure_attach_placeholder(job):
            return True  # the next pass tries again
        if self._governor.publish_accepted_job(job.id, admission_id=job.admission_id):
            self._broadcast_job_status(job)
            self._dispatch_wake.set()
        return True


    def _recover_pending_notices(self) -> None:
        """Queue the parent's notice for a Job whose terminal writer died first."""
        from openprogram.runtime.jobs import handoff

        try:
            jobs = self.list_jobs()
        except Exception:
            shared._log.exception("failed to list jobs with unsent notices")
            return
        for job in jobs:
            if (
                job.notice_pending
                and shared.is_terminal(job.status)
                and not handoff.process_alive(job.notice_pending)
            ):
                try:
                    self._update_attach_card(job, error_text=job.error)
                except Exception:
                    shared._log.exception("failed to replay the notice of job %s", job.id)


    def _reconcile_loop(self) -> None:
        while not self._shutdown_event.wait(shared._RECONCILE_SECS):
            self._reconcile_resources()
            try:
                self._execution_store.tables.forget_finished_sessions(shared._FINISHED_SESSION_MEMORY_SECS)
            except Exception:
                shared._log.exception("finished sessions were not released from memory")
            try:
                from openprogram.operations.self_update.delivery.restart import reconcile
                reconcile(self)
                from openprogram.operations.self_update.control.continuation import reconcile as continue_updates
                continue_updates(self)
                from openprogram.runtime.execution.restart import reconcile as resume_interrupted
                resume_interrupted(self)
                from openprogram.operations.self_update.control.recovery import stop_parked_stages
                stop_parked_stages(self)
            except Exception:
                shared._log.exception("self-update restart reconciliation failed")

