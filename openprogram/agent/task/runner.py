"""TaskRunner — ThreadPoolExecutor-backed worker pool.

Process-wide singleton. Tasks are submitted via :meth:`spawn_task`,
which returns immediately with a task id. The actual work runs in
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

Cancel events on the *task* level are stored in this runner's
``_cancel_events`` map (keyed by ``task_id``), in addition to the
session-level events the dispatcher already maintains. We set both
on cancel so:

  * session-level (existing behavior) — the cancel hook +
    asyncio bridge inside ``process_user_turn`` already keys off the
    session id, so they fire.
  * task-level (new) — if a runner is later upgraded to allow >1
    task per session, task-level cancel still scopes correctly.

Crash recovery: :func:`store.reconcile_orphans` runs at process start
(lazily, on first runner construction). Existing tasks left in
non-terminal state are flipped to ``errored``.

Broadcast events: each state transition fires a WS broadcast via
``openprogram.webui.server._broadcast`` (lazy import) so the UI
updates without an explicit poll. We also fire a ``session_reload``
on terminal so the existing attach card pickup path triggers.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

from openprogram.agent.task.store import (
    list_tasks as _store_list,
    load_task as _store_load,
    reconcile_orphans as _store_reconcile,
    save_task as _store_save,
    update_task_status as _store_update_status,
)
from openprogram.events import emit_safe
from openprogram.agent.task.types import (
    Task,
    TaskStatus,
    is_terminal,
    mint_task_id,
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

# Task id of the task currently executing on this context. Bound by
# ``_run_one`` for the duration of the child turn; read by ``spawn_task``
# to default ``parent_task_id`` so tasks spawned from inside a running
# task record their spawn chain. ``cancel_task`` walks that chain for
# cascading cancel. Propagates into tool threads because both
# ``contextvars.copy_context`` (this runner) and ``asyncio.to_thread``
# carry ContextVars across.
_current_task_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openprogram_current_task_id", default=None,
)
_current_task_runner: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "openprogram_current_task_runner", default=None,
)


def record_current_task_activity(activity_kind: str) -> bool:
    runner = _current_task_runner.get()
    task_id = _current_task_id.get()
    return bool(runner and task_id and runner.record_task_activity(task_id, activity_kind))


def current_task_operation_timeout(
    declared_timeout: float | None,
) -> float | None:
    runner = _current_task_runner.get()
    task_id = _current_task_id.get()
    if runner is None or task_id is None:
        return declared_timeout
    return runner.bounded_operation_timeout(task_id, declared_timeout)


def _broadcast(payload: dict) -> None:
    """Send a WS frame to the frontend — best-effort.

    步 4：不再 import webui。把现成的帧 emit 到总线（``ws.frame`` 事件），
    webui 作为订阅者原样广播。帧内容（type / data 字段）一字不变，前端无感。
    """
    from openprogram.events import emit_ws_frame
    emit_ws_frame(payload)


def _broadcast_session_reload(session_id: str, *, reason: str = "task") -> None:
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


def _broadcast_task_status(task: Task) -> None:
    _broadcast({
        "type": "task_status",
        "data": {
            "task_id": task.id,
            "session_id": task.parent_session_id,
            "status": task.status.value,
            "parent_msg_id": task.parent_msg_id,
            "target_branch_head_id": task.target_branch_head_id,
            "head_id": task.head_id,
            "label": task.label,
            "subject": task.subject,
            "error": task.error,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
        },
    })
    # 事件层 tap：状态转移的单一漏斗，RUNNING → subagent.started，
    # 终止态 → subagent.ended。worker 线程里 ContextVar 不可靠，session 显式给。
    if task.status == TaskStatus.RUNNING:
        emit_safe(
            "subagent.started", "system",
            {"task_id": task.id, "label": task.label},
            {"session": task.parent_session_id},
        )
    elif is_terminal(task.status):
        emit_safe(
            "subagent.ended", "system",
            {"task_id": task.id, "status": task.status.value, "error": task.error},
            {"session": task.parent_session_id},
        )


class TaskRunner:
    """Singleton task pool. Use :func:`get_runner`.

    Public surface:

      * :meth:`spawn_task` — submit, return task_id
      * :meth:`cancel_task` — set cancel event, schedule timeout,
        cascade to descendant tasks (parent_task_id chain)
      * :meth:`get_task` / :meth:`list_tasks` — read
      * :meth:`await_task` — block until terminal, return final Task

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
                    os.environ.get("OPENPROGRAM_TASK_WORKERS")
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
        self._instance_id = f"worker_{os.getpid()}_{uuid.uuid4().hex}"
        self._dispatch_wake = threading.Event()
        self._shutdown_event = threading.Event()
        # Reconcile orphans before opening the pool so any "running"
        # task from a previous process is flipped to errored. The
        # state-machine transition rules cover (running, errored).
        try:
            _store_reconcile(legacy_only=True)
        except Exception:
            pass
        self._reconcile_resources()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="op-task",
        )
        self._lock = threading.Lock()
        # task_id → {"event": Event, "future": Future, "session_id": str}
        self._tasks: dict[str, dict[str, Any]] = {}
        # task_id → threading.Event used to wake await_task() callers.
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
            name="op-task-dispatcher",
        )
        self._dispatcher_thread.start()
        self._reconciler_thread = threading.Thread(
            target=self._reconcile_loop,
            daemon=True,
            name="op-task-reconciler",
        )
        self._reconciler_thread.start()
        self._budget_thread = threading.Thread(
            target=self._budget_loop,
            daemon=True,
            name="op-task-budget",
        )
        self._budget_thread.start()

    def _followup_lock(self, session_id: str) -> threading.Lock:
        """The per-session follow-up lock, created on first use."""
        with self._lock:
            lk = self._followup_locks.get(session_id)
            if lk is None:
                lk = threading.Lock()
                self._followup_locks[session_id] = lk
            return lk

    # Public API

    def admit_task_entity(
        self,
        task: Task,
        *,
        creates_agent: bool,
        caller_turn_id: str | None = None,
    ):
        """Durably admit and publish one queued Task, without executing it."""
        from openprogram.agent.resource_governance import AdmissionRejected

        decision = self._governor.admit_task(
            task,
            persist=lambda accepted: _store_save(task.parent_session_id, accepted),
            creates_agent=creates_agent,
            caller_turn_id=caller_turn_id,
        )
        if not decision.accepted:
            raise AdmissionRejected(decision)
        return decision

    def spawn_task(
        self,
        session_id: str,
        prompt: str,
        agent_id: str,
        *,
        subject: str = "",
        description: str = "",
        context_mode: str = "inherit",
        parent_msg_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
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
        task_id: Optional[str] = None,
        authority: Optional[dict] = None,
        creates_agent: bool = True,
    ) -> str:
        """Create a Task entity, persist it, queue it on the pool.

        Returns ``task_id`` immediately. The task pickup happens on
        a worker thread and walks through the state machine. The
        caller can ``await_task(task_id)`` to block on completion.

        ``caller_session_id`` (cross-session messaging): the session the
        reply should be delivered back to. Defaults to ``session_id``
        (the task runs and replies in the caller's own session).

        ``task_id``: reuse a pre-created pending Task (a tracked
        dispatch that sat in the target's inbox while it was busy).
        The pre-created entity's dispatch-time facts (parent_task_id,
        created_at) survive the resubmission — the inbox drain runs on
        the TARGET's thread, whose ambient task context is not the
        dispatcher's. A terminal pre-created task (withdrawn while
        queued) is NOT resurrected: the id is returned untouched.
        """
        existing: Optional[Task] = None
        if task_id:
            existing = _store_load(session_id, task_id)
            if existing is not None and is_terminal(existing.status):
                return task_id
        if parent_task_id is None:
            if existing is not None:
                parent_task_id = existing.parent_task_id
            else:
                # Spawned from inside a running task's turn — record the
                # chain so cascading cancel can find this child.
                parent_task_id = _current_task_id.get()
        from openprogram.agent.authority import normalize_authority
        task_authority = normalize_authority(authority or existing or {})
        task = Task(
            id=task_id or mint_task_id(),
            parent_session_id=session_id,
            prompt=prompt,
            agent_id=agent_id,
            **task_authority,
            subject=subject or (prompt[:60] or "task"),
            description=description or prompt,
            context_mode=context_mode if context_mode in ("inherit", "clean") else "inherit",
            parent_msg_id=parent_msg_id,
            parent_task_id=parent_task_id,
            label=label,
            attach_pointer_id=attach_pointer_id,
            target_branch_head_id=target_branch_head_id,
            worktree_id=worktree_id,
            wait=wait,
            caller_msg_id=caller_msg_id,
            caller_session_id=caller_session_id,
            chain_messages=chain_messages,
            chain_generations=chain_generations,
            caller_chain_generations=caller_chain_generations,
            archive_when_done=archive_when_done,
            status=TaskStatus.PENDING,
            created_at=existing.created_at if existing is not None else time.time(),
        )
        decision = self.admit_task_entity(
            task,
            creates_agent=creates_agent,
            caller_turn_id=caller_msg_id,
        )
        if decision.idempotent:
            with self._lock:
                if task.id in self._tasks:
                    return task.id
            task = _store_load(session_id, task.id) or task
        _broadcast_task_status(task)

        # Done-event for await_task / await_tasks callers.
        done_ev = threading.Event()
        cancel_ev = threading.Event()
        # Copy the current ContextVars so things like
        # ``run_control._current_session_id`` set by the spawning
        # thread don't leak into the worker. Each task gets its own
        # context — the worker function rebinds session_id explicitly.
        ctx = contextvars.copy_context()
        # Register *before* submitting: a fast task can reach the
        # finally-pop in _run_one before this thread gets the lock,
        # which would leave the entry orphaned in _tasks forever.
        # "future" is filled in right after submit, under the same lock.
        entry: dict = {
            "event": cancel_ev,
            "future": None,
            "session_id": session_id,
            "context": ctx,
        }
        with self._lock:
            self._tasks[task.id] = entry
            self._done_events[task.id] = done_ev
        self._dispatch_wake.set()

        return task.id

    def cancel_task(self, task_id: str, *, reason: Optional[str] = None) -> Optional[Task]:
        return self._cancel_cascade(
            task_id, reason=reason, root_reason_code="cancel.user",
        )

    def _cancel_cascade(
        self,
        task_id: str,
        *,
        reason: Optional[str],
        root_reason_code: str,
    ) -> Optional[Task]:
        """Cancel ``task_id`` and every descendant task on its
        ``parent_task_id`` chain (cascading cancel). Returns the
        (post-update) Task entity for ``task_id``, or None if not found.

        Descendants are collected breadth-first over the persisted task
        entities with a visited-set guard (a cycle in parent_task_id
        would otherwise loop forever). Pending/queued descendants flip
        straight to cancelled; running ones go through the same
        per-task cancel path as the root.

        Descendants are cancelled BEFORE the root. Cancelling the root
        makes its worker drop out, which frees a pool slot, and a queued
        descendant gets picked up in that slot — running a task that the
        cascade was about to cancel. Signalling descendants first means
        the worker finds an already-terminal entity and bails without
        calling ``run_agent_turn``.
        """
        # Unknown root: return None without touching anything, same as
        # before. The lookup is free for a task on the pool.
        if self._find_session_for_task(task_id) is None:
            return None
        cascade_reason = reason or f"parent task {task_id} cancelled"
        for child in self._descendant_tasks(task_id):
            if is_terminal(child.status):
                continue
            try:
                self._cancel_single(
                    child.id, reason=cascade_reason, reason_code="cancel.parent",
                )
            except Exception:
                pass
        return self._cancel_single(
            task_id, reason=reason, reason_code=root_reason_code,
        )

    def _descendant_tasks(self, root_task_id: str) -> list[Task]:
        """All tasks reachable from ``root_task_id`` via parent_task_id,
        breadth-first, cycle-safe. Terminal ancestors are still
        traversed — a completed child may have spawned a grandchild
        that is still running."""
        children: dict[str, list[Task]] = {}
        for t in self.list_tasks():
            if t.parent_task_id:
                children.setdefault(t.parent_task_id, []).append(t)
        out: list[Task] = []
        seen = {root_task_id}
        queue = [root_task_id]
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
        task_id: str,
        *,
        reason: Optional[str] = None,
        reason_code: str = "cancel.user",
    ) -> Optional[Task]:
        """Cancel one task, no cascade. Returns the (post-update)
        Task entity, or None if not found.

        Effect:

          * sets the task's cancel event (worker drops out on next
            cooperative checkpoint)
          * sets the session-level cancel event via
            ``run_control.mark_cancelled`` so the existing dispatcher
            cancel path fires
          * kills any active BashTool subprocess via
            ``kill_active_runtime``
          * if the task is still in pending/queued, flips to cancelled
            immediately (no worker pickup yet, nothing to wait for)
          * if running, schedules a 30s watchdog that force-flips to
            cancelled if the worker hasn't honoured the signal
        """
        with self._lock:
            info = self._tasks.get(task_id)
        if not info:
            # Maybe loaded from disk-only state — try to find session.
            cur = self._find_session_for_task(task_id)
            if cur is None:
                return None
            session_id = cur
            info = None
        else:
            session_id = info["session_id"]
        if info is None:
            # Not on the pool. A queued task with no pool entry is a
            # tracked dispatch still queued in the target's inbox
            # (agent(to=…) hit a busy target) — withdraw it: pull the
            # inbox entry and flip the entity. NO session-level cancel
            # here: the target is busy running someone ELSE's turn,
            # which withdrawing a queued task must not kill.
            queued_task = _store_load(session_id, task_id)
            if queued_task is not None and queued_task.status in (
                TaskStatus.PENDING, TaskStatus.QUEUED,
            ):
                try:
                    from openprogram.agent import inbox
                    inbox.discard_task(session_id, task_id)
                except Exception:
                    pass
                try:
                    self._governor.request_stop(task_id, reason_code)
                    updated = _store_update_status(
                        session_id, task_id, TaskStatus.CANCELLED,
                        cancel_requested_at=time.time(),
                        error=reason or "withdrawn before delivery",
                        reason_code=reason_code,
                    )
                except ValueError:
                    updated = _store_load(session_id, task_id)
                if updated is not None:
                    _broadcast_task_status(updated)
                    self._wake_done(task_id)
                return updated
        cur_task = _store_load(session_id, task_id)
        if cur_task is None:
            return None
        if cur_task.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
            if info is not None:
                info["event"].set()
            self._governor.request_stop(task_id, reason_code)
            try:
                updated = _store_update_status(
                    session_id, task_id, TaskStatus.CANCELLED,
                    cancel_requested_at=time.time(),
                    error=reason or "cancelled before pickup",
                    reason_code=reason_code,
                )
            except ValueError:
                updated = _store_load(session_id, task_id)
            if updated is not None:
                _broadcast_task_status(updated)
                self._wake_done(task_id)
                self._update_attach_card(updated)
                _broadcast_session_reload(session_id, reason="task_cancelled")
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
            mark_cancelled(session_id)
            kill_active_runtime(session_id)
        except Exception:
            pass
        if info is not None:
            info["event"].set()

        # Status-side: if still pending/queued, flip terminal now.
        # If running, leave terminal flip to the worker (or the
        # watchdog).
        self._governor.request_stop(task_id, reason_code)
        try:
            if cur_task.status == TaskStatus.RUNNING:
                # Stamp request time but stay in running. Worker will
                # detect cancel and self-flip.
                #
                # This MUST go through the store's locked
                # read-modify-write, not save_task: the worker was
                # signalled a few lines above and can reach its own
                # terminal write inside the window since _store_load.
                # A blind save of the snapshot we read then rewrote
                # ``status: running`` over the worker's ``cancelled``,
                # resurrecting a finished task — the row stayed
                # non-terminal forever (phantom "running" in the task
                # panel, reconciled to "worker died before completion"
                # on next startup). update_task_status applies the
                # field under the same lock and raises on a status that
                # has since moved on.
                _store_update_status(
                    session_id, task_id, TaskStatus.RUNNING,
                    cancel_requested_at=time.time(),
                    reason_code=reason_code,
                )
                # Watchdog: force cancel if worker doesn't honour signal.
                lease_generation = info.get("lease_generation") if info else None
                if lease_generation is not None:
                    self._schedule_force_cancel(
                        session_id, task_id, lease_generation,
                    )
                return _store_load(session_id, task_id) or cur_task
        except ValueError:
            # The worker moved the task on while we were stamping —
            # its state is the truthful one.
            return _store_load(session_id, task_id) or cur_task
        return cur_task

    def get_task(self, task_id: str) -> Optional[Task]:
        sid = self._find_session_for_task(task_id)
        if not sid:
            return None
        return _store_load(sid, task_id)

    def list_tasks(
        self,
        session_id: Optional[str] = None,
        *,
        status_filter: Optional[set[TaskStatus]] = None,
        limit: Optional[int] = None,
    ) -> list[Task]:
        if session_id:
            return _store_list(session_id, status_filter=status_filter, limit=limit)
        # Walk every session — used by the global task panel.
        from openprogram.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return []
        out: list[Task] = []
        for sdir in sorted(store.root_path.iterdir()):
            if not sdir.is_dir():
                continue
            out.extend(_store_list(sdir.name, status_filter=status_filter))
        out.sort(key=lambda t: t.created_at or 0, reverse=True)
        if limit is not None:
            out = out[:limit]
        return out

    def await_task(self, task_id: str, timeout: Optional[float] = None) -> Optional[Task]:
        """Block the calling thread until the task reaches terminal.

        Returns the final Task. Returns None on unknown task. Returns
        the current (possibly non-terminal) entity on timeout.
        """
        cur = self.get_task(task_id)
        if cur is None:
            return None
        if is_terminal(cur.status):
            return cur
        with self._lock:
            done = self._done_events.get(task_id)
        if done is None:
            # Lost track (process restart with persisted task) — poll.
            deadline = time.time() + (timeout or 60.0)
            while time.time() < deadline:
                cur = self.get_task(task_id)
                if cur is not None and is_terminal(cur.status):
                    return cur
                time.sleep(0.5)
            return self.get_task(task_id)
        done.wait(timeout=timeout)
        return self.get_task(task_id)

    def record_task_activity(self, task_id: str, activity_kind: str) -> bool:
        with self._lock:
            entry = self._tasks.get(task_id)
            lease_generation = (
                entry.get("lease_generation") if entry is not None else None
            )
        if lease_generation is None:
            return False
        recorded = self._governor.record_activity(
            task_id,
            owner_instance_id=self._instance_id,
            lease_generation=lease_generation,
            activity_kind=activity_kind,
        )
        if not recorded:
            return False
        lineage = [task_id]
        current = self.get_task(task_id)
        seen = {task_id}
        while current is not None and current.parent_task_id:
            parent_id = current.parent_task_id
            if parent_id in seen:
                break
            seen.add(parent_id)
            lineage.append(parent_id)
            current = self.get_task(parent_id)
        now = self._monotonic()
        with self._lock:
            for lineage_task_id in lineage:
                entry = self._tasks.get(lineage_task_id)
                if entry is not None and entry.get("started_monotonic") is not None:
                    entry["last_activity_monotonic"] = now
        return True

    def _finalize_task_status(
        self,
        session_id: str,
        task_id: str,
        lease_generation: int,
        status: TaskStatus,
        reason_code: str,
        **fields: Any,
    ) -> Optional[Task]:
        terminal: dict[str, Task] = {}
        try:
            self._governor.finalize_task(
                task_id, reason_code,
                owner_instance_id=self._instance_id,
                lease_generation=lease_generation,
                mutate=lambda: terminal.setdefault(
                    "task", _store_update_status(
                        session_id, task_id, status,
                        reason_code=reason_code, **fields,
                    ),
                ),
            )
        except ValueError:
            return None
        return terminal.get("task")

    def bounded_operation_timeout(
        self, task_id: str, declared_timeout: float | None,
    ) -> float | None:
        if declared_timeout is not None and declared_timeout <= 0:
            raise ValueError("declared timeout must be positive")
        runtime_limit, idle_limit = self._governor.task_time_limits(task_id)
        strict = runtime_limit is not None or idle_limit is not None
        bounds = [] if declared_timeout is None else [float(declared_timeout)]
        with self._lock:
            entry = self._tasks.get(task_id)
            snapshot = dict(entry) if entry is not None else None
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
                "strict time-budget task requires a live bounded operation",
            )
        return min(bounds) if bounds else None

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
        """Submit only durably claimed tasks to the executor."""
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
                    )
                except Exception:
                    self._executor_slots.release()
                    _log.exception("failed to claim next durable task")
                    break
                if claim is None:
                    self._executor_slots.release()
                    break
                time_limits = self._governor.task_time_limits(claim.task_id)
                claimed_monotonic = self._monotonic()
                with self._lock:
                    entry = self._tasks.get(claim.task_id)
                    if entry is None:
                        cancel_ev = threading.Event()
                        done_ev = self._done_events.setdefault(
                            claim.task_id, threading.Event(),
                        )
                        entry = {
                            "event": cancel_ev,
                            "future": None,
                            "session_id": claim.session_id,
                            "context": contextvars.copy_context(),
                        }
                        self._tasks[claim.task_id] = entry
                    else:
                        cancel_ev = entry["event"]
                        done_ev = self._done_events[claim.task_id]
                    ctx = entry["context"]
                from openprogram.agent.run_control import claim_cancel_event
                if not claim_cancel_event(claim.session_id, cancel_ev):
                    requeued = self._governor.requeue_task(
                        claim.task_id,
                        owner_instance_id=self._instance_id,
                        lease_generation=claim.lease_generation,
                    )
                    if not requeued:
                        released, reason_code = self._governor.release_stopping_task(
                            claim.task_id,
                            owner_instance_id=self._instance_id,
                            lease_generation=claim.lease_generation,
                        )
                        if released:
                            current = _store_load(
                                claim.session_id, claim.task_id,
                            )
                            if current is not None and not is_terminal(current.status):
                                try:
                                    current = _store_update_status(
                                        claim.session_id, claim.task_id,
                                        TaskStatus.CANCELLED,
                                        error="cancelled before execution",
                                        reason_code=reason_code or "cancel.concurrent",
                                    )
                                except ValueError:
                                    current = _store_load(
                                        claim.session_id, claim.task_id,
                                    )
                            if current is not None:
                                _broadcast_task_status(current)
                            self._wake_done(claim.task_id)
                            with self._lock:
                                self._tasks.pop(claim.task_id, None)
                                self._done_events.pop(claim.task_id, None)
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
                        ctx.run, self._run_one, claim.task_id, claim.session_id,
                        cancel_ev, done_ev, claim.lease_generation,
                    )
                except Exception:
                    from openprogram.agent.run_control import unregister_cancel_event
                    unregister_cancel_event(claim.session_id, cancel_ev)
                    self._executor_slots.release()
                    self._governor.release_task(
                        claim.task_id, "error.dispatch_failed",
                        owner_instance_id=self._instance_id,
                        lease_generation=claim.lease_generation,
                    )
                    with self._lock:
                        self._tasks.pop(claim.task_id, None)
                        self._done_events.pop(claim.task_id, None)
                    _log.exception("failed to submit claimed task %s", claim.task_id)
                    continue
                with self._lock:
                    if self._tasks.get(claim.task_id) is entry:
                        entry["future"] = future

    def _run_one(
        self, task_id: str, claimed_session_id: str,
        cancel_ev: threading.Event, done_ev: threading.Event,
        lease_generation: int,
    ) -> None:
        """Worker thread entry point.

        Wraps :func:`run_agent_turn` so the same code that handles the
        synchronous ``/spawn`` path runs underneath us. Catches
        everything so a buggy tool doesn't leave the task pinned at
        ``running`` forever — exceptions flip to ``errored``.

        Important: the dispatcher's cancel hook reads
        ``run_control._current_session_id`` from the worker thread
        ContextVar. We bind it at entry so the hook can find the
        right session.
        """
        # Look up the task entity at entry — fields like
        # parent_session_id, prompt, agent_id are stable from this
        # point forward.
        task = self._lookup_or_load(task_id)
        if task is None:
            from openprogram.agent.run_control import unregister_cancel_event
            unregister_cancel_event(claimed_session_id, cancel_ev)
            self._governor.release_task(
                task_id, "error.task_missing",
                owner_instance_id=self._instance_id,
                lease_generation=lease_generation,
            )
            self._executor_slots.release()
            self._dispatch_wake.set()
            done_ev.set()
            with self._lock:
                self._tasks.pop(task_id, None)
                self._done_events.pop(task_id, None)
            return
        session_id = task.parent_session_id

        # Bind the session id ContextVar for the cancel hook. Same
        # contract _execute_in_context honours in the webui worker.
        from openprogram.agent.run_control import (
            unregister_cancel_event,
            set_current_session_id,
            reset_current_session_id,
        )
        sid_token = set_current_session_id(session_id)
        # Bind the running task id so spawns made inside this child turn
        # record parent_task_id (cascading cancel walks that chain).
        _task_id_token = _current_task_id.set(task_id)
        _runner_token = _current_task_runner.set(self)
        # If this task is bound to an agent worktree, bind the
        # _current_worktree_path ContextVar so bash / edit / write /
        # read use it as default cwd. Reset is handled in the finally
        # below via a token, mirroring the session-id pattern.
        _wt_token = None
        if task.worktree_id:
            try:
                from openprogram.worktree.context import set_worktree as _set_wt
                from openprogram.worktree.manager import get_manager as _get_wt_mgr
                wt = _get_wt_mgr().get_worktree(task.worktree_id)
                if wt is not None:
                    _wt_token = _set_wt(wt.worktree_path)
            except Exception:
                _wt_token = None

        lease_stop = threading.Event()
        lease_thread = threading.Thread(
            target=self._renew_task_lease,
            args=(task_id, lease_generation, lease_stop),
            daemon=True,
            name=f"op-task-lease-{task_id}",
        )
        lease_thread.start()

        try:
            # pending → running. If state went to cancelled (pre-pickup)
            # the transition fails — bail out cleanly.
            try:
                updated = _store_update_status(
                    session_id, task_id, TaskStatus.RUNNING,
                    started_at=time.time(),
                )
                if updated is None:
                    # task entity vanished
                    return
                _broadcast_task_status(updated)
            except ValueError:
                # Transition rejected — likely already terminal. Done.
                return
            if cancel_ev.is_set():
                # Cancel arrived between queue + pickup.
                updated = self._finalize_task_status(
                    session_id, task_id, lease_generation,
                    TaskStatus.CANCELLED, "cancel.user",
                    error="cancelled before run",
                )
                if updated is not None:
                    _broadcast_task_status(updated)
                return

            # Progress poller — while the sub-agent is grinding, patch
            # the placeholder attach card's preview text with the
            # latest sub-agent message so the chat row stops reading
            # "(running)" forever. Runs on a daemon thread; stop_ev
            # is set in the finally block once run_agent_turn returns.
            stop_progress = threading.Event()
            progress_thread: Optional[threading.Thread] = None
            if task.attach_pointer_id:
                progress_thread = threading.Thread(
                    target=self._poll_progress,
                    args=(task, stop_progress),
                    daemon=True,
                )
                progress_thread.start()
            try:
                from openprogram.agent.sub_agent_run import (
                    run_agent_turn,
                )
                # Resolve parent for inherit-mode: walk through to the
                # parent_msg_id supplied at spawn time.
                branch_from: Optional[str]
                if (task.context_mode or "inherit") == "clean":
                    branch_from = None
                else:
                    branch_from = task.parent_msg_id
                # Bind both chain counters so send_message / agent calls
                # made INSIDE this child turn see the right budgets and
                # the guards can trip (send_message §5.1). A spawned
                # child arrives with one more generation than its
                # dispatcher; a delivery to an existing agent arrives
                # with the same generation count it was sent at.
                _chain_tokens: list = []
                try:
                    from openprogram.functions.tools.send_message.send_message.depth import (
                        set_chain_generations, set_chain_messages,
                    )
                    _chain_tokens = [
                        set_chain_messages(int(task.chain_messages or 0)),
                        set_chain_generations(int(task.chain_generations or 0)),
                    ]
                except Exception:
                    pass
                try:
                    from openprogram.agent.authority import normalize_authority
                    _turn_kwargs = dict(
                        session_id=session_id,
                        prompt=task.prompt,
                        agent_id=task.agent_id,
                        branch_from=branch_from,
                        label=task.label,
                        # clean mode = new branch → its root's caller = the
                        # spawning node, so it's an explicit spawn (not
                        # seq-stitched into a sibling). dag/overview.md §2.3.
                        spawn_caller=task.caller_msg_id if branch_from is None else None,
                        # Same-session spawn: never steal the head.
                        advance_head=False,
                    )
                    _task_authority = normalize_authority(task)
                    if _task_authority:
                        _turn_kwargs["authority"] = _task_authority
                    result = run_agent_turn(**_turn_kwargs)
                finally:
                    for _tok in _chain_tokens:
                        try:
                            _tok.var.reset(_tok)
                        except Exception:
                            pass
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                updated = self._finalize_task_status(
                    session_id, task_id, lease_generation,
                    TaskStatus.ERRORED, "error.execution", error=err,
                )
                if updated is not None:
                    _broadcast_task_status(updated)
                    self._update_attach_card(updated, error_text=err)
                _broadcast_session_reload(session_id, reason="task_errored")
                return
            finally:
                stop_progress.set()
                if progress_thread is not None:
                    try:
                        progress_thread.join(timeout=1.0)
                    except Exception:
                        pass

            # Decide terminal status.
            self.record_task_activity(task_id, "terminal")
            cancelled = cancel_ev.is_set() or (
                result.error and "stopped" in (result.error or "").lower()
            )
            if cancelled:
                new_status = TaskStatus.CANCELLED
            elif result.failed:
                new_status = TaskStatus.ERRORED
            else:
                new_status = TaskStatus.COMPLETED
            current_reason = (_store_load(session_id, task_id) or task).reason_code
            reason_code = (
                (current_reason or "cancel.user")
                if new_status == TaskStatus.CANCELLED
                else "error.execution" if new_status == TaskStatus.ERRORED
                else "completed"
            )
            updated = self._finalize_task_status(
                session_id, task_id, lease_generation, new_status, reason_code,
                head_id=result.head_id,
                result_text=result.final_text or "",
                error=result.error,
            )
            if updated is not None:
                _broadcast_task_status(updated)
                self._update_attach_card(updated)
                # Auto-followup: when an async task completes (or
                # errors / is cancelled), nobody is listening unless
                # we explicitly nudge the caller's session. Fire a
                # follow-up LLM turn that says "task X is done" — the
                # next turn's context will include the attach pointer
                # the runner just wrote, so the agent naturally sees
                # the sub-agent's output and can react.
                #
                # Skip when wait=True (sync path doesn't need it —
                # the caller is already blocked on the result).
                if new_status == TaskStatus.COMPLETED and not updated.wait:
                    self._dispatch_followup(updated)
                # Spawn-branch bookkeeping at terminal state, AFTER the
                # result flowed back: archive the branch when the spawn
                # asked for archive_when_done. Best-effort — a meta
                # write failure must never affect the result path.
                self._finalize_spawn_branch_meta(updated)
            # Tell tail clients the session changed so attach card
            # picks up the new head / text.
            _broadcast_session_reload(session_id, reason=f"task_{new_status.value}")
        finally:
            lease_stop.set()
            lease_thread.join(timeout=1.0)
            try:
                # Pass our Event: if a newer turn (e.g. a chat turn the
                # user started while this task ran) has re-registered,
                # its token must survive our teardown or its Stop dies.
                unregister_cancel_event(session_id, cancel_ev)
            except Exception:
                pass
            try:
                reset_current_session_id(sid_token)
            except Exception:
                pass
            try:
                _current_task_id.reset(_task_id_token)
            except Exception:
                pass
            try:
                _current_task_runner.reset(_runner_token)
            except Exception:
                pass
            if _wt_token is not None:
                try:
                    from openprogram.worktree.context import reset_worktree
                    reset_worktree(_wt_token)
                except Exception:
                    pass
            # If the task was cancelled (D15) and it owned a worktree,
            # auto-discard the worktree. Completion / error → leave the
            # worktree alone so the parent agent or user can decide
            # what to do with it.
            try:
                cur = _store_load(session_id, task_id)
                if (cur is not None
                        and cur.status == TaskStatus.CANCELLED
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
                cur = _store_load(session_id, task_id)
                self._governor.release_task(
                    task_id, cur.reason_code if cur is not None else None,
                    owner_instance_id=self._instance_id,
                    lease_generation=lease_generation,
                )
            except Exception:
                _log.exception("failed to release resource admission for %s", task_id)
            self._wake_done(task_id)
            with self._lock:
                self._tasks.pop(task_id, None)
                # Drop the done-event too, else it leaks one Event per
                # task for the process lifetime. Waiters already hold a
                # reference (await_task reads it before waiting) and it
                # is set by _wake_done above; anyone arriving later sees
                # the task is terminal and returns without waiting.
                self._done_events.pop(task_id, None)
            self._executor_slots.release()
            self._dispatch_wake.set()

    # Internals

    def _renew_task_lease(
        self, task_id: str, lease_generation: int, stop: threading.Event,
    ) -> None:
        while not stop.wait(_LEASE_RENEW_SECS):
            try:
                if not self._governor.renew_lease(
                    task_id, owner_instance_id=self._instance_id,
                    lease_generation=lease_generation,
                ):
                    return
            except Exception:
                _log.exception("failed to renew resource lease for %s", task_id)
                return

    def _owner_holds_worker_lock(self, owner_instance_id: str) -> bool:
        if owner_instance_id == self._instance_id:
            return True
        try:
            owner_pid = int(owner_instance_id.split("_", 2)[1])
        except (IndexError, ValueError):
            return False
        try:
            from openprogram.worker.lock import read_holder_pid
            return read_holder_pid() == owner_pid
        except Exception:
            return False

    @staticmethod
    def _mark_worker_lost(session_id: str, task_id: str) -> None:
        task = _store_load(session_id, task_id)
        if task is None or is_terminal(task.status):
            return
        try:
            _store_update_status(
                session_id, task_id, TaskStatus.ERRORED,
                error="worker died before completion",
                reason_code="error.worker_lost",
            )
        except ValueError:
            return

    def _reconcile_resources(self) -> None:
        try:
            result = self._governor.reconcile(
                task_lookup=lambda session_id, task_id: _store_load(
                    session_id, task_id,
                ),
                mark_worker_lost=self._mark_worker_lost,
                owner_is_alive=self._owner_holds_worker_lock,
            )
        except Exception:
            _log.exception("failed to reconcile durable task resources")
            return
        if (
            result.finalized_preparing
            or result.released_missing
            or result.released_worker_lost
        ):
            self._dispatch_wake.set()

    def _reconcile_loop(self) -> None:
        while not self._shutdown_event.wait(_RECONCILE_SECS):
            self._reconcile_resources()

    def _budget_loop(self) -> None:
        while not self._shutdown_event.wait(self._budget_poll_seconds):
            now = self._monotonic()
            expired: list[tuple[str, str]] = []
            with self._lock:
                for task_id, entry in self._tasks.items():
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
                        expired.append((task_id, reason_code))
            for task_id, reason_code in expired:
                try:
                    self._cancel_cascade(
                        task_id,
                        reason=reason_code.replace(".", " "),
                        root_reason_code=reason_code,
                    )
                except Exception:
                    _log.exception(
                        "failed to cancel task %s after budget expiry", task_id,
                    )

    def _wake_done(self, task_id: str) -> None:
        with self._lock:
            ev = self._done_events.get(task_id)
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass

    def _lookup_or_load(self, task_id: str) -> Optional[Task]:
        """Find the session for this task (via in-memory map) and load
        the entity from disk."""
        sid = self._find_session_for_task(task_id)
        if not sid:
            return None
        return _store_load(sid, task_id)

    def _find_session_for_task(self, task_id: str) -> Optional[str]:
        with self._lock:
            info = self._tasks.get(task_id)
        if info:
            return info["session_id"]
        # Not in memory — scan disk. Tasks always live under the
        # session repo they were spawned for, so a walk is bounded.
        from openprogram.store import default_store
        store = default_store()
        if not store.root_path.exists():
            return None
        for sdir in sorted(store.root_path.iterdir()):
            if not sdir.is_dir():
                continue
            if (sdir / "tasks.json").exists():
                t = _store_load(sdir.name, task_id)
                if t is not None:
                    return sdir.name
        return None

    def _poll_progress(
        self, task: Task, stop_ev: threading.Event,
    ) -> None:
        """Watch the session for sub-agent messages while the task is
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
        if not task.attach_pointer_id or not task.parent_session_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            from openprogram.store import SessionNodeWriter
            db = default_db()
            pair = db._open(task.parent_session_id)  # noqa: SLF001
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
            shim = SessionNodeWriter(db, task.parent_session_id)
        except Exception:
            return
        while not stop_ev.is_set():
            if stop_ev.wait(1.5):
                break
            try:
                pair2 = db._open(task.parent_session_id)  # noqa: SLF001
                if pair2 is None:
                    continue
                _, idx2 = pair2
                latest = None
                for n in idx2.all_nodes():
                    if (n.seq or 0) <= baseline_seq:
                        continue
                    if n.id == task.attach_pointer_id:
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
                node = idx2.nodes_by_id.get(task.attach_pointer_id)
                if not node:
                    continue
                shim.update(task.attach_pointer_id, output=preview)
                last_patched_id = latest.id
                self.record_task_activity(task.id, "child_progress")
                try:
                    _broadcast_session_reload(
                        task.parent_session_id, reason="task_progress",
                    )
                except Exception:
                    pass
            except Exception:
                pass

    def _update_attach_card(
        self, task: Task, *, error_text: Optional[str] = None,
    ) -> None:
        """Patch the placeholder attach card the spawn path wrote so its
        ``extra.attach`` reflects the final task outcome. Best-effort —
        the attach card pickup path in the existing UI already shows
        ``result.final_text``; this layer adds the task_id linkage
        and status badge.
        """
        if not task.attach_pointer_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            db = default_db()
            pair = db._open(task.parent_session_id)  # noqa: SLF001
            if pair is None:
                return
            _git, idx = pair
            node = idx.nodes_by_id.get(task.attach_pointer_id)
            if not node:
                return
            md = dict(node.metadata or {})
            extra_raw = md.get("extra")
            try:
                extra_json = json.loads(extra_raw) if isinstance(extra_raw, str) else (extra_raw or {})
            except Exception:
                extra_json = {}
            attach = dict(extra_json.get("attach") or {})
            attach["task_id"] = task.id
            attach["status"] = task.status.value
            if task.head_id:
                attach["head_id"] = task.head_id
            # The human name of the sub-agent ("后端架构"). It lives on the
            # Task, and the attach node is the only thing the graph wire and
            # the transcript both read — without it here every reader falls
            # back to a hex id and the branch has no identity anywhere.
            if task.label or task.subject:
                attach["label"] = task.label or task.subject
            # When the task completes, fill source_commit_id from the
            # ContextCommit that ended up on its branch. The existing
            # _run_spawn does this in synchronous mode — we mirror.
            if task.head_id and not attach.get("source_commit_id"):
                try:
                    from openprogram.context.commit.store import (
                        load_commit_for_head,
                    )
                    src = load_commit_for_head(
                        db, task.parent_session_id, task.head_id,
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
            # "running" status long after the task completes.
            md["attach"] = attach

            # Stamp the spawned branch's tip with the human label so
            # the Branches panel and DAG figure show "fox-research"
            # instead of the chain-tail fallback name (which picked
            # up the prompt text or assistant reply as a stand-in).
            # run_agent_turn does this too, but the call has slipped
            # through under specific paths — set it here as well so
            # every task → attach finalization guarantees the name.
            if task.label and task.head_id:
                try:
                    db.set_branch_name(
                        task.parent_session_id,
                        task.head_id,
                        task.label,
                    )
                except Exception:
                    pass
            # Hide the spawned sub-branch from the Branches panel
            # once the task completes successfully. Same idea as
            # merge: the sub-agent's content is now reachable from
            # main via the attach pointer, so the standalone branch
            # tip is redundant in the panel. DAG nodes stay
            # intact — a user can still checkout to revisit the
            # sub-agent's history. Only retire on COMPLETED;
            # errored / cancelled tasks remain visible so the user
            # can see what failed.
            if task.head_id and task.status == TaskStatus.COMPLETED:
                try:
                    db.mark_merged(task.parent_session_id, [task.head_id])
                except Exception:
                    pass
            # Update the persisted node's metadata + output text.
            output = task.result_text or error_text or node.output or ""
            try:
                from openprogram.store import SessionNodeWriter
                shim = SessionNodeWriter(db, task.parent_session_id)
                shim.update(
                    task.attach_pointer_id,
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
                task.parent_session_id, reason="task_attach",
            )
            _refresh_context_stats(task.parent_session_id)
        except Exception:
            pass

    def _finalize_spawn_branch_meta(self, task: Task) -> None:
        """Terminal-state meta for a branch this task CREATED.

        Only the agent tool's spawn form sets ``archive_when_done``
        (deliveries to existing branches leave it False) — nothing here
        runs for them. Archiving stops further send_message / agent(to=)
        deliveries to the branch and keeps its history.
        Best-effort: failures are logged and swallowed.
        """
        if not task.archive_when_done or not task.head_id:
            return
        try:
            from openprogram.agent.session_db import default_db
            default_db().set_branch_meta(
                task.parent_session_id, task.head_id,
                archived=True, archived_at=time.time(),
            )
        except Exception:
            _log.debug(
                "spawn branch meta finalize failed for task %s",
                task.id, exc_info=True,
            )

    def _dispatch_followup(self, task: Task) -> None:
        """Auto-followup: async task finished, nobody's listening on
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
        if not task.parent_session_id:
            return
        label = task.label or task.subject or task.id[:8]
        sub_prompt = (task.prompt or task.description or "").strip()
        # Deliver the reply back to the INITIATOR's session. Same-session
        # spawn: caller_session_id is None → deliver to parent_session_id.
        # Cross-session send_message: deliver to caller_session_id (the
        # sender), NOT the target session the task ran in.
        deliver_session = task.caller_session_id or task.parent_session_id
        # Carry the reply INLINE unless the initiator has an attach pointer
        # to expand. Two things remove one: the task ran in a different
        # session (the pointer, if any, is not on the delivery session's
        # chain), or the task wrote no pointer at all — a delivery to an
        # EXISTING branch (``agent(to=…)``, ``send_message``) creates none,
        # because it spawns nothing to attach. Without this second case a
        # same-session delivery told the initiator "the transcript is
        # attached above" with nothing attached, and the result never
        # arrived.
        inline_reply = bool(
            task.caller_session_id
            and task.caller_session_id != task.parent_session_id
        ) or not task.attach_pointer_id

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
                    reply_text = (task.result_text or "").strip() or "(无输出)"
                    reply_block = (
                        f"分支 {task.parent_session_id}:"
                        f"{task.head_id or '?'} 的回复是：\n{reply_text}\n\n"
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
                    agent_id=task.agent_id or "main",
                    source="task_followup",
                    **runtime_authority(task, "task_followup"),
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
            # and tasks spawned here would record no parent, escaping the
            # cascade in cancel_task.
            from openprogram.functions.tools.send_message.send_message.depth import (
                set_chain_generations, set_chain_messages,
            )
            # The reply hop costs what the child already spent — an
            # explicit send_message reply lands at the same count.
            set_chain_messages(int(task.chain_messages or 0))
            # Generations are the dispatcher's, not the child's: this
            # turn is the dispatcher reading a result, and reading a
            # result creates nobody. Binding the child's count instead
            # left an agent that read one worker's reply unable to
            # create any further agent in that chain, which is exactly
            # the "dispatch a batch, read it, dispatch the next batch"
            # shape the whole tool exists for.
            set_chain_generations(int(task.caller_chain_generations or 0))
            # The follow-up continues the DISPATCHER's work, not the
            # finished task's, so it chains where the task did. None for a
            # task spawned from a plain user turn, which had no task either.
            _current_task_id.set(task.parent_task_id)
            # One follow-up at a time per delivery session: the next one
            # reads a HEAD that already includes the previous answer.
            with self._followup_lock(deliver_session):
                _go()

        threading.Thread(target=_serial, daemon=True).start()

    def _schedule_force_cancel(
        self, session_id: str, task_id: str, lease_generation: int,
    ) -> None:
        """Watchdog: if the worker doesn't honour cancel within
        ``_CANCEL_TIMEOUT_SECS``, force the entity to terminal."""
        def _watch():
            time.sleep(_CANCEL_TIMEOUT_SECS)
            cur = _store_load(session_id, task_id)
            if cur is None or is_terminal(cur.status):
                return
            updated = self._finalize_task_status(
                session_id, task_id, lease_generation,
                TaskStatus.CANCELLED, "cancel.timeout",
                error="cancel timed out; worker may still be running",
            )
            if updated is not None:
                _broadcast_task_status(updated)
                self._wake_done(task_id)
                self._update_attach_card(updated)
                _broadcast_session_reload(session_id, reason="task_cancel_timeout")
        threading.Thread(target=_watch, daemon=True).start()


# Module-level singleton

_runner_lock = threading.Lock()
_runner: Optional[TaskRunner] = None


def get_runner() -> TaskRunner:
    """Process-wide TaskRunner. Idempotent."""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = TaskRunner()
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
    "TaskRunner",
    "get_runner",
    "shutdown_runner",
]
