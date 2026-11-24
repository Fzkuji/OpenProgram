"""Server session persistence."""
from __future__ import annotations

import logging
import queue
from contextlib import contextmanager

from openprogram.runtime.execution.run_control.tokens import turn_ended

from .. import server as state

_log = logging.getLogger(__name__)


def reply_row_id(task: dict) -> str | None:
    """The reply row a running task writes: the server mints it from the turn's message id.

    ``None`` for an operation that writes no reply (/compact, a rewind)."""
    from .run_slots import is_reply
    if not is_reply(task):
        return None
    msg_id = task.get("msg_id")
    return task.get("reply_id") or (f"{msg_id}_reply" if msg_id else None)


def _get_messages(session_id: str) -> list[dict]:
    """Return the active-branch messages for a conversation.

    Reads from cache when warm, falls back to SessionDB.get_branch on
    miss. The cache contains COPIES — callers that mutate the list
    won't accidentally invalidate the cache, but they must call
    _invalidate_messages(session_id) afterwards if they wrote anything
    that should be visible.

    Returns ``[]`` for unknown session_ids — same as the dict-based
    reader's behavior, so existing call sites don't need null-guards.
    """
    with state._msg_cache_lock:
        if session_id in state._msg_cache:
            state._msg_cache.move_to_end(session_id)
            return list(state._msg_cache[session_id])
    # Cache miss — load from DB. Out of the lock so concurrent
    # different-conv reads don't serialize.
    try:
        from openprogram.runtime.context.session_db import default_db
        msgs = default_db().get_branch(session_id)
    except Exception:
        msgs = []
    with state._msg_cache_lock:
        state._msg_cache[session_id] = msgs
        state._msg_cache.move_to_end(session_id)
        while len(state._msg_cache) > state._MSG_CACHE_CAP:
            state._msg_cache.popitem(last=False)
        return list(msgs)


def _invalidate_messages(session_id: str) -> None:
    """Drop ``session_id``'s cached branch list. Call after any write
    that should be visible to the next reader: append_message,
    set_head, retry/edit, deepest_leaf jumps."""
    with state._msg_cache_lock:
        state._msg_cache.pop(session_id, None)


def _hydrate_messages_from_db(session_id: str) -> list[dict]:
    """Force-refresh and return the active branch. Used by paths that
    just wrote to SessionDB and need the next read to be fresh."""
    state._invalidate_messages(session_id)
    return state._get_messages(session_id)


def _set_active_head(
    session_id: str,
    head_id: state.Optional[str],
    *,
    expected_head_id: object = state._HEAD_UNSET,
    meta_update: state.Optional[dict] = None,
    branch_id: state.Optional[str] = None,
) -> bool:
    """Switch the conversation's active branch leaf.

    Used by retry / edit / sibling-checkout / deepest-leaf jump /
    branch-checkout / branch-delete / attach / rewind UIs — every path
    that moves HEAD must come through here.

    Writes SessionDB.sessions.head_id (so cross-process readers and the
    dispatcher's next get_branch see the new head), re-reads the new
    branch into the in-memory ``conv["head_id"]`` / ``conv["messages"]``
    mirror, and invalidates the messages cache.

    The ``conv["messages"]`` refresh is not optional: ``_save_session``
    flushes that list and ``conv["head_id"]`` straight back into
    SessionDB, so a mirror left on the old branch silently reverts the
    head move on the next save.
    """
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        from openprogram.state.store.session.rewind_view import checkout_meta
        from openprogram.runtime.conversation.rewinds.rewind_marks import forget_read_baselines
        from openprogram.state.store.session.branch_config import snapshot
        source_session = db.get_session(session_id) or {}
        source = source_session.get("head_id")
        update = checkout_meta(db, session_id, head_id, branch_id)
        if expected_head_id is state._HEAD_UNSET:
            expected_head_id = source
        # The CAS compares against the fields as stored, so the expectation
        # is read in that form too: ``get_session`` reports a missing
        # ``agent_id`` as ``""``, which never equals the stored absence.
        disk_meta = db.read_disk_meta(session_id) or {}
        if not db.compare_and_set_head(
            session_id, expected_head_id, head_id,
            expected_meta={"active_branch_id": disk_meta.get("active_branch_id"), **snapshot(disk_meta)},
            meta_update={**update, **(meta_update or {})},
        ):
            return False
        if update:
            forget_read_baselines(session_id)
    except Exception as e:
        state._log(f"_set_active_head: SessionDB write failed for {session_id}: {e}")
        return False
    from openprogram.state.store.session.branch_config import BRANCH_CONFIG_FIELDS
    old_runtime = None
    with state._sessions_lock:
        # Another checkout or setting write may have committed since our CAS.
        # Read after acquiring the mirror lock so an older operation cannot
        # replace a newer operation's displayed selection or configuration.
        selected = db.get_session(session_id) or {}
        selected_head = selected.get("head_id")
        branch = None
        try:
            branch = db.get_branch(session_id, selected_head) or []
        except Exception as e:
            state._log(f"_set_active_head: get_branch failed for {session_id}: {e}")
        conv = state._sessions.get(session_id)
        if conv is not None:
            if update:
                old_runtime = conv.pop("runtime", None)
                # The last turn's model and window were the branch's that
                # was left; this one's context is counted against its own.
                conv.pop("_turn_model_ref", None)
                conv.pop("_turn_context_window", None)
            for key in BRANCH_CONFIG_FIELDS:
                conv[key] = selected.get(key)
            conv["provider_name"] = selected.get("provider_override")
            conv["active_branch_id"] = selected.get("active_branch_id")
            conv["head_id"] = selected_head
            if branch is not None:
                conv["messages"] = branch
    if old_runtime is not None and hasattr(old_runtime, "close"):
        try:
            old_runtime.close()
        except Exception:
            state._log("_set_active_head: previous runtime could not be closed")
    state._invalidate_messages(session_id)
    return True


def _deepest_leaf_db(session_id: str, root_id: str) -> state.Optional[str]:
    """SessionDB-backed deepest_leaf — finds the tip of the subtree
    under ``root_id`` so sibling-checkout lands on the latest reply,
    not the fork point. Mirrors openprogram.runtime.context.git.deepest_leaf
    but reads from SQL instead of an in-memory message list."""
    try:
        from openprogram.runtime.context.session_db import default_db
        return default_db().get_deepest_leaf(session_id, root_id)
    except Exception:
        return None


def _emit_running_task_event(
    session_id: str,
    *,
    cleared_execution_id: str | None = None,
    cleared_msg_id: str | None = None,
) -> None:
    """Broadcast the current running-task state for ``session_id``.

    Emits a ``running_task`` envelope if a task is active, or a
    ``running_task_clear`` envelope otherwise. A clear names the
    finished turn so a newer reservation (or a just-sent placeholder)
    is not idled by a late frame. When the slot already belongs to
    another owner, the frame is still that clear: the end of one turn
    never announces the next owner, whose own admission does. Callers
    should invoke this immediately after mutating ``_running_tasks``
    (still under the lock is fine — the actual socket send is queued).
    """
    try:
        run_seq = state._next_run_seq()
        with state._running_tasks_lock:
            task = state._running_tasks.get(session_id)
            task = dict(task) if task else None
        if cleared_msg_id and task and task.get("msg_id") != cleared_msg_id:
            task = None
        else:
            from .session_lifecycle import _reported_run_task
            task = _reported_run_task(session_id, task)
        if task:
            from .session_lifecycle import operation_identity
            identity = operation_identity(task)
            payload = {
                "type": "running_task",
                "data": {
                    "session_id": session_id,
                    "msg_id": task.get("msg_id"),
                    "reply_id": reply_row_id(task),
                    "func_name": task.get("func_name"),
                    "started_at": task.get("started_at"),
                    "display_params": task.get("display_params", ""),
                    "execution_id": task.get("execution_id"),
                    "status_version": identity.get("status_version", task.get("status_version")),
                    "op_kind": identity["op_kind"],
                    **({"op_id": identity["op_id"]} if identity.get("op_id") else {}),
                    "run_seq": run_seq,
                    **({"branch_id": task["branch_id"]} if task.get("branch_id") else {}),
                },
            }
            # A turn paused at a question or approval stays the running
            # turn; its state tells the client to show it as waiting.
            if task.get("status"):
                payload["data"]["status"] = task.get("status")
                payload["data"]["reason_code"] = task.get("reason_code")
        else:
            data = {"session_id": session_id, "run_seq": run_seq}
            if cleared_execution_id:
                data["execution_id"] = cleared_execution_id
            if cleared_msg_id:
                data["msg_id"] = cleared_msg_id
            payload = {
                "type": "running_task_clear",
                "data": data,
            }
        state._broadcast(state.json.dumps(payload, default=str))
        if payload["type"] == "running_task_clear" or cleared_msg_id or cleared_execution_id:
            # The slot was given back: a notice held for this conversation
            # goes now, and an admitted turn waiting for the slot takes it
            # (runtime/conversation/run_control/idle.py). That turn's own
            # queued record can make the frame above say "running".
            turn_ended(session_id)
    except Exception:
        # Broadcast is best-effort; never let it kill the turn.
        _log.warning(
            "running-task state not broadcast for session %s", session_id, exc_info=True)


def branch_wait_state(task: dict) -> str | None:
    """``approval`` when the branch's turn waits for a permission or system
    access answer, ``input`` when it waits for an answer to a question,
    else ``None`` (it is running)."""
    reason = task.get("reason_code")
    if reason == "system_access_required":
        return "approval"
    if reason != "wait_open":
        return None
    kind = None
    execution_id = task.get("execution_id")
    if execution_id:
        try:
            from openprogram.runtime.execution import default_store
            from openprogram.runtime.execution.waits import DurableWaitStore
            waits = DurableWaitStore(default_store()).list_open(execution_id=execution_id)
            kind = waits[0].kind if waits else None
        except Exception:  # noqa: BLE001 - shown as waiting for an approval
            kind = None
    # ask / form / ask_many wait for words from the person; approval,
    # confirm and system_access wait for a yes or no.
    return "input" if kind in {"ask", "form", "ask_many"} else "approval"


def _branch_name(session_id: str, branch_id: str) -> str | None:
    try:
        from .session_lifecycle import _branch_names
        names, _selected = _branch_names(session_id)
        return names.get(branch_id)
    except Exception:  # noqa: BLE001 - the badge falls back to a generic name
        return None


def _emit_branch_running_task_event(
    session_id: str,
    branch_id: str,
    *,
    cleared_execution_id: str | None = None,
    cleared_msg_id: str | None = None,
) -> None:
    """Broadcast the run state of one branch that is not selected.

    ``branch_running_task`` while the branch has a turn, else
    ``branch_running_task_clear`` naming the turn that ended. Clients that
    know only ``running_task`` ignore both, so their per-conversation state
    keeps describing the selected branch.
    """
    try:
        run_seq = state._next_run_seq()
        task = next((item for item in state._side_branch_tasks(session_id)
                     if item.get("branch_id") == branch_id), None)
        if task is not None:
            from .session_lifecycle import operation_identity
            identity = operation_identity(task)
            data = {
                "session_id": session_id,
                "branch_id": branch_id,
                "msg_id": task.get("msg_id"),
                "reply_id": reply_row_id(task),
                "func_name": task.get("func_name"),
                "started_at": task.get("started_at"),
                "execution_id": task.get("execution_id"),
                "status_version": identity.get("status_version", task.get("status_version")),
                "op_kind": identity["op_kind"],
                **({"op_id": identity["op_id"]} if identity.get("op_id") else {}),
                "run_seq": run_seq,
            }
            if task.get("status"):
                data["status"] = task.get("status")
                data["reason_code"] = task.get("reason_code")
            # What a pane showing another branch draws for this one: its
            # name, and whether it waits on the person.
            data["wait_state"] = branch_wait_state(task)
            name = _branch_name(session_id, branch_id)
            if name:
                data["branch_name"] = name
            payload = {"type": "branch_running_task", "data": data}
        else:
            data = {"session_id": session_id, "branch_id": branch_id, "run_seq": run_seq}
            if cleared_execution_id:
                data["execution_id"] = cleared_execution_id
            if cleared_msg_id:
                data["msg_id"] = cleared_msg_id
            payload = {"type": "branch_running_task_clear", "data": data}
        state._broadcast(state.json.dumps(payload, default=str))
        if payload["type"] == "branch_running_task_clear" or cleared_msg_id or cleared_execution_id:
            turn_ended(session_id)
    except Exception:
        _log.warning("branch run state not broadcast for session %s", session_id, exc_info=True)


@contextmanager
def _web_follow_up(session_id: str, msg_id: str, func_name: str, tree_cb=None):
    """Set up follow-up question support for a web UI command execution.

    Registers a global ask_user handler that sends follow-up questions to
    the browser via WebSocket and blocks until the user answers.

    Args:
        session_id:   Conversation ID (for routing the answer back).
        msg_id:    Message ID (for associating with the right chat message).
        func_name: Function name (for display in the frontend).
        tree_cb:   Optional tree event callback to trigger on follow-up.
    """
    fq = queue.Queue()
    ended = state.threading.Event()
    with state._follow_up_lock:
        state._follow_up_queues[session_id] = fq

    def _handler(question: str) -> state.Optional[str]:
        if ended.is_set():
            return None
        state._broadcast_chat_response(session_id, msg_id, {
            "type": "follow_up_question",
            "question": question,
            "function": func_name,
        })
        if tree_cb is not None:
            tree_cb("follow_up", {})
        try:
            answer = fq.get(timeout=300)
        except queue.Empty:
            return None
        if answer is state._FOLLOW_UP_DISCONNECTED:
            ended.set()
            return None
        if isinstance(answer, dict) and answer.get("_cancelled"):
            ended.set()
            return None
        return answer

    state.set_ask_user(_handler)
    try:
        yield
    finally:
        state.set_ask_user(None)
        with state._follow_up_lock:
            state._follow_up_queues.pop(session_id, None)


def _saved_context_stats(stats):
    """The context stats a reopened conversation starts from: the measurement.

    The per-category breakdown is worked out from the conversation's own
    nodes, prompt and tools, and a first open works it out again
    (``load._after_session_loaded``); saving it put the whole tool catalog
    and prompt in the log after every provider request.
    """
    if not isinstance(stats, dict):
        return stats
    return {key: value for key, value in stats.items() if key != "breakdown"}


def _save_session(session_id: str):
    """Persist one conversation's meta + messages under its agent.

    Per-function execution trees are written incrementally by
    append_tree_event in the tree event callback — we do not rewrite
    them here. An empty conversation (no messages yet, no session row
    in SessionDB) is skipped entirely so the user doesn't see "ghost"
    history rows for chats they never typed in.
    """
    if not session_id:
        return
    with state._sessions_lock:
        conv = state._sessions.get(session_id)
        if conv is None:
            return
        # Skip persistence for brand-new conversations the user hasn't
        # actually used. Once _append_msg lands the first message it
        # creates the session row, and from that point on this guard
        # passes (db.get_session is non-None) and we save normally.
        if not conv.get("messages"):
            try:
                from openprogram.runtime.context.session_db import default_db
                if default_db().get_session(session_id) is None:
                    return
            except Exception:
                # Unknown row state: save anyway rather than drop the
                # conversation.
                _log.debug("session row check failed for %s", session_id, exc_info=True)
        runtime = conv.get("runtime")
        from openprogram.runtime.context.session_db import default_db as _save_db
        _db_sess = _save_db().get_session(session_id)
        agent_id = (_db_sess or {}).get("agent_id") or state._default_agent_id()
        meta = {
            "id": session_id,
            "agent_id": agent_id,
            "provider_name": conv.get("provider_name"),
            "provider_override": conv.get("provider_override"),
            "model_override": conv.get("model_override"),
            "session_id": getattr(runtime, "_session_id", None),
            "model": getattr(runtime, "model", None),
            "context_tree": None,
            "_chat_usage": conv.get("_chat_usage"),
            "_last_context_stats": _saved_context_stats(conv.get("_last_context_stats")),
            "_last_exec_session": conv.get("_last_exec_session"),
            "_last_exec_cumulative_usage": conv.get("_last_exec_cumulative_usage"),
            # No head_id here — the conv dict is a display mirror and
            # HEAD has exactly one writer, SessionStore.set_head
            # (context/compaction.md §5). Writing the mirror's head back
            # was the phantom head-move path: a stale mirror (restored
            # from an old save, or advanced by a transcript-only row
            # like a compaction marker) silently overwrote real moves.
            "tools_override": conv.get("tools_override"),
            "thinking_effort": conv.get("thinking_effort"),
            "permission_mode": conv.get("permission_mode"),
        }
    try:
        state._persist.save_meta(agent_id, session_id, meta)
        # No save_messages: every real row reaches the store through
        # db.append_message at write time (_append_msg / dispatcher);
        # the mirror's extra rows are transcript-only (status lines,
        # compaction markers) and must never become store nodes.
    except Exception as e:
        state._log(f"[save_conversation] {session_id} error: {e}")


def _default_agent_id() -> str:
    """Which agent does a new conversation land in when the client
    didn't specify one? Falls back to the registry default."""
    try:
        from openprogram.runtime.agent_registry import manager as _A
        spec = _A.get_default()
        if spec is not None:
            return spec.id
    except Exception:
        # Registry unreadable: fall back to the built-in default agent.
        _log.debug("default agent lookup failed", exc_info=True)
    return "main"
