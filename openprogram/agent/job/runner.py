"""JobRunner — ThreadPoolExecutor-backed worker pool.

Process-wide singleton. Jobs are submitted via :meth:`spawn_job`,
which returns immediately with a job id. The actual work runs in
the pool's worker thread by calling :func:`run_agent_turn` internally
(see ``sub_agent_run.py``).

Why a pool of OS threads instead of asyncio: every existing
``process_user_turn`` call already opens its own ``asyncio.new_event_loop``
inside the calling thread. Stacking a top-level asyncio scheduler
would double-loop. Threads also play nice with the synchronous
BashTool / file IO that dominates wall-clock time of a sub-agent.

Cancel signalling reuses the dispatcher contract:

  * ``run_control.claim_cancel_event(session_id, ev)`` atomically
    admits the session and exposes the cancel event to invocation hooks.
  * ``process_user_turn(cancel_event=ev)`` bridges the event into
    asyncio for the LLM-stream side.
  * ``kill_active_runtime(session_id)`` terminates any live BashTool
    subprocess (best-effort, depends on the runtime registration).

Cancel events on the *job* level are stored in this runner's
``_cancel_events`` map (keyed by ``job_id``), in addition to the
session-level events the dispatcher already maintains. We set both
on cancel so:

  * session-level (existing behavior) — the cancel hook +
    asyncio bridge inside ``process_user_turn`` already keys off the
    session id, so they fire.
  * job-level (new) — if a runner is later upgraded to allow >1
    job per session, job-level cancel still scopes correctly.

Crash recovery: :func:`store.reconcile_orphans` runs at process start
(lazily, on first runner construction). Existing jobs left in
non-terminal state are flipped to ``errored``.

Broadcast events: each state transition fires a WS broadcast via
``openprogram.webui.server._broadcast`` (lazy import) so the UI
updates without an explicit poll. We also fire a ``session_reload``
on terminal so the existing attach card pickup path triggers.
"""
from __future__ import annotations

import asyncio
import contextvars
from contextlib import contextmanager, nullcontext
import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from openprogram.agent.job.store import (
    list_jobs as _store_list,
    load_job as _store_load,
    reconcile_orphans as _store_reconcile,
    save_job as _store_save,
    update_job_status as _store_update_status,
)
from openprogram.events import emit_safe
from openprogram.agent.job.types import (
    Job,
    JobStatus,
    is_terminal,
    mint_job_id,
)


_log = logging.getLogger(__name__)

_DEFAULT_MAX_WORKERS = 4
# Hard ceiling on the wait we'll give a worker to honour cancel before
# forcibly flipping the entity to cancelled.
_CANCEL_TIMEOUT_SECS = 30.0
_LEASE_RENEW_SECS = 10.0
_RECONCILE_SECS = 5.0


class NonPreemptibleOperation(RuntimeError):
    reason_code = "error.nonpreemptible_operation"


class JobOperationTimeout(asyncio.TimeoutError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _execution_failure_reason(error: str | None) -> str:
    text = error or ""
    for reason_code in (
        NonPreemptibleOperation.reason_code,
        "budget.runtime_exhausted",
        "budget.idle_exhausted",
    ):
        if reason_code in text:
            return reason_code
    return "error.execution"


def _terminal_fields(
    status: JobStatus,
    reason_code: str,
    *,
    head_id: str | None = None,
    result_text: str | None = None,
    error: str | None = None,
) -> dict[str, str | None]:
    return {
        "status": status.value,
        "head_id": head_id,
        "result_text": result_text,
        "error": error,
        "reason_code": reason_code,
    }


def _store_write_terminal(
    session_id: str,
    job_id: str,
    fields: dict[str, Any],
) -> Optional[Job]:
    terminal_fields = dict(fields)
    status = JobStatus(terminal_fields.pop("status"))
    return _store_update_status(session_id, job_id, status, **terminal_fields)

# Job id of the job currently executing on this context. Bound by
# ``_run_one`` for the duration of the child turn; read by ``spawn_job``
# to default ``parent_job_id`` so jobs spawned from inside a running
# job record their spawn chain. ``cancel_job`` walks that chain for
# cascading cancel. Propagates into tool threads because both
# ``contextvars.copy_context`` (this runner) and ``asyncio.to_thread``
# carry ContextVars across.
_current_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openprogram_current_job_id", default=None,
)
_current_job_runner: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "openprogram_current_job_runner", default=None,
)
_borrowed_claim: contextvars.ContextVar[
    tuple[str, str, int, str] | None
] = contextvars.ContextVar("openprogram_borrowed_claim", default=None)


@dataclass(frozen=True)
class JobGovernanceContext:
    job_id: str
    budget_scope_id: str
    governor: Any
    ledger_identity: str
    effective_limits: tuple[tuple[str, int | str | None], ...]
    deadline_callback: Callable[[float | None], float | None]
    activity_callback: Callable[[str], bool]


_current_job_governance: contextvars.ContextVar[
    JobGovernanceContext | None
] = contextvars.ContextVar("openprogram_current_job_governance", default=None)


def record_current_job_activity(activity_kind: str) -> bool:
    runner = _current_job_runner.get()
    job_id = _current_job_id.get()
    return bool(runner and job_id and runner.record_job_activity(job_id, activity_kind))


def current_job_operation_timeout(
    declared_timeout: float | None,
    *,
    preemptibility: str = "async",
) -> float | None:
    runner = _current_job_runner.get()
    job_id = _current_job_id.get()
    if runner is None or job_id is None:
        return declared_timeout
    return runner.bounded_operation_timeout(
        job_id, declared_timeout, preemptibility=preemptibility,
    )


def current_job_operation_timeout_reason(
    declared_timeout: float | None,
) -> str | None:
    runner = _current_job_runner.get()
    job_id = _current_job_id.get()
    if runner is None or job_id is None:
        return None
    resolver = getattr(runner, "operation_timeout_reason", None)
    if not callable(resolver):
        return None
    return resolver(job_id, declared_timeout)


def current_job_resource_context() -> JobGovernanceContext | None:
    """Return the immutable governance handle for this claimed job body."""
    return _current_job_governance.get()


def _broadcast(payload: dict) -> None:
    """Send a WS frame to the frontend — best-effort.

    步 4：不再 import webui。把现成的帧 emit 到总线（``ws.frame`` 事件），
    webui 作为订阅者原样广播。帧内容（type / data 字段）一字不变，前端无感。
    """
    from openprogram.events import emit_ws_frame
    emit_ws_frame(payload)


def _broadcast_session_reload(session_id: str, *, reason: str = "job") -> None:
    _broadcast({
        "type": "session_reload",
        "data": {"session_id": session_id, "reason": reason},
    })


def _refresh_context_stats(session_id: str) -> None:
    """Re-estimate the context ring after the runner moved the graph.

    Same call every other out-of-turn graph mutation makes (compaction,
    checkout, branch delete). Best-effort and lazily imported: the runner
    also runs in CLI processes where no webui server exists.
    """
    try:
        from openprogram.webui.server import refresh_context_stats
        refresh_context_stats(session_id)
    except Exception:
        pass


def _stamp_job_change_owner(job: Job) -> None:
    """Persist actor/ownership facts on the child assistant change set."""
    if not job.head_id:
        return
    try:
        from openprogram.store.session.session_store import default_store

        store = default_store()
        store.merge_node_metadata(
            job.parent_session_id,
            job.head_id,
            {"change_owner": {
                "relation": job.relation,
                "origin_turn_id": job.origin_turn_id,
                "actor_id": job.agent_id,
                "job_id": job.id,
                "worktree_id": job.worktree_id,
                "session_id": job.parent_session_id,
                "status": job.status.value,
            }},
        )
    except Exception:
        _log.warning("job change ownership stamp failed for %s", job.id, exc_info=True)


def _broadcast_job_status(job: Job, resource: dict | None = None) -> None:
    data = {
        "job_id": job.id,
        "session_id": job.parent_session_id,
        "status": job.status.value,
        "parent_msg_id": job.parent_msg_id,
        "target_branch_head_id": job.target_branch_head_id,
        "head_id": job.head_id,
        "label": job.label,
        "subject": job.subject,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }
    if resource is not None:
        data["resource"] = resource
    _broadcast({
        "type": "job_status",
        "data": data,
    })
    # 事件层 tap：状态转移的单一漏斗，RUNNING → subagent.started，
    # 终止态 → subagent.ended。worker 线程里 ContextVar 不可靠，session 显式给。
    if job.status == JobStatus.RUNNING:
        emit_safe(
            "subagent.started", "system",
            {"job_id": job.id, "label": job.label},
            {"session": job.parent_session_id},
        )
    elif is_terminal(job.status):
        emit_safe(
            "subagent.ended", "system",
            {"job_id": job.id, "status": job.status.value, "error": job.error},
            {"session": job.parent_session_id},
        )


class JobRunner:
    """Singleton job pool. Use :func:`get_runner`.

    Public surface:

      * :meth:`spawn_job` — submit, return job_id
      * :meth:`cancel_job` — set cancel event, schedule timeout,
        cascade to descendant jobs (parent_job_id chain)
      * :meth:`get_job` / :meth:`list_jobs` — read
      * :meth:`await_job` — block until terminal, return final Job

    The runner is *thread-safe* — all maps are guarded by
    ``self._lock``.
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        *,
        governor=None,
        monotonic_clock: Callable[[], float] | None = None,
        budget_poll_seconds: float = 0.25,
    ) -> None:
        if max_workers is None:
            try:
                max_workers = int(
                    os.environ.get("OPENPROGRAM_JOB_WORKERS")
                    or _DEFAULT_MAX_WORKERS
                )
            except ValueError:
                max_workers = _DEFAULT_MAX_WORKERS
        if max_workers < 1:
            max_workers = 1
        self.max_workers = max_workers
        if governor is None:
            from openprogram.agent.resource_governance import ResourceGovernor
            from openprogram.store import default_store
            from openprogram.usage.ledger import UsageLedger

            governor = ResourceGovernor(
                UsageLedger(default_store().root_path.parent / "usage.db")
            )
        self._governor = governor
        self._monotonic = monotonic_clock or time.monotonic
        self._budget_poll_seconds = budget_poll_seconds
        self._claim_only_job_id: str | None = None
        self._claim_scope_lock = threading.Lock()
        self._instance_id = f"worker_{os.getpid()}_{uuid.uuid4().hex}"
        self._dispatch_wake = threading.Event()
        self._shutdown_event = threading.Event()
        # Reconcile orphans before opening the pool so any "running"
        # job from a previous process is flipped to errored. The
        # state-machine transition rules cover (running, errored).
        try:
            _store_reconcile(legacy_only=True)
        except Exception:
            pass
        self._reconcile_resources()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="op-job",
        )
        self._lock = threading.Lock()
        # job_id → {"event": Event, "future": Future, "session_id": str}
        self._jobs: dict[str, dict[str, Any]] = {}
        # job_id → threading.Event used to wake await_job() callers.
        self._done_events: dict[str, threading.Event] = {}
        # delivery session id → lock serialising follow-up turns on that
        # session. Two sub-agents finishing at once each want to append at
        # HEAD; without this they read the same HEAD and write siblings.
        # See ``_dispatch_followup``.
        self._followup_locks: dict[str, threading.Lock] = {}
        self._executor_slots = threading.BoundedSemaphore(max_workers)
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop,
            daemon=True,
            name="op-job-dispatcher",
        )
        self._dispatcher_thread.start()
        self._reconciler_thread = threading.Thread(
            target=self._reconcile_loop,
            daemon=True,
            name="op-job-reconciler",
        )
        self._reconciler_thread.start()
        self._budget_thread = threading.Thread(
            target=self._budget_loop,
            daemon=True,
            name="op-job-budget",
        )
        self._budget_thread.start()

    def _governance_context(self, job: Job) -> JobGovernanceContext:
        ledger = self._governor.ledger
        return JobGovernanceContext(
            job_id=job.id,
            budget_scope_id=job.budget_scope_id or "",
            governor=self._governor,
            ledger_identity=str(ledger._path().resolve()),
            effective_limits=tuple(sorted((job.effective_limits or {}).items())),
            deadline_callback=lambda declared: self.bounded_operation_timeout(
                job.id, declared,
            ),
            activity_callback=lambda kind: self.record_job_activity(job.id, kind),
        )

    def _followup_lock(self, session_id: str) -> threading.Lock:
        """The per-session follow-up lock, created on first use."""
        with self._lock:
            lk = self._followup_locks.get(session_id)
            if lk is None:
                lk = threading.Lock()
                self._followup_locks[session_id] = lk
            return lk

    # Public API

    def admit_job_entity(
        self,
        job: Job,
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
            persist=lambda accepted: _store_save(job.parent_session_id, accepted),
            creates_agent=creates_agent,
            caller_session_id=job.caller_session_id,
            caller_turn_id=caller_turn_id,
            dispatch_ready=dispatch_ready,
            borrowed_claim=borrowed_claim,
        )
        if not decision.accepted:
            raise AdmissionRejected(decision)
        return decision

    def spawn_job(
        self,
        session_id: str,
        prompt: str,
        agent_id: str,
        *,
        subject: str = "",
        description: str = "",
        context_mode: str = "inherit",
        parent_msg_id: Optional[str] = None,
        parent_job_id: Optional[str] = None,
        label: Optional[str] = None,
        attach_pointer_id: Optional[str] = None,
        target_branch_head_id: Optional[str] = None,
        worktree_id: Optional[str] = None,
        wait: bool = True,
        caller_msg_id: Optional[str] = None,
        caller_session_id: Optional[str] = None,
        chain_messages: int = 0,
        chain_generations: int = 0,
        caller_chain_generations: int = 0,
        archive_when_done: bool = False,
        spawn_caller: Optional[str] = None,
        advance_head: bool = False,
        tools_override: Optional[list[str]] = None,
        model_override: Optional[str] = None,
        thinking_effort: Optional[str] = None,
        render_range: Optional[dict[str, int]] = None,
        deferred_inbox: Optional[dict[str, Any]] = None,
        job_id: Optional[str] = None,
        authority: Optional[dict] = None,
        creates_agent: bool = True,
        on_accepted: Optional[Callable[[Job], None]] = None,
        defer_dispatch: bool = False,
        resume_deferred: bool = False,
        borrow_current_claim: bool = False,
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
        existing: Optional[Job] = None
        if job_id:
            existing = _store_load(session_id, job_id)
            if existing is not None and is_terminal(existing.status):
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
                parent_job_id = _current_job_id.get()
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
        job = Job(
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
            deferred_inbox=deferred_inbox,
            status=JobStatus.PENDING,
            created_at=existing.created_at if existing is not None else time.time(),
        )
        admission = nullcontext()
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
                job = replace(existing, parent_msg_id=parent_msg_id)
                _store_save(session_id, job)
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
                decision = self.admit_job_entity(
                    job,
                    creates_agent=creates_agent,
                    caller_turn_id=caller_msg_id,
                    dispatch_ready=not defer_dispatch and borrowed_claim is None,
                    borrowed_claim=(borrowed_claim[:3] if borrowed_claim else None),
                )
                idempotent = decision.idempotent
        if on_accepted is not None and not idempotent:
            try:
                on_accepted(job)
            except Exception:
                self._governor.request_stop(job.id, "error.accepted_side_effect")
                try:
                    _store_update_status(
                        session_id, job.id, JobStatus.ERRORED,
                        error="accepted job side effect failed",
                        reason_code="error.accepted_side_effect",
                    )
                except Exception:
                    pass
                raise
        if borrowed_claim is not None:
            self._run_borrowed_job(job, borrowed_claim)
            return job.id
        if idempotent:
            with self._lock:
                if job.id in self._jobs:
                    return job.id
            job = _store_load(session_id, job.id) or job
        if defer_dispatch:
            return job.id
        if not idempotent:
            self._broadcast_job_status(job)

        # Done-event for await_job / await_jobs callers.
        done_ev = threading.Event()
        cancel_ev = threading.Event()
        # Copy the current ContextVars so things like
        # ``run_control._current_session_id`` set by the spawning
        # thread don't leak into the worker. Each job gets its own
        # context — the worker function rebinds session_id explicitly.
        ctx = contextvars.copy_context()
        # Register *before* submitting: a fast job can reach the
        # finally-pop in _run_one before this thread gets the lock,
        # which would leave the entry orphaned in _jobs forever.
        # "future" is filled in right after submit, under the same lock.
        entry: dict = {
            "event": cancel_ev,
            "future": None,
            "session_id": session_id,
            "context": ctx,
        }
        with self._lock:
            self._jobs[job.id] = entry
            self._done_events[job.id] = done_ev
        self._dispatch_wake.set()

        return job.id

    def can_borrow_current_claim(self, session_id: str) -> bool:
        return self._current_borrowable_claim(session_id) is not None

    def _current_borrowable_claim(
        self, session_id: str,
    ) -> tuple[str, str, int, str] | None:
        inherited = _borrowed_claim.get()
        if inherited is not None:
            return inherited if inherited[3] == session_id else None
        if _current_job_runner.get() is not self:
            return None
        parent_job_id = _current_job_id.get()
        if not parent_job_id:
            return None
        parent = _store_load(session_id, parent_job_id)
        if parent is None or parent.parent_session_id != session_id:
            return None
        with self._lock:
            info = self._jobs.get(parent_job_id)
            generation = info.get("lease_generation") if info else None
        if generation is None:
            return None
        return (
            parent_job_id, self._instance_id, int(generation), session_id,
        )

    def _run_borrowed_job(
        self,
        job: Job,
        claim: tuple[str, str, int, str],
    ) -> None:
        """Execute a sync child inline under its same-session parent fence."""
        parent_job_id, owner_instance_id, lease_generation, session_id = claim
        if not self._governor.start_borrowed_job(
            job.id,
            parent_job_id=parent_job_id,
            owner_instance_id=owner_instance_id,
            lease_generation=lease_generation,
        ):
            raise RuntimeError(f"borrowed job {job.id!r} lost its parent fence")

        started_monotonic = self._monotonic()
        try:
            time_limits = self._borrowed_time_limits(job, started_monotonic)
        except Exception:
            self._governor.release_borrowed_job(
                job.id,
                parent_job_id=parent_job_id,
                owner_instance_id=owner_instance_id,
                lease_generation=lease_generation,
                reason_code="error.runtime_registration",
            )
            raise
        cancel_ev = threading.Event()
        done_ev = threading.Event()
        entry = {
            "event": cancel_ev,
            "future": None,
            "session_id": session_id,
            "context": contextvars.copy_context(),
            "started_monotonic": started_monotonic,
            "last_activity_monotonic": started_monotonic,
            "time_limits": time_limits,
            "lease_generation": lease_generation,
            "budget_cancelled": False,
            "borrowed_parent_job_id": parent_job_id,
        }
        with self._lock:
            self._jobs[job.id] = entry
            self._done_events[job.id] = done_ev

        from openprogram.agent.run_control import (
            is_cancelled,
            reset_current_execution_id,
            reset_current_session_id,
            set_current_execution_id,
            set_current_session_id,
            claim_cancel_event,
            unregister_cancel_event,
        )
        if not claim_cancel_event(
            session_id, cancel_ev, execution_id=job.id,
        ):
            with self._lock:
                self._jobs.pop(job.id, None)
                self._done_events.pop(job.id, None)
            self._governor.release_borrowed_job(
                job.id,
                parent_job_id=parent_job_id,
                owner_instance_id=owner_instance_id,
                lease_generation=lease_generation,
                reason_code="error.cancel_token_conflict",
            )
            raise RuntimeError(f"borrowed job {job.id!r} already owns a runtime")

        sid_token = set_current_session_id(session_id)
        execution_token = set_current_execution_id(job.id)
        job_token = _current_job_id.set(job.id)
        governance_token = _current_job_governance.set(
            self._governance_context(job),
        )
        claim_token = _borrowed_claim.set(claim)
        lease_stop = threading.Event()
        lease_thread = threading.Thread(
            target=self._renew_borrowed_lease,
            args=(
                job.id, parent_job_id, lease_generation, lease_stop,
            ),
            daemon=True,
            name=f"op-job-borrowed-lease-{job.id}",
        )
        lease_thread.start()
        chain_tokens: list = []
        result = None
        fatal_exception: BaseException | None = None
        released = False
        try:
            try:
                updated = _store_update_status(
                    session_id, job.id, JobStatus.RUNNING,
                    started_at=time.time(),
                )
                if updated is None:
                    raise RuntimeError(
                        f"borrowed job {job.id!r} disappeared"
                    )
                self._broadcast_job_status(updated)
                job = updated
                if not self.record_job_activity(job.id, "operation_start"):
                    raise RuntimeError(
                        f"borrowed job {job.id!r} lost its activity fence"
                    )
                from openprogram.programs.tools.agents.send_message.send_message.depth import (
                    set_chain_generations,
                    set_chain_messages,
                )
                chain_tokens = [
                    set_chain_messages(int(job.chain_messages or 0)),
                    set_chain_generations(int(job.chain_generations or 0)),
                ]
                from openprogram.agent.authority import normalize_authority
                from openprogram.agent.sub_agent_run import _execute_agent_turn
                branch_from = (
                    None if job.context_mode == "clean" else job.parent_msg_id
                )
                kwargs = {
                    "session_id": session_id,
                    "prompt": job.prompt,
                    "agent_id": job.agent_id,
                    "branch_from": branch_from,
                    "label": job.label,
                    "spawn_caller": (
                        job.spawn_caller or job.caller_msg_id
                        if branch_from is None else None
                    ),
                    "advance_head": job.advance_head,
                }
                if job.tools_override is not None:
                    kwargs["tools_override"] = job.tools_override
                if job.model_override is not None:
                    kwargs["model_override"] = job.model_override
                if job.thinking_effort is not None:
                    kwargs["thinking_effort"] = job.thinking_effort
                if job.render_range is not None:
                    kwargs["render_range"] = job.render_range
                authority = normalize_authority(job)
                if authority:
                    kwargs["authority"] = authority
                result = _execute_agent_turn(**kwargs)
            except BaseException as exc:  # noqa: BLE001
                from openprogram.agent.sub_agent_run import AgentTurnResult
                fatal_exception = exc
                result = AgentTurnResult(
                    failed=True, error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                for token in chain_tokens:
                    try:
                        token.var.reset(token)
                    except Exception:
                        pass
            assert result is not None
            self.record_job_activity(job.id, "terminal")
            cancelled = cancel_ev.is_set() or is_cancelled(session_id)
            current = _store_load(session_id, job.id) or job
            if cancelled:
                status = JobStatus.CANCELLED
                reason_code = current.reason_code or "cancel.user"
            elif result.failed:
                status = JobStatus.ERRORED
                reason_code = _execution_failure_reason(result.error)
            else:
                status = JobStatus.COMPLETED
                reason_code = "completed"
            fields = _terminal_fields(
                status,
                reason_code,
                head_id=result.head_id,
                result_text=result.final_text or "",
                error=result.error,
            )
            terminal: dict[str, Job] = {}
            released = self._governor.finalize_borrowed_job(
                job.id,
                parent_job_id=parent_job_id,
                owner_instance_id=owner_instance_id,
                lease_generation=lease_generation,
                reason_code=reason_code,
                terminal_fields=fields,
                mutate=lambda staged_fields: terminal.setdefault(
                    "job", _store_write_terminal(
                        session_id, job.id, staged_fields,
                    ),
                ),
            )
            if not released:
                raise RuntimeError(
                    f"borrowed job {job.id!r} lost its parent fence"
                )
            updated = terminal.get("job")
            if updated is not None:
                _stamp_job_change_owner(updated)
                self._broadcast_job_status(updated)
                _broadcast_session_reload(
                    session_id, reason=f"job_{status.value}",
                )
        finally:
            if not released:
                try:
                    self._governor.release_borrowed_job(
                        job.id,
                        parent_job_id=parent_job_id,
                        owner_instance_id=owner_instance_id,
                        lease_generation=lease_generation,
                        reason_code="error.borrowed_cleanup",
                    )
                except Exception:
                    _log.exception(
                        "failed to release borrowed job %s", job.id,
                    )
            lease_stop.set()
            lease_thread.join(timeout=1.0)
            try:
                from openprogram.agent.run_control import (
                    unregister_active_runtime,
                )
                unregister_active_runtime(session_id, execution_id=job.id)
            except Exception:
                pass
            try:
                unregister_cancel_event(
                    session_id, cancel_ev, execution_id=job.id,
                )
            except Exception:
                pass
            try:
                reset_current_execution_id(execution_token)
            except Exception:
                pass
            try:
                reset_current_session_id(sid_token)
            except Exception:
                pass
            try:
                _borrowed_claim.reset(claim_token)
            except Exception:
                pass
            try:
                _current_job_id.reset(job_token)
            except Exception:
                pass
            try:
                _current_job_governance.reset(governance_token)
            except Exception:
                pass
            self._wake_done(job.id)
            with self._lock:
                self._jobs.pop(job.id, None)
                self._done_events.pop(job.id, None)
        if fatal_exception is not None:
            raise fatal_exception

    def _borrowed_time_limits(
        self, job: Job, started_monotonic: float,
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

    def cancel_job(self, job_id: str, *, reason: Optional[str] = None) -> Optional[Job]:
        return self._cancel_cascade(
            job_id, reason=reason, root_reason_code="cancel.user",
        )

    def _cancel_cascade(
        self,
        job_id: str,
        *,
        reason: Optional[str],
        root_reason_code: str,
    ) -> Optional[Job]:
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
        if self._find_session_for_job(job_id) is None:
            return None
        cascade_reason = reason or f"parent job {job_id} cancelled"
        for child in self._descendant_jobs(job_id):
            if is_terminal(child.status):
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

    def _descendant_jobs(self, root_job_id: str) -> list[Job]:
        """All jobs reachable from ``root_job_id`` via parent_job_id,
        breadth-first, cycle-safe. Terminal ancestors are still
        traversed — a completed child may have spawned a grandchild
        that is still running."""
        children: dict[str, list[Job]] = {}
        for t in self.list_jobs():
            if t.parent_job_id:
                children.setdefault(t.parent_job_id, []).append(t)
        out: list[Job] = []
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
        reason: Optional[str] = None,
        reason_code: str = "cancel.user",
    ) -> Optional[Job]:
        """Cancel one job, no cascade. Returns the (post-update)
        Job entity, or None if not found.

        Effect:

          * sets the job's cancel event (worker drops out on next
            cooperative checkpoint)
          * sets the session-level cancel event via
            ``run_control.mark_cancelled`` so the existing dispatcher
            cancel path fires
          * kills any active BashTool subprocess via
            ``kill_active_runtime``
          * if the job is still in pending/queued, flips to cancelled
            immediately (no worker pickup yet, nothing to wait for)
          * if running, schedules a 30s watchdog that force-flips to
            cancelled if the worker hasn't honoured the signal
        """
        with self._lock:
            info = self._jobs.get(job_id)
        if not info:
            # Maybe loaded from disk-only state — try to find session.
            cur = self._find_session_for_job(job_id)
            if cur is None:
                return None
            session_id = cur
            info = None
        else:
            session_id = info["session_id"]
        if info is None:
            # Not on the pool. A queued job with no pool entry is a
            # tracked dispatch still queued in the target's inbox
            # (agent(to=…) hit a busy target) — withdraw it: pull the
            # inbox entry and flip the entity. NO session-level cancel
            # here: the target is busy running someone ELSE's turn,
            # which withdrawing a queued job must not kill.
            queued_job = _store_load(session_id, job_id)
            if queued_job is not None and queued_job.status in (
                JobStatus.PENDING, JobStatus.QUEUED,
            ):
                try:
                    from openprogram.agent import inbox
                    inbox.discard_job(session_id, job_id)
                except Exception:
                    pass
                try:
                    self._governor.request_stop(job_id, reason_code)
                    updated = _store_update_status(
                        session_id, job_id, JobStatus.CANCELLED,
                        cancel_requested_at=time.time(),
                        error=reason or "withdrawn before delivery",
                        reason_code=reason_code,
                    )
                except ValueError:
                    updated = _store_load(session_id, job_id)
                if updated is not None:
                    self._broadcast_job_status(updated)
                    self._wake_done(job_id)
                return updated
        cur_job = _store_load(session_id, job_id)
        if cur_job is None:
            return None
        if cur_job.status in (JobStatus.PENDING, JobStatus.QUEUED):
            if info is not None:
                info["event"].set()
            self._governor.request_stop(job_id, reason_code)
            try:
                updated = _store_update_status(
                    session_id, job_id, JobStatus.CANCELLED,
                    cancel_requested_at=time.time(),
                    error=reason or "cancelled before pickup",
                    reason_code=reason_code,
                )
            except ValueError:
                updated = _store_load(session_id, job_id)
            if updated is not None:
                self._broadcast_job_status(updated)
                self._wake_done(job_id)
                self._update_attach_card(updated)
                _broadcast_session_reload(session_id, reason="job_cancelled")
            self._dispatch_wake.set()
            return updated
        # Bridge to existing session-level cancel infra so the LLM
        # stream + bash subprocess + agent_loop pre-invocation hook
        # all see the signal.
        try:
            from openprogram.agent.run_control import (
                kill_active_runtime,
                mark_cancelled,
            )
            mark_cancelled(session_id, execution_id=job_id)
            kill_active_runtime(session_id, execution_id=job_id)
            from openprogram.agent.process_runner import kill_active_subprocess
            kill_active_subprocess(session_id, execution_id=job_id)
        except Exception:
            pass
        if info is not None:
            info["event"].set()

        # Status-side: if still pending/queued, flip terminal now.
        # If running, leave terminal flip to the worker (or the
        # watchdog).
        self._governor.request_stop(job_id, reason_code)
        try:
            if cur_job.status == JobStatus.RUNNING:
                # Stamp request time but stay in running. Worker will
                # detect cancel and self-flip.
                #
                # This MUST go through the store's locked
                # read-modify-write, not save_job: the worker was
                # signalled a few lines above and can reach its own
                # terminal write inside the window since _store_load.
                # A blind save of the snapshot we read then rewrote
                # ``status: running`` over the worker's ``cancelled``,
                # resurrecting a finished job — the row stayed
                # non-terminal forever (phantom "running" in the job
                # panel, reconciled to "worker died before completion"
                # on next startup). update_job_status applies the
                # field under the same lock and raises on a status that
                # has since moved on.
                _store_update_status(
                    session_id, job_id, JobStatus.RUNNING,
                    cancel_requested_at=time.time(),
                    reason_code=reason_code,
                )
                # Watchdog: force cancel if worker doesn't honour signal.
                lease_generation = info.get("lease_generation") if info else None
                if lease_generation is not None:
                    self._schedule_force_cancel(
                        session_id, job_id, lease_generation,
                    )
                return _store_load(session_id, job_id) or cur_job
        except ValueError:
            # The worker moved the job on while we were stamping —
            # its state is the truthful one.
            return _store_load(session_id, job_id) or cur_job
        return cur_job

    def get_job(self, job_id: str) -> Optional[Job]:
        sid = self._find_session_for_job(job_id)
        if not sid:
            return None
        return _store_load(sid, job_id)

    def get_job_resource_view(self, job_id: str):
        """Return the canonical resource DTO for one persisted Job."""
        job = self.get_job(job_id)
        if job is None:
            return None
        from openprogram.agent.resource_governance import build_job_resource_view

        return build_job_resource_view(
            job,
            ledger=self._governor.ledger,
            resolved=self._governor._limit_resolver(
                job.parent_session_id, job,
            ),
        )

    def _broadcast_job_status(self, job: Job) -> None:
        try:
            view = self.get_job_resource_view(job.id)
            resource = view.to_dict() if view is not None else None
        except Exception:
            resource = None
        _broadcast_job_status(job, resource)

    def list_jobs(
        self,
        session_id: Optional[str] = None,
        *,
        status_filter: Optional[set[JobStatus]] = None,
        limit: Optional[int] = None,
    ) -> list[Job]:
        if session_id:
            return _store_list(session_id, status_filter=status_filter, limit=limit)
        # Walk every session — used by the global job panel.
        from openprogram.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return []
        out: list[Job] = []
        for sdir in sorted(store.root_path.iterdir()):
            if not sdir.is_dir():
                continue
            out.extend(_store_list(sdir.name, status_filter=status_filter))
        out.sort(key=lambda t: t.created_at or 0, reverse=True)
        if limit is not None:
            out = out[:limit]
        return out

    def await_job(self, job_id: str, timeout: Optional[float] = None) -> Optional[Job]:
        """Block the calling thread until the job reaches terminal.

        Returns the final Job. Returns None on unknown job. Returns
        the current (possibly non-terminal) entity on timeout.
        """
        cur = self.get_job(job_id)
        if cur is None:
            return None
        if is_terminal(cur.status):
            return cur
        with self._lock:
            done = self._done_events.get(job_id)
        if done is None:
            # Lost track (process restart with persisted job) — poll.
            deadline = time.time() + (timeout or 60.0)
            while time.time() < deadline:
                cur = self.get_job(job_id)
                if cur is not None and is_terminal(cur.status):
                    return cur
                time.sleep(0.5)
            return self.get_job(job_id)
        done.wait(timeout=timeout)
        return self.get_job(job_id)

    def await_job_durable(
        self,
        job_id: str,
        *,
        timeout: Optional[float] = None,
        on_poll: Callable[[], None] | None = None,
    ) -> Optional[Job]:
        """Wait on JobStore state that another worker process can update."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            job = self.get_job(job_id)
            if job is None or is_terminal(job.status):
                return job
            if deadline is not None and time.monotonic() >= deadline:
                return job
            if on_poll is not None:
                on_poll()
            time.sleep(0.05)

    def retire_external_waiter(self, job_id: str) -> None:
        """Drop local wait state after another process completed the job."""
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None or entry.get("future") is not None:
                return
            self._jobs.pop(job_id, None)
            self._done_events.pop(job_id, None)

    @contextmanager
    def claim_only(self, job_id: str):
        """Temporarily restrict this runner to one direct synchronous job."""
        with self._claim_scope_lock:
            if self._claim_only_job_id is not None:
                raise RuntimeError("a direct claim scope is already active")
            self._claim_only_job_id = job_id
            self._dispatch_wake.set()
            try:
                yield
            finally:
                self._claim_only_job_id = None
                self._dispatch_wake.set()

    def record_job_activity(self, job_id: str, activity_kind: str) -> bool:
        with self._lock:
            entry = self._jobs.get(job_id)
            lease_generation = (
                entry.get("lease_generation") if entry is not None else None
            )
        if lease_generation is None:
            return False
        recorded = self._governor.record_activity(
            job_id,
            owner_instance_id=self._instance_id,
            lease_generation=lease_generation,
            activity_kind=activity_kind,
        )
        if not recorded:
            return False
        lineage = [job_id]
        current = self.get_job(job_id)
        seen = {job_id}
        while current is not None and current.parent_job_id:
            parent_id = current.parent_job_id
            if parent_id in seen:
                break
            seen.add(parent_id)
            lineage.append(parent_id)
            current = self.get_job(parent_id)
        now = self._monotonic()
        with self._lock:
            for lineage_job_id in lineage:
                entry = self._jobs.get(lineage_job_id)
                if entry is not None and entry.get("started_monotonic") is not None:
                    entry["last_activity_monotonic"] = now
        return True

    def _finalize_job_status(
        self,
        session_id: str,
        job_id: str,
        lease_generation: int,
        status: JobStatus,
        reason_code: str,
        **fields: Any,
    ) -> Optional[Job]:
        terminal: dict[str, Job] = {}
        terminal_fields = _terminal_fields(
            status,
            reason_code,
            head_id=fields.get("head_id"),
            result_text=fields.get("result_text"),
            error=fields.get("error"),
        )
        try:
            self._governor.finalize_job(
                job_id, reason_code,
                owner_instance_id=self._instance_id,
                lease_generation=lease_generation,
                terminal_fields=terminal_fields,
                mutate=lambda staged_fields: terminal.setdefault(
                    "job", _store_write_terminal(
                        session_id, job_id, staged_fields,
                    ),
                ),
            )
        except ValueError:
            return None
        return terminal.get("job")

    def bounded_operation_timeout(
        self,
        job_id: str,
        declared_timeout: float | None,
        *,
        preemptibility: str = "async",
    ) -> float | None:
        if declared_timeout is not None and declared_timeout <= 0:
            raise ValueError("declared timeout must be positive")
        with self._lock:
            entry = self._jobs.get(job_id)
            snapshot = dict(entry) if entry is not None else None
        if snapshot is not None and snapshot.get("time_limits") is not None:
            runtime_limit, idle_limit = snapshot["time_limits"]
        else:
            runtime_limit, idle_limit = self._governor.job_time_limits(job_id)
        strict = runtime_limit is not None or idle_limit is not None
        if strict and preemptibility not in {"async", "process"}:
            raise NonPreemptibleOperation(
                "error.nonpreemptible_operation: strict time-budget job "
                "cannot start an operation without a guaranteed stop boundary",
            )
        bounds = [] if declared_timeout is None else [float(declared_timeout)]
        if snapshot is not None and snapshot.get("started_monotonic") is not None:
            now = self._monotonic()
            if runtime_limit is not None:
                bounds.append(max(
                    0.0, float(runtime_limit) - (
                        now - snapshot["started_monotonic"]
                    ),
                ))
            if idle_limit is not None:
                bounds.append(max(
                    0.0, float(idle_limit) - (
                        now - snapshot["last_activity_monotonic"]
                    ),
                ))
        if strict and not bounds:
            raise NonPreemptibleOperation(
                "strict time-budget job requires a live bounded operation",
            )
        return min(bounds) if bounds else None

    def operation_timeout_reason(
        self, job_id: str, declared_timeout: float | None,
    ) -> str | None:
        with self._lock:
            entry = self._jobs.get(job_id)
            snapshot = dict(entry) if entry is not None else None
        if snapshot is None or snapshot.get("started_monotonic") is None:
            return None
        runtime_limit, idle_limit = snapshot.get("time_limits", (None, None))
        now = self._monotonic()
        candidates: list[tuple[float, str]] = []
        if declared_timeout is not None:
            candidates.append((float(declared_timeout), "error.operation_timeout"))
        if runtime_limit is not None:
            candidates.append((max(
                0.0,
                float(runtime_limit) - (now - snapshot["started_monotonic"]),
            ), "budget.runtime_exhausted"))
        if idle_limit is not None:
            candidates.append((max(
                0.0,
                float(idle_limit) - (now - snapshot["last_activity_monotonic"]),
            ), "budget.idle_exhausted"))
        return min(candidates, default=(0.0, None), key=lambda item: item[0])[1]

    def shutdown(self, wait: bool = True) -> None:
        """Tear down the pool. Used in tests / process shutdown."""
        self._shutdown_event.set()
        self._dispatch_wake.set()
        self._dispatcher_thread.join(timeout=1.0)
        self._reconciler_thread.join(timeout=1.0)
        self._budget_thread.join(timeout=1.0)
        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            # Python 3.8 fallback (no cancel_futures kwarg).
            self._pool.shutdown(wait=wait)

    # Worker body

    def _dispatch_loop(self) -> None:
        """Submit only durably claimed jobs to the executor."""
        while not self._shutdown_event.is_set():
            self._dispatch_wake.wait(0.5)
            self._dispatch_wake.clear()
            blocked_sessions: set[str] = set()
            while not self._shutdown_event.is_set():
                if not self._executor_slots.acquire(blocking=False):
                    break
                try:
                    claim = self._governor.claim_next(
                        owner_instance_id=self._instance_id,
                        excluded_sessions=blocked_sessions,
                        only_job_id=self._claim_only_job_id,
                    )
                except Exception:
                    self._executor_slots.release()
                    _log.exception("failed to claim next durable job")
                    break
                if claim is None:
                    self._executor_slots.release()
                    break
                time_limits = self._governor.job_time_limits(claim.job_id)
                claimed_monotonic = self._monotonic()
                with self._lock:
                    entry = self._jobs.get(claim.job_id)
                    if entry is None:
                        cancel_ev = threading.Event()
                        done_ev = self._done_events.setdefault(
                            claim.job_id, threading.Event(),
                        )
                        entry = {
                            "event": cancel_ev,
                            "future": None,
                            "session_id": claim.session_id,
                            "context": contextvars.copy_context(),
                        }
                        self._jobs[claim.job_id] = entry
                    else:
                        cancel_ev = entry["event"]
                        done_ev = self._done_events[claim.job_id]
                    ctx = entry["context"]
                from openprogram.agent.run_control import claim_cancel_event
                if not claim_cancel_event(
                    claim.session_id, cancel_ev, execution_id=claim.job_id,
                ):
                    requeued = self._governor.requeue_job(
                        claim.job_id,
                        owner_instance_id=self._instance_id,
                        lease_generation=claim.lease_generation,
                    )
                    if not requeued:
                        terminal: dict[str, Job | None] = {}
                        current = _store_load(claim.session_id, claim.job_id)
                        reason_code = (
                            current.reason_code if current is not None else None
                        ) or "cancel.concurrent"
                        terminal_fields = _terminal_fields(
                            JobStatus.CANCELLED,
                            reason_code,
                            error="cancelled before execution",
                        )

                        def cancel_store(staged_fields: dict[str, Any]) -> None:
                            current = _store_load(claim.session_id, claim.job_id)
                            if current is not None and not is_terminal(current.status):
                                try:
                                    current = _store_write_terminal(
                                        claim.session_id, claim.job_id,
                                        staged_fields,
                                    )
                                except ValueError:
                                    current = _store_load(
                                        claim.session_id, claim.job_id,
                                    )
                            terminal["job"] = current

                        try:
                            finalized = self._governor.finalize_stopping_job(
                                claim.job_id,
                                owner_instance_id=self._instance_id,
                                lease_generation=claim.lease_generation,
                                reason_code=reason_code,
                                terminal_fields=terminal_fields,
                                mutate=cancel_store,
                            )
                        except Exception:
                            finalized = False
                            _log.exception(
                                "failed to finalize stopping job %s",
                                claim.job_id,
                            )
                            try:
                                self._governor.abandon_stopping_job(
                                    claim.job_id,
                                    owner_instance_id=self._instance_id,
                                    lease_generation=claim.lease_generation,
                                )
                            except Exception:
                                _log.exception(
                                    "failed to abandon stopping job %s",
                                    claim.job_id,
                                )
                        if finalized:
                            current = terminal.get("job")
                            if current is not None:
                                self._broadcast_job_status(current)
                            self._wake_done(claim.job_id)
                            with self._lock:
                                self._jobs.pop(claim.job_id, None)
                                self._done_events.pop(claim.job_id, None)
                    blocked_sessions.add(claim.session_id)
                    self._executor_slots.release()
                    continue
                with self._lock:
                    entry["started_monotonic"] = claimed_monotonic
                    entry["last_activity_monotonic"] = claimed_monotonic
                    entry["time_limits"] = time_limits
                    entry["lease_generation"] = claim.lease_generation
                    entry["budget_cancelled"] = False
                try:
                    future: Future = self._pool.submit(
                        ctx.run, self._run_one, claim.job_id, claim.session_id,
                        cancel_ev, done_ev, claim.lease_generation,
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
                            JobStatus.ERRORED,
                            "error.dispatch_failed",
                            error="executor submission failed",
                        )
                    except Exception:
                        _log.exception(
                            "failed to durably finalize undispatched job %s",
                            claim.job_id,
                        )
                    if updated is None:
                        current = _store_load(claim.session_id, claim.job_id)
                        if current is not None and is_terminal(current.status):
                            updated = current
                    if updated is not None:
                        self._broadcast_job_status(updated)
                        self._wake_done(claim.job_id)
                        with self._lock:
                            self._jobs.pop(claim.job_id, None)
                            self._done_events.pop(claim.job_id, None)
                    self._dispatch_wake.set()
                    _log.exception("failed to submit claimed job %s", claim.job_id)
                    continue
                with self._lock:
                    if self._jobs.get(claim.job_id) is entry:
                        entry["future"] = future

    def _run_one(
        self, job_id: str, claimed_session_id: str,
        cancel_ev: threading.Event, done_ev: threading.Event,
        lease_generation: int,
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
        # Look up the job entity at entry — fields like
        # parent_session_id, prompt, agent_id are stable from this
        # point forward.
        job = self._lookup_or_load(job_id)
        if job is None:
            from openprogram.agent.run_control import unregister_cancel_event
            unregister_cancel_event(
                claimed_session_id, cancel_ev, execution_id=job_id,
            )
            self._governor.release_job(
                job_id, "error.job_missing",
                owner_instance_id=self._instance_id,
                lease_generation=lease_generation,
            )
            self._executor_slots.release()
            self._dispatch_wake.set()
            done_ev.set()
            with self._lock:
                self._jobs.pop(job_id, None)
                self._done_events.pop(job_id, None)
            return
        session_id = job.parent_session_id

        # Bind the session id ContextVar for the cancel hook. Same
        # contract _execute_in_context honours in the webui worker.
        from openprogram.agent.run_control import (
            reset_current_execution_id,
            unregister_cancel_event,
            set_current_execution_id,
            set_current_session_id,
            reset_current_session_id,
        )
        sid_token = set_current_session_id(session_id)
        execution_token = set_current_execution_id(job_id)
        # Bind the running job id so spawns made inside this child turn
        # record parent_job_id (cascading cancel walks that chain).
        _job_id_token = _current_job_id.set(job_id)
        _runner_token = _current_job_runner.set(self)
        _governance_token = _current_job_governance.set(
            self._governance_context(job),
        )
        # If this job is bound to an agent worktree, bind the
        # _current_worktree_path ContextVar so bash / edit / write /
        # read use it as default cwd. Reset is handled in the finally
        # below via a token, mirroring the session-id pattern.
        _wt_token = None
        if job.worktree_id:
            try:
                from openprogram.worktree.context import set_worktree as _set_wt
                from openprogram.worktree.manager import get_manager as _get_wt_mgr
                wt = _get_wt_mgr().get_worktree(job.worktree_id)
                if wt is not None:
                    _wt_token = _set_wt(wt.worktree_path)
            except Exception:
                _wt_token = None

        lease_stop = threading.Event()
        lease_thread = threading.Thread(
            target=self._renew_job_lease,
            args=(job_id, lease_generation, lease_stop),
            daemon=True,
            name=f"op-job-lease-{job_id}",
        )
        lease_thread.start()

        try:
            # pending → running. If state went to cancelled (pre-pickup)
            # the transition fails — bail out cleanly.
            try:
                updated = _store_update_status(
                    session_id, job_id, JobStatus.RUNNING,
                    started_at=time.time(),
                )
                if updated is None:
                    # job entity vanished
                    return
                self._broadcast_job_status(updated)
            except ValueError:
                # Transition rejected — likely already terminal. Done.
                return
            if cancel_ev.is_set():
                # Cancel arrived between queue + pickup.
                updated = self._finalize_job_status(
                    session_id, job_id, lease_generation,
                    JobStatus.CANCELLED, "cancel.user",
                    error="cancelled before run",
                )
                if updated is not None:
                    self._broadcast_job_status(updated)
                return

            # Progress poller — while the sub-agent is grinding, patch
            # the placeholder attach card's preview text with the
            # latest sub-agent message so the chat row stops reading
            # "(running)" forever. Runs on a daemon thread; stop_ev
            # is set in the finally block once run_agent_turn returns.
            stop_progress = threading.Event()
            progress_thread: Optional[threading.Thread] = None
            if job.attach_pointer_id:
                progress_thread = threading.Thread(
                    target=self._poll_progress,
                    args=(job, stop_progress),
                    daemon=True,
                )
                progress_thread.start()
            try:
                from openprogram.agent.sub_agent_run import _execute_agent_turn
                # Resolve parent for inherit-mode: walk through to the
                # parent_msg_id supplied at spawn time.
                branch_from: Optional[str]
                if (job.context_mode or "inherit") == "clean":
                    branch_from = None
                else:
                    branch_from = job.parent_msg_id
                # Bind both chain counters so send_message / agent calls
                # made INSIDE this child turn see the right budgets and
                # the guards can trip (send_message §5.1). A spawned
                # child arrives with one more generation than its
                # dispatcher; a delivery to an existing agent arrives
                # with the same generation count it was sent at.
                _chain_tokens: list = []
                try:
                    from openprogram.programs.tools.agents.send_message.send_message.depth import (
                        set_chain_generations, set_chain_messages,
                    )
                    _chain_tokens = [
                        set_chain_messages(int(job.chain_messages or 0)),
                        set_chain_generations(int(job.chain_generations or 0)),
                    ]
                except Exception:
                    pass
                try:
                    from openprogram.agent.authority import normalize_authority
                    _turn_kwargs = dict(
                        session_id=session_id,
                        prompt=job.prompt,
                        agent_id=job.agent_id,
                        branch_from=branch_from,
                        label=job.label,
                        # clean mode = new branch → its root's caller = the
                        # spawning node, so it's an explicit spawn (not
                        # seq-stitched into a sibling). dag/overview.md §2.3.
                        spawn_caller=(
                            job.spawn_caller or job.caller_msg_id
                            if branch_from is None else None
                        ),
                        # Same-session spawn: never steal the head.
                        advance_head=job.advance_head,
                    )
                    if job.tools_override is not None:
                        _turn_kwargs["tools_override"] = job.tools_override
                    if job.model_override is not None:
                        _turn_kwargs["model_override"] = job.model_override
                    if job.thinking_effort is not None:
                        _turn_kwargs["thinking_effort"] = job.thinking_effort
                    if job.render_range is not None:
                        _turn_kwargs["render_range"] = job.render_range
                    _job_authority = normalize_authority(job)
                    if _job_authority:
                        _turn_kwargs["authority"] = _job_authority
                    result = _execute_agent_turn(**_turn_kwargs)
                finally:
                    for _tok in _chain_tokens:
                        try:
                            _tok.var.reset(_tok)
                        except Exception:
                            pass
            except BaseException as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                reason_code = _execution_failure_reason(err)
                updated = self._finalize_job_status(
                    session_id, job_id, lease_generation,
                    JobStatus.ERRORED, reason_code, error=err,
                )
                if updated is not None:
                    self._broadcast_job_status(updated)
                    self._update_attach_card(updated, error_text=err)
                _broadcast_session_reload(session_id, reason="job_errored")
                return
            finally:
                stop_progress.set()
                if progress_thread is not None:
                    try:
                        progress_thread.join(timeout=1.0)
                    except Exception:
                        pass

            # Decide terminal status.
            self.record_job_activity(job_id, "terminal")
            cancelled = cancel_ev.is_set() or (
                result.error and "stopped" in (result.error or "").lower()
            )
            if cancelled:
                new_status = JobStatus.CANCELLED
            elif result.failed:
                new_status = JobStatus.ERRORED
            else:
                new_status = JobStatus.COMPLETED
            current_reason = (_store_load(session_id, job_id) or job).reason_code
            reason_code = (
                (current_reason or "cancel.user")
                if new_status == JobStatus.CANCELLED
                else _execution_failure_reason(result.error)
                if new_status == JobStatus.ERRORED
                else "completed"
            )
            updated = self._finalize_job_status(
                session_id, job_id, lease_generation, new_status, reason_code,
                head_id=result.head_id,
                result_text=result.final_text or "",
                error=result.error,
            )
            if updated is not None:
                self._broadcast_job_status(updated)
                self._update_attach_card(updated)
                # Auto-followup: when an async job completes (or
                # errors / is cancelled), nobody is listening unless
                # we explicitly nudge the caller's session. Fire a
                # follow-up LLM turn that says "job X is done" — the
                # next turn's context will include the attach pointer
                # the runner just wrote, so the agent naturally sees
                # the sub-agent's output and can react.
                #
                # Skip when wait=True (sync path doesn't need it —
                # the caller is already blocked on the result).
                if new_status == JobStatus.COMPLETED and not updated.wait:
                    self._dispatch_followup(updated)
                # Spawn-branch bookkeeping at terminal state, AFTER the
                # result flowed back: archive the branch when the spawn
                # asked for archive_when_done. Best-effort — a meta
                # write failure must never affect the result path.
                self._finalize_spawn_branch_meta(updated)
            # Tell tail clients the session changed so attach card
            # picks up the new head / text.
            _broadcast_session_reload(session_id, reason=f"job_{new_status.value}")
        except BaseException as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            try:
                updated = self._finalize_job_status(
                    session_id, job_id, lease_generation,
                    JobStatus.ERRORED, "error.execution", error=err,
                )
                if updated is not None:
                    self._broadcast_job_status(updated)
            except BaseException:
                # finalize_job stages a durable intent before the job-store
                # write. Reconcile completes it once persistence recovers.
                _log.exception("failed to persist terminal job %s", job_id)
        finally:
            lease_stop.set()
            lease_thread.join(timeout=1.0)
            try:
                # Pass our Event: if a newer turn (e.g. a chat turn the
                # user started while this job ran) has re-registered,
                # its token must survive our teardown or its Stop dies.
                unregister_cancel_event(
                    session_id, cancel_ev, execution_id=job_id,
                )
            except Exception:
                pass
            try:
                reset_current_execution_id(execution_token)
            except Exception:
                pass
            try:
                reset_current_session_id(sid_token)
            except Exception:
                pass
            try:
                _current_job_id.reset(_job_id_token)
            except Exception:
                pass
            try:
                _current_job_runner.reset(_runner_token)
            except Exception:
                pass
            try:
                _current_job_governance.reset(_governance_token)
            except Exception:
                pass
            if _wt_token is not None:
                try:
                    from openprogram.worktree.context import reset_worktree
                    reset_worktree(_wt_token)
                except Exception:
                    pass
            # If the job was cancelled (D15) and it owned a worktree,
            # auto-discard the worktree. Completion / error → leave the
            # worktree alone so the parent agent or user can decide
            # what to do with it.
            try:
                cur = _store_load(session_id, job_id)
                if (cur is not None
                        and cur.status == JobStatus.CANCELLED
                        and cur.worktree_id):
                    try:
                        from openprogram.worktree.manager import (
                            get_manager as _get_wt_mgr,
                        )
                        _get_wt_mgr().discard_worktree(
                            cur.worktree_id,
                            force=True,
                            delete_branch=True,
                        )
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                # Release the admission unconditionally: a job that never
                # reached a terminal state (store row vanished, or the
                # pending→running transition was rejected) still holds a
                # 'live' row that would consume max_live_per_session for
                # good. release_job refuses to act while a pending
                # finalization intent exists, so reconcile still owns the
                # "terminal write staged but not persisted" case.
                cur = _store_load(session_id, job_id)
                if cur is None or not is_terminal(cur.status):
                    _log.warning(
                        "job %s ended without a persisted terminal state; "
                        "releasing admission with no reason code", job_id,
                    )
                    cur = None
                self._governor.release_job(
                    job_id, cur.reason_code if cur is not None else None,
                    owner_instance_id=self._instance_id,
                    lease_generation=lease_generation,
                )
            except Exception:
                _log.exception("failed to release resource admission for %s", job_id)
            self._wake_done(job_id)
            with self._lock:
                self._jobs.pop(job_id, None)
                # Drop the done-event too, else it leaks one Event per
                # job for the process lifetime. Waiters already hold a
                # reference (await_job reads it before waiting) and it
                # is set by _wake_done above; anyone arriving later sees
                # the job is terminal and returns without waiting.
                self._done_events.pop(job_id, None)
            self._executor_slots.release()
            self._dispatch_wake.set()

    # Internals

    def _renew_job_lease(
        self, job_id: str, lease_generation: int, stop: threading.Event,
    ) -> None:
        while not stop.wait(_LEASE_RENEW_SECS):
            try:
                if not self._governor.renew_lease(
                    job_id, owner_instance_id=self._instance_id,
                    lease_generation=lease_generation,
                ):
                    return
            except Exception:
                _log.exception("failed to renew resource lease for %s", job_id)
                return

    def _renew_borrowed_lease(
        self,
        job_id: str,
        parent_job_id: str,
        lease_generation: int,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(_LEASE_RENEW_SECS):
            try:
                if not self._governor.renew_borrowed_lease(
                    job_id,
                    parent_job_id=parent_job_id,
                    owner_instance_id=self._instance_id,
                    lease_generation=lease_generation,
                ):
                    return
            except Exception:
                _log.exception(
                    "failed to renew borrowed resource lease for %s", job_id,
                )
                return

    def _owner_holds_worker_lock(self, owner_instance_id: str) -> bool:
        try:
            owner_pid = int(owner_instance_id.split("_", 2)[1])
        except (IndexError, ValueError):
            return False
        try:
            from openprogram.worker.lock import is_held_by
            return is_held_by(owner_pid)
        except Exception:
            return False

    @staticmethod
    def _mark_worker_lost(session_id: str, job_id: str) -> None:
        job = _store_load(session_id, job_id)
        if job is None or is_terminal(job.status):
            return
        try:
            _store_update_status(
                session_id, job_id, JobStatus.ERRORED,
                error="worker died before completion",
                reason_code="error.worker_lost",
            )
        except ValueError:
            return

    def _reconcile_resources(self) -> None:
        try:
            result = self._governor.reconcile(
                job_lookup=lambda session_id, job_id: _store_load(
                    session_id, job_id,
                ),
                write_terminal=_store_write_terminal,
                mark_worker_lost=self._mark_worker_lost,
                owner_is_alive=self._owner_holds_worker_lock,
            )
        except Exception:
            _log.exception("failed to reconcile durable job resources")
            return
        for job_id, session_id in result.completed_pending:
            job = _store_load(session_id, job_id)
            if job is not None and is_terminal(job.status):
                self._broadcast_job_status(job)
                _broadcast_session_reload(
                    session_id, reason=f"job_{job.status.value}",
                )
        try:
            self._governor.recover_provider_reservations()
        except Exception:
            _log.exception("failed to reconcile provider reservations")
        try:
            orphaned_borrowed = self._governor.release_orphaned_borrowed_jobs()
        except Exception:
            _log.exception("failed to reconcile borrowed job resources")
            orphaned_borrowed = []
        for job_id, session_id in orphaned_borrowed:
            job = _store_load(session_id, job_id)
            if job is None or is_terminal(job.status):
                continue
            try:
                _store_update_status(
                    session_id, job_id, JobStatus.ERRORED,
                    error="borrowed parent claim was lost",
                    reason_code="error.borrowed_parent_lost",
                )
            except ValueError:
                pass
        self._recover_deferred_resumes()
        self._recover_deferred_inboxes()
        if (
            result.finalized_preparing
            or result.released_missing
            or result.released_worker_lost
        ):
            self._dispatch_wake.set()

    def _recover_deferred_resumes(self) -> None:
        """Publish a staged resume if the Job target save was durable."""
        try:
            pending = self._governor.pending_deferred_resumes()
        except Exception:
            _log.exception("failed to list staged deferred resumes")
            return
        for job_id, session_id, admission_id, parent_msg_id in pending:
            job = _store_load(session_id, job_id)
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
                _log.exception(
                    "failed to recover deferred resume for job %s", job_id,
                )

    def _recover_deferred_inboxes(self) -> None:
        """Recreate inbox entries lost after a durable deferred admission."""
        try:
            deferred = self._governor.deferred_dispatches()
        except Exception:
            _log.exception("failed to list deferred job admissions")
            return
        from openprogram.agent import inbox
        for job_id, session_id in deferred:
            job = _store_load(session_id, job_id)
            intent = job.deferred_inbox if job is not None else None
            if not isinstance(intent, dict):
                self._governor.request_stop(
                    job_id, "error.deferred_inbox_intent_missing",
                )
                if job is not None and not is_terminal(job.status):
                    try:
                        _store_update_status(
                            session_id, job_id, JobStatus.ERRORED,
                            error="deferred inbox intent missing",
                            reason_code="error.deferred_inbox_intent_missing",
                        )
                    except Exception:
                        pass
                continue
            try:
                inbox.enqueue(session_id, **intent)
            except Exception:
                _log.exception(
                    "failed to recover deferred inbox for job %s", job_id,
                )

    def _reconcile_loop(self) -> None:
        while not self._shutdown_event.wait(_RECONCILE_SECS):
            self._reconcile_resources()

    def _budget_loop(self) -> None:
        while not self._shutdown_event.wait(self._budget_poll_seconds):
            now = self._monotonic()
            expired: list[tuple[str, str]] = []
            with self._lock:
                for job_id, entry in self._jobs.items():
                    started = entry.get("started_monotonic")
                    if started is None or entry.get("budget_cancelled"):
                        continue
                    runtime_limit, idle_limit = entry.get(
                        "time_limits", (None, None),
                    )
                    reason_code = None
                    if (
                        runtime_limit is not None
                        and now - started >= float(runtime_limit)
                    ):
                        reason_code = "budget.runtime_exhausted"
                    elif (
                        idle_limit is not None
                        and now - entry["last_activity_monotonic"] >= float(idle_limit)
                    ):
                        reason_code = "budget.idle_exhausted"
                    if reason_code is not None:
                        entry["budget_cancelled"] = True
                        expired.append((job_id, reason_code))
            for job_id, reason_code in expired:
                try:
                    self._cancel_cascade(
                        job_id,
                        reason=reason_code.replace(".", " "),
                        root_reason_code=reason_code,
                    )
                except Exception:
                    _log.exception(
                        "failed to cancel job %s after budget expiry", job_id,
                    )

    def _wake_done(self, job_id: str) -> None:
        with self._lock:
            ev = self._done_events.get(job_id)
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass

    def _lookup_or_load(self, job_id: str) -> Optional[Job]:
        """Find the session for this job (via in-memory map) and load
        the entity from disk."""
        sid = self._find_session_for_job(job_id)
        if not sid:
            return None
        return _store_load(sid, job_id)

    def _find_session_for_job(self, job_id: str) -> Optional[str]:
        with self._lock:
            info = self._jobs.get(job_id)
        if info:
            return info["session_id"]
        # Not in memory — scan disk. Jobs always live under the
        # session repo they were spawned for, so a walk is bounded.
        from openprogram.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return None
        for sdir in sorted(store.root_path.iterdir()):
            if not sdir.is_dir():
                continue
            if (sdir / "jobs.json").exists():
                t = _store_load(sdir.name, job_id)
                if t is not None:
                    return sdir.name
        return None

    def _poll_progress(
        self, job: Job, stop_ev: threading.Event,
    ) -> None:
        """Watch the session for sub-agent messages while the job is
        running and stream the latest message preview into the
        placeholder attach card so the chat row reflects progress
        instead of a static "(running)".

        Best-effort and idle-safe: snapshots the current high-water
        seq as the baseline, then every ~1.5s scans for new nodes
        past that mark. The latest text-bearing node's output (first
        ~300 chars) becomes the attach pointer's preview. Skips itself
        (the placeholder) and runtime-display rows. Broadcasts a
        session reload so the chat view refreshes without polling.
        """
        if not job.attach_pointer_id or not job.parent_session_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            from openprogram.store import SessionNodeWriter
            db = default_db()
            pair = db._open(job.parent_session_id)  # noqa: SLF001
            if pair is None:
                return
            _git, idx = pair
            try:
                baseline_seq = max(
                    (n.seq for n in idx.all_nodes() if n.seq is not None),
                    default=-1,
                )
            except Exception:
                baseline_seq = -1
            last_patched_id: Optional[str] = None
            shim = SessionNodeWriter(db, job.parent_session_id)
        except Exception:
            return
        while not stop_ev.is_set():
            if stop_ev.wait(1.5):
                break
            try:
                pair2 = db._open(job.parent_session_id)  # noqa: SLF001
                if pair2 is None:
                    continue
                _, idx2 = pair2
                latest = None
                for n in idx2.all_nodes():
                    if (n.seq or 0) <= baseline_seq:
                        continue
                    if n.id == job.attach_pointer_id:
                        continue
                    md = n.metadata or {}
                    if md.get("display") == "runtime":
                        continue
                    if not (n.output or "").strip():
                        continue
                    latest = n
                if latest is None or latest.id == last_patched_id:
                    continue
                preview = str(latest.output or "").strip()
                if not preview:
                    continue
                if len(preview) > 600:
                    preview = preview[:600].rstrip() + "…"
                node = idx2.nodes_by_id.get(job.attach_pointer_id)
                if not node:
                    continue
                shim.update(job.attach_pointer_id, output=preview)
                last_patched_id = latest.id
                self.record_job_activity(job.id, "child_progress")
                try:
                    _broadcast_session_reload(
                        job.parent_session_id, reason="job_progress",
                    )
                except Exception:
                    pass
            except Exception:
                pass

    def _update_attach_card(
        self, job: Job, *, error_text: Optional[str] = None,
    ) -> None:
        """Patch the placeholder attach card the spawn path wrote so its
        ``extra.attach`` reflects the final job outcome. Best-effort —
        the attach card pickup path in the existing UI already shows
        ``result.final_text``; this layer adds the job_id linkage
        and status badge.
        """
        if not job.attach_pointer_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            pair = db._open(job.parent_session_id)  # noqa: SLF001
            if pair is None:
                return
            _git, idx = pair
            node = idx.nodes_by_id.get(job.attach_pointer_id)
            if not node:
                return
            md = dict(node.metadata or {})
            extra_raw = md.get("extra")
            try:
                extra_json = json.loads(extra_raw) if isinstance(extra_raw, str) else (extra_raw or {})
            except Exception:
                extra_json = {}
            attach = dict(extra_json.get("attach") or {})
            attach["job_id"] = job.id
            attach["status"] = job.status.value
            if job.head_id:
                attach["head_id"] = job.head_id
            # The human name of the sub-agent ("后端架构"). It lives on the
            # Job, and the attach node is the only thing the graph wire and
            # the transcript both read — without it here every reader falls
            # back to a hex id and the branch has no identity anywhere.
            if job.label or job.subject:
                attach["label"] = job.label or job.subject
            # When the job completes, fill source_commit_id from the
            # ContextCommit that ended up on its branch. The existing
            # _run_spawn does this in synchronous mode — we mirror.
            if job.head_id and not attach.get("source_commit_id"):
                try:
                    from openprogram.context.commit.store import (
                        load_commit_for_head,
                    )
                    src = load_commit_for_head(
                        db, job.parent_session_id, job.head_id,
                    )
                    if src is not None:
                        attach["source_commit_id"] = src.id
                except Exception:
                    pass
            extra_json["attach"] = attach
            md["extra"] = json.dumps(extra_json, default=str)
            # Mirror the same attach dict onto the top-level
            # ``metadata.attach`` field. The frontend's _readAttach
            # helper checks the top-level field first (set by the
            # spawn path) and only falls back to extra-json; if we
            # only patch extra, the panel keeps showing the stale
            # "running" status long after the job completes.
            md["attach"] = attach

            # Stamp the spawned branch's tip with the human label so
            # the Branches panel and DAG figure show "fox-research"
            # instead of the chain-tail fallback name (which picked
            # up the prompt text or assistant reply as a stand-in).
            # run_agent_turn does this too, but the call has slipped
            # through under specific paths — set it here as well so
            # every job → attach finalization guarantees the name.
            if job.label and job.head_id:
                try:
                    db.set_branch_name(
                        job.parent_session_id,
                        job.head_id,
                        job.label,
                    )
                except Exception:
                    pass
            # Hide the spawned sub-branch from the Branches panel
            # once the job completes successfully. Same idea as
            # merge: the sub-agent's content is now reachable from
            # main via the attach pointer, so the standalone branch
            # tip is redundant in the panel. DAG nodes stay
            # intact — a user can still checkout to revisit the
            # sub-agent's history. Only retire on COMPLETED;
            # errored / cancelled jobs remain visible so the user
            # can see what failed.
            if job.head_id and job.status == JobStatus.COMPLETED:
                try:
                    db.mark_merged(job.parent_session_id, [job.head_id])
                except Exception:
                    pass
            # Update the persisted node's metadata + output text.
            output = job.result_text or error_text or node.output or ""
            try:
                from openprogram.store import SessionNodeWriter
                shim = SessionNodeWriter(db, job.parent_session_id)
                shim.update(
                    job.attach_pointer_id,
                    output=output,
                    metadata=md,
                )
            except Exception:
                pass
            # The attach node just changed what the session's graph holds,
            # so both readers of that graph are told at the same point:
            # the DAG re-pulls the session, and the context ring
            # re-estimates. Firing here, at the write, is what keeps a
            # sub-agent finishing from leaving a stale graph on screen.
            _broadcast_session_reload(
                job.parent_session_id, reason="job_attach",
            )
            _refresh_context_stats(job.parent_session_id)
        except Exception:
            pass

    def _finalize_spawn_branch_meta(self, job: Job) -> None:
        """Terminal-state meta for a branch this job CREATED.

        Only the agent tool's spawn form sets ``archive_when_done``
        (deliveries to existing branches leave it False) — nothing here
        runs for them. Archiving stops further send_message / agent(to=)
        deliveries to the branch and keeps its history.
        Best-effort: failures are logged and swallowed.
        """
        if not job.archive_when_done or not job.head_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            default_db().set_branch_meta(
                job.parent_session_id, job.head_id,
                archived=True, archived_at=time.time(),
            )
        except Exception:
            _log.debug(
                "spawn branch meta finalize failed for job %s",
                job.id, exc_info=True,
            )

    def _dispatch_followup(self, job: Job) -> None:
        """Auto-followup: async job finished, nobody's listening on
        the caller session — fire a synthetic user-role turn that
        prompts the parent agent to react to the result.

        A spawn's attach pointer lives in the chain already, so the next
        turn's context-commit generator expands it as
        ``[Attached from branch "X"]:`` items and the LLM sees the
        sub-agent's output naturally. A delivery to an existing branch
        writes no pointer, so its reply travels inline in the
        notification — see ``inline_reply`` below.

        **Anchoring** (dag/overview.md §4): the notification lands at the
        delivery session's HEAD *at injection time* and advances it, so
        N sub-agents finishing produce one serial chain
        ``… → notice₁ → answer₁ → notice₂ → answer₂``. Anchoring at the
        spawning node instead — which is what this used to do — made
        every notification a sibling of the same turn, and one user
        message got answered N times on N parallel branches.

        Runs on a daemon thread so the runner worker doesn't block, and
        holds the delivery session's follow-up lock so two sub-agents
        finishing together still append in sequence rather than both
        reading the same HEAD.
        """
        if not job.parent_session_id:
            return
        label = job.label or job.subject or job.id[:8]
        sub_prompt = (job.prompt or job.description or "").strip()
        # Deliver the reply back to the INITIATOR's session. Same-session
        # spawn: caller_session_id is None → deliver to parent_session_id.
        # Cross-session send_message: deliver to caller_session_id (the
        # sender), NOT the target session the job ran in.
        deliver_session = job.caller_session_id or job.parent_session_id
        # Carry the reply INLINE unless the initiator has an attach pointer
        # to expand. Two things remove one: the job ran in a different
        # session (the pointer, if any, is not on the delivery session's
        # chain), or the job wrote no pointer at all — a delivery to an
        # EXISTING branch (``agent(to=…)``, ``send_message``) creates none,
        # because it spawns nothing to attach. Without this second case a
        # same-session delivery told the initiator "the transcript is
        # attached above" with nothing attached, and the result never
        # arrived.
        inline_reply = bool(
            job.caller_session_id
            and job.caller_session_id != job.parent_session_id
        ) or not job.attach_pointer_id

        def _go():
            try:
                from openprogram.agent.dispatcher import (
                    TurnRequest, process_user_turn,
                )
                from openprogram.agent.authority import runtime_authority
                # Followup prompt — push the parent agent to synthesize a
                # reply, not echo the sub-agent's last line. With an attach
                # pointer the sub-agent transcript is already in context via
                # the attach expansion; without one the reply text has to
                # travel inline.
                sub_request_line = (
                    f"用户原本让子 agent 做的事是：{sub_prompt}\n"
                    if sub_prompt else ""
                )
                reply_block = ""
                if inline_reply:
                    reply_text = (job.result_text or "").strip() or "(无输出)"
                    reply_block = (
                        f"分支 {job.parent_session_id}:"
                        f"{job.head_id or '?'} 的回复是：\n{reply_text}\n\n"
                    )
                if inline_reply:
                    followup_text = (
                        f"[系统消息] 你之前发消息给的另一个分支 \"{label}\" "
                        f"回复了。\n{sub_request_line}{reply_block}"
                        f"请基于这条回复继续——做总结、解读，或决定下一步"
                        f"（继续追问可再调 send_message）。"
                    )
                else:
                    followup_text = (
                        f"[系统消息] 你派发的子 agent \"{label}\" "
                        f"已经跑完了，它完整的对话记录作为附加内容嵌在上面。\n"
                        f"{sub_request_line}"
                        f"现在请你直接面向原始用户给出完整回答，"
                        f"基于子 agent 跑出来的结果做总结、解读、给"
                        f"出后续建议。不要原样复读子 agent 的最后"
                        f"一句话。如果子 agent 的输出已经直接回答"
                        f"了用户问题，用你自己的话重新组织一遍，"
                        f"并补充必要的背景或上下文。"
                    )
                req = TurnRequest(
                    session_id=deliver_session,
                    user_text=followup_text,
                    agent_id=job.agent_id or "main",
                    source="job_followup",
                    **runtime_authority(job, "job_followup"),
                    # branch_from is left at INHERIT_PARENT: the dispatcher
                    # resolves it to the delivery session's HEAD and advances
                    # it, which is exactly the serial chain this method's
                    # docstring describes. Pinning it to the spawning node
                    # is what produced the parallel-branch double answer.
                )
                process_user_turn(req)
            except Exception:
                # Best-effort — don't blow up the runner if the
                # caller session is gone / dispatcher errors.
                pass

        def _serial():
            # A fresh thread starts with empty ContextVars, so the chain
            # state this turn belongs to has to be re-bound by hand or the
            # follow-up looks like a brand-new chain: the message budget
            # would restart at 0 (A↔B ping-pong could never exhaust it)
            # and jobs spawned here would record no parent, escaping the
            # cascade in cancel_job.
            from openprogram.programs.tools.agents.send_message.send_message.depth import (
                set_chain_generations, set_chain_messages,
            )
            # The reply hop costs what the child already spent — an
            # explicit send_message reply lands at the same count.
            set_chain_messages(int(job.chain_messages or 0))
            # Generations are the dispatcher's, not the child's: this
            # turn is the dispatcher reading a result, and reading a
            # result creates nobody. Binding the child's count instead
            # left an agent that read one worker's reply unable to
            # create any further agent in that chain, which is exactly
            # the "dispatch a batch, read it, dispatch the next batch"
            # shape the whole tool exists for.
            set_chain_generations(int(job.caller_chain_generations or 0))
            # The follow-up continues the DISPATCHER's work, not the
            # finished job's, so it chains where the job did. None for a
            # job spawned from a plain user turn, which had no job either.
            _current_job_id.set(job.parent_job_id)
            # One follow-up at a time per delivery session: the next one
            # reads a HEAD that already includes the previous answer.
            with self._followup_lock(deliver_session):
                _go()

        threading.Thread(target=_serial, daemon=True).start()

    def _schedule_force_cancel(
        self, session_id: str, job_id: str, lease_generation: int,
    ) -> None:
        """Report slow cancellation without releasing a live worker claim."""
        def _watch():
            time.sleep(_CANCEL_TIMEOUT_SECS)
            cur = _store_load(session_id, job_id)
            if cur is None or is_terminal(cur.status):
                return
            _log.warning(
                "job %s cancellation exceeded %.1fs; retaining live claim "
                "for lease generation %s",
                job_id, _CANCEL_TIMEOUT_SECS, lease_generation,
            )
        threading.Thread(target=_watch, daemon=True).start()


# Module-level singleton

_runner_lock = threading.Lock()
_runner: Optional[JobRunner] = None


def get_runner() -> JobRunner:
    """Process-wide JobRunner. Idempotent."""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = JobRunner()
        return _runner


def shutdown_runner() -> None:
    """Tear down the singleton (mainly for tests)."""
    global _runner
    with _runner_lock:
        if _runner is not None:
            try:
                _runner.shutdown(wait=False)
            except Exception:
                pass
            _runner = None


__all__ = [
    "NonPreemptibleOperation",
    "JobGovernanceContext",
    "JobRunner",
    "get_runner",
    "shutdown_runner",
]
