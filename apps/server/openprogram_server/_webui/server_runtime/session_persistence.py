"""Server session persistence."""
from __future__ import annotations
from ... import server as state


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
        from openprogram.agent.session_db import default_db
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
        from openprogram.agent.session_db import default_db
        db = default_db()
        if expected_head_id is state._HEAD_UNSET:
            db.set_head(session_id, head_id)
        elif not db.compare_and_set_head(
            session_id,
            expected_head_id,  # type: ignore[arg-type]
            head_id,
            meta_update=meta_update,
        ):
            return False
    except Exception as e:
        state._log(f"_set_active_head: SessionDB write failed for {session_id}: {e}")
        return False
    branch = None
    if db is not None:
        try:
            branch = db.get_branch(session_id) or []
        except Exception as e:
            state._log(f"_set_active_head: get_branch failed for {session_id}: {e}")
    with state._sessions_lock:
        conv = state._sessions.get(session_id)
        if conv is not None:
            conv["head_id"] = head_id
            if branch is not None:
                conv["messages"] = branch
    state._invalidate_messages(session_id)
    return True


def _deepest_leaf_db(session_id: str, root_id: str) -> state.Optional[str]:
    """SessionDB-backed deepest_leaf — finds the tip of the subtree
    under ``root_id`` so sibling-checkout lands on the latest reply,
    not the fork point. Mirrors openprogram.context.git.deepest_leaf
    but reads from SQL instead of an in-memory message list."""
    try:
        from openprogram.agent.session_db import default_db
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
    is not idled by a late frame. Callers should invoke this
    immediately after mutating ``_running_tasks`` (still under the
    lock is fine — the actual socket send is queued).
    """
    try:
        with state._running_tasks_lock:
            task = state._running_tasks.get(session_id)
            task = dict(task) if task else None
        task = state._canonical_foreground_task(session_id) or task
        if task:
            payload = {
                "type": "running_task",
                "data": {
                    "session_id": session_id,
                    "msg_id": task.get("msg_id"),
                    "func_name": task.get("func_name"),
                    "started_at": task.get("started_at"),
                    "display_params": task.get("display_params", ""),
                    "execution_id": task.get("execution_id"),
                    "status_version": task.get("status_version"),
                },
            }
        else:
            data = {"session_id": session_id}
            if cleared_execution_id:
                data["execution_id"] = cleared_execution_id
            if cleared_msg_id:
                data["msg_id"] = cleared_msg_id
            payload = {
                "type": "running_task_clear",
                "data": data,
            }
        state._broadcast(state.json.dumps(payload, default=str))
    except Exception:
        # Broadcast is best-effort; never let it kill the turn.
        pass


@state._contextmanager
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
    fq = state.queue.Queue()
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
        except state.queue.Empty:
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
                from openprogram.agent.session_db import default_db
                if default_db().get_session(session_id) is None:
                    return
            except Exception:
                pass
        runtime = conv.get("runtime")
        from openprogram.agent.session_db import default_db as _save_db
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
            "_last_context_stats": conv.get("_last_context_stats"),
            "_last_exec_session": conv.get("_last_exec_session"),
            "_last_exec_cumulative_usage": conv.get("_last_exec_cumulative_usage"),
            # No head_id here — the conv dict is a display mirror and
            # HEAD has exactly one writer, SessionStore.set_head
            # (context/compaction.md §5). Writing the mirror's head back
            # was the phantom head-move path: a stale mirror (restored
            # from an old save, or advanced by a transcript-only row
            # like a compaction marker) silently overwrote real moves.
            "tools_enabled": conv.get("tools_enabled"),
            "tools_override": conv.get("tools_override"),
            "thinking_effort": conv.get("thinking_effort"),
            "permission_mode": conv.get("permission_mode"),
        }
        messages = list(conv.get("messages", []))
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
        from openprogram.agent.management import manager as _A
        spec = _A.get_default()
        if spec is not None:
            return spec.id
    except Exception:
        pass
    return "main"


def _delete_session_files(session_id: str):
    """Destroy a session by id via the source of truth (SessionStore).

    No agent_id lookup: SessionStore deletes by session_id alone. The
    old "find the owning agent, then delete its dir" path could not see
    sessions whose meta had no agent_id (forced-tool-call shells), which
    is what left orphans that reappeared on refresh."""
    try:
        from openprogram.agent.session_db import default_db
        default_db().delete_session(session_id)
    except Exception as e:
        state._log(f"[delete_session_files] {session_id} error: {e}")


def _restore_sessions():
    """Walk every agent's sessions dir and hydrate _sessions."""
    for agent_id, session_id in state._persist.list_sessions():
        try:
            data = state._persist.load_session(agent_id, session_id)
            if data is None:
                continue

            root_ctx = None  # tree Context retired — UI now reads DAG nodes

            provider_name = data.get("provider_name")
            provider_override = data.get("provider_override")
            model_override = data.get("model_override")
            # The "session_id" inside meta is the LLM runtime's own
            # session identifier (Claude Code, etc.) — separate from
            # session_id in this loop, which is the SessionDB primary
            # key. Use a different local name to keep them apart.
            runtime_session_id = data.get("session_id") or data.get("llm_session_id")
            model = data.get("model")

            # Skip eager runtime restore unless this session was
            # explicitly switched (provider_override). Without an
            # override we can't tell whether the persisted
            # ``provider_name`` reflects a user choice or stale state
            # written by the old auto-default-on-create path; letting
            # ``_get_session_runtime`` build the runtime lazily from agent
            # config is the only way old buggy sessions escape the
            # legacy claude-code default.
            runtime = None
            if provider_override:
                try:
                    runtime = state._create_runtime_for_visualizer(
                        provider_override, model=model_override or model
                    )
                    if runtime_session_id and hasattr(runtime, "_session_id"):
                        runtime._session_id = runtime_session_id
                        runtime._turn_count = 1
                        runtime.has_session = True
                except Exception:
                    runtime = None

            # ContextGit migration: backfill predecessor on legacy
            # messages and pick a head_id. Old conversations become a
            # straight linear chain (see docs/design/context/overview.md).
            from openprogram.context.git import (
                normalize_parent_pointers,
                head_or_tip,
            )
            msgs = data.get("messages", [])
            normalize_parent_pointers(msgs)
            head_id = data.get("head_id") or head_or_tip({}, msgs)

            with state._sessions_lock:
                state._sessions[session_id] = {
                    "id": session_id,
                    "agent_id": data.get("agent_id") or agent_id,
                    "runtime": runtime,
                    "provider_name": provider_override or None,
                    "provider_override": provider_override,
                    "model_override": model_override,
                    "messages": msgs,
                    "_chat_usage": data.get("_chat_usage"),
                    "_last_context_stats": data.get("_last_context_stats"),
                    "_last_exec_session": data.get("_last_exec_session"),
                    "_last_exec_cumulative_usage": data.get("_last_exec_cumulative_usage"),
                    "head_id": head_id,
                }
            state._log(f"[restore] agent={agent_id} session={session_id}: "
                 f"{data.get('title')} (runtime_session={runtime_session_id})")
        except Exception as e:
            state._log(f"[restore] failed for {session_id}: {e}")
