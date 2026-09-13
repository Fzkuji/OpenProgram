"""JobRunner admission operations."""
from __future__ import annotations
from . import shared


class AdmissionOperations:
    @staticmethod
    def _canonical_input(job: shared.Job, *, run_id: str | None,
                         parent_execution_id: str | None = None,
                         permission_snapshot: shared.Mapping[str, shared.Any] | None = None) -> tuple[str, str, dict[str, shared.Any]]:
        from openprogram.agent.job.input import JobAgentInputV1

        immutable = JobAgentInputV1.from_job(job, run_id=run_id,
                                            parent_execution_id=parent_execution_id,
                                            permission_snapshot=permission_snapshot).to_dict()
        payload = shared.json.dumps(
            immutable,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if len(payload.encode("utf-8")) > shared._MAX_CANONICAL_JOB_INPUT_BYTES:
            raise ValueError("canonical Job input exceeds size limit")
        return (
            f"job-input-v1:{payload}",
            shared.hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            immutable,
        )


    def _admit_canonical_job(self, job: shared.Job) -> None:
        """Admit one public Job under its own canonical execution identity."""
        from openprogram.agent.production_driver import AgentProductionDriver
        from openprogram.agent.authority import normalize_authority

        existing = self._execution_store.get_execution(job.id)
        parent = (
            self._execution_store.get_execution(job.parent_job_id)
            if job.parent_job_id else None
        )
        caller_session = job.caller_session_id or job.parent_session_id
        if parent is not None and parent.session_id != caller_session:
            parent = None
        if parent is None and not job.parent_job_id and existing is None:
            from openprogram.agent.run_control import get_current_execution_id, get_current_session_id

            caller_execution_id = get_current_execution_id()
            if caller_execution_id and get_current_session_id() == caller_session:
                candidate = self._execution_store.get_execution(caller_execution_id)
                if (candidate is not None and candidate.session_id == caller_session
                        and candidate.status.value == "running" and candidate.current_attempt_id is not None):
                    parent = candidate
        if existing is not None:
            record = self._execution_store.get_job_agent_input(job.id)
            if record is None:
                raise RuntimeError(f"canonical execution identity conflict: {job.id}")
            return
        from openprogram.agent.permissions.lifecycle import spawn_permission_snapshot
        permission_snapshot = spawn_permission_snapshot(self._execution_store, parent, job)
        if job.source == "self_update_continue":
            from openprogram.self_update.control.continuation import permission_snapshot as update_permission
            permission_snapshot = update_permission(job)
        if job.source == "self_update_replan":
            from openprogram.self_update.delivery.restart import replan_permission_snapshot
            permission_snapshot = replan_permission_snapshot(self._execution_store, job)
        run_id = parent.run_id if parent is not None else f"job-run-{job.id}"
        input_ref, input_hash, input_payload = self._canonical_input(
            job, run_id=run_id, parent_execution_id=parent.execution_id if parent else None,
            permission_snapshot=permission_snapshot,
        )
        revision = self._execution_store.create_revision(
            revision_id=f"job-revision-{input_hash[:24]}",
            manifest={
                "kind": "job",
                "job_id": job.id,
                "entrypoint": "openprogram.agent.production_driver:AgentProductionDriver",
            },
        )
        actor = normalize_authority(job) or {
            "speaker_kind": "job",
            "speaker_id": job.id,
            "source": "job_runner",
        }
        self._execution_store.admit_execution(
            execution_id=job.id,
            parent_execution_id=parent.execution_id if parent is not None else None,
            session_id=job.parent_session_id,
            revision_id=revision.revision_id,
            input_ref=input_ref,
            input_hash=input_hash,
            entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
            trusted_actor=actor,
            config_snapshot_ref=f"job-config:{job.id}",
            user_message_id=f"{job.id}_user",
            assistant_message_id=f"{job.id}_user_reply",
            run_id=run_id,
            capabilities=AgentProductionDriver.capabilities_for_payload(input_payload),
            job_agent_payload=input_payload,
            track_process_owner=True,
        )


    def _canonical_job(self, job_id: str) -> shared.Job | None:
        record = self._execution_store.get_execution_input(job_id)
        payload = self._execution_store.get_job_agent_input(job_id)
        if record is None or payload is None:
            return None
        try:
            from openprogram.agent.job.input import JobAgentInputV1
            job = JobAgentInputV1.parse(payload).to_job(
                execution_id=job_id, session_id=record.session_id,
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not all(
            isinstance(value, str) and value
            for value in (job.id, job.parent_session_id, job.prompt, job.agent_id)
        ):
            return None
        if job.id != job_id or job.parent_session_id != record.session_id:
            return None
        continuation_reader = getattr(
            self._governor, "continuation_parent_msg_id", None,
        )
        continuation = (
            continuation_reader(job_id) if continuation_reader is not None else None
        )
        if continuation is not None:
            job = shared.replace(job, parent_msg_id=continuation)
        scope_reader = getattr(self._governor, "budget_scope_id", None)
        if scope_reader is not None:
            scope_id = scope_reader(job_id)
            if scope_id:
                job = shared.replace(job, budget_scope_id=scope_id)
        limits_reader = getattr(self._governor, "canonical_limits", None)
        if limits_reader is not None:
            limits = limits_reader(job_id)
            if limits:
                job = shared.replace(job, effective_limits=limits)
        return job


    def _recover_orphan_canonical_jobs(self) -> None:
        """Close canonical Job rows left without a resource admission."""
        from openprogram.execution import ExecutionStatus

        for execution in self._execution_store.list_nonterminal():
            record = self._execution_store.get_execution_input(execution.execution_id)
            if record is None or not record.input_ref.startswith("job-input-v1:"):
                continue
            admission_exists = getattr(self._governor, "admission_exists", None)
            if admission_exists is None or admission_exists(execution.execution_id):
                continue
            if execution.status is not ExecutionStatus.QUEUED:
                continue
            try:
                self._execution_store.transition_execution(
                    execution.execution_id,
                    expected_version=execution.status_version,
                    target=ExecutionStatus.FAILED,
                    reason_code="error.canonical_admission_orphan",
                )
            except Exception:
                shared._log.exception(
                    "failed to close orphan canonical Job %s",
                    execution.execution_id,
                )


    def _migrate_orphan_job_projections(self) -> None:
        """Terminalize persisted Job projections without canonical identity."""
        for job in self.list_jobs():
            if shared.is_terminal(job.status) or not job.admission_id:
                continue
            if self._execution_store.get_execution(job.id) is not None:
                continue
            try:
                updated = shared._store_update_status(
                    job.parent_session_id,
                    job.id,
                    shared.JobStatus.ERRORED,
                    error="canonical execution is unavailable",
                    reason_code="error.canonical_unavailable",
                )
                if updated is not None:
                    self._governor.release_job(
                        job.id, "error.canonical_unavailable",
                    )
            except Exception:
                shared._log.exception(
                    "failed to terminalize orphan Job projection %s", job.id,
                )


    def admit_job_entity(
        self,
        job: shared.Job,
        *,
        creates_agent: bool,
        caller_turn_id: str | None = None,
        dispatch_ready: bool = True,
        borrowed_claim: tuple[str, str, int] | None = None,
    ):
        """Durably admit and publish one queued Job, without executing it."""
        from openprogram.agent.resource_governance import AdmissionRejected

        decision = self._governor.admit_job(
            job,
            persist=lambda accepted: self._persist_job_projection(accepted),
            creates_agent=creates_agent,
            caller_session_id=job.caller_session_id,
            caller_turn_id=caller_turn_id,
            dispatch_ready=dispatch_ready,
            borrowed_claim=borrowed_claim,
        )
        if not decision.accepted:
            raise AdmissionRejected(decision)
        # The legacy admission row remains a read projection during this
        # cutover, but every Job also records the cross-authority hand-off.
        # Replaying it is harmless: Governor admission is fenced by the same
        # immutable admission id and request fingerprint.
        self._resource_saga.admit(
            job.id,
            job,
            creates_agent=creates_agent,
            caller_turn_id=caller_turn_id,
            dispatch_ready=dispatch_ready,
        )
        self._resource_saga.reconcile()
        if decision.idempotent:
            # Older durable admission rows may predate the execution record;
            # repair the canonical row before dispatching the projection.
            self._admit_canonical_job(job)
            shared._store_save(job.parent_session_id, job)
        return decision


    def _persist_job_projection(self, job: shared.Job) -> None:
        """Write canonical admission first, then its legacy read projection."""
        self._admit_canonical_job(job)
        try:
            shared._store_save(job.parent_session_id, job)
        except Exception:
            # The governor rolls back its preparing ledger row when this
            # callback fails.  Close the canonical row as compensation so a
            # partial admission cannot remain queued without a ledger owner.
            try:
                from openprogram.execution import ExecutionStatus

                execution = self._execution_store.get_execution(job.id)
                if execution is not None and execution.status is ExecutionStatus.QUEUED:
                    self._execution_store.transition_execution(
                        job.id,
                        expected_version=execution.status_version,
                        target=ExecutionStatus.FAILED,
                        reason_code="error.projection_admission_failed",
                    )
            except Exception:
                shared._log.exception("failed to compensate canonical Job %s", job.id)
            raise


    def spawn_job(
        self,
        session_id: str,
        prompt: str,
        agent_id: str,
        *,
        subject: str = "",
        description: str = "",
        context_mode: str = "inherit",
        parent_msg_id: shared.Optional[str] = None,
        parent_job_id: shared.Optional[str] = None,
        label: shared.Optional[str] = None,
        attach_pointer_id: shared.Optional[str] = None,
        target_branch_head_id: shared.Optional[str] = None,
        worktree_id: shared.Optional[str] = None,
        wait: bool = True,
        caller_msg_id: shared.Optional[str] = None,
        caller_session_id: shared.Optional[str] = None,
        chain_messages: int = 0,
        chain_generations: int = 0,
        caller_chain_generations: int = 0,
        archive_when_done: bool = False,
        spawn_caller: shared.Optional[str] = None,
        advance_head: bool = False,
        tools_override: shared.Optional[list[str] | dict[str, shared.Any]] = None,
        model_override: shared.Optional[str] = None,
        thinking_effort: shared.Optional[str] = None,
        render_range: shared.Optional[dict[str, int]] = None,
        deferred_inbox: shared.Optional[dict[str, shared.Any]] = None,
        job_id: shared.Optional[str] = None,
        authority: shared.Optional[dict] = None,
        creates_agent: bool = True,
        on_accepted: shared.Optional[shared.Callable[[shared.Job], None]] = None,
        defer_dispatch: bool = False,
        resume_deferred: bool = False,
        borrow_current_claim: bool = False,
        source: str = "agent_spawn",
        profile_snapshot: shared.Optional[dict[str, shared.Any]] = None,
        response_format: shared.Optional[dict[str, shared.Any]] = None,
    ) -> str:
        """Create a Job entity, persist it, queue it on the pool.

        Returns ``job_id`` immediately. The job pickup happens on
        a worker thread and walks through the state machine. The
        caller can ``await_job(job_id)`` to block on completion.

        ``caller_session_id`` (cross-session messaging): the session the
        reply should be delivered back to. Defaults to ``session_id``
        (the job runs and replies in the caller's own session).

        ``job_id``: reuse a pre-created pending Job (a tracked
        dispatch that sat in the target's inbox while it was busy).
        The pre-created entity's dispatch-time facts (parent_job_id,
        created_at) survive the resubmission — the inbox drain runs on
        the TARGET's thread, whose ambient job context is not the
        dispatcher's. A terminal pre-created job (withdrawn while
        queued) is NOT resurrected: the id is returned untouched.
        """
        from openprogram.agent.session_db import default_db
        if default_db().get_session(session_id) is None:
            raise ValueError(f"session {session_id!r} not found")
        existing: shared.Optional[shared.Job] = None
        if job_id:
            existing = shared._store_load(session_id, job_id)
            if existing is not None and shared.is_terminal(existing.status):
                return job_id
        if resume_deferred and existing is None:
            raise ValueError(f"deferred job {job_id!r} not found")
        borrowed_claim = (
            self._current_borrowable_claim(session_id)
            if borrow_current_claim else None
        )
        if borrow_current_claim and borrowed_claim is None:
            raise ValueError("no same-session parent claim is available to borrow")
        if parent_job_id is None:
            if existing is not None:
                parent_job_id = existing.parent_job_id
            else:
                # Spawned from inside a running job's turn — record the
                # chain so cascading cancel can find this child.
                parent_job_id = shared._current_job_id.get()
        from openprogram.agent.authority import normalize_authority
        job_authority = normalize_authority(authority or existing or {})
        origin_turn_id = (
            existing.origin_turn_id if existing is not None else None
        ) or caller_msg_id or parent_msg_id
        relation = (
            existing.relation if existing is not None and existing.origin_turn_id
            else "worktree" if worktree_id
            else "linked" if (
                not creates_agent
                or bool(caller_session_id and caller_session_id != session_id)
            )
            else "owned"
        )
        job = shared.Job(
            id=job_id or shared.mint_job_id(),
            parent_session_id=session_id,
            prompt=prompt,
            agent_id=agent_id,
            **job_authority,
            subject=subject or (prompt[:60] or "job"),
            description=description or prompt,
            context_mode=context_mode if context_mode in ("inherit", "clean") else "inherit",
            parent_msg_id=parent_msg_id,
            parent_job_id=parent_job_id,
            label=label,
            attach_pointer_id=attach_pointer_id,
            target_branch_head_id=target_branch_head_id,
            worktree_id=worktree_id,
            creates_agent=creates_agent,
            relation=relation,
            origin_turn_id=origin_turn_id,
            wait=wait,
            caller_msg_id=caller_msg_id,
            caller_session_id=caller_session_id,
            chain_messages=chain_messages,
            chain_generations=chain_generations,
            caller_chain_generations=caller_chain_generations,
            archive_when_done=archive_when_done,
            spawn_caller=spawn_caller,
            advance_head=advance_head,
            tools_override=tools_override,
            model_override=model_override,
            thinking_effort=thinking_effort,
            render_range=render_range,
            source=source,
            profile_snapshot=profile_snapshot,
            response_format=response_format,
            deferred_inbox=deferred_inbox,
            status=shared.JobStatus.PENDING,
            created_at=existing.created_at if existing is not None else shared.time.time(),
        )
        admission = shared.nullcontext()
        if parent_job_id:
            from openprogram.agent.run_control import child_execution_admission
            admission = child_execution_admission(session_id, parent_job_id)
        with admission:
            if resume_deferred:
                admission_id = existing.admission_id
                if not admission_id or parent_msg_id is None:
                    raise RuntimeError(
                        f"deferred job {job.id!r} has no resumable admission fence"
                    )
                if not self._governor.stage_deferred_resume(
                    job.id,
                    admission_id=admission_id,
                    parent_msg_id=parent_msg_id,
                ):
                    raise RuntimeError(
                        f"deferred job {job.id!r} could not stage its target head"
                    )
                job = shared.replace(existing, parent_msg_id=parent_msg_id)
                shared._store_save(session_id, job)
                self._admit_canonical_job(job)
                if not self._governor.mark_dispatch_ready(
                    job.id,
                    admission_id=admission_id,
                    parent_msg_id=parent_msg_id,
                ):
                    raise RuntimeError(
                        f"deferred job {job.id!r} could not become dispatchable"
                    )
                idempotent = True
            else:
                hold_for_accepted = bool(
                    on_accepted is not None
                    and borrowed_claim is None
                )
                decision = self.admit_job_entity(
                    job,
                    creates_agent=creates_agent,
                    caller_turn_id=caller_msg_id,
                    dispatch_ready=(
                        not defer_dispatch
                        and borrowed_claim is None
                        and not hold_for_accepted
                    ),
                    borrowed_claim=(borrowed_claim[:3] if borrowed_claim else None),
                )
                idempotent = decision.idempotent
        if on_accepted is not None and not idempotent:
            try:
                on_accepted(job)
            except Exception:
                self._governor.request_stop(job.id, "error.accepted_side_effect")
                updated = None
                try:
                    updated = shared._store_update_status(
                        session_id, job.id, shared.JobStatus.ERRORED,
                        error="accepted job side effect failed",
                        reason_code="error.accepted_side_effect",
                    )
                except Exception:
                    pass
                if updated is not None:
                    self._broadcast_job_status(updated)
                    self._update_attach_card(
                        updated, error_text="accepted job side effect failed",
                    )
                raise
        if borrowed_claim is not None:
            self._run_borrowed_job(job, borrowed_claim)
            return job.id
        if idempotent:
            with self._lock:
                if job.id in self._jobs:
                    return job.id
            job = shared._store_load(session_id, job.id) or job
        if defer_dispatch:
            return job.id
        if not idempotent:
            self._broadcast_job_status(job)

        # Admission can make a Job visible to the dispatcher before this
        # caller returns.  In that case the dispatcher has already created
        # and may have enriched the runtime entry.  Reuse it instead of
        # replacing monitor and canonical-attempt state with a fresh shell.
        with self._lock:
            entry = self._jobs.get(job.id)
            if entry is None:
                done_ev = shared.threading.Event()
                cancel_ev = shared.threading.Event()
                # Copy the current ContextVars so things like
                # ``run_control._current_session_id`` set by the spawning
                # thread don't leak into the worker. Each job gets its own
                # context — the worker function rebinds session_id explicitly.
                entry = {
                    "event": cancel_ev,
                    "future": None,
                    "session_id": session_id,
                    "context": shared.contextvars.copy_context(),
                }
                self._jobs[job.id] = entry
                self._done_events[job.id] = done_ev
            else:
                cancel_ev = entry["event"]
                done_ev = self._done_events.setdefault(job.id, shared.threading.Event())
        if on_accepted is not None and not idempotent:
            if not self._governor.publish_accepted_job(
                job.id,
                admission_id=job.admission_id or "",
            ):
                error = f"job {job.id!r} could not become dispatchable"
                self._governor.request_stop(
                    job.id, "error.accepted_side_effect",
                )
                try:
                    updated = shared._store_update_status(
                        session_id,
                        job.id,
                        shared.JobStatus.ERRORED,
                        error=error,
                        reason_code="error.accepted_side_effect",
                    )
                except Exception:
                    updated = None
                if updated is not None:
                    self._broadcast_job_status(updated)
                    self._update_attach_card(updated, error_text=error)
                with self._lock:
                    self._jobs.pop(job.id, None)
                    self._done_events.pop(job.id, None)
                done_ev.set()
                raise RuntimeError(error)
        self._dispatch_wake.set()

        return job.id

