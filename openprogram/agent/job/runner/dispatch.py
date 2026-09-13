"""JobRunner dispatch operations."""
from __future__ import annotations
from . import shared


class DispatchOperations:
    def _activate_canonical_execution(self, execution_id: str, cancel_ev):
        """Lease and activate the canonical attempt for one Job execution."""
        from openprogram.execution import ActivationInput

        execution = self._execution_store.get_execution(execution_id)
        if execution is None or execution.status.value != "queued":
            return None
        binding = None
        abort_unbound = False
        try:
            leased, reserved = self._execution_attempts.lease(
                execution_id,
                expected_version=execution.status_version,
                owner_id=self._instance_id,
                ttl_seconds=30.0,
            )
            active, running = self._execution_attempts.activate(
                leased.attempt_id,
                generation=leased.generation,
                expected_execution_version=reserved.status_version,
            )
            driver = self._agent_driver()
            binding = self._run_control(
                driver.activate(active, ActivationInput(checkpoint=None)),
            )
            abort_unbound = True
            projected = shared._store_update_status(
                execution.session_id,
                execution_id,
                shared.JobStatus.RUNNING,
                started_at=active.activated_at,
            )
            if projected is None:
                raise RuntimeError(
                    f"job projection {execution_id!r} is unavailable after activation"
            )
            shared._broadcast_job_status(projected)
            # _bind_driver aborts its own failed bind. Only failures before
            # this call need the explicit pre-bind cleanup below.
            abort_unbound = False
            self._execution_control._bind_driver(binding)
            return active, running, binding
        except Exception:
            if abort_unbound and binding is not None:
                aborted = getattr(binding.driver, "activation_aborted", None)
                if callable(aborted):
                    try:
                        aborted(binding)
                    except Exception:
                        shared._log.exception(
                            "failed to abort unbound canonical job driver %s",
                            execution_id,
                        )
            # Canonical recovery is the only cleanup for an activated attempt;
            # it fences any partial owner before the resource claim is released.
            current = self._execution_store.get_execution(execution_id)
            if current is not None and current.current_attempt_id is not None:
                try:
                    self._execution_control.recover_owner_loss(
                        execution_id,
                        attempt_id=current.current_attempt_id,
                        generation=current.owner_lease.get("generation"),
                    )
                except Exception:
                    shared._log.exception(
                        "failed to recover canonical job activation %s", execution_id,
                    )
            return None


    def _terminate_canonical_worker(self, handle, reason: str):
        """Return the process runner's result as an exact termination receipt."""
        from openprogram.execution import TerminationReceipt

        execution = self._execution_store.get_execution(handle.execution_id)
        if execution is None:
            return TerminationReceipt(
                attempt_id=handle.attempt_id,
                terminated=False,
                reason=reason,
                details={"code": "execution_not_found"},
            )
        try:
            from openprogram.agent.process_runner import kill_active_subprocess

            terminated = bool(
                kill_active_subprocess(
                    execution.session_id,
                    execution_id=handle.execution_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return TerminationReceipt(
                attempt_id=handle.attempt_id,
                terminated=False,
                reason=reason,
                details={"code": "termination_error", "error": str(exc)},
            )
        return TerminationReceipt(
            attempt_id=handle.attempt_id,
            terminated=terminated,
            reason=reason,
            details={"source": "process_runner"},
        )


    def _activate_canonical_claim(self, claim, cancel_ev):
        """Lease and activate the canonical attempt for one resource claim."""
        return self._activate_canonical_execution(claim.job_id, cancel_ev)


    def _agent_driver(self):
        """Build the one Agent owner used by canonical Job activations."""
        from openprogram.agent.production_driver import AgentProductionDriver

        if self._agent_driver_factory is not None:
            return self._agent_driver_factory(
                self._execution_store, self._execution_control,
            )
        return AgentProductionDriver(
            self._execution_store,
            control_service=self._execution_control,
            event_sink=shared._broadcast,
            job_resume_resolver=self._governor.continuation_parent_msg_id,
        )


    def _activate_claimed_job_command(self, claim, command_id: str, cancel_ev):
        """Consume one resource-claimed Job command through RuntimeControlService."""
        command = self._execution_store.get_command(command_id)
        if command is None:
            return None
        try:
            driver = self._agent_driver()
            bindings = []

            async def activate(attempt, activation):
                binding = await driver.activate(attempt, activation)
                bindings.append(binding)
                return binding

            dispatch = self._run_control(
                self._execution_control.activate_accepted_job_command(
                    command_id=command_id,
                    execution_id=claim.job_id,
                    owner_id=self._instance_id,
                    activator=activate,
                ),
            )
            if not dispatch.delivered or len(bindings) != 1:
                return None
            binding = bindings[0]
            attempt = self._execution_attempts.get(binding.attempt_id)
            if attempt is None:
                return None
            return attempt, dispatch.execution, binding
        except Exception:
            shared._log.exception(
                "failed to activate claimed Job command %s for %s",
                command_id, claim.job_id,
            )
            return None


    def _claimed_resume_command_id(self, job_id: str) -> str | None:
        row = self._governor.ledger.connection().execute(
            "SELECT resume_command_id FROM job_admissions "
            "WHERE job_id = ? AND state = 'live'",
            (job_id,),
        ).fetchone()
        value = row["resume_command_id"] if row is not None else None
        return value if isinstance(value, str) and value else None


    def _dispatch_loop(self) -> None:
        """Submit only durably claimed jobs to the executor."""
        while not self._shutdown_event.is_set():
            self._dispatch_wake.wait(0.5)
            self._dispatch_wake.clear()
            # A continue/step command writes its intent before capacity is
            # claimed.  Reconcile that hand-off before selecting the next
            # admission so an available slot starts promptly without a
            # separate polling delay.
            try:
                self._resource_saga.reconcile()
            except Exception:
                shared._log.exception("failed to reconcile pending Job resource claims")
            blocked_sessions: set[str] = set()
            while not self._shutdown_event.is_set():
                if not self._executor_slots.acquire(blocking=False):
                    break
                try:
                    from openprogram.self_update.control.maintenance import claim_job
                    claim = claim_job(self._governor,
                        owner_instance_id=self._instance_id,
                        excluded_sessions=blocked_sessions,
                        only_job_id=self._claim_only_job_id,
                    )
                except Exception:
                    self._executor_slots.release()
                    shared._log.exception("failed to claim next durable job")
                    break
                if claim is None:
                    self._executor_slots.release()
                    break
                # The durable canonical record is the dispatch gate.  A
                # resumed Job remains paused until its resource claim reaches
                # this dispatcher, where RuntimeControlService creates its
                # next attempt.  JobStore is never used to upgrade lifecycle.
                canonical = self._execution_store.get_execution(claim.job_id)
                canonical_job = self._canonical_job(claim.job_id)
                resume_command_id = self._claimed_resume_command_id(claim.job_id)
                resume_command = (
                    self._execution_store.get_command(resume_command_id)
                    if resume_command_id is not None else None
                )
                claimed_resume = (
                    canonical is not None
                    and canonical.status.value in {"paused", "queued"}
                    and resume_command is not None
                    and resume_command.status.value == "accepted"
                    and resume_command.kind.value in {
                        "execution.continue", "execution.step",
                    }
                    and (
                        canonical.status.value == "paused"
                        or (
                            canonical.status.value == "queued"
                            and resume_command.kind.value == "execution.step"
                        )
                    )
                )
                if (
                    canonical is None
                    or (canonical.status.value != "queued" and not claimed_resume)
                    or canonical_job is None
                ):
                    if canonical is not None and canonical.status.value in {
                        "completed", "cancelled", "failed", "interrupted",
                    }:
                        try:
                            self._project_canonical_terminal(
                                canonical,
                                admission_owner_instance_id=self._instance_id,
                                admission_lease_generation=claim.lease_generation,
                            )
                        except Exception:
                            shared._log.exception(
                                "failed to project terminal claim for %s",
                                claim.job_id,
                            )
                            # Keep the durable admission fenced while the
                            # projection is repaired, then return the slot.
                            try:
                                self._governor.block_dispatch(claim.job_id)
                                self._governor.requeue_job(
                                    claim.job_id,
                                    owner_instance_id=self._instance_id,
                                    lease_generation=claim.lease_generation,
                                )
                            except Exception:
                                shared._log.exception(
                                    "failed to park terminal claim for %s",
                                    claim.job_id,
                                )
                        self._executor_slots.release()
                        self._dispatch_wake.set()
                        continue
                    reason_code = (
                        canonical.reason_code
                        if canonical is not None and canonical.reason_code
                        else "error.canonical_admission"
                    )
                    projection = shared._store_load(claim.session_id, claim.job_id)
                    if projection is not None and not shared.is_terminal(projection.status):
                        try:
                            shared._store_update_status(
                                claim.session_id,
                                claim.job_id,
                                shared.JobStatus.ERRORED,
                                error="canonical execution admission is missing",
                                reason_code=reason_code,
                            )
                        except Exception:
                            shared._log.exception(
                                "failed to terminalize invalid claim projection for %s",
                                claim.job_id,
                            )
                    try:
                        released = self._governor.release_job(
                            claim.job_id,
                            reason_code,
                            owner_instance_id=self._instance_id,
                            lease_generation=claim.lease_generation,
                        )
                    except Exception:
                        released = False
                        shared._log.exception(
                            "failed to release terminal claim for job %s",
                            claim.job_id,
                        )
                    if released:
                        projection = shared._store_load(claim.session_id, claim.job_id)
                        if projection is not None:
                            self._broadcast_job_status(projection)
                            self._update_attach_card(projection)
                        self._wake_done(claim.job_id)
                        with self._lock:
                            self._jobs.pop(claim.job_id, None)
                            self._done_events.pop(claim.job_id, None)
                        self._executor_slots.release()
                        self._dispatch_wake.set()
                        continue
                time_limits = self._governor.job_time_limits(claim.job_id)
                claimed_monotonic = self._monotonic()
                # Establish the process-local handle before canonical
                # activation.  The driver must retain this exact Event; a
                # later lookup must not silently replace it.
                with self._lock:
                    entry = self._jobs.get(claim.job_id)
                    if entry is None:
                        cancel_ev = shared.threading.Event()
                        done_ev = self._done_events.setdefault(
                            claim.job_id, shared.threading.Event(),
                        )
                        entry = {
                            "event": cancel_ev,
                            "future": None,
                            "session_id": claim.session_id,
                            "context": shared.contextvars.copy_context(),
                        }
                        self._jobs[claim.job_id] = entry
                    else:
                        cancel_ev = entry["event"]
                        done_ev = self._done_events[claim.job_id]
                    ctx = entry["context"]
                canonical_claim = (
                    self._activate_claimed_job_command(
                        claim, resume_command_id, cancel_ev,
                    )
                    if claimed_resume and resume_command_id is not None
                    else self._activate_canonical_claim(claim, cancel_ev)
                )
                if canonical_claim is None:
                    from openprogram.execution import ExecutionStatus

                    current_execution = self._execution_store.get_execution(claim.job_id)
                    if claimed_resume:
                        current_command = (
                            self._execution_store.get_command(resume_command_id)
                            if resume_command_id is not None else None
                        )
                        # An activation fault before RuntimeControlService
                        # claims the command leaves the canonical execution
                        # paused and the command accepted.  Return only this
                        # fenced resource claim to the queue; it is not a
                        # failed execution and must not be terminalized.
                        if (
                            current_execution is not None
                            and current_execution.status.value == "paused"
                            and current_command is not None
                            and current_command.status.value == "accepted"
                        ):
                            try:
                                self._governor.requeue_job(
                                    claim.job_id,
                                    owner_instance_id=self._instance_id,
                                    lease_generation=claim.lease_generation,
                                )
                            except Exception:
                                shared._log.exception(
                                    "failed to requeue paused Job resume %s",
                                    claim.job_id,
                                )
                        else:
                            try:
                                self._governor.release_job(
                                    claim.job_id,
                                    "resume.command_rejected",
                                    owner_instance_id=self._instance_id,
                                    lease_generation=claim.lease_generation,
                                )
                            except Exception:
                                shared._log.exception(
                                    "failed to release rejected Job resume %s",
                                    claim.job_id,
                                )
                        with self._lock:
                            self._jobs.pop(claim.job_id, None)
                            self._done_events.pop(claim.job_id, None)
                        blocked_sessions.add(claim.session_id)
                        self._executor_slots.release()
                        continue
                    if (
                        current_execution is not None
                        and current_execution.status.value == "queued"
                    ):
                        try:
                            current_execution = self._execution_store.transition_execution(
                                claim.job_id,
                                expected_version=current_execution.status_version,
                                target=ExecutionStatus.FAILED,
                                reason_code="error.activation_failed",
                            )
                        except Exception:
                            current_execution = self._execution_store.get_execution(
                                claim.job_id,
                            )
                    activation_reason = (
                        current_execution.reason_code
                        if current_execution is not None
                        and current_execution.reason_code
                        else "error.worker_lost"
                    )
                    self._governor.release_job(
                        claim.job_id,
                        activation_reason,
                        owner_instance_id=self._instance_id,
                        lease_generation=claim.lease_generation,
                    )
                    if current_execution is not None:
                        self._project_canonical_terminal(current_execution)
                    self._executor_slots.release()
                    self._dispatch_wake.set()
                    continue
                canonical_attempt, canonical_running, canonical_binding = canonical_claim
                with self._lock:
                    entry["started_monotonic"] = claimed_monotonic
                    entry["last_activity_monotonic"] = claimed_monotonic
                    entry["time_limits"] = time_limits
                    entry["lease_generation"] = claim.lease_generation
                    entry["attempt_id"] = canonical_attempt.attempt_id
                    entry["attempt_generation"] = canonical_attempt.generation
                    entry["execution_version"] = canonical_running.status_version
                    entry["driver"] = canonical_binding.driver
                    entry["budget_cancelled"] = False
                self._consume_pending_canonical_cancel(
                    claim.job_id,
                    canonical_attempt.attempt_id,
                    canonical_attempt.generation,
                    cancel_ev,
                )
                try:
                    future: shared.Future = self._pool.submit(
                        ctx.run, self._run_one, claim.job_id, claim.session_id,
                        cancel_ev, done_ev, claim.lease_generation,
                        canonical_attempt.attempt_id, canonical_attempt.generation,
                        canonical_binding,
                    )
                except Exception:
                    from openprogram.agent.run_control import unregister_cancel_event
                    unregister_cancel_event(
                        claim.session_id, cancel_ev, execution_id=claim.job_id,
                    )
                    self._executor_slots.release()
                    updated = None
                    try:
                        updated = self._finalize_job_status(
                            claim.session_id,
                            claim.job_id,
                            claim.lease_generation,
                            shared.JobStatus.ERRORED,
                            "error.dispatch_failed",
                            attempt_id=canonical_attempt.attempt_id,
                            attempt_generation=canonical_attempt.generation,
                            error="executor submission failed",
                        )
                    except Exception:
                        shared._log.exception(
                            "failed to durably finalize undispatched job %s",
                            claim.job_id,
                        )
                    if updated is None:
                        current = shared._store_load(claim.session_id, claim.job_id)
                        if current is not None and shared.is_terminal(current.status):
                            updated = current
                    if updated is not None:
                        self._broadcast_job_status(updated)
                        self._update_attach_card(
                            updated, error_text="executor submission failed",
                        )
                        self._wake_done(claim.job_id)
                        with self._lock:
                            self._jobs.pop(claim.job_id, None)
                            self._done_events.pop(claim.job_id, None)
                    self._dispatch_wake.set()
                    shared._log.exception("failed to submit claimed job %s", claim.job_id)
                    continue
                with self._lock:
                    if self._jobs.get(claim.job_id) is entry:
                        entry["future"] = future


    def _run_one(
        self, job_id: str, claimed_session_id: str,
        cancel_ev: shared.threading.Event, done_ev: shared.threading.Event,
        lease_generation: int, attempt_id: str | None = None,
        attempt_generation: int | None = None,
        binding=None,
    ) -> None:
        """Worker thread entry point.

        Wraps :func:`run_agent_turn` so the same code that handles the
        synchronous ``/spawn`` path runs underneath us. Catches
        everything so a buggy tool doesn't leave the job pinned at
        ``running`` forever — exceptions flip to ``errored``.

        Important: the dispatcher's cancel hook reads
        ``run_control._current_session_id`` from the worker thread
        ContextVar. We bind it at entry so the hook can find the
        right session.
        """
        # The canonical AgentProductionDriver owns the only provider turn.
        # JobRunner retains resource fencing and the Job projection, but
        # never starts a second inner execution for this Job identity.
        self._wait_for_canonical_driver(
            job_id,
            claimed_session_id,
            done_ev,
            lease_generation,
            attempt_id,
            attempt_generation,
            binding,
        )


    def _wait_for_canonical_driver(
        self,
        job_id: str,
        session_id: str,
        done_ev: shared.threading.Event,
        lease_generation: int,
        attempt_id: str | None,
        attempt_generation: int | None,
        binding,
    ) -> None:
        """Wait for the canonical Agent owner and project only its outcome."""
        result = None
        lease_stop = shared.threading.Event()
        lease_thread = None
        try:
            if attempt_id is None or attempt_generation is None:
                raise RuntimeError("canonical attempt identity is unavailable")
            if binding is None:
                raise RuntimeError("canonical driver binding is unavailable")
            lease_thread = shared.threading.Thread(
                target=self._renew_job_lease,
                args=(
                    job_id,
                    lease_generation,
                    lease_stop,
                    attempt_id,
                    attempt_generation,
                ),
                daemon=True,
                name=f"op-job-lease-{job_id}",
            )
            lease_thread.start()
            result = binding.handle.done.result()
        except Exception:
            shared._log.exception("canonical Agent owner ended unexpectedly for %s", job_id)
        finally:
            lease_stop.set()
            if lease_thread is not None:
                lease_thread.join(timeout=1.0)
            execution = self._execution_store.get_execution(job_id)
            try:
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
                        admission_owner_instance_id=self._instance_id,
                        admission_lease_generation=lease_generation,
                    )
                    self._execution_control.reconcile_terminal_cancel(execution)
                    projected = shared._store_load(session_id, job_id)
                    if projected is not None and shared.is_terminal(projected.status):
                        self._broadcast_job_status(projected)
                        self._update_attach_card(
                            projected,
                            error_text=projected.error,
                        )
                elif execution is not None and execution.status.value == "paused":
                    self.release_paused_job_resource(
                        job_id, reason_code="pause.safe_point",
                        attempt_id=attempt_id, generation=attempt_generation,
                        resource_lease_generation=lease_generation,
                    )
            except Exception:
                shared._log.exception("failed to project canonical Job completion for %s", job_id)
            self._wake_done(job_id)
            with self._lock:
                self._jobs.pop(job_id, None)
                self._done_events.pop(job_id, None)
            self._executor_slots.release()
            self._dispatch_wake.set()

