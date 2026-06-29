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

  * ``_pause_stop.register_cancel_event(session_id, ev)`` exposes the
    cancel event so the existing pre-invocation hook fires.
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
import os
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

from openprogram.agent.task.store import (
    list_tasks as _store_list,
    load_task as _store_load,
    reconcile_orphans as _store_reconcile,
    save_task as _store_save,
    update_task_status as _store_update_status,
)
from openprogram.agent.event_bus import emit_safe
from openprogram.agent.task.types import (
    Task,
    TaskStatus,
    is_terminal,
    mint_task_id,
)


_DEFAULT_MAX_WORKERS = 4
# Hard ceiling on the wait we'll give a worker to honour cancel before
# forcibly flipping the entity to cancelled.
_CANCEL_TIMEOUT_SECS = 30.0


def _broadcast(payload: dict) -> None:
    """Send a WS frame to the frontend — best-effort.

    步 4：不再 import webui。把现成的帧 emit 到总线（``ws.frame`` 事件），
    webui 作为订阅者原样广播。帧内容（type / data 字段）一字不变，前端无感。
    """
    from openprogram.agent.event_bus import emit_ws_frame
    emit_ws_frame(payload)


def _broadcast_session_reload(session_id: str, *, reason: str = "task") -> None:
    _broadcast({
        "type": "session_reload",
        "data": {"session_id": session_id, "reason": reason},
    })


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
      * :meth:`cancel_task` — set cancel event, schedule timeout
      * :meth:`get_task` / :meth:`list_tasks` — read
      * :meth:`await_task` — block until terminal, return final Task

    The runner is *thread-safe* — all maps are guarded by
    ``self._lock``.
    """

    def __init__(self, max_workers: Optional[int] = None) -> None:
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
        # Reconcile orphans before opening the pool so any "running"
        # task from a previous process is flipped to errored. The
        # state-machine transition rules cover (running, errored).
        try:
            _store_reconcile()
        except Exception:
            pass
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="op-task",
        )
        self._lock = threading.Lock()
        # task_id → {"event": Event, "future": Future, "session_id": str}
        self._tasks: dict[str, dict[str, Any]] = {}
        # task_id → threading.Event used to wake await_task() callers.
        self._done_events: dict[str, threading.Event] = {}

    # Public API

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
    ) -> str:
        """Create a Task entity, persist it, queue it on the pool.

        Returns ``task_id`` immediately. The task pickup happens on
        a worker thread and walks through the state machine. The
        caller can ``await_task(task_id)`` to block on completion.

        ``caller_session_id`` (cross-session messaging): the session the
        reply should be delivered back to. Defaults to ``session_id``
        (the task runs and replies in the caller's own session).
        """
        task = Task(
            id=mint_task_id(),
            parent_session_id=session_id,
            prompt=prompt,
            agent_id=agent_id,
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
            status=TaskStatus.PENDING,
            created_at=time.time(),
        )
        _store_save(session_id, task)
        _broadcast_task_status(task)

        # Done-event for await_task / await_tasks callers.
        done_ev = threading.Event()
        cancel_ev = threading.Event()
        # Copy the current ContextVars so things like
        # ``_pause_stop._current_session_id`` set by the spawning
        # thread don't leak into the worker. Each task gets its own
        # context — the worker function rebinds session_id explicitly.
        ctx = contextvars.copy_context()
        # Capture future *before* registering so cancel_task can find it.
        future: Future = self._pool.submit(
            ctx.run, self._run_one, task.id, cancel_ev, done_ev,
        )
        with self._lock:
            self._tasks[task.id] = {
                "event": cancel_ev,
                "future": future,
                "session_id": session_id,
            }
            self._done_events[task.id] = done_ev

        # Mark queued. The transition pending→queued is allowed; the
        # worker may have already flipped it to running by the time
        # this lands. update_task_status is idempotent on no-op.
        try:
            updated = _store_update_status(session_id, task.id, TaskStatus.QUEUED,
                                            queued_at=time.time())
            if updated is not None:
                _broadcast_task_status(updated)
        except ValueError:
            # Worker beat us — already at running. Read back and broadcast.
            cur = _store_load(session_id, task.id)
            if cur is not None:
                _broadcast_task_status(cur)
        return task.id

    def cancel_task(self, task_id: str, *, reason: Optional[str] = None) -> Optional[Task]:
        """Trigger cancel for ``task_id``. Returns the (post-update)
        Task entity, or None if not found.

        Effect:

          * sets the task's cancel event (worker drops out on next
            cooperative checkpoint)
          * sets the session-level cancel event via
            ``_pause_stop.mark_cancelled`` so the existing dispatcher
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
        # Bridge to existing session-level cancel infra so the LLM
        # stream + bash subprocess + agent_loop pre-invocation hook
        # all see the signal.
        try:
            from openprogram.webui._pause_stop import (
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
        cur_task = _store_load(session_id, task_id)
        if cur_task is None:
            return None
        try:
            if cur_task.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
                updated = _store_update_status(
                    session_id, task_id, TaskStatus.CANCELLED,
                    cancel_requested_at=time.time(),
                    error=reason or "cancelled before pickup",
                )
                if updated is not None:
                    _broadcast_task_status(updated)
                    self._wake_done(task_id)
                    self._update_attach_card(updated)
                    _broadcast_session_reload(session_id, reason="task_cancelled")
                return updated
            elif cur_task.status == TaskStatus.RUNNING:
                # Stamp request time but stay in running. Worker will
                # detect cancel and self-flip.
                stamped = Task.from_dict({
                    **cur_task.to_dict(),
                    "cancel_requested_at": time.time(),
                })
                _store_save(
                    session_id,
                    stamped,
                    commit_message=f"task: {task_id} cancel requested",
                )
                # Watchdog: force cancel if worker doesn't honour signal.
                self._schedule_force_cancel(session_id, task_id)
                return cur_task
        except ValueError:
            pass
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

    def shutdown(self, wait: bool = True) -> None:
        """Tear down the pool. Used in tests / process shutdown."""
        try:
            self._pool.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            # Python 3.8 fallback (no cancel_futures kwarg).
            self._pool.shutdown(wait=wait)

    # Worker body

    def _run_one(self, task_id: str, cancel_ev: threading.Event,
                 done_ev: threading.Event) -> None:
        """Worker thread entry point.

        Wraps :func:`run_agent_turn` so the same code that handles the
        synchronous ``/spawn`` path runs underneath us. Catches
        everything so a buggy tool doesn't leave the task pinned at
        ``running`` forever — exceptions flip to ``errored``.

        Important: the dispatcher's cancel hook reads
        ``_pause_stop._current_session_id`` from the worker thread
        ContextVar. We bind it at entry so the hook can find the
        right session.
        """
        # Look up the task entity at entry — fields like
        # parent_session_id, prompt, agent_id are stable from this
        # point forward.
        task = self._lookup_or_load(task_id)
        if task is None:
            done_ev.set()
            return
        session_id = task.parent_session_id

        # Bind the session id ContextVar for the cancel hook. Same
        # contract _execute_in_context honours in the webui worker.
        from openprogram.webui._pause_stop import (
            register_cancel_event,
            unregister_cancel_event,
            set_current_session_id,
            reset_current_session_id,
            clear_cancel,
        )
        sid_token = set_current_session_id(session_id)
        register_cancel_event(session_id, cancel_ev)
        # Reset any stale cancel flag from a previous turn on this
        # session — otherwise a /api/stop fired against a prior turn
        # would short-circuit ours.
        try:
            clear_cancel(session_id)
        except Exception:
            pass

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
                updated = _store_update_status(
                    session_id, task_id, TaskStatus.CANCELLED,
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
                result = run_agent_turn(
                    session_id=session_id,
                    prompt=task.prompt,
                    agent_id=task.agent_id,
                    branch_from=branch_from,
                    label=task.label,
                )
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                try:
                    updated = _store_update_status(
                        session_id, task_id, TaskStatus.ERRORED,
                        error=err,
                    )
                except ValueError:
                    updated = _store_load(session_id, task_id)
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
            cancelled = cancel_ev.is_set() or (
                result.error and "stopped" in (result.error or "").lower()
            )
            if cancelled:
                new_status = TaskStatus.CANCELLED
            elif result.failed:
                new_status = TaskStatus.ERRORED
            else:
                new_status = TaskStatus.COMPLETED
            try:
                updated = _store_update_status(
                    session_id, task_id, new_status,
                    head_id=result.head_id,
                    result_text=result.final_text or "",
                    error=result.error,
                )
            except ValueError:
                # State already moved (e.g. force-cancel watchdog).
                updated = _store_load(session_id, task_id)
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
            # Tell tail clients the session changed so attach card
            # picks up the new head / text.
            _broadcast_session_reload(session_id, reason=f"task_{new_status.value}")
        finally:
            try:
                unregister_cancel_event(session_id)
            except Exception:
                pass
            try:
                reset_current_session_id(sid_token)
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
            self._wake_done(task_id)
            with self._lock:
                self._tasks.pop(task_id, None)

    # Internals

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
            from openprogram.store import GraphStoreShim
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
            shim = GraphStoreShim(db, task.parent_session_id)
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
                from openprogram.store import GraphStoreShim
                shim = GraphStoreShim(db, task.parent_session_id)
                shim.update(
                    task.attach_pointer_id,
                    output=output,
                    metadata=md,
                )
            except Exception:
                pass
        except Exception:
            pass

    def _dispatch_followup(self, task: Task) -> None:
        """Auto-followup: async task finished, nobody's listening on
        the caller session — fire a synthetic user-role turn that
        prompts the parent agent to react to the result.

        The attach pointer the runner just wrote lives in the chain
        already, so the next turn's context-commit generator will
        expand it as ``[Attached from branch "X"]:`` items and the
        LLM sees the sub-agent's output naturally.

        Runs on a daemon thread so the runner worker doesn't block.
        """
        if not task.parent_session_id:
            return
        label = task.label or task.subject or task.id[:8]
        sub_prompt = (task.prompt or task.description or "").strip()
        # Deliver the reply back to the INITIATOR's session. Same-session
        # spawn: caller_session_id is None → deliver to parent_session_id.
        # Cross-session message_branch: deliver to caller_session_id (the
        # sender), NOT the target session the task ran in.
        deliver_session = task.caller_session_id or task.parent_session_id
        cross = bool(
            task.caller_session_id
            and task.caller_session_id != task.parent_session_id
        )

        def _go():
            try:
                from openprogram.agent.dispatcher import (
                    TurnRequest, process_user_turn,
                )
                # CRITICAL (same-session): reset session head back to the
                # spawn user msg (on the caller / main lane) before the
                # follow-up runs, so the follow-up commit sees the attach
                # pointer on main. Cross-session: the attach pointer lives
                # in the TARGET session, not here — there's nothing on the
                # caller lane to reset to, and the reply text is carried in
                # the prompt instead (see below).
                head_to_reset = task.caller_msg_id or task.parent_msg_id
                if head_to_reset and not cross:
                    try:
                        from openprogram.agent.session_db import default_db
                        default_db().set_head(
                            task.parent_session_id, head_to_reset,
                        )
                    except Exception:
                        pass
                # Followup prompt — push the parent agent to synthesize a
                # reply, not echo the sub-agent's last line. Same-session:
                # the sub-agent transcript is in context via the attach
                # expansion. Cross-session: the attach pointer is in the
                # other session and won't expand here, so carry the reply
                # text inline.
                sub_request_line = (
                    f"用户原本让子 agent 做的事是：{sub_prompt}\n"
                    if sub_prompt else ""
                )
                cross_reply = ""
                if cross:
                    reply_text = (task.result_text or "").strip() or "(无输出)"
                    cross_reply = (
                        f"分支 {task.parent_session_id}:"
                        f"{task.head_id or '?'} 的回复是：\n{reply_text}\n\n"
                    )
                if cross:
                    followup_text = (
                        f"[系统消息] 你之前发消息给的另一个分支 \"{label}\" "
                        f"回复了。\n{sub_request_line}{cross_reply}"
                        f"请基于这条回复继续——做总结、解读，或决定下一步"
                        f"（继续追问可再调 message_branch）。"
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
                )
                process_user_turn(req)
            except Exception:
                # Best-effort — don't blow up the runner if the
                # caller session is gone / dispatcher errors.
                pass

        threading.Thread(target=_go, daemon=True).start()

    def _schedule_force_cancel(self, session_id: str, task_id: str) -> None:
        """Watchdog: if the worker doesn't honour cancel within
        ``_CANCEL_TIMEOUT_SECS``, force the entity to terminal."""
        def _watch():
            time.sleep(_CANCEL_TIMEOUT_SECS)
            cur = _store_load(session_id, task_id)
            if cur is None or is_terminal(cur.status):
                return
            try:
                updated = _store_update_status(
                    session_id, task_id, TaskStatus.CANCELLED,
                    error="cancel timed out; worker may still be running",
                )
            except ValueError:
                updated = None
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
    "TaskRunner",
    "get_runner",
    "shutdown_runner",
]
