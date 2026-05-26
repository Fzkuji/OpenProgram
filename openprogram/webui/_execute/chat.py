"""action="query" branch of execute_in_context.

Extracted verbatim from openprogram/webui/server.py:_execute_in_context.
Behavior is unchanged. server-module globals are accessed via
`from openprogram.webui import server as _s`, mirroring the pattern in
openprogram/webui/ws_actions/*.py.

Exceptions raised here are intentionally NOT caught — the caller in
_execute/__init__.py wraps the dispatch in a unified try/except so the
chat + run paths share the same cancellation / error handling.
"""
from __future__ import annotations

import threading
import time


def run_query(
    *,
    session_id: str,
    msg_id: str,
    query: str,
    conv: dict,
    runtime,
    run_cfg,
    effective_thinking: str,
    effective_permission: str,
    agent_id: str,
    attachments: list | None,
) -> None:
    """Run the chat-query branch. Returns None; results are broadcast via WS."""
    from openprogram.webui import server as _s
    from openprogram.agent.session_config import tools_override_from_config

    # Direct chat — include conversation history for context
    _s._log(f"[exec] query: {query[:80]}... (thinking={effective_thinking})")
    with _s._running_tasks_lock:
        _s._running_tasks[session_id] = {
            "msg_id": msg_id,
            "func_name": "_chat",
            "started_at": time.time(),
            "last_event_at": time.time(),
            "display_params": "",
            "loaded_func_ref": None,
            "stream_events": [],
        }
    _s._emit_running_task_event(session_id)
    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "status", "content": "Thinking...",
    })

    # Build conversation context from history
    # Rough token estimate: ~4 chars per token, keep under 80k tokens
    _MAX_CONTEXT_CHARS = 320_000
    history_parts = []
    total_chars = 0
    # Build history from the ACTIVE BRANCH only. conv["messages"]
    # is a flat DAG store containing every sibling branch; if we
    # iterate it raw after a retry/edit, the model sees content
    # from the branch we forked away from, which both pollutes
    # the prompt and inflates token counts. linear_history walks
    # the parent chain from HEAD, so retries are isolated.
    _all_msgs = conv.get("messages", [])
    _head = _s._head_or_tip(conv, _all_msgs)
    messages = _s._linear_history(_all_msgs, _head) if _head else _all_msgs
    # Drop the in-flight placeholder so the query isn't duplicated
    # (assistant placeholder has empty content; retry of a user
    # turn has msg_id == HEAD with content == query).
    messages = [m for m in messages if m.get("id") != msg_id]
    # Walk backwards to prioritize recent messages
    for m in reversed(messages):
        role = m.get("role", "")
        content = m.get("content", "")
        if not content:
            continue
        if role == "user":
            display = m.get("display", "")
            if display == "runtime":
                entry = f"[User ran function]: {content}"
            else:
                entry = f"[User]: {content}"
        elif role == "assistant":
            fn = m.get("function", "")
            if fn:
                entry = f"[Function {fn} returned]: {content}"
            else:
                entry = f"[Assistant]: {content}"
        else:
            continue
        if total_chars + len(entry) > _MAX_CONTEXT_CHARS:
            break
        history_parts.append(entry)
        total_chars += len(entry)
    history_parts.reverse()

    chat_content = []
    if history_parts:
        context_text = (
            "── Conversation history ──\n"
            + "\n".join(history_parts)
            + "\n── End of history ──\n\n"
        )
        chat_content.append({"type": "text", "text": context_text})
    chat_content.append({"type": "text", "text": query})

    # Resolve tools from the session-level setting. When
    # unset, dispatcher falls back to the agent profile.
    resolved_tools_override = tools_override_from_config(run_cfg)

    # Hand the turn to the unified dispatcher. The user
    # message is already persisted by the WS handler that
    # spawned us, so we set user_already_persisted=True and
    # pass the same msg_id frontend used for chat_ack.
    #
    # The dispatcher emits chat_response envelopes that we
    # forward to the existing _broadcast_chat_response so
    # the WS contract stays unchanged.
    from openprogram.agent.dispatcher import (
        TurnRequest as _TurnRequest,
        process_user_turn as _process_user_turn,
    )

    _s._register_active_runtime(session_id, runtime)
    _chat_cancel_event = threading.Event()
    _s._register_cancel_event(session_id, _chat_cancel_event)
    if _s._is_cancelled(session_id):
        _chat_cancel_event.set()

    tool_calls_collected: list[dict] = []
    # Live block accumulator. Each tool_use opens a new
    # block keyed by tool_call_id; the matching tool_result
    # fills in `result` / `is_error`. The final list is
    # shipped on the result envelope so the frontend can
    # store it on the in-memory message dict (and so the
    # immediate post-run render matches the after-refresh
    # render that pulls msg.blocks from DB).
    tool_blocks_collected: list[dict] = []
    tool_blocks_by_id: dict[str, dict] = {}

    def _on_dispatcher_event(env: dict) -> None:
        et = env.get("type")
        if et != "chat_response":
            return
        payload = env.get("data") or {}
        # Track stream events on the running_tasks entry
        # so the existing reconnect/replay logic works.
        if payload.get("type") == "stream_event":
            evt = payload.get("event") or {}
            with _s._running_tasks_lock:
                ti = _s._running_tasks.get(session_id)
                if ti and "stream_events" in ti:
                    ti["stream_events"].append(evt)
                    if len(ti["stream_events"]) > 200:
                        ti["stream_events"] = ti["stream_events"][-200:]
                    ti["last_event_at"] = time.time()
            if evt.get("type") == "tool_use":
                _tid = evt.get("tool_call_id")
                blk = {
                    "type": "tool",
                    "tool": evt.get("tool"),
                    "tool_call_id": _tid,
                    "input": evt.get("input"),
                    "result": None,
                    "is_error": False,
                }
                tool_blocks_collected.append(blk)
                if _tid:
                    tool_blocks_by_id[_tid] = blk
            if evt.get("type") == "tool_result":
                _tid = evt.get("tool_call_id")
                blk = tool_blocks_by_id.get(_tid)
                if blk is None:
                    # Result without prior tool_use (rare,
                    # but degrade gracefully so the user
                    # still sees something).
                    blk = {
                        "type": "tool",
                        "tool": evt.get("tool"),
                        "tool_call_id": _tid,
                        "input": None,
                        "result": None,
                        "is_error": False,
                    }
                    tool_blocks_collected.append(blk)
                    if _tid:
                        tool_blocks_by_id[_tid] = blk
                blk["result"] = evt.get("result")
                blk["is_error"] = bool(evt.get("is_error"))
                tool_calls_collected.append({
                    "tool": evt.get("tool"),
                    "result": evt.get("result"),
                    "is_error": evt.get("is_error"),
                })
            # Fan out to WS clients with the same envelope
            # shape the legacy on_stream hook used.
            _s._broadcast_chat_response(session_id, msg_id, {
                "type": "stream_event",
                "event": evt,
                "function": "_chat",
            })
        elif payload.get("type") in ("result", "error"):
            # Final-result / error envelopes arrive last;
            # we surface them after our own context_stats
            # broadcast below, so swallow here.
            pass

    # Carry the conversation's picker choice (if any) into
    # the dispatcher so it doesn't fall back to the agent
    # profile's default model. Without this the model
    # picker only updates `conv["runtime"]`, but the
    # dispatcher re-resolves through `_resolve_model` and
    # silently routes back to the agent default — that's
    # the "I picked Opus but it answers as Sonnet" bug.
    _conv_now = _s._sessions.get(session_id) or {}
    _picker_provider = _conv_now.get("provider_override")
    _picker_model = _conv_now.get("model_override")
    _model_override = None
    if _picker_provider and _picker_model:
        _model_override = f"{_picker_provider}/{_picker_model}"
    elif _picker_model:
        _model_override = _picker_model
    _s._log(
        f"[model resolve] session={session_id!r} "
        f"provider_override={_picker_provider!r} "
        f"model_override={_picker_model!r} "
        f"agent_model={_conv_now.get('agent_id')!r}/profile "
        f"resolved={_model_override!r}"
    )

    req_obj = _TurnRequest(
        session_id=session_id,
        user_text=query,
        agent_id=agent_id,
        source="web",
        permission_mode=effective_permission,
        tools_override=resolved_tools_override,
        thinking_effort=effective_thinking,
        user_msg_id=msg_id,
        user_already_persisted=True,
        model_override=_model_override,
        attachments=attachments,
    )

    try:
        turn_result = _process_user_turn(
            req_obj, on_event=_on_dispatcher_event,
            cancel_event=_chat_cancel_event,
        )
    finally:
        with _s._running_tasks_lock:
            _s._running_tasks.pop(session_id, None)
        _s._emit_running_task_event(session_id)
        _s._unregister_active_runtime(session_id)
        _s._unregister_cancel_event(session_id)

    if turn_result.failed:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": turn_result.error or "(unknown error)",
        })
        return

    result = turn_result.final_text
    _s._log(f"[exec] query completed, result length: {len(str(result))}")

    # Dispatcher persisted the assistant message itself
    # (with id=msg_id+'_reply'). Hydrate the in-memory mirror
    # from SessionDB so subsequent webui readers
    # (load_session, retry, etc.) see it.
    _s._hydrate_messages_from_db(session_id)
    with _s._sessions_lock:
        refreshed = _s._sessions.get(session_id)
        if refreshed is not None:
            try:
                from openprogram.agent.session_db import default_db
                refreshed["messages"] = default_db().get_branch(session_id) or []
                sess = default_db().get_session(session_id)
                if sess:
                    refreshed["head_id"] = sess.get("head_id")
            except Exception:
                pass

    # Blocks: dispatcher persists them in extra; we also
    # ship them on the result envelope so the in-memory
    # transcript carries the same collapsible scaffold as
    # the after-refresh DB-rebuilt view (otherwise the
    # user sees rich tool bubbles during streaming, then
    # plain text once we stamp the message, then the
    # rebuilt scaffold after refresh — three different
    # renders for the same turn).
    # Prefer the dispatcher-built ordered blocks (thinking / text /
    # tool, in original LLM emission order). Fall back to the
    # tool-only stream-event accumulator for paths that don't
    # populate TurnResult.blocks (e.g. cancellation mid-stream).
    _ordered_blocks = list(getattr(turn_result, "blocks", None) or [])
    _blocks_out = _ordered_blocks if _ordered_blocks else tool_blocks_collected
    _s._broadcast_chat_response(session_id, msg_id, {
        "type": "result",
        "content": str(result),
        "tool_calls": tool_calls_collected,
        "blocks": _blocks_out,
    })
    # dispatcher 走 stream_simple 不走 runtime.exec, last_usage 永远是 0;
    # 把 TurnResult.usage 同步过去, 不然 token pill 永远显示 0.
    _s._runtime_management.sync_turn_usage_to_runtime(
        runtime, turn_result.usage,
    )
    _s._broadcast_context_stats(session_id, msg_id, chat_runtime=runtime)

    # If this is a channel-bound agent session (WeChat /
    # Telegram / etc.), push the web-side reply back out to
    # the external user so their phone sees it too. The
    # session meta carries channel + account_id + peer — we
    # look it up from disk because the webui's in-memory
    # conversation dict doesn't always carry these fields
    # yet.
    try:
        from openprogram.channels.outbound import send as _send
        meta = _s._load_agent_session_meta(session_id)
        if meta and meta.get("channel") and meta.get("account_id"):
            peer_id = (meta.get("peer") or {}).get("id") or ""
            if peer_id:
                _send(
                    meta["channel"],
                    meta["account_id"],
                    str(peer_id),
                    str(result),
                )
    except Exception as e:  # noqa: BLE001
        _s._log(f"[channel outbound] skipped: "
                f"{type(e).__name__}: {e}")
