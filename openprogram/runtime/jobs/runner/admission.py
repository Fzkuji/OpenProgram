"""JobRunner admission operations."""
from __future__ import annotations
from contextlib import nullcontext
import dataclasses
from dataclasses import replace
from openprogram.runtime.jobs.types import mint_job_id
from typing import Mapping
import hashlib
import json
import time
from openprogram.runtime.jobs import handoff
from openprogram.runtime.jobs.runner import shared


def _name_job_branch(job: shared.Job, *, creates_agent: bool) -> None:
    """A new agent's branch carries its task's label from admission on.

    The run's reply, ``<job>_user_reply``, is the branch's tip for as long
    as the run lasts, so the label is recorded there before the run writes
    anything: the agent list and Activity name the branch while it runs,
    not only after its first reply. A Job that continues a branch (a
    message to an agent, a later hop of one) keeps that branch's name.
    """
    if (not job.label or not creates_agent or job.target_branch_head_id
            or shared._continued_job(job)):
        return
    try:
        from openprogram.runtime.context.session_db import default_db
        default_db().name_spawned_branch(
            job.parent_session_id, f"{job.id}{shared._JOB_REPLY_SUFFIX}", str(job.label))
    except Exception:
        # The branch is named after its first reply instead.
        shared._log.warning("branch label not recorded for job %s", job.id, exc_info=True)


def _scheduled_permission_snapshot(job: shared.Job) -> dict[str, shared.Any] | None:
    """The policy of a sub-agent started by a scheduled run.

    The run's turn lives in its own process, so the Worker has no row to read.
    What it verified when it started that process says whose run this is, and
    the conversation's settings are where the run took its own policy from.
    """
    from openprogram.runtime.conversation_link import conversation_permission
    from openprogram.runtime.permissions.modes import VALID_PERMISSION
    from openprogram.state.authority import normalize_authority

    scheduled = shared.scheduled_caller.get()
    if (scheduled is None or job.source != "agent_spawn"
            or (job.caller_session_id or job.parent_session_id) != scheduled["session_id"]):
        return None
    authority = normalize_authority(job)
    if (not authority or authority.get("authority_tier") != "owner"
            or scheduled["authority_tier"] != "owner" or not scheduled["principal_id"]
            or authority.get("principal_id") != scheduled["principal_id"]):
        return None
    permission = conversation_permission(scheduled["session_id"])
    if permission["permission_mode"] not in VALID_PERMISSION:
        return None
    rules = permission["permission_rules"]
    return {"mode": permission["permission_mode"],
            "rules": dataclasses.asdict(rules) if dataclasses.is_dataclass(rules) else rules}


def _started_by_detached_turn() -> bool:
    """Whether the turn starting this Job has no window. A scheduled run's turn
    lives in its own process, so the Worker admitting its sub-agents has only
    the caller it verified to go by."""
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.identity import detached_from_window

    return detached_from_window(get_turn_request()) or shared.scheduled_caller.get() is not None


class AdmissionOperations:
    @staticmethod
    def _canonical_input(job: shared.Job, *, run_id: str | None,
                         parent_execution_id: str | None = None,
                         permission_snapshot: Mapping[str, shared.Any] | None = None,
                         usage_goal: Mapping[str, shared.Any] | None = None,
                         automation: str | None = None,
                         detached: bool = False,
                         asks_through: Mapping[str, shared.Any] | None = None) -> tuple[str, str, dict[str, shared.Any]]:
        from openprogram.runtime.jobs.input import JobAgentInputV1

        immutable = JobAgentInputV1.from_job(job, run_id=run_id,
                                            parent_execution_id=parent_execution_id,
                                            permission_snapshot=permission_snapshot,
                                            usage_goal=usage_goal,
                                            automation=automation,
                                            detached=detached,
                                            asks_through=asks_through).to_dict()
        payload = json.dumps(
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
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            immutable,
        )


    def _admit_canonical_job(self, job: shared.Job) -> None:
        """Admit one public Job under its own canonical execution identity."""
        from openprogram.runtime.conversation_link import agent_capabilities
        from openprogram.state.authority import normalize_authority

        existing = self._execution_store.get_execution(job.id)
        parent = (
            self._execution_store.get_execution(job.parent_job_id)
            if job.parent_job_id else None
        )
        caller_session = job.caller_session_id or job.parent_session_id
        if parent is not None and parent.session_id != caller_session:
            parent = None
        if parent is None and not job.parent_job_id and existing is None:
            from openprogram.runtime.execution.run_control import (
                get_current_execution_id,
                get_current_session_id,
            )

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
        from openprogram.runtime.permissions.approvals.lifecycle import spawn_permission_snapshot, spawn_question_route
        permission_snapshot = spawn_permission_snapshot(self._execution_store, parent, job)
        detached = _started_by_detached_turn()
        asks_through = None if detached else spawn_question_route(
            self._execution_store, parent, job, permission_snapshot)
        if parent is None and permission_snapshot is None:
            permission_snapshot = _scheduled_permission_snapshot(job)
        from openprogram.runtime import maintenance
        permission_snapshot = maintenance.job_permission_snapshot(
            self._execution_store, job, permission_snapshot)
        canonical_parent = parent if parent is not None and parent.session_id == job.parent_session_id else None
        run_id = canonical_parent.run_id if canonical_parent is not None else f"job-run-{job.id}"
        usage_goal = None
        if parent is not None and parent.status.value == "running" and parent.current_attempt_id is not None:
            from openprogram.runtime.execution.run_control import (
                get_current_execution_id,
                get_current_session_id,
            )
            from openprogram.models.usage.context import GOAL_FIELDS, request_context
            if get_current_execution_id() == parent.execution_id and get_current_session_id() == caller_session:
                usage = request_context(caller_session)
                if usage.goal_id:
                    usage_goal = {key: getattr(usage, key) for key in GOAL_FIELDS}
        from openprogram.runtime.agent.turn_request import get_turn_request
        from openprogram.runtime.permissions.identity import inherited_automation
        input_ref, input_hash, input_payload = self._canonical_input(
            job, run_id=run_id, parent_execution_id=parent.execution_id if parent else None,
            permission_snapshot=permission_snapshot, usage_goal=usage_goal,
            automation=inherited_automation(get_turn_request()),
            detached=detached, asks_through=asks_through,
        )
        revision = self._execution_store.create_revision(
            revision_id=f"job-revision-{input_hash[:24]}",
            manifest={
                "kind": "job",
                "job_id": job.id,
                "entrypoint": "openprogram.runtime.conversation.production_driver:AgentProductionDriver",
            },
        )
        actor = normalize_authority(job) or {
            "speaker_kind": "job",
            "speaker_id": job.id,
            "source": "job_runner",
        }
        self._execution_store.admit_execution(
            execution_id=job.id,
            # Canonical parentage is same-session. Cross-session delegation
            # keeps exact caller provenance in immutable Job input instead.
            parent_execution_id=canonical_parent.execution_id if canonical_parent is not None else None,
            session_id=job.parent_session_id,
            revision_id=revision.revision_id,
            input_ref=input_ref,
            input_hash=input_hash,
            entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
            trusted_actor=actor,
            config_snapshot_ref=f"job-config:{job.id}",
            user_message_id=f"{job.id}_user",
            assistant_message_id=f"{job.id}_user_reply",
            run_id=run_id,
            capabilities=agent_capabilities(input_payload),
            job_agent_payload=input_payload,
            track_process_owner=True,
        )


    def _canonical_job(self, job_id: str) -> shared.Job | None:
        record = self._execution_store.get_execution_input(job_id)
        payload = self._execution_store.get_job_agent_input(job_id)
        if record is None or payload is None:
            return None
        try:
            from openprogram.runtime.jobs.input import JobAgentInputV1
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
            job = replace(job, parent_msg_id=continuation)
        scope_reader = getattr(self._governor, "budget_scope_id", None)
        if scope_reader is not None:
            scope_id = scope_reader(job_id)
            if scope_id:
                job = replace(job, budget_scope_id=scope_id)
        limits_reader = getattr(self._governor, "canonical_limits", None)
        if limits_reader is not None:
            limits = limits_reader(job_id)
            if limits:
                job = replace(job, effective_limits=limits)
        return job


    def _recover_orphan_canonical_jobs(self) -> None:
        """Close canonical Job rows left without a resource admission."""
        from openprogram.runtime.execution import ExecutionStatus

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
        from openprogram.runtime.jobs.resource_governance import AdmissionRejected

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
        _name_job_branch(job, creates_agent=creates_agent)
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
                from openprogram.runtime.execution import ExecutionStatus

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
        message_origin: shared.Optional[str] = None,
        tool_call_id: shared.Optional[str] = None,
        chain_ancestors: shared.Optional[list[str]] = None,
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
        from openprogram.runtime.context.session_db import default_db
        if default_db().get_session(session_id) is None:
            raise ValueError(f"session {session_id!r} not found")
        existing: shared.Optional[shared.Job] = None
        if job_id:
            existing = shared._store_load(session_id, job_id)
            if existing is not None and shared.is_terminal(existing.status):
                return job_id
        if resume_deferred and existing is None:
            raise ValueError(f"deferred job {job_id!r} not found")
        published = getattr(self._governor, "dispatch_published", None)
        if resume_deferred and published is not None and published(existing.id):
            # A drain that crashed after publishing left its inbox entry
            # behind; delivering it again is the same hand-off, already done.
            return existing.id
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
        from openprogram.state.authority import normalize_authority
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
            id=job_id or mint_job_id(),
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
            message_origin=message_origin,
            deferred_inbox=deferred_inbox,
            tool_call_id=tool_call_id or (existing.tool_call_id if existing is not None else None),
            chain_ancestors=(list(chain_ancestors) if chain_ancestors
                             else existing.chain_ancestors if existing is not None else None),
            status=shared.JobStatus.PENDING,
            created_at=existing.created_at if existing is not None else time.time(),
            submitter=(existing.submitter if existing is not None else handoff.this_process()),
        )
        admission = nullcontext()
        if parent_job_id:
            from openprogram.runtime.execution.run_control import (
                child_execution_admission,
            )
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
                job = replace(existing, parent_msg_id=parent_msg_id)
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
                if hold_for_accepted:
                    # Until publish, recovery leaves this held Job to us.
                    handoff.begin_accept(job.id)
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
                    # The stop was already requested; only the persisted status is missing.
                    shared._log.warning("job %s not marked errored after side effect failure", job.id, exc_info=True)
                if updated is not None:
                    self._broadcast_job_status(updated)
                    self._update_attach_card(
                        updated, error_text="accepted job side effect failed",
                    )
                handoff.end_accept(job.id)
                raise
        if borrowed_claim is not None:
            self._run_borrowed_job(job, borrowed_claim)
            return job.id
        if idempotent:
            with self._lock:
                if job.id in self._jobs:
                    handoff.end_accept(job.id)
                    return job.id
            job = shared._store_load(session_id, job.id) or job
        if defer_dispatch:
            handoff.end_accept(job.id)
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
                self._budget_wake.set()
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
                handoff.end_accept(job.id)
                raise RuntimeError(error)
        handoff.end_accept(job.id)
        self._dispatch_wake.set()

        return job.id
