"""Server session lifecycle."""
from __future__ import annotations
from ... import server as state


def _cleanup_session_resources(session_id: str, conv: dict):
    """Clean up all resources associated with a deleted conversation."""
    # Clean up follow-up queues and running tasks
    with state._follow_up_lock:
        state._follow_up_queues.pop(session_id, None)
    with state._running_tasks_lock:
        state._running_tasks.pop(session_id, None)


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
        session_id = "local_" + state.uuid.uuid4().hex[:10]
    with state._sessions_lock:
        if session_id not in state._sessions:
            resolved_agent = agent_id or state._default_agent_id()
            # Hydrate the active branch from SessionDB so a webui
            # restart / fresh worker process sees the same messages
            # the dispatcher and channels worker have been writing.
            # Empty list for brand-new conversations.
            try:
                from openprogram.agent.session_db import default_db
                _db = default_db()
                _hydrated = _db.get_branch(session_id) or []
                _sess = _db.get_session(session_id)
                _hydrated_head = _sess.get("head_id") if _sess else None
            except Exception:
                _hydrated = []
                _sess = None
                _hydrated_head = None
            resolved_agent = (
                agent_id
                or ((_sess or {}).get("agent_id") if isinstance(_sess, dict) else None)
                or resolved_agent
            )
            # Inherit a user-pinned (provider, model) from the most
            # recent picker click that didn't have a session attached.
            # Lets the welcome-page flow "pick Opus, then start a chat"
            # actually run Opus — otherwise the new conv falls back to
            # the agent profile's default model.
            _inherit_prov = state._user_pinned_provider
            _inherit_model = state._user_pinned_model
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
                "tools_enabled": ((_sess or {}).get("tools_enabled") if isinstance(_sess, dict) else None),
                "tools_override": ((_sess or {}).get("tools_override") if isinstance(_sess, dict) else None),
                "thinking_effort": ((_sess or {}).get("thinking_effort") if isinstance(_sess, dict) else None),
                "permission_mode": ((_sess or {}).get("permission_mode") if isinstance(_sess, dict) else None),
            }
            # session.start on the bus — plugin subscribers included.
            # emit_safe swallows failures: never break session creation.
            from openprogram.events import emit_safe
            emit_safe("session.start", "system", {
                "session_id": session_id,
                "agent_id": resolved_agent,
                "channel": channel,
            }, {"session": session_id})
        return state._sessions[session_id]


def _canonical_foreground_task(session_id: str) -> dict | None:
    from openprogram.execution import default_store
    from openprogram.execution.foreground import active_foreground_task

    return active_foreground_task(default_store(), session_id)


def _is_run_active(session_id: str) -> bool:
    """Is there an in-flight agent run for this conversation?

    Single source of truth for UI gating (Edit / Retry buttons go grey
    while a run is active). Driven off ``_running_tasks`` — the same
    dict we use for pause / stop, so we can't drift out of sync.
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
            if state.time.time() - task.get("started_at", 0) > 300:
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


def _try_reserve_run(session_id: str, msg_id: str) -> bool:
    from openprogram.store.session.session_lock import session_interprocess_lock
    try:
        with session_interprocess_lock(session_id, timeout=0.25):
            return state._try_reserve_run_locked(session_id, msg_id)
    except (TimeoutError, BlockingIOError):
        return False


def _try_reserve_run_locked(session_id: str, msg_id: str) -> bool:
    """Atomically reserve one session for a chat turn before DAG mutation."""
    try:
        from openprogram.store.session.migration import session_hold_active
        from openprogram.paths import get_state_dir
        if session_hold_active(state.Path(get_state_dir()) / "sessions", session_id):
            return False
    except Exception:
        return False
    if state._is_run_active(session_id):
        return False
    now = state.time.time()
    with state._running_tasks_lock:
        if session_id in state._running_tasks:
            return False
        state._running_tasks[session_id] = {
            "msg_id": msg_id,
            "func_name": "_chat",
            "started_at": now,
            "last_event_at": now,
            "display_params": "",
            "loaded_func_ref": None,
            "stream_events": [],
            # Admission mints the canonical execution id. A provisional
            # reservation must not publish or derive one from msg_id.
            "execution_id": None,
            "_reserved": True,
        }
    return True


def _release_run_reservation(session_id: str, msg_id: str) -> None:
    """Release only the still-provisional reservation owned by ``msg_id``."""
    with state._running_tasks_lock:
        task = state._running_tasks.get(session_id)
        if task and task.get("_reserved") and task.get("msg_id") == msg_id:
            state._running_tasks.pop(session_id, None)


def _activate_run_reservation(session_id: str, msg_id: str, runtime) -> bool:
    """Atomically hand an owned reservation to its registered runtime."""
    with state._running_tasks_lock:
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
    with state._running_tasks_lock:
        task = state._running_tasks.get(session_id)
        if not (task and task.get("msg_id") == msg_id):
            return False
        state._running_tasks.pop(session_id, None)
    # The active-runtime registration is the handoff counterpart to the
    # provisional reservation.  Retire it only after ownership was matched;
    # a late finisher must not clear a newer run in the same session.
    state._unregister_active_runtime(session_id)
    return True


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
        task = state._running_tasks.get(session_id)
        task_msg = task.get("msg_id") if task else None
        task_exec = task.get("execution_id") if task else None
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
