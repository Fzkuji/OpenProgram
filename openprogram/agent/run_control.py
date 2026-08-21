"""
Run control for turn execution: pause / cancel / session binding /
active-runtime registry.

This is turn-execution state, not a UI concern — the web UI, the job
runner, channels, process runners and long-running tools all steer the
same machine. Importing this module claims the core's host-integration
seams (``set_cancellation_check`` / ``set_session_id_provider``), which
is what makes the exec loop cancellable; ``agentic_programming`` itself
never imports this layer and keeps its headless defaults when nobody
does.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from openprogram.agentic_programming.function import (
    CancelledError,
    add_pre_invocation_hook,
    set_cancellation_check,
    set_session_id_provider,
)


# ---------------------------------------------------------------------------
# Pause/resume — cooperative: only blocks at `node_created` event hooks.
# ---------------------------------------------------------------------------

_pause_event = threading.Event()
_pause_event.set()  # starts un-paused


def pause_execution() -> None:
    """Block agentic functions from proceeding (cooperative)."""
    _pause_event.clear()


def resume_execution() -> None:
    """Resume blocked agentic functions."""
    _pause_event.set()


def wait_if_paused() -> None:
    """Called by the event hook; blocks until resumed."""
    _pause_event.wait()


# ---------------------------------------------------------------------------
# Turn cancellation tokens — one per turn, never per session.
#
# A turn opens a fresh CancelToken; the LLM call, tool execution and every
# sub-task check that one object. Stopping trips the token of the turn that
# is running now. When the turn ends the token is retired, so a stop that
# arrives late cannot reach into the next turn. Nothing has to be reset on
# cleanup — the next turn simply gets a different object.
#
# The public names below (mark_cancelled / is_cancelled / clear_cancel)
# keep their meaning for callers and the WS protocol; they now resolve to
# the session's current token instead of a sticky boolean.
# ---------------------------------------------------------------------------


class CancellationToken:
    """Cancellation signal scoped to exactly one execution.

    Wraps a ``threading.Event`` so blocked worker threads can wait on it,
    and carries a ``retired`` flag: once the turn ends, ``cancel()`` is a
    no-op, which is what keeps a late stop from leaking into the next turn.
    """

    __slots__ = (
        "_event", "_retired", "_lock", "session_id", "execution_id",
    )

    def __init__(
        self, session_id: str, execution_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.execution_id = execution_id
        self._event = threading.Event()
        self._retired = False
        self._lock = threading.Lock()

    @property
    def event(self) -> threading.Event:
        """The underlying Event, for code that must block until cancelled."""
        return self._event

    def cancel(self) -> bool:
        """Trip this token. Returns False if the turn already ended."""
        with self._lock:
            if self._retired:
                return False
            self._event.set()
        return True

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def retire(self) -> None:
        """End this token's life. Later ``cancel()`` calls do nothing."""
        with self._lock:
            self._retired = True

    @property
    def retired(self) -> bool:
        with self._lock:
            return self._retired


# Compatibility during the execution-cancellation migration.
CancelToken = CancellationToken


class ExecutionNotFound(Exception):
    """The requested execution is absent or not visible to the caller."""


class ExecutionNotCancellable(Exception):
    """The requested execution has already reached a non-cancel terminal."""

    def __init__(self, execution_id: str, execution: Any = None) -> None:
        super().__init__(execution_id)
        self.execution_id = execution_id
        self.execution = execution


class ExecutionSpawnRefused(Exception):
    """Creating a descendant was refused because an ancestor is cancelling."""

    def __init__(self, reason_code: str = "cancel.parent") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


# Server-side grace before exact-owner terminate. Tests may shorten this.
CANCEL_GRACE_S = 4.0

_TERMINAL_STATUSES = frozenset({
    "completed", "failed", "interrupted", "cancelled", "error",
})
_ACTIVE_STATUSES = frozenset({"queued", "running", "pending", "streaming"})
_CANCEL_INTENT_STATUSES = frozenset({"cancelling", "cancelled"})

_after_intent_hook: Callable[[str], None] | None = None
_execution_update_hook: Callable[[dict[str, Any]], None] | None = None
_cancel_reason: ContextVar[str] = ContextVar(
    "_cancel_reason", default="cancel.user",
)
_generation_seq = 0


def set_after_intent_hook(hook: Callable[[str], None] | None) -> None:
    """Test seam: run after durable cancelling is written, before signaling."""
    global _after_intent_hook
    _after_intent_hook = hook


def set_execution_update_hook(
    hook: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Register the host transport used for asynchronous terminal updates."""
    global _execution_update_hook
    _execution_update_hook = hook


@dataclass
class _OwnerEntry:
    execution_id: str
    session_id: str
    generation: int
    token: CancellationToken | None = None
    is_alive: Callable[[], bool] | None = None
    terminate: Callable[[], bool] | None = None
    finalize: Callable[[], None] | None = None
    process: Any = None
    stop_queue: Any = None
    retired: bool = False
    grace_deadline: float | None = None
    diagnostics: list[str] = field(default_factory=list)


_owners: dict[str, _OwnerEntry] = {}
_session_index: dict[str, set[str]] = {}
_grace_threads: dict[str, threading.Thread] = {}
_finalizing: set[str] = set()


_cancel_flags_lock = threading.Lock()

# (session_id, execution_id) → the token owned by that execution. A None
# execution_id is the foreground slot shared by Web, MCP and ACP turns, which
# admit one at a time; background jobs sharing a session bind their job id
# so a stop aimed at one never reaches a sibling.
# Absent when no turn is in flight, which is why a stop between turns is a
# no-op rather than a flag that poisons whatever runs next.
_current_tokens: dict[tuple[str, str | None], CancellationToken] = {}

_execution_cancel_lock = threading.RLock()

# session_id -> exact Event whose owner is performing session-keyed cleanup.
# Registration fails closed while a lease exists; cleanup callbacks run outside
# this module's lock and release the lease in a finally block. Cleanup is
# session-keyed, so it gates the foreground slot only.
_cancel_cleanup_leases: dict[str, threading.Event] = {}

# Per-thread session_id so the cancel hook knows whose token to check.
# Set by `_execute_in_context` at entry. ContextVars do not propagate across
# threading.Thread starts, so the value is always set from inside the worker.
_current_session_id: ContextVar = ContextVar("_current_session_id", default=None)

# Background jobs sharing a session bind their job id here. Foreground
# turns leave it as None and retain the historical single-turn semantics.
_current_execution_id: ContextVar = ContextVar(
    "_current_execution_id", default=None,
)

# The active token for the current worker context. Set alongside the
# session id so nested agentic frames check the same object even when a
# turn for another session is running elsewhere in the process.
_current_token: ContextVar = ContextVar("_current_token", default=None)


def begin_turn(
    session_id: str, turn_id: str | None = None,
) -> CancellationToken:
    """Open a fresh cancellation token for a turn and register it as current.

    Any token left registered for this session belongs to a turn that has
    already ended; it is retired here so a stop racing the handover cannot
    land on a dead turn. When ``turn_id`` is set it is the execution id
    and is indexed as such; the session foreground slot stays occupied
    so one-at-a-time chat admission still works.
    """
    token = CancelToken(session_id, turn_id)
    with _cancel_flags_lock:
        if session_id in _cancel_cleanup_leases:
            raise RuntimeError("session cancellation cleanup in progress")
        key = (session_id, None)
        stale = _current_tokens.get(key)
        _current_tokens[key] = token
        if turn_id:
            _current_tokens[(session_id, turn_id)] = token
    if stale is not None and stale is not token:
        stale.retire()
        if stale.execution_id:
            retire_execution_owner(stale.execution_id)
    if turn_id:
        register_execution_owner(turn_id, session_id, token=token)
    return token


def end_turn(session_id: str, token: CancelToken | None = None) -> None:
    """Retire the turn's token and deregister it.

    Passing the token makes this safe against a turn that already handed
    the session over to a successor: only the matching registration is
    removed, never a newer turn's.
    """
    with _cancel_flags_lock:
        key = (session_id, None)
        current = _current_tokens.get(key)
        doomed = token if token is not None else current
        if token is None or current is token:
            _current_tokens.pop(key, None)
        if doomed is not None:
            for stored_key, stored in list(_current_tokens.items()):
                if stored is doomed:
                    _current_tokens.pop(stored_key, None)
    if doomed is not None:
        doomed.retire()
        if doomed.execution_id:
            retire_execution_owner(doomed.execution_id)


def current_token(
    session_id: str, *, execution_id: str | None = None,
) -> CancellationToken | None:
    """The token of the turn running on this session, or None between turns."""
    with _cancel_flags_lock:
        return _current_tokens.get((session_id, execution_id))


def register_cancel_event(
    session_id: str, ev: threading.Event, *, execution_id: str | None = None,
) -> None:
    """Adopt a caller-owned Event as the session's current turn token.

    Kept for call sites (chat turns, job runner) that create their own
    Event and hand it to the dispatcher. The Event becomes the token's
    Event, so tripping either one is visible through both.
    """
    token = CancellationToken(session_id, execution_id)
    token._event = ev
    with _cancel_flags_lock:
        if execution_id is None and session_id in _cancel_cleanup_leases:
            raise RuntimeError("session cancellation cleanup in progress")
        key = (session_id, execution_id)
        stale = _current_tokens.get(key)
        _current_tokens[key] = token
    if stale is not None and stale._event is not ev:
        stale.retire()
        if stale.execution_id:
            retire_execution_owner(stale.execution_id)
    if execution_id:
        register_execution_owner(execution_id, session_id, token=token)


def claim_cancel_event(
    session_id: str,
    ev: threading.Event,
    *,
    execution_id: str | None = None,
    foreground: bool | None = None,
) -> bool:
    """Register ``ev`` only when this slot has no owner or cleanup lease.

    The foreground slot (``execution_id`` None, or ``foreground=True``)
    additionally fails closed while a session-keyed cleanup lease is held.
    Jobs pass an execution id and leave the foreground slot alone.
    """
    occupy_fg = (execution_id is None) if foreground is None else foreground
    token = CancellationToken(session_id, execution_id)
    token._event = ev
    with _cancel_flags_lock:
        if occupy_fg and session_id in _cancel_cleanup_leases:
            return False
        key = (session_id, execution_id)
        if key in _current_tokens:
            return False
        if occupy_fg and execution_id is not None:
            fg = (session_id, None)
            if fg in _current_tokens:
                return False
            _current_tokens[fg] = token
        _current_tokens[key] = token
    if execution_id:
        register_execution_owner(execution_id, session_id, token=token)
    return True


def acquire_cancel_cleanup(session_id: str, ev: threading.Event) -> bool:
    """Atomically lease session-keyed cleanup to the exact current event.

    A successful lease prevents ``begin_turn`` and ``register_cancel_event``
    from handing the session to a successor until ``release_cancel_cleanup``.
    The caller must release in ``finally`` after all blocking cleanup work.
    """
    with _cancel_flags_lock:
        current = _current_tokens.get((session_id, None))
        if (
            current is None
            or current._event is not ev
            or session_id in _cancel_cleanup_leases
        ):
            return False
        _cancel_cleanup_leases[session_id] = ev
        return True


def release_cancel_cleanup(session_id: str, ev: threading.Event) -> None:
    """Release the cleanup lease only when ``ev`` still owns it."""
    with _cancel_flags_lock:
        if _cancel_cleanup_leases.get(session_id) is ev:
            _cancel_cleanup_leases.pop(session_id, None)


def unregister_cancel_event(
    session_id: str,
    ev: threading.Event | None = None,
    *,
    execution_id: str | None = None,
) -> None:
    """Retire the registration made with ``ev`` (see register_cancel_event).

    Callers that registered an Event MUST pass it back here: without it
    this pops whatever token is CURRENT, including a newer turn's — the
    concrete failure was ``/task --async`` finishing after the user had
    already started a chat turn, popping the chat turn's token and
    leaving its Stop button dead. With ``ev`` only the matching
    registration is removed; a mismatch means a newer turn already
    replaced (and retired) ours via register_cancel_event, so there is
    nothing left to do. ``ev=None`` keeps the unconditional force-clear
    for callers that explicitly want to tear down whatever is current
    (the /api/stop handler).
    """
    if ev is None:
        end_turn(session_id)
        return
    doomed: CancellationToken | None = None
    with _cancel_flags_lock:
        key = (session_id, execution_id)
        current = _current_tokens.get(key)
        if current is None or current._event is not ev:
            # Fall back to matching the Event anywhere in this session so
            # a foreground alias of the same token is retired with it.
            for stored_key, stored in list(_current_tokens.items()):
                if stored_key[0] == session_id and stored._event is ev:
                    current = stored
                    break
            else:
                return
        doomed = current
        for stored_key, stored in list(_current_tokens.items()):
            if stored is doomed:
                _current_tokens.pop(stored_key, None)
    doomed.retire()
    if doomed.execution_id:
        retire_execution_owner(doomed.execution_id)


def is_turn_running(session_id: str) -> bool:
    """True while a turn is in flight on this session.

    The authoritative in-process busy check: every turn entry point that
    can run concurrently (webui chat, job runner workers) registers its
    cancel token in ``_current_tokens`` and unregisters it in a finally
    block, so presence here means a turn is executing right now.
    send_message uses this to decide direct delivery vs. inbox queueing.
    """
    # ponytail: channel-worker turns don't register a token, so they are
    # invisible here; register one there if channel sessions ever need
    # busy-queueing.
    with _cancel_flags_lock:
        return any(key[0] == session_id for key in _current_tokens)


def mark_cancelled(session_id: str, *, execution_id: str | None = None) -> None:
    """Compatibility: trip the live token, then cancel through the service.

    MCP/ACP still call this session-scoped helper. The token is tripped
    first so waiters unblock even when no DAG record exists. When an
    execution id or in-process owner is known, the call continues into
    ``cancel_execution``.
    """
    with _cancel_flags_lock:
        token = _current_tokens.get((session_id, execution_id))
        if token is None and execution_id is None:
            token = _current_tokens.get((session_id, None))
    if token is not None:
        token.cancel()
    target = execution_id or (token.execution_id if token is not None else None)
    if target:
        try:
            cancel_execution(target)
        except (ExecutionNotFound, ExecutionNotCancellable):
            pass
        except Exception:
            pass
        return
    if _session_index.get(session_id):
        try:
            cancel_session_executions(session_id)
        except Exception:
            pass


def _execution_dto(session_id: str, node: Any) -> dict[str, Any]:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    return {
        "execution_id": node.id,
        "session_id": session_id,
        "parent_execution_id": node.caller or None,
        "execution_kind": metadata.get("execution_kind"),
        "status": metadata.get("status"),
        "reason_code": metadata.get("reason_code"),
        "cancellation_requested_at": metadata.get(
            "cancellation_requested_at",
        ),
        "finished_at": metadata.get("finished_at"),
        "partial_output_available": bool(node.output),
    }


def _get_node(store: Any, session_id: str, execution_id: str) -> Any | None:
    return next(
        (node for node in store.get_nodes(session_id) if node.id == execution_id),
        None,
    )


_JOB_STATUS_TO_EXEC = {
    "pending": "queued",
    "queued": "queued",
    "running": "running",
    "cancelled": "cancelled",
    "completed": "completed",
    "errored": "failed",
}


class _ExecutionView:
    """DTO-shaped view of a Job (or other non-DAG execution) for cancel."""

    __slots__ = ("id", "caller", "output", "metadata")

    def __init__(
        self,
        *,
        id: str,
        caller: str = "",
        output: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.id = id
        self.caller = caller or ""
        self.output = output
        self.metadata = metadata or {}


def _job_execution_view(job: Any) -> _ExecutionView:
    raw = job.status.value if hasattr(job.status, "value") else str(job.status)
    status = _JOB_STATUS_TO_EXEC.get(raw, raw)
    if status == "running" and getattr(job, "cancel_requested_at", None):
        status = "cancelling"
    return _ExecutionView(
        id=job.id,
        caller=getattr(job, "parent_job_id", None) or "",
        output=getattr(job, "result_text", None)
        or getattr(job, "error", None)
        or getattr(job, "prompt", "")
        or "",
        metadata={
            "status": status,
            "reason_code": getattr(job, "reason_code", None),
            "execution_kind": "job",
            "cancellation_requested_at": getattr(
                job, "cancel_requested_at", None,
            ),
            "finished_at": getattr(job, "completed_at", None),
        },
    )


def _persist_job_cancel_intent(
    session_id: str,
    job_id: str,
    *,
    reason_code: str,
    requested_at: float,
    terminal: bool,
) -> bool:
    """Stamp job cancel intent without signaling owners."""
    try:
        from openprogram.agent.job.store import load_job, update_job_status
        from openprogram.agent.job.types import JobStatus, is_terminal
    except Exception:
        return False
    job = load_job(session_id, job_id) or _find_job(job_id)
    if job is None:
        return False
    session_id = getattr(job, "parent_session_id", None) or session_id
    if is_terminal(job.status):
        return False
    try:
        if terminal or job.status in (JobStatus.PENDING, JobStatus.QUEUED):
            update_job_status(
                session_id,
                job_id,
                JobStatus.CANCELLED,
                cancel_requested_at=requested_at,
                reason_code=reason_code,
            )
        else:
            update_job_status(
                session_id,
                job_id,
                job.status,
                cancel_requested_at=requested_at,
                reason_code=reason_code,
            )
        return True
    except Exception:
        return False


def _find_job(execution_id: str) -> Any | None:
    try:
        from openprogram.agent.job import runner as job_runner
        existing = job_runner._runner
        if existing is not None:
            job = existing.get_job(execution_id)
            if job is not None:
                return job
    except Exception:
        pass
    try:
        from openprogram.agent.job.store import load_job
        store = _canonical_store()
        for session in store.list_sessions(limit=10**9, include_archived=True):
            job = load_job(session["id"], execution_id)
            if job is not None:
                return job
    except Exception:
        pass
    return None


def _canonical_store(store: Any | None = None) -> Any:
    if store is not None:
        return store
    try:
        from openprogram.store import _store
        writer = _store.get()
        bound = getattr(writer, "store", None)
        if bound is not None:
            return bound
    except Exception:
        pass
    from openprogram.agent.session_db import default_db
    return default_db()


def _find_dag_execution(
    store: Any, execution_id: str, *, session_id: str | None = None,
) -> tuple[str, Any] | None:
    if session_id:
        node = _get_node(store, session_id, execution_id)
        if node is not None:
            return session_id, node
    try:
        sessions = store.list_sessions(limit=10**9, include_archived=True)
    except Exception:
        return None
    for session in sessions:
        sid = session["id"]
        node = _get_node(store, sid, execution_id)
        if node is not None:
            return sid, node
    return None


def _find_execution(store: Any, execution_id: str) -> tuple[str, Any] | None:
    found = _find_dag_execution(store, execution_id)
    if found is not None:
        return found
    job = _find_job(execution_id)
    if job is not None:
        return job.parent_session_id, _job_execution_view(job)
    return None


def _caller_descendants(store: Any, session_id: str, execution_id: str) -> list[Any]:
    nodes = store.get_nodes(session_id)
    child_ids = {execution_id}
    descendants: list[Any] = []
    pending = list(nodes)
    while pending:
        added = False
        for node in pending[:]:
            if node.caller in child_ids:
                child_ids.add(node.id)
                descendants.append(node)
                pending.remove(node)
                added = True
        if not added:
            break
    return descendants


def _job_descendants(execution_id: str) -> list[tuple[str, Any]]:
    try:
        from openprogram.agent.job.store import list_jobs
        store = _canonical_store()
        jobs = []
        for session in store.list_sessions(limit=10**9, include_archived=True):
            jobs.extend(list_jobs(session["id"]))
    except Exception:
        return []
    children: dict[str, list[Any]] = {}
    for job in jobs:
        parent_id = getattr(job, "parent_job_id", None)
        if parent_id:
            children.setdefault(parent_id, []).append(job)
    seen = {execution_id}
    queue = [execution_id]
    descendants: list[tuple[str, Any]] = []
    while queue:
        parent_id = queue.pop(0)
        for job in children.get(parent_id, []):
            if job.id in seen:
                continue
            seen.add(job.id)
            queue.append(job.id)
            descendants.append((
                job.parent_session_id,
                _job_execution_view(job),
            ))
    return descendants


def _node_status(node: Any) -> str | None:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    return metadata.get("status")


def _token_for(session_id: str, execution_id: str) -> CancellationToken | None:
    token = current_token(session_id, execution_id=execution_id)
    if token is not None:
        return token
    owner = _owners.get(execution_id)
    if owner is not None and not owner.retired:
        return owner.token
    return None


def _owner_needs_process_grace(owner: _OwnerEntry) -> bool:
    """True when CANCEL_GRACE_S should wait for a real child to die.

    Token-only chat owners have a cancel token and no subprocess. The
    HTTP stream aborts on the cancel signal; do not extend another 4s.
    A terminate hook is treated as "kills a child" — tools and
    process runners keep the grace window.
    """
    if owner.process is not None:
        return True
    if owner.terminate is not None:
        return True
    return False


def _owner_appears_live(owner: _OwnerEntry) -> bool:
    if owner.retired:
        return False
    if owner.is_alive is not None:
        try:
            return bool(owner.is_alive())
        except Exception:
            return True
    if owner.process is not None:
        poll = getattr(owner.process, "poll", None)
        if callable(poll):
            try:
                return poll() is None
            except Exception:
                return True
        is_alive = getattr(owner.process, "is_alive", None)
        if callable(is_alive):
            try:
                return bool(is_alive())
            except Exception:
                return True
    if owner.token is not None and not owner.token.retired:
        return True
    return False


def owner_is_alive(execution_id: str) -> bool:
    owner = _owners.get(execution_id)
    return owner is not None and _owner_appears_live(owner)


def register_execution_owner(
    execution_id: str,
    session_id: str,
    *,
    token: CancellationToken | None = None,
    is_alive: Callable[[], bool] | None = None,
    terminate: Callable[[], bool] | None = None,
    finalize: Callable[[], None] | None = None,
    process: Any = None,
    stop_queue: Any = None,
) -> int:
    """Bind a live owner to ``execution_id``. Returns the generation."""
    global _generation_seq
    cancel_pending = False
    with _execution_cancel_lock:
        try:
            from openprogram.agent.session_db import default_db
            found = _find_execution(default_db(), execution_id)
            if found is not None:
                _found_session, record = found
                cancel_pending = _node_status(record) in _CANCEL_INTENT_STATUSES
        except Exception:
            cancel_pending = False
        _generation_seq += 1
        generation = _generation_seq
        previous = _owners.get(execution_id)
        if previous is not None and not previous.retired:
            previous.retired = True
        entry = _OwnerEntry(
            execution_id=execution_id,
            session_id=session_id,
            generation=generation,
            token=token or (previous.token if previous is not None else None),
            is_alive=is_alive or (previous.is_alive if previous is not None else None),
            terminate=terminate or (previous.terminate if previous is not None else None),
            finalize=finalize or (previous.finalize if previous is not None else None),
            process=process if process is not None else (
                previous.process if previous is not None else None
            ),
            stop_queue=stop_queue if stop_queue is not None else (
                previous.stop_queue if previous is not None else None
            ),
        )
        if token is None and previous is not None:
            entry.token = previous.token
        if token is not None:
            entry.token = token
        _owners[execution_id] = entry
        _session_index.setdefault(session_id, set()).add(execution_id)
    if cancel_pending:
        _request_cancel_signals(session_id, execution_id)
        _ensure_grace_watch(execution_id)
    return generation


def retire_execution_owner(execution_id: str) -> None:
    """Mark the owner gone and complete cancellation if intent is persisted.

    Cooperative owner exit must write durable ``cancelled`` via the unified
    finalizer. Registry entries are dropped after that so process/queue
    references do not accumulate.
    """
    owner = _owners.get(execution_id)
    if owner is None:
        _drop_finished_grace_thread(execution_id)
        return
    first_retire = not owner.retired
    owner.retired = True
    if owner.token is not None:
        owner.token.retire()
    ids = _session_index.get(owner.session_id)
    if ids is not None:
        ids.discard(execution_id)
        if not ids:
            _session_index.pop(owner.session_id, None)
    if first_retire and execution_id not in _finalizing:
        _try_finalize(execution_id)
    current = _owners.get(execution_id)
    if current is owner:
        _owners.pop(execution_id, None)
    _drop_finished_grace_thread(execution_id)


def _drop_finished_grace_thread(execution_id: str) -> None:
    thread = _grace_threads.get(execution_id)
    if thread is None:
        return
    if thread is threading.current_thread() or not thread.is_alive():
        _grace_threads.pop(execution_id, None)


def _has_live_owner(session_id: str, execution_id: str) -> bool:
    owner = _owners.get(execution_id)
    if owner is not None and _owner_appears_live(owner):
        return True
    token = _token_for(session_id, execution_id)
    return token is not None and not token.retired


def _cas_write_status(
    store: Any,
    session_id: str,
    node_id: str,
    *,
    from_statuses: frozenset[str],
    metadata: dict[str, Any],
) -> bool:
    live = _get_node(store, session_id, node_id)
    if live is None:
        return False
    current = _node_status(live)
    if current not in from_statuses:
        return False
    store.update_node(session_id, node_id, metadata=metadata)
    return True


def _request_cancel_signals(session_id: str, execution_id: str) -> None:
    owner = _owners.get(execution_id)
    if owner is not None and owner.retired:
        return
    token = _token_for(session_id, execution_id)
    if token is not None:
        token.cancel()
    if owner is not None and owner.stop_queue is not None:
        try:
            owner.stop_queue.put("stop", block=False)
        except Exception:
            pass
    try:
        from openprogram.agent.process_runner import request_graceful_stop
        request_graceful_stop(session_id, execution_id=execution_id)
    except Exception:
        pass
    try:
        from openprogram.agent.questions import get_question_registry
        get_question_registry().cancel_execution(session_id, execution_id)
    except Exception:
        pass


def _default_terminate(owner: _OwnerEntry) -> bool:
    killed = False
    try:
        from openprogram.agent.process_runner import kill_active_subprocess
        if kill_active_subprocess(
            owner.session_id, execution_id=owner.execution_id,
        ):
            killed = True
    except Exception:
        pass
    try:
        kill_active_runtime(
            owner.session_id, execution_id=owner.execution_id,
        )
        killed = True
    except Exception:
        pass
    if owner.process is not None:
        try:
            kill = getattr(owner.process, "kill", None)
            if callable(kill):
                kill()
                killed = True
            else:
                terminate = getattr(owner.process, "terminate", None)
                if callable(terminate):
                    terminate()
                    killed = True
        except Exception:
            killed = False
    return killed


def _try_finalize(execution_id: str) -> bool:
    from openprogram.agent.session_db import default_db

    store = default_db()
    with _execution_cancel_lock:
        if execution_id in _finalizing:
            return False
        _finalizing.add(execution_id)
        try:
            owner = _owners.get(execution_id)
            if owner is not None and _owner_appears_live(owner):
                if owner.diagnostics is not None:
                    owner.diagnostics.append("finalize skipped: owner alive")
                return False
            found = _find_execution(store, execution_id)
            if found is None:
                retire_execution_owner(execution_id)
                return False
            session_id, node = found
            status = _node_status(node)
            if status in _TERMINAL_STATUSES:
                retire_execution_owner(execution_id)
                if node.caller:
                    _try_finalize(node.caller)
                return status == "cancelled"
            if status != "cancelling":
                return False
            if any(
                _node_status(descendant) not in _TERMINAL_STATUSES
                for descendant in _caller_descendants(
                    store, session_id, execution_id,
                )
            ):
                return False
            if owner is not None and owner.finalize is not None:
                try:
                    owner.finalize()
                except Exception:
                    owner.diagnostics.append("finalize raised")
            dag = _get_node(store, session_id, execution_id)
            if dag is not None:
                store.update_node(
                    session_id,
                    execution_id,
                    metadata={
                        "status": "cancelled",
                        "finished_at": time.time(),
                    },
                )
            try:
                from openprogram.agent.job.store import load_job, update_job_status
                from openprogram.agent.job.types import JobStatus, is_terminal
                job = load_job(session_id, execution_id) or _find_job(execution_id)
                if job is not None and not is_terminal(job.status):
                    update_job_status(
                        getattr(job, "parent_session_id", None) or session_id,
                        execution_id,
                        JobStatus.CANCELLED,
                        completed_at=time.time(),
                        reason_code=(node.metadata or {}).get("reason_code")
                        or "cancel.user",
                    )
            except Exception:
                pass
            _finalize_projections(store, session_id, execution_id, node)
            retire_execution_owner(execution_id)
            hook = _execution_update_hook
            if hook is not None:
                try:
                    refreshed = _get_node(store, session_id, execution_id)
                    if refreshed is None:
                        job = _find_job(execution_id)
                        if job is not None:
                            refreshed = _job_execution_view(job)
                    if refreshed is not None:
                        hook(_execution_dto(session_id, refreshed))
                except Exception:
                    pass
            if node.caller:
                _try_finalize(node.caller)
            return True
        finally:
            _finalizing.discard(execution_id)


def _finalize_projections(
    store: Any, session_id: str, execution_id: str, node: Any,
) -> None:
    reason_code = (
        (node.metadata or {}).get("reason_code") if node is not None else None
    ) or "cancel.user"
    try:
        from openprogram.agent.job import runner as job_runner
        from openprogram.agent.job.types import JobStatus, is_terminal
        from openprogram.agent.job.store import update_job_status

        runner = job_runner._runner
        if runner is not None:
            job = runner.get_job(execution_id)
            if job is not None and not is_terminal(job.status):
                cancelled = runner.cancel_job(
                    execution_id, reason="execution cancelled",
                )
                if cancelled is None or not is_terminal(cancelled.status):
                    try:
                        update_job_status(
                            session_id,
                            execution_id,
                            JobStatus.CANCELLED,
                            cancel_requested_at=time.time(),
                            reason_code=reason_code,
                        )
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        from openprogram.agent.questions import get_question_registry
        get_question_registry().cancel_execution(session_id, execution_id)
    except Exception:
        pass


def _ensure_grace_watch(execution_id: str) -> None:
    owner = _owners.get(execution_id)
    if owner is None or owner.retired:
        _try_finalize(execution_id)
        return
    if owner.grace_deadline is not None:
        return
    if not _owner_needs_process_grace(owner):
        # Token-only: cancel signal + HTTP abort. Do not wait 4s.
        _try_finalize(execution_id)
        return
    owner.grace_deadline = time.time() + CANCEL_GRACE_S
    thread = threading.Thread(
        target=_grace_then_terminate,
        args=(execution_id, owner.generation),
        daemon=True,
        name=f"op-cancel-grace-{execution_id}",
    )
    _grace_threads[execution_id] = thread
    thread.start()


def _grace_then_terminate(execution_id: str, generation: int) -> None:
    def finalize_or_retry() -> bool:
        try:
            _try_finalize(execution_id)
            return True
        except Exception:
            owner = _owners.get(execution_id)
            if owner is not None and owner.generation == generation:
                owner.diagnostics.append("finalize raised")
                owner.grace_deadline = time.time() + max(
                    CANCEL_GRACE_S, 0.01,
                )
            time.sleep(max(CANCEL_GRACE_S, 0.01))
            return False

    try:
        while True:
            owner = _owners.get(execution_id)
            if owner is None:
                if finalize_or_retry():
                    return
                continue
            if owner.generation != generation:
                return
            deadline = owner.grace_deadline or time.time()
            while time.time() < deadline:
                if owner.generation != generation:
                    return
                if owner.retired or not _owner_appears_live(owner):
                    if finalize_or_retry():
                        return
                    continue
                time.sleep(min(0.05, max(0.0, deadline - time.time())))
            owner = _owners.get(execution_id)
            if owner is None:
                if finalize_or_retry():
                    return
                continue
            if owner.generation != generation:
                return
            if owner.retired:
                if finalize_or_retry():
                    return
                continue
            if _owner_appears_live(owner):
                if not _owner_needs_process_grace(owner):
                    # Token-only owners must not get another 4s slice.
                    if finalize_or_retry():
                        return
                    return
                killed = False
                try:
                    if owner.terminate is not None:
                        killed = bool(owner.terminate())
                    else:
                        killed = _default_terminate(owner)
                except Exception:
                    killed = False
                    owner.diagnostics.append("terminate raised")
                if not killed or _owner_appears_live(owner):
                    owner.diagnostics.append("owner still alive after terminate")
                    owner.grace_deadline = time.time() + max(
                        CANCEL_GRACE_S, 0.01,
                    )
                    continue
            if finalize_or_retry():
                return
    finally:
        _drop_finished_grace_thread(execution_id)


def resume_cancel(execution_id: str) -> None:
    """Re-drive cooperative cancel + grace for a persisted cancelling record."""
    from openprogram.agent.session_db import default_db

    store = default_db()
    found = _find_execution(store, execution_id)
    if found is None:
        return
    session_id, node = found
    if _node_status(node) != "cancelling":
        return
    _request_cancel_signals(session_id, execution_id)
    if owner_is_alive(execution_id):
        _ensure_grace_watch(execution_id)
    else:
        _try_finalize(execution_id)


def mark_execution_terminal(
    execution_id: str, status: str, *, store: Any | None = None,
) -> bool:
    """CAS a non-cancel terminal write. First terminal transition wins.

    ``cancelling`` cannot become ``completed`` / ``failed`` / ``error``.
    A ``cancelled`` write is allowed only from an active status, not
    from ``cancelling`` (finalize owns that transition).
    """
    if status not in {
        "completed", "failed", "interrupted", "error", "cancelled",
    }:
        raise ValueError(status)
    store = _canonical_store(store)
    session_hint = None
    try:
        from openprogram.store import _store
        writer = _store.get()
        if writer is not None:
            session_hint = writer.session_id
            if getattr(writer, "store", None) is not None:
                store = writer.store
    except Exception:
        pass
    with _execution_cancel_lock:
        found = _find_dag_execution(
            store, execution_id, session_id=session_hint,
        )
        if found is None:
            return False
        session_id, node = found
        current = _node_status(node)
        if current in _TERMINAL_STATUSES:
            return False
        if current == "cancelling":
            return False
        if current not in _ACTIVE_STATUSES and current is not None:
            return False
        store.update_node(
            session_id,
            execution_id,
            metadata={"status": status, "finished_at": time.time()},
        )
        if status in _TERMINAL_STATUSES:
            retire_execution_owner(execution_id)
        return True


def admit_child_execution(session_id: str, parent_execution_id: str) -> None:
    """Refuse spawn when any ancestor is cancelling or cancelled."""
    from openprogram.agent.session_db import default_db

    store = default_db()
    current: str | None = parent_execution_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        node = _get_node(store, session_id, current)
        if node is not None:
            if _node_status(node) in _CANCEL_INTENT_STATUSES:
                raise ExecutionSpawnRefused("cancel.parent")
            current = node.caller or None
            continue
        job = _find_job(current)
        if job is None:
            break
        if _node_status(_job_execution_view(job)) in _CANCEL_INTENT_STATUSES:
            raise ExecutionSpawnRefused("cancel.parent")
        current = getattr(job, "parent_job_id", None)


@contextmanager
def child_execution_admission(
    session_id: str,
    parent_execution_id: str,
):
    """Hold admission through the child's durable entry write."""
    with _execution_cancel_lock:
        admit_child_execution(session_id, parent_execution_id)
        yield


def resolve_foreground_execution(session_id: str) -> str | None:
    """Best-effort current execution id for a compatibility session stop."""
    token = current_token(session_id, execution_id=None)
    if token is not None and token.execution_id:
        return token.execution_id
    with _cancel_flags_lock:
        ids = [
            key[1] for key in _current_tokens
            if key[0] == session_id and key[1]
        ]
    if len(ids) == 1:
        return ids[0]
    from openprogram.agent.session_db import default_db

    store = default_db()
    try:
        nodes = store.get_nodes(session_id)
    except Exception:
        return None
    active = [
        node for node in nodes
        if _node_status(node) in {"queued", "running", "cancelling"}
    ]
    if not active:
        return None
    roots = [node for node in active if not node.caller]
    return (roots[-1] if roots else active[-1]).id


def cancel_session_executions(session_id: str) -> list[dict[str, Any]]:
    """Cancel every non-terminal root execution in a session."""
    from openprogram.agent.session_db import default_db

    store = default_db()
    try:
        nodes = store.get_nodes(session_id)
    except Exception:
        return []
    roots = [
        node for node in nodes
        if not node.caller
        and _node_status(node) in {"queued", "running", "cancelling"}
    ]
    if not roots:
        foreground = resolve_foreground_execution(session_id)
        if foreground:
            roots = [
                node for node in nodes if node.id == foreground
            ]
    token = _cancel_reason.set("cancel.session")
    results: list[dict[str, Any]] = []
    try:
        for node in roots:
            try:
                results.append(cancel_execution(node.id))
            except (ExecutionNotFound, ExecutionNotCancellable):
                continue
    finally:
        _cancel_reason.reset(token)
    return results


def cancel_execution(execution_id: str):
    """Cancel exactly one execution and its active ``caller`` descendants."""
    from openprogram.agent.session_db import default_db

    store = default_db()
    root_reason = _cancel_reason.get()
    with _execution_cancel_lock:
        found = _find_execution(store, execution_id)
        if found is None:
            raise ExecutionNotFound(execution_id)

        session_id, root = found
        root_status = _node_status(root)
        if root_status in {"completed", "failed", "interrupted", "error"}:
            raise ExecutionNotCancellable(
                execution_id, _execution_dto(session_id, root),
            )
        if root_status in _CANCEL_INTENT_STATUSES:
            return _execution_dto(session_id, root)

        dag_descendants = _caller_descendants(store, session_id, execution_id)
        known_ids = {execution_id, *(node.id for node in dag_descendants)}
        descendants = [
            (session_id, node) for node in dag_descendants
        ]
        descendants.extend(
            (target_session_id, node)
            for target_session_id, node in _job_descendants(execution_id)
            if node.id not in known_ids
        )
        requested_at = time.time()
        targets = [(session_id, root, root_reason), *[
            (target_session_id, node, "cancel.parent")
            for target_session_id, node in descendants
        ]]
        scope_has_live_owner = any(
            _has_live_owner(target_session_id, node.id)
            for target_session_id, node, _reason in targets
        )
        for target_session_id, node, reason_code in targets:
            dag = _get_node(store, target_session_id, node.id)
            live = dag if dag is not None else node
            status = _node_status(live)
            if status in {"completed", "failed", "interrupted", "error"}:
                if node.id == execution_id:
                    raise ExecutionNotCancellable(
                        execution_id, _execution_dto(session_id, live),
                    )
                continue
            if status in _CANCEL_INTENT_STATUSES:
                continue
            if status not in _ACTIVE_STATUSES:
                continue
            has_live = _has_live_owner(target_session_id, node.id)
            final_status = (
                "cancelling"
                if has_live or (node.id == execution_id and scope_has_live_owner)
                else "cancelled"
            )
            patch = {
                "status": final_status,
                "reason_code": reason_code,
                "cancellation_requested_at": requested_at,
            }
            if final_status == "cancelled":
                patch["finished_at"] = time.time()
            wrote = False
            if dag is not None:
                wrote = _cas_write_status(
                    store,
                    target_session_id,
                    node.id,
                    from_statuses=_ACTIVE_STATUSES,
                    metadata=patch,
                )
            else:
                wrote = _persist_job_cancel_intent(
                    target_session_id,
                    node.id,
                    reason_code=reason_code,
                    requested_at=requested_at,
                    terminal=final_status == "cancelled",
                )
            if not wrote:
                if node.id == execution_id:
                    live = _get_node(store, session_id, execution_id) or live
                    live_status = _node_status(live) if live is not None else None
                    if live_status in {
                        "completed", "failed", "interrupted", "error",
                    }:
                        raise ExecutionNotCancellable(
                            execution_id,
                            _execution_dto(session_id, live),
                        )
                    if live is not None and live_status in _CANCEL_INTENT_STATUSES:
                        return _execution_dto(session_id, live)
                continue
            if final_status == "cancelled":
                retire_execution_owner(node.id)
                _finalize_projections(
                    store, target_session_id, node.id, live,
                )

        hook = _after_intent_hook
        if hook is not None:
            hook(execution_id)

        to_signal = []
        to_grace = []
        for target_session_id, node, _reason in targets:
            live = _get_node(store, target_session_id, node.id)
            if live is None:
                job = _find_job(node.id)
                live = _job_execution_view(job) if job is not None else node
            status = _node_status(live)
            if status in _CANCEL_INTENT_STATUSES or _has_live_owner(
                target_session_id, node.id,
            ):
                to_signal.append((target_session_id, node.id))
            if status == "cancelling" or _has_live_owner(
                target_session_id, node.id,
            ):
                to_grace.append(node.id)

        refreshed = _get_node(store, session_id, execution_id)
        if refreshed is None:
            job = _find_job(execution_id)
            if job is not None:
                refreshed = _job_execution_view(job)
        if refreshed is None:
            raise ExecutionNotFound(execution_id)
        result = _execution_dto(session_id, refreshed)

    for target_session_id, node_id in to_signal:
        _request_cancel_signals(target_session_id, node_id)
    for node_id in to_grace:
        _ensure_grace_watch(node_id)
    return result


def is_cancelled(
    session_id: str, *, execution_id: str | None = None,
) -> bool:
    """True while the current turn is cancelled. False once it has ended.

    A background task checking its own session resolves to its own slot,
    so a stop aimed at the foreground turn never reads as cancelled here.
    """
    if execution_id is None and _current_session_id.get(None) == session_id:
        execution_id = _current_execution_id.get(None)
    with _cancel_flags_lock:
        token = _current_tokens.get((session_id, execution_id))
    return token.is_cancelled() if token is not None else False


def clear_cancel(session_id: str) -> None:
    """Retire the session's token — the turn is over, cancelled or not."""
    end_turn(session_id)


def set_current_execution_id(execution_id: str | None):
    """Bind task-keyed cancellation/runtime ownership to this context."""
    return _current_execution_id.set(execution_id)


def clear_turn_context() -> None:
    """Drop context-bound session/execution/token (test teardown)."""
    _current_execution_id.set(None)
    _current_token.set(None)


def reset_current_execution_id(token) -> None:
    try:
        _current_execution_id.reset(token)
    except Exception:
        pass


def get_current_execution_id() -> str | None:
    return _current_execution_id.get(None)


def set_current_session_id(session_id: str):
    """Bind session_id to the current worker context. Call at the top of
    _execute_in_context. Returns the token for later reset()."""
    return _current_session_id.set(session_id)


def get_current_session_id() -> str | None:
    """The webui session bound to the current worker context, or None when
    not inside a dispatcher-driven turn (CLI / tests / headless)."""
    return _current_session_id.get()


def reset_current_session_id(token) -> None:
    """Reset the session_id ContextVar using a token from set_current_session_id."""
    try:
        _current_session_id.reset(token)
    except Exception:
        pass


def _active_token() -> "CancellationToken | None":
    """The token this frame must check.

    The context-bound token wins: it is the one the enclosing turn opened,
    so a nested frame keeps checking its own turn even if the session has
    since moved on. Falling back to the session registry covers workers
    that bind only the session id.
    """
    token = _current_token.get(None)
    if token is not None:
        return token
    cid = _current_session_id.get(None)
    eid = _current_execution_id.get(None)
    return current_token(cid, execution_id=eid) if cid else None


def _cancel_hook() -> None:
    """Pre-invocation hook: raise CancelledError if this turn was stopped.

    Registered with agentic_function's hook list, so every @agentic_function
    entry (and every Runtime.exec call) aborts once the turn is cancelled.
    """
    token = _active_token()
    if token is not None and token.is_cancelled():
        raise CancelledError(f"Execution stopped by user (conv={token.session_id})")


def check_cancelled() -> None:
    """Public cancel checkpoint usable from inside long-running tool code.

    Same semantics as ``_cancel_hook`` but exported so non-@agentic_function
    code paths (e.g. GUI-Agent observe / OCR / detector pipelines) can yield
    to the stop signal between heavy synchronous stages without waiting for
    the next @agentic_function boundary. Safe no-op when no turn is bound
    (e.g. CLI / unit test contexts).
    """
    _cancel_hook()


# Register the cancel hook once at import time.
add_pre_invocation_hook(_cancel_hook)

# Claim the core's host-integration seams for the webui. Importing this module
# is what makes the exec loop cancellable and gives Runtime.ask a session to
# route to; without it the core keeps its headless defaults.
set_cancellation_check(_cancel_hook)
set_session_id_provider(get_current_session_id)


# ---------------------------------------------------------------------------
# Active exec runtimes — keep track so /api/stop can kill the CLI subprocess.
# ---------------------------------------------------------------------------

_active_exec_runtimes: dict[tuple[str, str | None], Any] = {}
_active_exec_runtimes_lock = threading.Lock()


def register_active_runtime(
    session_id: str, rt: Any, *, execution_id: str | None = None,
) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes[(session_id, execution_id)] = rt


def unregister_active_runtime(
    session_id: str, *, execution_id: str | None = None,
) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes.pop((session_id, execution_id), None)


def has_active_runtime(session_id: str) -> bool:
    """True iff a foreground runtime is registered for this session.

    Used as a zombie check against ``_running_tasks``: an entry there
    without a paired live runtime (process died, cleanup missed) is
    stale and should be treated as no-op.
    """
    with _active_exec_runtimes_lock:
        return (session_id, None) in _active_exec_runtimes


def kill_active_runtime(
    session_id: str, *, execution_id: str | None = None,
) -> None:
    """Terminate the subprocess of the active exec runtime, if any."""
    with _active_exec_runtimes_lock:
        rt = _active_exec_runtimes.get((session_id, execution_id))
    if rt is None:
        return
    proc = getattr(rt, "_proc", None)
    if proc is None:
        return
    try:
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception:
        pass
