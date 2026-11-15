"""Execution owners: registration, retirement, grace-period termination and finalization."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import hooks
from .tokens import (
    CancellationToken,
    current_token,
)
from .records import (
    _ACTIVE_STATUSES,
    _TERMINAL_STATUSES,
    _caller_descendants,
    _canonical_store,
    _execution_dto,
    _finalize_projections,
    _find_dag_execution,
    _find_execution,
    _find_job,
    _get_node,
    _job_execution_view,
    _node_status,
)
import logging

_log = logging.getLogger(__name__)


# Server-side grace before exact-owner terminate. Tests may shorten this.
CANCEL_GRACE_S = 4.0

_execution_update_hook: Callable[[dict[str, Any]], None] | None = None

_generation_seq = 0


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

_execution_cancel_lock = threading.RLock()


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
            from openprogram.runtime.context.session_db import default_db
            found = _find_execution(
                default_db(), execution_id, session_id=session_id,
            )
            if found is not None:
                _found_session, record = found
                status = _node_status(record)
                # In-flight cancel: reconnect must keep stopping.
                # Terminal cancelled + a late process/terminate hook:
                # the owner showed up after cancel was written — stop it.
                # Terminal cancelled + token-only (chat retry on the
                # same {msg}_reply): a new attempt, do not re-trip.
                cancel_pending = status == "cancelling"
                if status == "cancelled" and (
                    is_alive is not None
                    or terminate is not None
                    or process is not None
                ):
                    cancel_pending = True
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
            # The cancel token and engine signals below still stop the owner.
            _log.debug("stop not queued for execution %s", execution_id, exc_info=True)
    signals = hooks.live_owner_signals()
    if signals is not None:
        try:
            signals.request_graceful_stop(session_id, execution_id)
        except Exception:
            # Engine signal hook: the cancel token was already set.
            _log.warning("graceful stop not requested for execution %s", execution_id, exc_info=True)


def _default_terminate(owner: _OwnerEntry) -> bool:
    killed = False
    # Subprocesses and exec runtimes belong to the engines that started them.
    signals = hooks.live_owner_signals()
    if signals is not None:
        try:
            killed = bool(signals.kill(owner.session_id, owner.execution_id))
        except Exception:
            # Fall through to terminating the owned process directly.
            _log.warning("engine kill failed for execution %s", owner.execution_id, exc_info=True)
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
    from openprogram.runtime.context.session_db import default_db

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
            # The owner names its session, as it did when it registered:
            # look there, not in every session's log (a turn's end would
            # otherwise read the whole store twice).
            try:
                found = _find_execution(
                    store, execution_id,
                    session_id=owner.session_id if owner is not None and owner.session_id else None,
                )
            except Exception:
                # A session that cannot be read has no record to finalize.
                _log.debug("execution %s: session read failed", execution_id, exc_info=True)
                found = None
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
            jobs = hooks.job_records()
            try:
                job = jobs.load(session_id, execution_id) or _find_job(execution_id)
                if job is not None and not jobs.is_terminal(job):
                    jobs.update_status(
                        getattr(job, "parent_session_id", None) or session_id,
                        execution_id,
                        "cancelled",
                        completed_at=time.time(),
                        reason_code=(node.metadata or {}).get("reason_code")
                        or "cancel.user",
                    )
            except Exception:
                # The DAG node is already cancelled; finalization continues.
                _log.warning("job %s not marked cancelled", execution_id, exc_info=True)
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
                    # Update hook boundary: a failing consumer must not block finalization.
                    _log.warning("execution update not delivered for %s", execution_id, exc_info=True)
            if node.caller:
                _try_finalize(node.caller)
            return True
        finally:
            _finalizing.discard(execution_id)


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
            if _try_finalize(execution_id):
                return True
            # A false result can be a transient ownership/finalizer race.
            # Stop only when the durable record no longer needs cancellation;
            # otherwise keep the bounded retry loop alive.
            from openprogram.runtime.context.session_db import default_db

            found = _find_execution(default_db(), execution_id)
            if found is None or _node_status(found[1]) != "cancelling":
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
    from openprogram.runtime.context.session_db import default_db

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
        from openprogram.state.store import _store
        writer = _store.get()
        if writer is not None:
            session_hint = writer.session_id
            if getattr(writer, "store", None) is not None:
                store = writer.store
    except Exception:
        # Fall back to the canonical store without a session hint.
        _log.debug("bound session writer unavailable", exc_info=True)
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
