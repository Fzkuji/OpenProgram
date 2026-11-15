"""Cancellation requests: exact execution cancel, session cancel, child admission and the canonical control service."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

from .. import hooks
from .tokens import (
    _cancel_flags_lock,
    _current_tokens,
    current_token,
)
from .errors import (
    ExecutionNotCancellable,
    ExecutionNotFound,
    ExecutionSpawnRefused,
)
from .records import (
    _ACTIVE_STATUSES,
    _CANCEL_INTENT_STATUSES,
    _TERMINAL_STATUSES,
    _caller_descendants,
    _cas_write_status,
    _execution_dto,
    _finalize_projections,
    _find_execution,
    _find_job,
    _get_node,
    _job_execution_view,
    _node_status,
)
from .owners import (
    _ensure_grace_watch,
    _execution_cancel_lock,
    _has_live_owner,
    _request_cancel_signals,
    retire_execution_owner,
)
import logging

_log = logging.getLogger(__name__)


_after_intent_hook: Callable[[str], None] | None = None

_cancel_reason: ContextVar[str] = ContextVar(
    "_cancel_reason", default="cancel.user",
)


def set_after_intent_hook(hook: Callable[[str], None] | None) -> None:
    """Test seam: run after durable cancelling is written, before signaling."""
    global _after_intent_hook
    _after_intent_hook = hook


def mark_cancelled(session_id: str, *, execution_id: str) -> None:
    """Cancel one exact execution through the canonical control path.

    ``session_id`` remains in the internal call signature so callers can
    retain their session context, but it is never used to infer a foreground
    token or to cancel another execution. A missing execution id is a caller
    error; session-scoped cancellation belongs to the canonical session
    control command and is not implemented by this helper.
    """
    if not execution_id:
        raise ValueError("execution_id is required for exact cancellation")
    with _cancel_flags_lock:
        token = _current_tokens.get((session_id, execution_id))
    try:
        result = cancel_execution(execution_id)
    except ExecutionNotCancellable:
        return
    except ExecutionNotFound:
        # The execution may be registered before its durable record is
        # visible. Signal only the exact registered execution token; never
        # infer or touch a session foreground token.
        if token is not None:
            token.cancel()
        return
    except Exception:
        if token is not None:
            token.cancel()
        return
    status = (
        result.get("status") if isinstance(result, dict)
        else getattr(result, "status", None)
    )
    if token is not None and status not in _TERMINAL_STATUSES:
        token.cancel()


def admit_child_execution(session_id: str, parent_execution_id: str, *, store=None) -> None:
    """Refuse spawn when any ancestor is cancelling or cancelled."""
    from openprogram.runtime.context.session_db import default_db

    store = default_db() if store is None else store
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
    *,
    store=None,
):
    """Hold admission through the child's durable entry write."""
    with _execution_cancel_lock:
        admit_child_execution(session_id, parent_execution_id, store=store)
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
    from openprogram.runtime.context.session_db import default_db

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
    """Cancel every non-terminal root execution in a session.

    A chat turn lives only in the execution store until its projection
    reaches the session graph, so the store is read first; graph nodes
    then cover function runs that have no canonical record.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.execution import default_store

    results: list[dict[str, Any]] = []
    canonical_ids: set[str] = set()
    for execution in default_store().list_nonterminal(session_id=session_id):
        canonical_ids.add(execution.execution_id)
        if execution.parent_execution_id:
            continue
        try:
            results.append(
                _cancel_canonical_execution(execution, reason_code="cancel.session"),
            )
        except (ExecutionNotFound, ExecutionNotCancellable):
            continue

    store = default_db()
    try:
        nodes = store.get_nodes(session_id)
    except Exception:
        return results
    roots = [
        node for node in nodes
        if not node.caller
        and _node_status(node) in {"queued", "running", "cancelling"}
    ]
    if not roots and not results:
        foreground = resolve_foreground_execution(session_id)
        if foreground:
            roots = [
                node for node in nodes if node.id == foreground
            ]
    token = _cancel_reason.set("cancel.session")
    try:
        for node in roots:
            if node.id in canonical_ids:
                continue
            try:
                results.append(cancel_execution(node.id))
            except (ExecutionNotFound, ExecutionNotCancellable):
                continue
    finally:
        _cancel_reason.reset(token)
    return results


def cancel_execution_for_reason(execution_id: str, reason_code: str):
    """:func:`cancel_execution`, recording why the system stopped it."""
    token = _cancel_reason.set(reason_code)
    try:
        return cancel_execution(execution_id)
    finally:
        _cancel_reason.reset(token)


def cancel_execution(execution_id: str):
    """Cancel exactly one execution and every execution below it."""
    canonical = _canonical_execution(execution_id)
    if canonical is not None:
        return _cancel_canonical_execution(canonical)
    # A JobStore row is only a read projection after the Job cutover.  Never
    # interpret it as an executable legacy record when its canonical identity
    # is absent; callers receive the same stable not-found result as any
    # unavailable execution.
    if _find_job(execution_id) is not None:
        raise ExecutionNotFound(execution_id)
    from openprogram.runtime.context.session_db import default_db

    store = default_db()
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

    root_reason = _cancel_reason.get()

    async def stop_root():
        return _cancel_graph_execution(store, execution_id, root_reason)

    # The canonical executions below a function run (the Jobs it started)
    # come from the same finder as every other stop; its graph-only
    # descendants are stopped with it below.
    return _run_control_awaitable(stop_with_descendants(
        execution_id, stop_root, waited_on_only=False,
    ))


def _cancel_graph_execution(store, execution_id: str, root_reason: str):
    """Stop a function run that has only a session-graph record, and the
    graph nodes it called."""
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

        descendants = [
            (session_id, node)
            for node in _caller_descendants(store, session_id, execution_id)
        ]
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
            if dag is None:
                continue
            live = dag
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
            wrote = _cas_write_status(
                store,
                target_session_id,
                node.id,
                from_statuses=_ACTIVE_STATUSES,
                metadata=patch,
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
            live = _get_node(store, target_session_id, node.id) or node
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
            raise ExecutionNotFound(execution_id)
        result = _execution_dto(session_id, refreshed)

    for target_session_id, node_id in to_signal:
        _request_cancel_signals(target_session_id, node_id)
    for node_id in to_grace:
        _ensure_grace_watch(node_id)
    return result


def _canonical_execution(execution_id: str):
    """Return the canonical record when this id belongs to the new runtime."""
    from openprogram.runtime.execution import default_store

    return default_store().get_execution(execution_id)


def _run_control_awaitable(awaitable):
    """Bridge canonical async control from both sync and event-loop callers."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(awaitable))
        except BaseException as exc:  # noqa: BLE001
            failure.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0]


def _cancel_canonical_execution(execution, *, reason_code: str | None = None):
    """The internal stop of one canonical execution and every descendant.

    The root takes the stop every surface takes (``request_cancel``): a
    running execution starts stopping, one already stopping has the stop in
    force delivered to an owner that has not taken it, and one that has
    ended answers with its final state and ``issue_code: already_terminal``,
    or :class:`ExecutionNotCancellable` when it ended other than by a stop.
    Every descendant stops with it (:func:`stop_with_descendants`).
    """
    reason_code = reason_code or _cancel_reason.get() or "cancel.user"
    return _run_control_awaitable(stop_with_descendants(
        execution.execution_id,
        lambda: _stop_one_canonical(execution, reason_code=reason_code),
        waited_on_only=False,
    ))


def _descendants_to_cancel(root_id: str, *, waited_on_only: bool = False) -> list:
    """The one answer to "what stops with this execution": live executions
    below ``root_id``, parents first.

    An execution's parents are its canonical ``parent_execution_id`` (a
    nested turn, a same-conversation Job) and, for a Job, the two ids its
    immutable input names: the execution that called it
    (``job_context.caller.execution_id``, in whichever conversation) and the
    Job that started it (``job_context.parent_execution_id``). The walk goes
    through executions that have ended, so a Job started by a sub-agent
    that has finished is still found. ``waited_on_only`` keeps to what the
    stopped turn is waiting on (a synchronous sub-agent, a nested turn): it
    leaves out a background sub-agent (a Job its caller did not wait for)
    and a Retry or Create branch (a run of its own from a saved point), and
    what is below them. A public stop takes that scope; the internal stop
    (channels' /stop, Goals, time limits) takes every descendant.
    """
    try:
        from openprogram.runtime.execution import default_store

        store = default_store()
        live = store.list_nonterminal()
    except Exception:
        _log.debug("descendants of %s not read", root_id, exc_info=True)
        return []
    depth: dict[str, int | None] = {root_id: 0}

    def depth_of(execution_id: str, record=None, trail: frozenset = frozenset()) -> int | None:
        if execution_id in depth:
            return depth[execution_id]
        if execution_id in trail:
            return None
        record = record if record is not None else store.get_execution(execution_id)
        found = None
        if record is not None and (not waited_on_only or _waited_on(store, record)):
            for parent_id in _parent_ids(store, record):
                above = depth_of(parent_id, None, trail | {execution_id})
                if above is not None and (found is None or above + 1 < found):
                    found = above + 1
        depth[execution_id] = found
        return found

    below = [
        (found, index, record) for index, record in enumerate(live)
        if record.execution_id != root_id
        and (found := depth_of(record.execution_id, record)) is not None
    ]
    return [record for _depth, _index, record in sorted(below, key=lambda item: item[:2])]


def _parent_ids(store, record) -> set[str]:
    """The executions ``record`` was started under (see :func:`_descendants_to_cancel`)."""
    ids = [record.parent_execution_id]
    try:
        payload = store.get_job_agent_input(record.execution_id)
    except Exception:
        _log.debug("job input of %s not read", record.execution_id, exc_info=True)
        payload = None
    context = payload.get("job_context") if isinstance(payload, dict) else None
    if isinstance(context, dict):
        ids.append(context.get("parent_execution_id"))
        caller = context.get("caller")
        if isinstance(caller, dict):
            ids.append(caller.get("execution_id"))
    return {item for item in ids if isinstance(item, str) and item and item != record.execution_id}


def _waited_on(store, child) -> bool:
    """Whether the execution that started ``child`` is waiting on it."""
    if child.source_checkpoint_id:
        return False
    if store.get_job_agent_input(child.execution_id) is None:
        return True
    job = _find_job(child.execution_id, session_id=child.session_id)
    return bool(getattr(job, "wait", True)) if job is not None else True


async def stop_with_descendants(execution_id: str, stop_root, *, waited_on_only: bool):
    """Stop ``execution_id`` and what stops with it, children first.

    ``stop_root`` is the root's own stop (a coroutine function) and its
    answer is returned. Every surface stops this way: the page, REST, the
    TUI and the protocol servers with ``waited_on_only`` (what the turn
    waits on), the internal stop with every descendant. The children go
    first, deepest first, so a queued child is fenced before its parent's
    ending frees the pool slot it would take; any child admitted while the
    root was being stopped is stopped after it. A root that has already
    ended takes its stop alone.
    """
    from openprogram.runtime.execution.control.shared import off_loop

    # The records are read in a worker thread (they live under the
    # conversation's lock); the stops themselves are awaited here.
    root = await off_loop(execution_id, _canonical_execution, execution_id)
    if root is not None and root.status.value in {"completed", "failed", "cancelled", "interrupted"}:
        return await stop_root()
    stopped: set[str] = set()

    async def stop_children() -> None:
        children = await off_loop(
            execution_id, _descendants_to_cancel, execution_id, waited_on_only=waited_on_only,
        )
        for child in reversed(children):
            if child.execution_id in stopped:
                continue
            stopped.add(child.execution_id)
            try:
                service = await off_loop(
                    child.execution_id, _canonical_control_service, child.execution_id,
                )
                await service.request_cancel(
                    command_id=f"execution-cancel:{child.execution_id}",
                    execution_id=child.execution_id,
                    actor={"source": "run_control"},
                    reason_code="cancel.parent",
                )
            except Exception:
                _log.warning("sub-execution %s not stopped with its parent", child.execution_id, exc_info=True)

    await stop_children()
    result = await stop_root()
    await stop_children()
    return result


async def _stop_one_canonical(execution, *, reason_code: str):
    """Submit the canonical stop of exactly ``execution``."""
    from openprogram.runtime.execution.control import ProjectionRecoveryRequired

    service = _canonical_control_service(execution.execution_id)
    command_id = f"execution-cancel:{execution.execution_id}"
    try:
        dispatch = await service.request_cancel(
            command_id=command_id,
            execution_id=execution.execution_id,
            actor={"source": "run_control"},
            reason_code=reason_code,
        )
    except ProjectionRecoveryRequired:
        current = _canonical_execution(execution.execution_id) or execution
        result = current.to_dict()
        result["issue_code"] = ProjectionRecoveryRequired.code
        result["recovery_required"] = True
        return result
    result = dispatch.execution.to_dict()
    command = dispatch.command
    if command is None:
        # Ended before this stop. A cancel that ended it may have left its
        # projection to recovery; that stays visible.
        try:
            command = service.executions.get_command(command_id)
        except Exception:
            command = None
    if command is not None and command.rejection_code == ProjectionRecoveryRequired.code:
        result["issue_code"] = ProjectionRecoveryRequired.code
        result["recovery_required"] = True
        return result
    if dispatch.issue_code == "already_terminal" and dispatch.execution.status.value != "cancelled":
        # Ended other than by a stop: nothing here can be cancelled.
        raise ExecutionNotCancellable(execution.execution_id, result)
    if dispatch.issue_code:
        result["issue_code"] = dispatch.issue_code
    return result


def _canonical_control_service(execution_id: str):
    """Use the JobRunner's live registry when this is a public Job."""
    from openprogram.runtime.execution import default_control_service

    try:
        from openprogram.runtime.execution import default_store

        runner = hooks.job_runner(default_store())
        if runner is not None:
            canonical = runner._execution_store.get_execution(execution_id)
            input_record = runner._execution_store.get_execution_input(execution_id)
            if (
                canonical is not None
                and input_record is not None
                and input_record.input_ref.startswith("job-input-v1:")
            ):
                return runner._execution_control
    except Exception:
        # Fall back to the default control service.
        _log.debug("job runner control lookup failed for %s", execution_id, exc_info=True)
    return default_control_service()
