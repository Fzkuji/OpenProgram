"""Slash-command actions: manual compaction and the system sandbox toggle."""
from __future__ import annotations

import asyncio
import json
import uuid

from .session_agent import _db_agent_id


_compact_tasks: set[asyncio.Task] = set()


async def handle_compact(ws, cmd: dict):
    """Manual /compact entry point — user-initiated compaction.

    Frontend sends ``{action: "compact", session_id, keep_recent_tokens?}``.
    We delegate to ``trigger_compaction``, which compacts the chain ending
    at the session's head through ``engine.compact`` (LLM summary, one
    summary node spliced in, compaction frames).

    Compaction rewrites the history the branch's next turn reads, so it is
    that branch's foreground operation (``op_kind: "compact"``): refused
    with ``run_active`` naming what runs while the branch is busy, and no
    turn starts on that branch until it ends. Other branches keep running.
    Every client shows it as the branch's run with a Stop, which names its
    ``op_id`` (``operation.cancel``). It runs as a background task so the
    requesting socket keeps serving its other actions during the summary
    call.
    """
    from openprogram.interfaces.server import server as _s
    from .turn_request import _send_chat_error

    session_id = cmd.get("session_id")
    if not session_id:
        await _send_chat_error(ws, None, "invalid_request", "compact: missing session_id")
        return

    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.conversation_work import run_for_conversation

    def read_agent() -> str | None:
        # The conversation's record is read under its lock: in its lane.
        if default_db().get_session(session_id) is None:
            return None
        return _db_agent_id(session_id)

    try:
        agent_id = await run_for_conversation(session_id, read_agent)
    except Exception:
        agent_id = None
    if agent_id is None:
        await _send_chat_error(
            ws, session_id, "unknown_session", f"compact: unknown session {session_id}",
        )
        return

    keep_recent_tokens = cmd.get("keep_recent_tokens")
    if keep_recent_tokens is not None:
        try:
            keep_recent_tokens = int(keep_recent_tokens)
        except (TypeError, ValueError):
            keep_recent_tokens = None

    reservation_id = "compact_" + uuid.uuid4().hex[:8]
    refusal = await run_for_conversation(
        session_id, _s.reserve_operation, session_id, "compact", reservation_id,
    )
    if refusal is not None:
        # The same frame a failed compaction sends, so the context panel
        # resets its compaction state; nothing touches a queued message.
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {
                "type": "compaction_failed",
                "session_id": session_id,
                "msg_id": "compact",
                "code": refusal.get("code") or "run_active",
                "kind": refusal.get("kind"),
                "error": refusal.get("error"),
                "user_initiated": True,
            },
        }))
        return

    task = asyncio.create_task(_run_compaction(
        _s, session_id, reservation_id, agent_id, keep_recent_tokens,
    ))
    _compact_tasks.add(task)
    task.add_done_callback(_compact_tasks.discard)


async def _run_compaction(_s, session_id: str, reservation_id: str,
                          agent_id: str, keep_recent_tokens) -> None:
    from openprogram.runtime.conversation.turn_runtime import trigger_compaction

    # The frame that ends compaction goes out only after the slot is free:
    # a client reloads the conversation on it, and a load that still found
    # the reservation would show the conversation as running.
    ending: list[dict] = []

    def _emit(envelope: dict) -> None:
        # Re-shape to the standard chat-response wire frame and
        # broadcast so every connected client sees compaction progress.
        if envelope.get("type") != "chat_response":
            return
        data = envelope.get("data") or {}
        if data.get("type") in ("compaction_finished", "compaction_failed"):
            ending.append(data)
            return
        if data.get("type") == "compaction_started" and op_id:
            # The terminal's stop key names this operation (operation.cancel).
            data = {**data, "op_id": op_id}
        _s._broadcast_chat_response(session_id, "compact", data)

    # A Stop of this operation (its op_id) ends the summary request.
    from openprogram.interfaces.server.server_runtime.run_slots import cancel_event
    with _s._running_tasks_lock:
        slot = _s._running_tasks.get(session_id) or {}
        op_id = slot.get("op_id") if slot.get("msg_id") == reservation_id else None
    stop = cancel_event(op_id)

    # Compaction is a blocking sync call (it runs an LLM under the hood
    # via its own event loop), so it runs on a worker thread.
    compacted = False
    try:
        await asyncio.to_thread(
            trigger_compaction,
            session_id,
            agent_id=agent_id,
            on_event=_emit,
            keep_recent_tokens=keep_recent_tokens,
            cancel_event=stop,
        )
        compacted = True
    except Exception as e:  # noqa: BLE001
        ending.append({
            "type": "error",
            "content": f"compact failed: {type(e).__name__}: {e}",
        })
    finally:
        # The clear reads the selected branch under the conversation's lock.
        from openprogram.state.store.session.conversation_work import run_for_conversation
        await run_for_conversation(
            session_id, _s._release_run_reservation, session_id, reservation_id)
        for data in ending:
            _s._broadcast_chat_response(session_id, "compact", data)
    if compacted:
        # Accounting reads the new history; it rewrites none, so it runs
        # outside the slot and broadcasts its own context_stats frame.
        try:
            await asyncio.to_thread(_s.refresh_context_stats, session_id, "compact")
        except Exception as e:  # noqa: BLE001
            _s._log(f"[compact] context stats {session_id}: {type(e).__name__}: {e}")


async def handle_operation_cancel(ws, cmd: dict):
    """Stop one foreground operation by its ``op_id``.

    ``{action: "operation.cancel", session_id, op_id}``. An operation with
    no execution (/compact, a rewind) ends through its cancel event and its
    slot's clear; one that became a turn is stopped like any turn. Answers
    ``operation_cancel_result`` with ``ok`` or ``code: "not_found"`` for an
    operation that already ended.
    """
    from openprogram.interfaces.server import server as _s

    session_id = (cmd.get("session_id") or "").strip()
    op_id = (cmd.get("op_id") or "").strip()
    if not session_id or not op_id:
        result = {"ok": False, "code": "invalid_request", "error": "session_id and op_id are required"}
    else:
        result = await asyncio.to_thread(_s.cancel_operation, session_id, op_id)
    await ws.send_text(json.dumps({
        "type": "operation_cancel_result",
        "data": {**result, "session_id": session_id or None, "op_id": op_id or None},
    }, default=str))


async def handle_sandbox(ws, cmd: dict):
    """Toggle the system sandbox on or off."""
    from openprogram.host.sandbox import is_enabled, set_mode, unavailable_reason
    current = is_enabled()
    reason = unavailable_reason()
    if not current and reason:
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {"type": "status",
                     "content": f"System sandbox not available ({reason})"},
        }))
        return
    # Persisted through the config, because the agent turn runs in a bare
    # thread that this asyncio task's context never reaches.
    set_mode(not current)
    state = "ON" if not current else "OFF"
    msg = f"Sandbox: {state}"
    if not current:
        msg += (" — bash writes confined to the working directory, "
                "credential paths unreadable, no network")
    await ws.send_text(json.dumps({
        "type": "chat_response",
        "data": {"type": "status", "content": msg},
    }))
