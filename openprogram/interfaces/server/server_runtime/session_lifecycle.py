"""Server session lifecycle."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from .. import server as state
from .run_slots import (
    SELECTED,
    ANNOUNCED_KINDS,
    branch_running_refusal,
    busy_refusal,
    conversation_wide_operation,
    forget_operation,
    holds_its_slot,
    locked_refusal,
    new_slot,
    repeat_refusal,
    slot_with_key,
)

_log = logging.getLogger(__name__)


def _cleanup_session_resources(session_id: str, conv: dict):
    """Clean up all resources associated with a deleted conversation."""
    # Clean up follow-up queues and running tasks
    with state._follow_up_lock:
        state._follow_up_queues.pop(session_id, None)
    with state._running_tasks_lock:
        state._running_tasks.pop(session_id, None)
        for key in [key for key in state._branch_running_tasks if key[0] == session_id]:
            state._branch_running_tasks.pop(key, None)


def _stop_session_work(session_id: str, *, reason: str) -> list[str]:
    """Stop everything still working for a session that is going away.

    Cancels every unfinished root execution (its descendants follow the
    cancel), closes the session's open questions and approvals, releases
    the run slot and drops the queued inbox entries. Each step is
    best-effort: a deleted session must still be deleted when one of them
    fails. Returns the execution ids a cancel was requested for.
    """
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.waits import DurableWaitStore
    from openprogram.runtime.execution.run_control.cancellation import (
        _cancel_canonical_execution,
    )

    cancelled: list[str] = []
    try:
        store = default_store()
        executions = store.list_nonterminal(session_id=session_id)
    except Exception as exc:
        state._log(f"[stop_session_work] {session_id} list: {exc}")
        store, executions = None, []
    for execution in executions:
        if execution.parent_execution_id:
            continue
        try:
            _cancel_canonical_execution(execution, reason_code="cancel.session")
            cancelled.append(execution.execution_id)
        except Exception as exc:
            state._log(f"[stop_session_work] cancel {execution.execution_id}: {exc}")
        state._release_session_occupancy_for_execution(
            {"session_id": session_id, "execution_id": execution.execution_id},
        )
    if store is not None:
        # A cancel closes its own execution's waits; a wait whose execution
        # could not be cancelled must still stop prompting the user.
        try:
            waits = DurableWaitStore(store)
            for execution_id in {w.execution_id for w in waits.list_open(session_id=session_id)}:
                waits.cancel_execution(execution_id)
        except Exception as exc:
            state._log(f"[stop_session_work] waits {session_id}: {exc}")
    with state._running_tasks_lock:
        task = state._running_tasks.pop(session_id, None)
        state._unregister_active_runtime(session_id)
        branch_tasks = [(key[1], state._branch_running_tasks.pop(key))
                        for key in list(state._branch_running_tasks) if key[0] == session_id]
    if task is not None:
        state._emit_running_task_event(
            session_id,
            cleared_msg_id=task.get("msg_id"),
            cleared_execution_id=task.get("execution_id"),
        )
    for branch_id, branch_task in branch_tasks:
        state._emit_branch_running_task_event(
            session_id, branch_id,
            cleared_msg_id=branch_task.get("msg_id"),
            cleared_execution_id=branch_task.get("execution_id"),
        )
    try:
        from openprogram.runtime.conversation.peer_agents import inbox
        inbox.clear(session_id, reason=reason)
    except Exception as exc:
        state._log(f"[stop_session_work] inbox {session_id}: {exc}")
    return cancelled


#: Fields ``_save_session`` keeps that a reopened conversation shows again.
_SAVED_USAGE_KEYS = ("_chat_usage", "_last_context_stats", "_last_exec_session", "_last_exec_cumulative_usage")


def _get_or_create_session(session_id: str = None,
                                agent_id: str = None,
                                *,
                                channel: str = None,
                                account_id: str = None,
                                peer: str = None) -> dict:
    """Get or create a conversation with its own DAG session + Runtime.

    If ``agent_id`` is provided the new conversation is bound to that
    agent; otherwise it lands in the registry's default agent. Existing
    conversations keep whatever agent they were created under — we
    never rebind on lookup.

    The optional ``channel`` / ``account_id`` / ``peer`` triple binds the
    new conversation to a chat channel (e.g. ``wechat`` + ``baby``).
    Ignored on lookup of existing conversations — call
    ``set_conversation_channel`` to change them after creation.
    """
    if session_id is None:
        session_id = "local_" + uuid.uuid4().hex[:10]
    with state._sessions_lock:
        known = session_id in state._sessions
    if not known:
        # Read outside the lock: a first read of a long conversation must
        # not hold every other conversation's lookup.
        try:
            from openprogram.runtime.context.session_db import default_db
            _db = default_db()
            _hydrated = _db.get_branch(session_id) or []
            _sess = _db.get_session(session_id)
            _hydrated_head = _sess.get("head_id") if _sess else None
        except Exception:
            _hydrated = []
            _sess = None
            _hydrated_head = None
    with state._sessions_lock:
        if session_id not in state._sessions:
            resolved_agent = agent_id or state._default_agent_id()
            if known:
                # Removed while unlocked: read it again.
                from openprogram.runtime.context.session_db import default_db
                _hydrated = default_db().get_branch(session_id) or []
                _sess = default_db().get_session(session_id)
                _hydrated_head = _sess.get("head_id") if _sess else None
            resolved_agent = (
                agent_id
                or ((_sess or {}).get("agent_id") if isinstance(_sess, dict) else None)
                or resolved_agent
            )
            # A new conversation follows its Agent's model (read at each
            # turn, never copied onto it); only a model the person picked
            # in the draft is stored as the branch's own. Existing rows keep
            # their stored pick.
            _is_new = not isinstance(_sess, dict) or not _sess
            _inherit_prov = None
            _inherit_model = None
            if _is_new and state._user_pinned_provider and state._user_pinned_model:
                _inherit_prov = state._user_pinned_provider
                _inherit_model = state._user_pinned_model
                state._user_pinned_provider = None
                state._user_pinned_model = None
            state._log(
                f"[_get_or_create_session] creating {session_id!r} "
                f"inherit_prov={_inherit_prov!r} inherit_model={_inherit_model!r}"
            )
            state._sessions[session_id] = {
                "id": session_id,
                "runtime": None,          # created lazily on first message
                "agent_id": resolved_agent,  # so _resolve's agent-default tier fires
                "provider_name": ((_sess or {}).get("provider_name") if isinstance(_sess, dict) else None)
                                 or _inherit_prov,
                # Read the persisted per-conv override back (same pattern as
                # provider_name above); fall back to the global pinned value.
                # Without this a rebuilt session loses the user's model pick and
                # dispatch falls back to the agent/global default.
                "provider_override": ((_sess or {}).get("provider_override") if isinstance(_sess, dict) else None)
                                     or _inherit_prov,
                "model_override": ((_sess or {}).get("model_override") if isinstance(_sess, dict) else None)
                                  or _inherit_model,
                "messages": _hydrated,
                "head_id": _hydrated_head,
                "tools_override": ((_sess or {}).get("tools_override") if isinstance(_sess, dict) else None),
                "thinking_effort": ((_sess or {}).get("thinking_effort") if isinstance(_sess, dict) else None),
                "permission_mode": ((_sess or {}).get("permission_mode") if isinstance(_sess, dict) else None),
                # What the last save kept of the conversation's usage
                # (``_save_session``), shown until the next turn measures it.
                **{key: _sess.get(key) for key in _SAVED_USAGE_KEYS
                   if isinstance(_sess, dict) and _sess.get(key) is not None},
            }
            # session.start on the bus — plugin subscribers included.
            # emit_safe swallows failures: never break session creation.
            from openprogram.runtime.events import emit_safe
            emit_safe("session.start", "system", {
                "session_id": session_id,
                "agent_id": resolved_agent,
                "channel": channel,
            }, {"session": session_id})
            if _is_new and _inherit_prov and _inherit_model:
                try:
                    from openprogram.runtime.conversation.session_settings.session_model import write_session_chat_model
                    write_session_chat_model(
                        session_id, _inherit_prov, _inherit_model, agent_id=resolved_agent)
                except Exception:
                    # The session opens on the default model instead of the
                    # inherited one.
                    _log.warning(
                        "inherited chat model not written for session %s", session_id, exc_info=True)
        return state._sessions[session_id]


def _selected_branch(session_id: str) -> str | None:
    """The branch selected now (``active_branch_id``), or ``None`` unread."""
    try:
        from openprogram.runtime.context.session_db import default_db
        return (default_db().get_session(session_id) or {}).get("active_branch_id")
    except Exception:
        _log.debug("selected branch not read for %s", session_id, exc_info=True)
        return None


def _canonical_foreground_task(session_id: str) -> dict | None:
    """The durable running turn on the branch selected now."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.foreground import active_foreground_task

    return active_foreground_task(default_store(), session_id,
                                  selected_branch=_selected_branch(session_id))


def _reported_run_task(session_id: str, slot: dict | None) -> dict | None:
    """The selected branch's turn as clients are told of it: what its
    ``running_task`` names and what its Stop cancels.

    The durable record first, keeping the kind and id of the slot's
    operation when the two are the same turn. A durable turn that is still
    queued for the slot, with no attempt started, has not run yet: while
    another turn holds the slot and runs (a background-command notice
    ahead of a Goal's next turn), the one running is reported. ``slot`` is
    a copy of the selected branch's slot."""
    task = state._canonical_foreground_task(session_id)
    if task is None:
        return slot
    if slot and slot.get("msg_id") == task.get("msg_id"):
        # The durable turn is the slot's operation (a retry, an edit, a
        # function run): it keeps the slot's kind and id.
        return {**task, **{key: slot[key] for key in ("op_id", "op_kind", "op_version") if key in slot}}
    if slot and not slot.get("_reserved") and _waits_for_slot(task):
        return slot
    return task


def _waits_for_slot(task: dict) -> bool:
    """Whether a durable turn is queued and has not started an attempt."""
    if task.get("status") != "queued" or not task.get("execution_id"):
        return False
    from openprogram.runtime.execution import default_store
    execution = default_store().get_execution(task["execution_id"])
    return execution is not None and execution.current_attempt_id is None


def _is_run_active(session_id: str) -> bool:
    """Is any turn of this conversation running, on any branch?

    The guard of every operation that moves the head or rewrites history
    (retry, edit, rewind, merge, delete): none of them may run
    while a turn writes to the conversation, whichever branch it is on.
    """
    return state._is_selected_run_active(session_id) or state._any_branch_run_active(session_id)


def _is_selected_run_active(session_id: str) -> bool:
    """Is the selected branch's turn in flight?

    What ``run_active`` has always reported, and the run slot a message to
    the selected branch takes. Driven off ``_running_tasks`` — the same
    dict we use for pause / stop, so we can't drift out of sync. A turn on
    another branch has its own slot (``_branch_running_tasks``).
    """
    # A durable continuation no longer owns the initial transport thread.
    if state._canonical_foreground_task(session_id) is not None:
        return True
    with state._running_tasks_lock:
        task = state._running_tasks.get(session_id)
        # A chat handler reserves the session before it mutates the DAG and
        # before its runtime thread exists. Treat that short state as active;
        # otherwise a second handler would delete the reservation as a
        # "zombie" and both turns could pass the guard.
        stale_reservation = False
        stale_task = None
        if task and task.get("_reserved"):
            if (not holds_its_slot(task)
                    and state.time.time() - task.get("started_at", 0) > 300):
                stale_task = state._running_tasks.pop(session_id, None)
                stale_reservation = True
            else:
                return True
    if stale_reservation:
        state._emit_running_task_event(
            session_id,
            cleared_msg_id=(stale_task or {}).get("msg_id"),
            cleared_execution_id=(stale_task or {}).get("execution_id"),
        )
        return False
    if task is None:
        # Runtime registration precedes reservation handoff. If another
        # observer arrives in that interval, the runtime itself still blocks
        # a second turn even if stop/cleanup just removed the task entry.
        return state._has_active_runtime(session_id)
    # Zombie entry (no live runtime registered) → not actually running.
    # Drop it so subsequent calls don't keep blocking Edit/Retry/etc.
    if not state._has_active_runtime(session_id):
        with state._running_tasks_lock:
            current = state._running_tasks.get(session_id)
            if current is not task:
                # A concurrent observer may already have removed the stale
                # owner and admitted another turn. Never clear its reservation.
                return current is not None or state._has_active_runtime(session_id)
            if state._has_active_runtime(session_id):
                return True
            zombie = state._running_tasks.pop(session_id, None)
        state._emit_running_task_event(
            session_id,
            cleared_msg_id=(zombie or {}).get("msg_id"),
            cleared_execution_id=(zombie or {}).get("execution_id"),
        )
        return False
    return True


def _run_identity(session_id: str) -> dict | None:
    """The turn that makes a session active, as the ids its clear will name.

    A client that learns ``run_active`` from a load holds a running slot until
    a ``running_task_clear`` matches it; an anonymous slot matches nothing, so
    the load hands over the identity together with the flag. The execution's
    ``status_version`` rides along when the turn has one: a Stop is an exact
    command, so a client that reconnected mid-turn needs the version to cancel
    the turn it sees rather than only pretending to.

    Every reader that tells a client about the run (a load, ``get_run_state``,
    ``running_branches``) goes through here. A reservation's release always
    sends the clear that names it, so one handed out here is closed too.
    """
    with state._running_tasks_lock:
        current = state._running_tasks.get(session_id)
        slot = dict(current) if current else None
    task = _reported_run_task(session_id, slot)
    if not task:
        return None
    return operation_identity(task)


def operation_identity(task: dict) -> dict:
    """The ids a client names a running operation by: its turn ids, its
    ``op_id`` and ``op_kind``, and the version an exact Stop needs."""
    identity = {
        "msg_id": task.get("msg_id") or "",
        "execution_id": task.get("execution_id") or "",
        "op_kind": task.get("op_kind") or "reply",
    }
    if task.get("op_id"):
        identity["op_id"] = task["op_id"]
    version = task.get("status_version")
    if identity["execution_id"] and isinstance(version, int) and not isinstance(version, bool):
        identity["status_version"] = version
    elif not identity["execution_id"] and isinstance(task.get("op_version"), int):
        # An operation with no execution is stopped by its op_id at this version.
        identity["status_version"] = task["op_version"]
    return identity


def _try_reserve_run(session_id: str, msg_id: str, *, key: str | None = None,
                     lock_wait: float | None = None) -> bool:
    """Take the selected branch's slot for a turn. A turn that holds it but
    has already sent its terminal frame is waited out (``_outwait_finishing``).

    ``False`` means only that the slot is held. A conversation whose lock
    is not had within :func:`admission_lock_wait` raises
    :class:`SessionLockTimeout` instead: nothing was found running, and
    the caller says so (``locked_refusal``) rather than "a reply is
    running". Call it off the event loop: it may wait for the lock.

    ``lock_wait`` bounds the wait for the lock (``None``: the store's own
    wait). ``0`` waits for nothing, neither the lock nor a finishing turn,
    for a caller that would rather skip than wait (a room event)."""
    if lock_wait is not None and lock_wait <= 0:
        return _try_reserve_run_once(session_id, msg_id, key=key, lock_wait=0.0)
    deadline = time.monotonic() + FINISHING_WAIT_SECONDS
    while True:
        if _try_reserve_run_once(session_id, msg_id, key=key, lock_wait=lock_wait):
            return True
        if not _outwait_finishing(session_id, None, key, deadline):
            return False


def _try_reserve_run_once(session_id: str, msg_id: str, *, key: str | None = None,
                          lock_wait: float | None = None) -> bool:
    from openprogram.state.store.session.files.session_lock import session_interprocess_lock
    wait = admission_lock_wait() if lock_wait is None else max(0.0, lock_wait)
    with session_interprocess_lock(session_id, timeout=wait):
        return state._try_reserve_run_locked(session_id, msg_id, key=key)


def admission_lock_wait() -> float:
    """How long taking a slot waits for the conversation's lock.

    The store's own wait (``DEFAULT_WAIT``): a holder does file work only,
    such as the first load of a large conversation or a session move, so
    a request waits it out the way a store write does instead of reading
    a held lock as a running turn. Past it, the lock is reported as what
    it is (``locked_refusal``)."""
    from openprogram.state.store.session.files import session_lock
    return session_lock.DEFAULT_WAIT


#: How long a request waits for a turn that has sent its terminal frame
#: (``result`` or ``error``) to give its slot back. The finish-up after that
#: frame (closing the execution record, the context figures, the clear)
#: takes about a tenth of a second; a holder still there after this is not
#: finishing, and the request is refused as before.
FINISHING_WAIT_SECONDS = 5.0


def mark_run_finishing(session_id: str, owner: str) -> bool:
    """Note on the slot ``owner`` holds, wherever a switch moved it, that its
    turn is about to send its terminal frame; ``False`` when it holds none.

    From then on the slot is still held (the turn still writes its
    execution record and figures) but is only finishing: a request for the
    branch waits for it to be given back instead of being refused."""
    from .run_slots import slot_items
    with state._running_tasks_lock:
        for _where, task in slot_items(session_id):
            if task.get("msg_id") == owner:
                task["finishing"] = True
                return True
    return False


def _outwait_finishing(session_id: str, branch_id: str | None, key: str | None,
                       deadline: float) -> bool:
    """Wait for the slot of ``branch_id`` (``None``: the selected branch) to
    be given back when its turn has already sent its terminal frame.

    ``True`` once it is free, so the caller tries again; ``False`` when the
    holder is not finishing, is this same request (its key), or is still
    there at ``deadline``. Waits with no lock held, so the finishing turn
    can take whatever locks its finish-up needs."""
    with state._running_tasks_lock:
        task = (state._running_tasks.get(session_id) if branch_id is None
                else state._branch_running_tasks.get((session_id, branch_id)))
        if not task or not task.get("finishing"):
            return False
        if key and task.get("op_key") == key:
            return False
        op_id = task.get("op_id")
    return bool(op_id) and _wait_for_slot_end(session_id, op_id, deadline)


def selected_run_blocks(session_id: str) -> bool:
    """Whether a turn on the selected branch stands in the way of a request
    that needs the branch still, after waiting out one that is only
    finishing (it has sent its terminal frame)."""
    deadline = time.monotonic() + FINISHING_WAIT_SECONDS
    while state._is_selected_run_active(session_id):
        if not _outwait_finishing(session_id, None, None, deadline):
            return True
    return False


#: How long a repeat of a keyed rewind, revert or reapply waits for the
#: request holding the slot with its key: the store's own budget for a
#: conversation lock.
REPEAT_JOIN_SECONDS = 60.0


def keyed_slot(session_id: str, key: str | None) -> dict | None:
    """A copy of the slot held by the request keyed ``key``, if one holds one."""
    if not key:
        return None
    with state._running_tasks_lock:
        task = slot_with_key(session_id, key)
        return dict(task) if task is not None else None


def repeated_request_refusal(session_id: str, key: str | None) -> dict | None:
    """``request_admitted`` when the send keyed ``key`` holds a slot of
    this conversation: a repeat of it is that send, not a second one."""
    task = keyed_slot(session_id, key)
    return repeat_refusal(task) if task is not None else None


def _wait_for_slot_end(session_id: str, op_id: str | None, deadline: float) -> bool:
    """Wait until no slot of the conversation holds operation ``op_id``;
    ``False`` when the deadline passes first."""
    from .run_slots import slot_items
    while True:
        with state._running_tasks_lock:
            held = any(task.get("op_id") == op_id for _key, task in slot_items(session_id))
        if not held:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def reserve_operation(session_id: str, kind: str, owner: str, *,
                      branch_id: str | None = None, scope: str = "branch",
                      key: str | None = None, on_repeat: str = "join") -> dict | None:
    """Start a foreground operation on one branch: ``None`` when it holds the
    slot, else the refusal (``code``, ``kind`` of what is running, ``error``).

    ``key`` is the request's own key. A request whose key is on the slot
    in the way repeats the operation holding it: with ``on_repeat="join"``
    (a rewind, revert or reapply, whose store returns the first outcome for
    a key) it waits for that operation to end, up to
    ``REPEAT_JOIN_SECONDS``, and then takes the slot; with
    ``on_repeat="refuse"`` (a send or a function run) it is answered
    ``request_admitted`` at once. Any other request is refused as before.
    """
    deadline = time.monotonic() + REPEAT_JOIN_SECONDS
    finishing_deadline = time.monotonic() + FINISHING_WAIT_SECONDS
    looked_again = False
    while True:
        refusal = _reserve_operation_once(session_id, kind, owner, branch_id=branch_id,
                                          scope=scope, key=key)
        if refusal is not None and _outwait_finishing(session_id, branch_id, key,
                                                      finishing_deadline):
            continue
        if refusal is None or not key:
            return refusal
        holder = keyed_slot(session_id, key)
        if holder is None:
            # The operation in the way may be this request's own first
            # attempt, ended between the refusal and this look: ask once
            # more instead of refusing a repeat whose first attempt is done.
            if looked_again or on_repeat != "join":
                return refusal
            looked_again = True
            continue
        if on_repeat != "join":
            return repeat_refusal(holder)
        if not _wait_for_slot_end(session_id, holder.get("op_id"), deadline):
            return refusal


def _reserve_operation_once(session_id: str, kind: str, owner: str, *,
                            branch_id: str | None = None, scope: str = "branch",
                            key: str | None = None) -> dict | None:
    """Start a foreground operation on one branch: ``None`` when it holds the
    slot, else the refusal (``code``, ``kind`` of what is running, ``error``).

    ``kind`` is what the operation is (``compact``, ``rewind``, ``retry``,
    ``edit``, ``branch``, ``function``); ``owner`` names it the way a turn's
    message id does. ``branch_id`` ``None`` is the selected branch. Branches
    never block each other: only the operation's own branch has to be free.
    ``scope="conversation"`` (a rewind that changes files) is refused while
    any other branch runs or waits on an approval, naming that branch, and
    while it runs no branch is admitted. Every check runs under the
    session's process lock, the lock a branch's admission takes.
    """
    from openprogram.state.store.session.files.session_lock import (
        SessionLockTimeout,
        session_interprocess_lock,
    )
    try:
        with session_interprocess_lock(session_id, timeout=admission_lock_wait()):
            refusal = _operation_refusal(session_id)
            if refusal is not None:
                return refusal
            busy = (state._is_selected_run_active(session_id) if branch_id is None
                    else state._branch_run_active(session_id, branch_id))
            if busy:
                return _busy_slot_refusal(session_id, branch_id)
            if scope == "conversation":
                blockers = _operation_blockers(session_id, branch_id)
                if blockers:
                    return branch_running_refusal(blockers)
            if branch_id is None:
                if not state._try_reserve_run_locked(session_id, owner, kind=kind, scope=scope,
                                                     key=key):
                    return _busy_slot_refusal(session_id, None)
            elif not _reserve_branch_slot_locked(session_id, branch_id, owner, kind=kind,
                                                 scope=scope, key=key):
                return _busy_slot_refusal(session_id, branch_id)
            _record_operation(session_id, owner, branch_id)
    except SessionLockTimeout as exc:
        return locked_refusal(exc.waited, exc.holder)
    except (TimeoutError, BlockingIOError):
        return busy_refusal("reply", other_branch=True)
    if kind in ANNOUNCED_KINDS:
        # Every client shows it as this branch's run, with its kind and Stop.
        if branch_id is None:
            state._emit_running_task_event(session_id)
        else:
            state._emit_branch_running_task_event(session_id, branch_id)
    return None


def reserve_branch_operation(session_id: str, owner: str, *, target_head: str | None = None,
                             subtree: bool = False) -> dict | None:
    """Hold the selected branch for a branch delete: ``None`` when held,
    else the refusal.

    Branches never block each other, so a turn on another branch does not
    refuse it, unless the delete lands on that branch: ``target_head`` is
    its tip, or the removed subtree (``subtree=True``) holds its tip. The
    refusal names that branch. The caller releases ``owner`` with
    ``_release_run_reservation`` when the operation ends. Switching the
    selected branch is not a branch operation (``switch_branch``).
    """
    refusal = reserve_operation(session_id, "branch", owner)
    if refusal is not None:
        return refusal
    blocker = _running_branch_at(session_id, target_head=target_head, subtree=subtree)
    if blocker is None:
        return None
    _release_run_reservation(session_id, owner)
    refusal = busy_refusal(blocker.get("op_kind") or "reply", branch_id=blocker.get("branch_id"),
                           op_id=blocker.get("op_id"), other_branch=True)
    name = blocker.get("branch_name")
    label = f"Branch “{name}”" if name else "Another branch"
    refusal["error"] = f"{label} is running there; stop it first or wait for it to end."
    refusal["blocking"] = blocker
    return refusal


def branches_through(session_id: str, node_ids) -> list[str]:
    """The recorded branches whose line holds any of ``node_ids``.

    A node on a common ancestor belongs to every branch that leaves from
    below it; a node only one branch reached belongs to that one."""
    wanted = {node for node in node_ids or () if node}
    if not wanted:
        return []
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        row = db.get_session(session_id) or {}
    except Exception:
        _log.debug("branch records not read for %s", session_id, exc_info=True)
        return []
    heads = {key: ref.get("head_id") for key, ref in (row.get("branch_refs") or {}).items()
             if isinstance(ref, dict) and ref.get("status") != "aborted" and ref.get("head_id")}
    active = row.get("active_branch_id")
    if active and row.get("head_id"):
        heads[active] = row.get("head_id")
    found = []
    for key, head in heads.items():
        if head in wanted:
            found.append(key)
            continue
        try:
            chain = db.get_branch(session_id, head) or []
        except Exception:
            chain = []
        if any(message.get("id") in wanted for message in chain):
            found.append(key)
    return sorted(found)


def running_branch_among(session_id: str, branch_ids) -> dict | None:
    """The first of ``branch_ids`` with a turn or an operation on it, as
    the identity a Stop needs and its name; ``None`` when all are free."""
    selected = _selected_branch(session_id)
    names, _ = _branch_names(session_id)
    for branch_id in sorted({b for b in branch_ids or () if b}):
        if branch_id == selected and state._is_selected_run_active(session_id):
            identity = {**(state._run_identity(session_id) or {}), "branch_id": branch_id,
                        "selected": True}
        elif state._branch_run_active(session_id, branch_id):
            with state._running_tasks_lock:
                task = dict(state._branch_running_tasks.get((session_id, branch_id)) or {})
            identity = {**(_branch_identity(task) if task else {}), "branch_id": branch_id}
        else:
            continue
        if branch_id in names:
            identity["branch_name"] = names[branch_id]
        return identity
    return None


def _branches_refusal(blocker: dict) -> dict:
    """The refusal naming the branch an operation would land on, with what
    a "Stop it" button needs (``blocking``)."""
    refusal = busy_refusal(blocker.get("op_kind") or "reply", branch_id=blocker.get("branch_id"),
                           op_id=blocker.get("op_id"), other_branch=not blocker.get("selected"))
    name = blocker.get("branch_name")
    label = ("This branch" if blocker.get("selected")
             else f"Branch “{name}”" if name else "Another branch")
    refusal["error"] = f"{label} is running; stop it first or wait for it to end."
    refusal["blocking"] = blocker
    return refusal


def reserve_branches_operation(session_id: str, branch_ids, kind: str,
                               owner: str, *, key: str | None = None) -> tuple[dict | None, list[str]]:
    """Hold every branch an operation touches: ``(None, owners)`` when all
    are held, else ``(refusal, [])`` naming the busy branch.

    Only the branches the operation touches have to be free (a delete on a
    common ancestor touches every branch below it; an Agent switch only its
    own; a merge its source and target): a turn on any other branch keeps
    running. Branches are taken in sorted order, so two operations that
    overlap cannot hold one each and wait for the other. Each branch gets
    its own owner (``<owner>@<branch>``); ``release_branches_operation``
    gives them back. ``key`` joins a repeat of the keyed operation that
    holds a branch (``reserve_operation``)."""
    selected = _selected_branch(session_id)
    targets = sorted({b for b in branch_ids or () if b}) or [selected or ""]
    held: list[str] = []
    for branch_id in targets:
        slot_owner = f"{owner}@{branch_id or 'selected'}"
        refusal = reserve_operation(session_id, kind, slot_owner, key=key,
                                    branch_id=None if not branch_id or branch_id == selected else branch_id)
        if refusal is not None:
            release_branches_operation(session_id, held)
            if refusal.get("code") == "conversation_locked":
                return refusal, []
            blocker = running_branch_among(session_id, [branch_id]) or {
                "branch_id": branch_id or selected, "selected": not branch_id or branch_id == selected}
            return {**refusal, **_branches_refusal(blocker)}, []
        held.append(slot_owner)
    return None, held


def release_branches_operation(session_id: str, owners) -> None:
    for owner in owners or ():
        _release_run_reservation(session_id, owner)


def _running_branch_at(session_id: str, *, target_head: str | None,
                       subtree: bool) -> dict | None:
    """The running turn on another branch that an operation at
    ``target_head`` would land on, if any."""
    if not target_head:
        return None
    tasks = state._side_branch_tasks(session_id)
    if not tasks:
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        row = db.get_session(session_id) or {}
    except Exception:
        _log.debug("branch records not read for %s", session_id, exc_info=True)
        db, row = None, {}
    refs = row.get("branch_refs") or {}
    names, _selected = _branch_names(session_id)
    for task in tasks:
        branch_id = task.get("branch_id")
        head = (refs.get(branch_id) or {}).get("head_id") if branch_id else None
        hit = False
        if head:
            if head == target_head:
                hit = True
            elif subtree and db is not None:
                try:
                    chain = db.get_branch(session_id, head) or []
                except Exception:
                    chain = []
                hit = any(message.get("id") == target_head for message in chain)
        if hit:
            identity = _branch_identity(task)
            if branch_id in names:
                identity["branch_name"] = names[branch_id]
            return identity
    return None


def _operation_refusal(session_id: str) -> dict | None:
    """Refused on every branch: a session move, or a conversation-wide operation."""
    try:
        from openprogram.state.store.session.files.migration import session_hold_active
        from openprogram.state.paths import get_state_dir
        if session_hold_active(Path(get_state_dir()) / "projects", session_id):
            return {"code": "session_migration", "kind": "branch",
                    "error": "This conversation is being moved; try again in a moment."}
    except Exception:
        return {"code": "session_migration", "kind": "branch",
                "error": "This conversation is being moved; try again in a moment."}
    with state._running_tasks_lock:
        wide = conversation_wide_operation(session_id)
    if wide is None:
        wide = _workspace_hold(session_id)
    if wide is not None:
        return busy_refusal(wide.get("op_kind") or wide.get("kind"), op_id=wide.get("op_id"),
                            other_branch=True)
    return None


def _busy_slot_refusal(session_id: str, branch_id: str | None) -> dict:
    """The refusal naming what holds this branch's slot."""
    if branch_id is None:
        with state._running_tasks_lock:
            task = dict(state._running_tasks.get(session_id) or {})
        task = task or (state._canonical_foreground_task(session_id) or {})
    else:
        with state._running_tasks_lock:
            task = dict(state._branch_running_tasks.get((session_id, branch_id)) or {})
    return busy_refusal(task.get("op_kind") or "reply", branch_id=branch_id, op_id=task.get("op_id"))


def _workspace_hold(session_id: str) -> dict | None:
    try:
        from openprogram.state.store.session.operations import workspace_hold
        return workspace_hold(session_id)
    except Exception:
        _log.debug("workspace hold not read for %s", session_id, exc_info=True)
        return None


def _record_operation(session_id: str, owner: str, branch_id: str | None) -> None:
    """Write the operation's durable record, so a restart marks it cancelled."""
    with state._running_tasks_lock:
        task = (state._running_tasks.get(session_id) if branch_id is None
                else state._branch_running_tasks.get((session_id, branch_id)))
        if not task or task.get("msg_id") != owner or not task.get("op_id"):
            return
        op = {"op_id": task["op_id"], "kind": task.get("op_kind"), "branch_id": branch_id,
              "scope": task.get("op_scope"), "started_at": task.get("started_at")}
    try:
        from openprogram.state.store.session.operations import record_operation
        record_operation(session_id, op)
    except Exception:
        if op["scope"] == "conversation":
            # Without its record the hold would not reach other processes.
            with state._running_tasks_lock:
                state._run_slots.pop((session_id, branch_id or SELECTED), None)
            raise TimeoutError("operation record not written")
        _log.debug("operation record not written for %s", session_id, exc_info=True)
        return
    with state._running_tasks_lock:
        task["_recorded"] = True


def _operation_blockers(session_id: str, branch_id: str | None) -> list[dict]:
    """What runs or waits on the conversation's other branches, each with
    the ids a Stop needs and its branch's name."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.foreground import open_root_turns

    blockers: dict[str, dict] = {}
    for task in state._side_branch_tasks(session_id):
        if task.get("branch_id") == branch_id:
            continue
        key = task.get("execution_id") or task.get("op_id") or task.get("msg_id") or ""
        blockers[key] = {**_branch_identity(task)}
    try:
        open_turns = open_root_turns(default_store(), session_id)
    except Exception:
        _log.debug("open turns not read for %s", session_id, exc_info=True)
        open_turns = []
    for turn in open_turns:
        if branch_id is not None and turn.get("branch_id") == branch_id:
            continue
        blockers.setdefault(turn["execution_id"], {**_branch_identity(turn)})
    names, selected = _branch_names(session_id)
    out = []
    for blocker in blockers.values():
        if blocker.get("branch_id") in names:
            blocker["branch_name"] = names[blocker["branch_id"]]
        if branch_id is None and blocker.get("branch_id") in (None, selected):
            blocker["selected"] = True
        out.append(blocker)
    return out


def _branch_names(session_id: str) -> tuple[dict[str, str], str | None]:
    """Each recorded branch's name, and the selected branch's id."""
    try:
        from openprogram.runtime.context.session_db import default_db
        row = default_db().get_session(session_id) or {}
    except Exception:
        return {}, None
    names = {}
    for key, ref in (row.get("branch_refs") or {}).items():
        if not isinstance(ref, dict):
            continue
        label = ref.get("name") or ref.get("label")
        name = label.get("name") if isinstance(label, dict) else label
        if isinstance(name, str) and name:
            names[key] = name
    return names, row.get("active_branch_id")


def cancel_operation(session_id: str, op_id: str) -> dict:
    """Stop the operation ``op_id`` of a conversation, on whichever branch.

    An operation with no execution (/compact, a rewind) sees its stop
    through its cancel event and ends; one that became a turn is stopped
    the way every turn is. Returns ``{"ok", "op_kind", "op_version"}`` or
    ``{"ok": False, "code": "not_found"}`` for an operation that ended.
    """
    from .run_slots import cancel_event, slot_items

    with state._running_tasks_lock:
        found = next((task for _key, task in slot_items(session_id)
                      if task.get("op_id") == op_id), None)
        if found is not None:
            found["op_version"] = int(found.get("op_version") or 1) + 1
            found["op_status"] = "cancelling"
            execution_id = found.get("execution_id")
            result = {"ok": True, "op_id": op_id, "op_kind": found.get("op_kind"),
                      "op_version": found["op_version"]}
    if found is None:
        return {"ok": False, "code": "not_found", "op_id": op_id,
                "error": "That operation has already ended."}
    event = cancel_event(op_id)
    if event is not None:
        event.set()
    if execution_id:
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.run_control.cancellation import _cancel_canonical_execution
        execution = default_store().get_execution(execution_id)
        if execution is not None:
            try:
                _cancel_canonical_execution(execution, reason_code="cancel.user")
            except Exception as exc:  # noqa: BLE001 - an ended turn has nothing to stop
                result["note"] = f"{type(exc).__name__}: {exc}"
    return result


def _try_reserve_run_locked(session_id: str, msg_id: str, *, kind: str = "reply",
                            scope: str = "branch", key: str | None = None) -> bool:
    """Atomically reserve the selected branch for an operation before DAG mutation.

    The slot records which branch was selected when it was taken: the
    operation stays on that branch if the person switches away before it
    writes (``switch_branch`` moves the slot with it)."""
    try:
        from openprogram.state.store.session.files.migration import session_hold_active
        from openprogram.state.paths import get_state_dir
        if session_hold_active(Path(get_state_dir()) / "projects", session_id):
            return False
    except Exception:
        return False
    if state._is_selected_run_active(session_id):
        return False
    if _workspace_hold(session_id) is not None:
        return False
    selected = _selected_branch(session_id)
    with state._running_tasks_lock:
        if session_id in state._running_tasks:
            return False
        if conversation_wide_operation(session_id) is not None:
            # A rewind that changes files holds every branch until it ends.
            return False
        slot = new_slot(msg_id, kind, scope=scope, key=key)
        if selected:
            slot["branch_id"] = selected
        state._running_tasks[session_id] = slot
    return True


def _release_run_reservation(session_id: str, msg_id: str) -> None:
    """Release only the still-provisional reservation owned by ``msg_id``.

    The release always sends a clear naming ``msg_id``: a load or a
    run-state probe may have shown the reservation to a client as the
    running task, and an operation with no execution announced itself when
    it began. A client that never saw it ignores a clear naming another
    turn.
    """
    shown = False
    released: list[dict] = []
    branch_released = None
    with state._running_tasks_lock:
        task = state._running_tasks.get(session_id)
        if task and task.get("_reserved") and task.get("msg_id") == msg_id:
            state._running_tasks.pop(session_id, None)
            released.append(task)
            shown = True
        key = _owned_branch_key(session_id, msg_id)
        if key is not None and state._branch_running_tasks[key].get("_reserved"):
            released.append(state._branch_running_tasks.pop(key, None))
            branch_released = key[1]
    for item in released:
        forget_operation(session_id, item)
    if shown:
        state._emit_running_task_event(session_id, cleared_msg_id=msg_id)
    if branch_released is not None:
        state._emit_branch_running_task_event(session_id, branch_released, cleared_msg_id=msg_id)


def _activate_run_reservation(session_id: str, msg_id: str, runtime) -> bool:
    """Atomically hand an owned reservation to its registered runtime."""
    with state._running_tasks_lock:
        key = _owned_branch_key(session_id, msg_id)
        if key is not None:
            # A turn on another branch: its thread is kept on its own slot,
            # never as the conversation's active runtime.
            task = state._branch_running_tasks[key]
            if not task.get("_reserved"):
                return False
            task.pop("_reserved", None)
            task["thread"] = runtime
            task["started_at"] = state.time.time()
            task["last_event_at"] = task["started_at"]
            return True
        task = state._running_tasks.get(session_id)
        if not (task and task.get("_reserved")
                and task.get("msg_id") == msg_id):
            return False
        # Publish the runtime handoff before clearing the provisional marker.
        # This closes the observer window in which _is_run_active could treat
        # an admitted turn as a zombie and allow a duplicate start.
        state._register_active_runtime(session_id, runtime)
        task.pop("_reserved", None)
        task["started_at"] = state.time.time()
        task["last_event_at"] = task["started_at"]
    return True


def _finish_owned_run(session_id: str, msg_id: str) -> bool:
    """Remove only the task/runtime pair still owned by ``msg_id``."""
    return _take_owned_run(session_id, msg_id) is not None


def _take_owned_run(session_id: str, msg_id: str) -> str | None:
    """``_finish_owned_run`` that says where the slot was when removed:
    ``SELECTED``, the branch id of its branch slot, or ``None`` when
    ``msg_id`` owned no slot. Read and removed under one lock, so a switch
    cannot move the slot in between."""
    with state._running_tasks_lock:
        key = _owned_branch_key(session_id, msg_id)
        if key is not None:
            forget_operation(session_id, state._branch_running_tasks.pop(key, None))
            return key[1]
        task = state._running_tasks.get(session_id)
        if not (task and task.get("msg_id") == msg_id):
            return None
        state._running_tasks.pop(session_id, None)
        forget_operation(session_id, task)
        # The active-runtime registration is the handoff counterpart to the
        # provisional reservation.  Retire it only after ownership was
        # matched (a late finisher must not clear a newer run), and under the
        # same lock: between the two, _is_run_active still answered True from
        # the runtime while _run_identity found no task, so a load in that
        # window handed clients an active run with no ids to clear it by.
        state._unregister_active_runtime(session_id)
    return SELECTED


def _release_session_occupancy_for_execution(execution: dict) -> bool:
    """Release the session slot as soon as cancel intent is accepted.

    Occupancy is the running-task entry AND the active runtime.
    Popping only ``_running_tasks`` is not enough: ``_is_run_active``
    still returns True via ``_has_active_runtime``. A newer reservation
    (different ``msg_id``) is left intact. Broadcasts
    ``running_task_clear`` so every client matches.
    """
    session_id = execution.get("session_id") if execution else None
    execution_id = (
        (execution.get("execution_id") or "").strip() if execution else ""
    )
    if not session_id or not execution_id:
        return False
    msg_id = None
    with state._running_tasks_lock:
        branch_key = next((key for key, item in state._branch_running_tasks.items()
                           if key[0] == session_id and item.get("execution_id") == execution_id), None)
        branch_task = state._branch_running_tasks.pop(branch_key) if branch_key else None
        task = state._running_tasks.get(session_id)
        task_msg = task.get("msg_id") if task else None
        task_exec = task.get("execution_id") if task else None
    if branch_task is not None:
        forget_operation(session_id, branch_task)
        # A stop of a turn on another branch frees only that branch's slot.
        state._emit_branch_running_task_event(
            session_id, branch_key[1],
            cleared_execution_id=execution_id, cleared_msg_id=branch_task.get("msg_id"),
        )
        return True
    if task_msg:
        if task_exec == execution_id:
            msg_id = task_msg
        else:
            return False
    else:
        return False
    released = state._finish_owned_run(session_id, msg_id)
    if not released:
        # Task already gone; still drop a leftover foreground runtime so
        # ``_is_run_active`` cannot stay True via ``_has_active_runtime``.
        with state._running_tasks_lock:
            leftover = state._running_tasks.get(session_id)
            if leftover is None or leftover.get("msg_id") == msg_id:
                state._unregister_active_runtime(session_id)
                if leftover is not None:
                    state._running_tasks.pop(session_id, None)
                released = True
    if released:
        state._emit_running_task_event(
            session_id,
            cleared_execution_id=execution_id,
            cleared_msg_id=msg_id,
        )
    return released


# -- Run slots of branches that are not selected ---------------------------
#
# The selected branch keeps the conversation's slot (``_running_tasks``,
# keyed by session). A turn on another recorded branch takes the slot of
# that branch (``_branch_running_tasks``, keyed by ``(session, branch)``),
# so two branches of one conversation run at once while one branch still
# runs one turn at a time. Ownership is the turn's message id, unique per
# turn, which lets the shared release / activate / finish calls find it.


def _owned_branch_key(session_id: str, msg_id: str):
    """The branch slot ``msg_id`` owns in this session; caller holds the lock."""
    return next((key for key, task in state._branch_running_tasks.items()
                 if key[0] == session_id and task.get("msg_id") == msg_id), None)


def _branch_task_alive(task: dict) -> bool:
    """A reserved slot, or one whose thread has not finished."""
    if task.get("_reserved"):
        return holds_its_slot(task) or state.time.time() - task.get("started_at", 0) <= 300
    thread = task.get("thread")
    is_alive = getattr(thread, "is_alive", None)
    if getattr(thread, "ident", None) is not None and callable(is_alive) and not is_alive():
        return False
    return True


def _side_branch_tasks(session_id: str) -> list[dict]:
    """Running turns on branches that are not selected, each with its ``branch_id``.

    The live slots, then the durable record of a turn whose thread let go
    of its slot while it waits on a question or approval.
    """
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.foreground import side_branch_tasks
    stale: list[tuple[str, dict]] = []
    found: dict[str, dict] = {}
    with state._running_tasks_lock:
        for key, task in list(state._branch_running_tasks.items()):
            if key[0] != session_id:
                continue
            if _branch_task_alive(task):
                found[key[1]] = {**task, "branch_id": key[1]}
            else:
                stale.append((key[1], state._branch_running_tasks.pop(key)))
    for branch_id, task in stale:
        state._emit_branch_running_task_event(
            session_id, branch_id,
            cleared_msg_id=task.get("msg_id"), cleared_execution_id=task.get("execution_id"),
        )
    try:
        durable = side_branch_tasks(default_store(), session_id,
                                    selected_branch=_selected_branch(session_id))
    except Exception:
        _log.debug("side branch runs not read for %s", session_id, exc_info=True)
        durable = []
    for task in durable:
        found.setdefault(task["branch_id"], task)
    return list(found.values())


def _any_branch_run_active(session_id: str) -> bool:
    return bool(state._side_branch_tasks(session_id))


def _branch_run_active(session_id: str, branch_id: str) -> bool:
    """Whether ``branch_id`` has a turn: its slot, or any unfinished
    execution on it (a paused turn, a scheduled run, a Job)."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.projections.foreground import branch_has_open_execution
    if any(task.get("branch_id") == branch_id for task in state._side_branch_tasks(session_id)):
        return True
    return branch_has_open_execution(default_store(), session_id, branch_id)


def _branch_identity(task: dict) -> dict:
    identity = {"branch_id": task.get("branch_id"), **operation_identity(task)}
    if task.get("reason_code"):
        identity["reason_code"] = task.get("reason_code")
    if task.get("status"):
        identity["status"] = task.get("status")
    return identity


def _running_branches(session_id: str) -> list[dict]:
    """Every running turn of the conversation, each naming its branch.

    The selected branch's turn first (the one ``run_active`` reports), then
    the turns on other branches.
    """
    from openprogram.interfaces.server.branch_settings import current_branch
    branches: list[dict] = []
    if state._is_selected_run_active(session_id):
        selected = state._run_identity(session_id)
        if selected is not None:
            branches.append({**selected, "branch_id": current_branch(session_id), "selected": True})
    for task in state._side_branch_tasks(session_id):
        branches.append({**_branch_identity(task), "selected": False})
    return branches


def side_branch_target(session_id: str | None, body: dict) -> str | None:
    """The recorded, unselected branch a message names with ``branch_id``.

    ``None`` for a message to the selected branch (no ``branch_id``, or the
    selected one). Raises ``BranchConfigConflict('branch_gone')`` for a
    branch with no live record.
    """
    branch_id = body.get("branch_id") if isinstance(body, dict) else None
    if not session_id or not isinstance(branch_id, str) or not branch_id:
        return None
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    row = default_db().get_session(session_id) or {}
    if branch_id == row.get("active_branch_id"):
        return None
    ref = (row.get("branch_refs") or {}).get(branch_id)
    if not isinstance(ref, dict) or ref.get("status") == "aborted" or not ref.get("head_id"):
        raise BranchConfigConflict("The branch no longer exists; reload the conversation.",
                                   code="branch_gone")
    return branch_id


def _try_reserve_branch_run(session_id: str, branch_id: str, msg_id: str, *,
                            key: str | None = None) -> bool:
    """Reserve one unselected branch for a turn, under the session's process lock.

    Only that branch has to be free: an operation on the selected branch
    (/compact, a retry, a rewind of the conversation only) runs beside it.
    A rewind that changes files holds every branch until it ends."""
    deadline = time.monotonic() + FINISHING_WAIT_SECONDS
    while True:
        if _try_reserve_branch_run_once(session_id, branch_id, msg_id, key=key):
            return True
        if not _outwait_finishing(session_id, branch_id, key, deadline):
            return False


def _try_reserve_branch_run_once(session_id: str, branch_id: str, msg_id: str, *,
                                 key: str | None = None, lock_wait: float | None = None) -> bool:
    from openprogram.state.store.session.files.session_lock import session_interprocess_lock
    # A lock not had in time raises SessionLockTimeout (``_try_reserve_run``);
    # ``lock_wait`` bounds that wait as it does there (``0``: none).
    wait = admission_lock_wait() if lock_wait is None else max(0.0, lock_wait)
    with session_interprocess_lock(session_id, timeout=wait):
        if _operation_refusal(session_id) is not None:
            return False
        return _reserve_branch_slot_locked(session_id, branch_id, msg_id, key=key)


def _reserve_branch_slot_locked(session_id: str, branch_id: str, msg_id: str, *,
                                kind: str = "reply", scope: str = "branch",
                                key: str | None = None) -> bool:
    """Take the slot of one unselected branch; caller holds the process lock."""
    if state._branch_run_active(session_id, branch_id):
        return False
    with state._running_tasks_lock:
        if (session_id, branch_id) in state._branch_running_tasks:
            return False
        if conversation_wide_operation(session_id) is not None:
            return False
        state._branch_running_tasks[(session_id, branch_id)] = new_slot(msg_id, kind, scope=scope, key=key)
    return True


def _set_branch_run_execution(session_id: str, msg_id: str, *, execution_id: str | None = None,
                              status_version: int | None = None) -> str | None:
    """Fill the admitted ids into the slot ``msg_id`` owns, wherever the
    selection has moved it; where it is (``SELECTED`` or a branch id), or
    ``None`` when it holds none."""
    with state._running_tasks_lock:
        key = _owned_branch_key(session_id, msg_id)
        if key is not None:
            task, where = state._branch_running_tasks[key], key[1]
        else:
            task = state._running_tasks.get(session_id)
            if not task or task.get("msg_id") != msg_id:
                return None
            where = SELECTED
        if execution_id is not None:
            task["execution_id"] = execution_id
        if status_version is not None:
            task["status_version"] = status_version
        return where


def _slot_location(session_id: str, msg_id: str) -> str | None:
    """Where the slot ``msg_id`` owns is now: ``SELECTED``, a branch id, or ``None``."""
    with state._running_tasks_lock:
        key = _owned_branch_key(session_id, msg_id)
        if key is not None:
            return key[1]
        task = state._running_tasks.get(session_id)
        return SELECTED if task and task.get("msg_id") == msg_id else None


def _slot_branch(session_id: str, msg_id: str) -> str | None:
    """The branch the slot ``msg_id`` owns was taken on."""
    with state._running_tasks_lock:
        key = _owned_branch_key(session_id, msg_id)
        if key is not None:
            return key[1]
        task = state._running_tasks.get(session_id)
        return task.get("branch_id") if task and task.get("msg_id") == msg_id else None


def _emit_slot_state(session_id: str, msg_id: str) -> None:
    """Broadcast the run state of the slot ``msg_id`` owns, as the slot of
    the selected branch or of the branch it is on."""
    where = _slot_location(session_id, msg_id)
    if where == SELECTED:
        state._emit_running_task_event(session_id)
    elif where:
        state._emit_branch_running_task_event(session_id, where)


# -- Switching the selected branch ------------------------------------------
#
# Which branch is selected is what the person looks at, not where anything
# runs. A switch takes no slot and is never refused because a turn or an
# operation runs on the branch left or the branch entered: each keeps its
# slot, and the slot table's ``SELECTED`` key moves to the branch now
# selected.


def switch_branch(session_id: str, head_id: str, *, branch_id: str | None = None,
                  meta_update: dict | None = None) -> dict:
    """Select the branch ending at ``head_id`` (``branch_id`` names its record).

    Moves HEAD with its compare-and-set, then re-keys the run slots so the
    selected branch's slot is the new branch's and the branch left keeps
    its own, and tells every client which run is now the selected one.
    Returns ``{"ok", "previous_head_id", "active_branch_id"}``, with
    ``code``/``error`` when HEAD moved under it. The re-key runs under the
    session's process lock, the lock a turn's admission takes; the head
    write takes that lock itself, so it runs before.
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.files.session_lock import session_interprocess_lock
    from openprogram.state.store.session.branch_config import branch_identity
    db = default_db()
    before = db.get_session(session_id) or {}
    previous_head = before.get("head_id")
    left = before.get("active_branch_id") or branch_identity(session_id, previous_head)
    if not state._set_active_head(session_id, head_id, expected_head_id=previous_head,
                                  branch_id=branch_id, meta_update=meta_update):
        return {"ok": False, "code": "head_changed", "previous_head_id": previous_head,
                "error": "conversation head changed during checkout"}
    moved_out = moved_in = home = None
    entered = _selected_branch(session_id)
    try:
        with session_interprocess_lock(session_id, timeout=10):
            entered = _selected_branch(session_id)
            moved_out, moved_in, home = _reselect_slots(session_id, left, entered)
    except (TimeoutError, BlockingIOError):
        _log.warning("run slots not re-keyed after switching branch in %s", session_id)
    if moved_out is not None:
        # The branch left keeps running: a client holding it as the
        # selected run lets it go, and sees it as that branch's run.
        state._emit_running_task_event(session_id, cleared_msg_id=moved_out.get("msg_id"),
                                       cleared_execution_id=moved_out.get("execution_id"))
        state._emit_branch_running_task_event(session_id, home)
    if moved_in is not None:
        state._emit_branch_running_task_event(session_id, entered, cleared_msg_id=moved_in.get("msg_id"),
                                              cleared_execution_id=moved_in.get("execution_id"))
    # The selected run, from the slot or the durable record (a turn on the
    # branch entered that waits on an approval holds no slot).
    if moved_in is not None or state._canonical_foreground_task(session_id) is not None:
        state._emit_running_task_event(session_id)
    return {"ok": True, "previous_head_id": previous_head, "active_branch_id": entered}


def _reselect_slots(session_id: str, left: str | None, entered: str | None):
    """Put each slot under the key the selection gives it now.

    The ``SELECTED`` slot goes to its own branch's key when that branch is
    no longer selected (the branch it was taken on, ``branch_id``, else
    ``left``), and the slot of the branch now selected becomes ``SELECTED``.
    The selected slot's turn is registered as the conversation's active
    runtime; a branch slot keeps its thread on the entry. Returns
    ``(moved_out, moved_in, home)``: the entries moved (or ``None``) and the
    key the first went to. Caller holds the session's process lock.
    """
    moved_out = moved_in = None
    with state._running_tasks_lock:
        outgoing = state._running_tasks.get(session_id)
        home = (outgoing or {}).get("branch_id") or left
        if (outgoing is not None and home and home != entered
                and (session_id, home) not in state._branch_running_tasks):
            state._running_tasks.pop(session_id, None)
            outgoing["branch_id"] = home
            if not outgoing.get("_reserved") and outgoing.get("thread") is None:
                runtime = _registered_runtime(session_id)
                if runtime is not None:
                    outgoing["thread"] = runtime
            state._unregister_active_runtime(session_id)
            state._branch_running_tasks[(session_id, home)] = outgoing
            moved_out = outgoing
        if entered and session_id not in state._running_tasks:
            incoming = state._branch_running_tasks.pop((session_id, entered), None)
            if incoming is not None:
                incoming["branch_id"] = entered
                thread = incoming.pop("thread", None)
                if thread is not None and not incoming.get("_reserved"):
                    state._register_active_runtime(session_id, thread)
                state._running_tasks[session_id] = incoming
                moved_in = incoming
    return moved_out, moved_in, home


def _registered_runtime(session_id: str):
    from openprogram.runtime.conversation.run_control import active_runtimes
    with active_runtimes._active_exec_runtimes_lock:
        return active_runtimes._active_exec_runtimes.get((session_id, None))


def _slot_of_branch(session_id: str, branch_id: str | None) -> str:
    """The slot a turn on ``branch_id`` holds: that branch's own when it is
    a recorded branch other than the selected one, else ``SELECTED``."""
    if not branch_id:
        return SELECTED
    try:
        from openprogram.runtime.context.session_db import default_db
        row = default_db().get_session(session_id) or {}
    except Exception:
        _log.debug("branch records not read for %s", session_id, exc_info=True)
        return SELECTED
    selected = row.get("active_branch_id")
    if not selected or branch_id == selected:
        return SELECTED
    ref = (row.get("branch_refs") or {}).get(branch_id)
    if not isinstance(ref, dict) or ref.get("status") == "aborted" or not ref.get("head_id"):
        return SELECTED
    return branch_id


def _adopt_admitted_turn(session_id: str, owner: str, *, execution_id: str,
                         status_version: int, branch_id: str | None) -> bool:
    """Put an already admitted turn in its branch's free slot, reserved.

    The turn's own durable record is what ``_is_selected_run_active`` reads
    as "running", so the ordinary reservation would refuse the turn for
    itself, and never gets to drop a slot nothing holds any more; here only
    another live holder of the slot refuses it, and a dead one is dropped
    first (``_slot_is_stale``), as the run guard drops it."""
    from openprogram.state.store.session.files.session_lock import session_interprocess_lock
    stale: dict | None = None
    try:
        with session_interprocess_lock(session_id, timeout=admission_lock_wait()):
            if _operation_refusal(session_id) is not None:
                return False
            where = _slot_of_branch(session_id, branch_id)
            selected = _selected_branch(session_id) if where == SELECTED else None
            with state._running_tasks_lock:
                if conversation_wide_operation(session_id) is not None:
                    return False
                slots = state._running_tasks if where == SELECTED else state._branch_running_tasks
                key = session_id if where == SELECTED else (session_id, where)
                held = slots.get(key)
                if held is not None and _slot_is_stale(session_id, held, selected=where == SELECTED):
                    stale = slots.pop(key)
                    held = None
                if held is not None or (where == SELECTED and state._has_active_runtime(session_id)):
                    return False
                _place_adopted_slot(session_id, owner, where, selected, execution_id, status_version)
        return True
    finally:
        if stale is not None:
            if where == SELECTED:
                state._emit_running_task_event(session_id, cleared_msg_id=stale.get("msg_id"),
                                               cleared_execution_id=stale.get("execution_id"))
            else:
                state._emit_branch_running_task_event(session_id, where, cleared_msg_id=stale.get("msg_id"),
                                                      cleared_execution_id=stale.get("execution_id"))


def _slot_is_stale(session_id: str, task: dict, *, selected: bool) -> bool:
    """A slot no turn holds any more: a reservation never activated within
    300 seconds (an executionless operation's excepted), or an activated one
    whose thread has ended with no runtime of the turn left."""
    if task.get("_reserved"):
        return not holds_its_slot(task) and state.time.time() - task.get("started_at", 0) > 300
    if selected and state._has_active_runtime(session_id):
        return False
    thread = task.get("thread")
    is_alive = getattr(thread, "is_alive", None)
    return getattr(thread, "ident", None) is not None and callable(is_alive) and not is_alive()


def _place_adopted_slot(session_id: str, owner: str, where: str, selected: str | None,
                        execution_id: str, status_version: int) -> None:
    """Record an adopted turn in its free slot; the caller holds both locks."""
    slot = new_slot(owner, "reply")
    slot["execution_id"] = execution_id
    slot["status_version"] = status_version
    if where == SELECTED:
        if selected:
            slot["branch_id"] = selected
        state._running_tasks[session_id] = slot
    else:
        state._branch_running_tasks[(session_id, where)] = slot


class ServerRunSlot:
    """The server's run reservation as the runtime's run-slot port.

    A turn the runtime starts on its own (a reminder wake, a handed-over
    message, a Goal's next turn, a background-command notice) reserves the
    same slot a typed message does, shows as the conversation's running
    task while it runs and frees the slot when it ends, so neither kind of
    turn can start on top of the other. A turn on a recorded branch that is
    not selected holds that branch's slot and is announced as that
    branch's run.
    """

    def reserve(self, session_id: str, owner: str, *,
                lock_wait: float | None = None, branch_id: str | None = None) -> bool:
        where = _slot_of_branch(session_id, branch_id)
        if where != SELECTED:
            if lock_wait is not None and lock_wait <= 0:
                return _try_reserve_branch_run_once(session_id, where, owner, lock_wait=0.0)
            return state._try_reserve_branch_run(session_id, where, owner)
        if lock_wait is None:
            return state._try_reserve_run(session_id, owner)
        return state._try_reserve_run(session_id, owner, lock_wait=lock_wait)

    def adopt(self, session_id: str, owner: str, *, execution_id: str,
              status_version: int, branch_id: str | None, runner) -> bool:
        if not _adopt_admitted_turn(session_id, owner, execution_id=execution_id,
                                    status_version=status_version, branch_id=branch_id):
            return False
        if not state._activate_run_reservation(session_id, owner, runner):
            state._release_run_reservation(session_id, owner)
            return False
        state._emit_slot_state(session_id, owner)
        return True

    def activate(self, session_id: str, owner: str, *, execution_id: str,
                 status_version: int, runner) -> bool:
        state._set_branch_run_execution(session_id, owner, execution_id=execution_id,
                                        status_version=status_version)
        if not state._activate_run_reservation(session_id, owner, runner):
            return False
        state._emit_slot_state(session_id, owner)
        return True

    def finish(self, session_id: str, owner: str, *, execution_id: str) -> None:
        where = state._take_owned_run(session_id, owner)
        if where is None:
            return
        if where == SELECTED:
            state._emit_running_task_event(
                session_id, cleared_msg_id=owner, cleared_execution_id=execution_id,
            )
        else:
            state._emit_branch_running_task_event(
                session_id, where, cleared_msg_id=owner, cleared_execution_id=execution_id,
            )

    def finishing(self, session_id: str, owner: str) -> None:
        mark_run_finishing(session_id, owner)

    def release(self, session_id: str, owner: str) -> None:
        state._release_run_reservation(session_id, owner)


def _watch_worker_loop() -> None:
    """Have the conversation lock log a wait taken on the Worker's loop."""
    from openprogram.state.store.session.files import session_lock
    session_lock.watch_event_loop(lambda: getattr(state, "_loop", None))


_watch_worker_loop()
