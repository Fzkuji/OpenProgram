"""Chat WS actions: chat / retry_node / retry_overwrite / switch_attempt /
set_conversation_channel.

The ``chat`` action is the primary turn entry point — equivalent to the
REST POST /api/chat. The retry / switch / channel-bind actions are
ws-only.
"""
from __future__ import annotations

import json
import threading
import time
import uuid


async def handle_chat(ws, cmd: dict):
    from openprogram.webui import server as _s
    text = cmd.get("text", "").strip()
    session_id = cmd.get("session_id")
    agent_id = cmd.get("agent_id") or None
    thinking_effort = cmd.get("thinking_effort") or None
    exec_thinking_effort = cmd.get("exec_thinking_effort") or None
    tools_flag = cmd.get("tools")
    web_search_flag = bool(cmd.get("web_search"))
    permission_mode = cmd.get("permission_mode") or None
    # "Web Search" plus-menu toggle: layer ``web_search`` on top of
    # whatever the Tools toggle resolves to. Three useful states:
    #   * tools=False, web_search=False → tools_override=[]  (no tools)
    #   * tools=False, web_search=True  → tools_override=["web_search"]
    #   * tools=True,  web_search=*     → DEFAULT_TOOLS [+web_search]
    #   * tools=None (defer to agent profile), web_search=True
    #       → agent's tools list with "web_search" guaranteed
    if web_search_flag:
        try:
            from openprogram.tools import DEFAULT_TOOLS as _DEFAULT_TOOLS
        except Exception:
            _DEFAULT_TOOLS = []
        if isinstance(tools_flag, list):
            base = list(tools_flag)
        elif tools_flag is True:
            base = list(_DEFAULT_TOOLS)
        elif tools_flag is False:
            base = []
        else:
            # tools_flag is None — caller wants "use the agent profile's
            # configured tools". We don't have those resolved here, so
            # fall back to DEFAULT_TOOLS for the explicit override list
            # the dispatcher will see. Better to be slightly broader
            # than to drop the agent's other tools entirely.
            base = list(_DEFAULT_TOOLS)
        if "web_search" not in base:
            base.append("web_search")
        tools_flag = base
    raw_attachments = cmd.get("attachments") or None
    attachments = None
    if isinstance(raw_attachments, list) and raw_attachments:
        attachments = [a for a in raw_attachments if isinstance(a, dict) and a.get("data")]
        if not attachments:
            attachments = None
    if not text and not attachments:
        return
    if not text and attachments:
        text = "(see attachment)"

    new_channel = (cmd.get("channel") or "").strip().lower() or None
    new_account_id = (cmd.get("account_id") or "").strip() or None
    new_peer = (cmd.get("peer") or "").strip() or None
    conv = _s._get_or_create_session(
        session_id,
        agent_id=agent_id,
        channel=new_channel,
        account_id=new_account_id,
        peer=new_peer,
    )
    session_id = conv["id"]
    from openprogram.agent.session_config import save_session_run_config
    run_cfg = save_session_run_config(
        session_id,
        agent_id=conv.get("agent_id") or _s._default_agent_id(),
        tools=tools_flag,
        thinking_effort=thinking_effort,
        permission_mode=permission_mode,
    )
    conv["tools_enabled"] = run_cfg.tools_enabled
    conv["tools_override"] = run_cfg.tools_override
    conv["thinking_effort"] = run_cfg.thinking_effort
    conv["permission_mode"] = run_cfg.permission_mode
    msg_id = str(uuid.uuid4())[:8]

    if not conv.get("_titled"):
        conv["title"] = text[:50] + ("..." if len(text) > 50 else "")
        conv["_titled"] = True

    parsed = _s._parse_chat_input(text)

    user_msg = {
        "role": "user",
        "id": msg_id,
        "content": text,
        "timestamp": time.time(),
        "source": "web",
    }
    if parsed["action"] == "run":
        user_msg["display"] = "runtime"
    if attachments:
        manifest = [
            {"type": a.get("type"), "media_type": a.get("media_type"),
             "size_b64": len(a.get("data") or "")}
            for a in attachments
        ]
        user_msg["extra"] = json.dumps({"attachments": manifest}, default=str)
    _s._append_msg(conv, user_msg)

    await ws.send_text(json.dumps({
        "type": "chat_ack",
        "data": {"session_id": session_id, "msg_id": msg_id},
    }))

    if parsed["action"] == "run":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "run"),
            kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"],
                    "thinking_effort": run_cfg.thinking_effort,
                    "exec_thinking_effort": exec_thinking_effort,
                    "permission_mode": run_cfg.permission_mode},
            daemon=True,
        ).start()
    elif parsed["action"] == "query":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "query"),
            kwargs={"query": parsed["raw"],
                    "thinking_effort": run_cfg.thinking_effort,
                    "tools_flag": tools_flag,
                    "permission_mode": run_cfg.permission_mode,
                    "attachments": attachments},
            daemon=True,
        ).start()


async def handle_retry_node(ws, cmd: dict):
    from openprogram.webui import server as _s
    node_path = cmd.get("node_path")
    session_id = cmd.get("session_id")
    params_override = cmd.get("params")
    _s._log(f"[retry] received retry_node: session_id={session_id}, node_path={node_path}, params_override={params_override}")
    if not node_path or not session_id:
        _s._log("[retry] missing node_path or session_id, aborting")
        await ws.send_text(json.dumps({
            "type": "chat_response",
            "data": {"type": "error",
                     "content": "Retry failed: missing node_path or session_id",
                     "session_id": session_id or "", "msg_id": "err"},
        }))
        return
    msg_id = str(uuid.uuid4())[:8]
    await ws.send_text(json.dumps({
        "type": "chat_ack",
        "data": {"session_id": session_id, "msg_id": msg_id},
    }))
    _s._log(f"[retry] starting retry thread msg_id={msg_id}")
    threading.Thread(
        target=_s._retry_node,
        args=(session_id, msg_id, node_path, params_override),
        daemon=True,
    ).start()


async def handle_retry_overwrite(ws, cmd: dict):
    """Overwrite retry: drop old user+assistant for the function, re-run."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    func_name = cmd.get("function")
    text = cmd.get("text", "").strip()
    thinking_effort = cmd.get("thinking_effort") or None
    exec_thinking_effort = cmd.get("exec_thinking_effort") or None
    if not session_id or not text:
        return

    conv = _s._get_or_create_session(session_id)
    conv.pop("_last_exec_session", None)
    old_rt = conv.pop("_last_exec_runtime", None)
    if old_rt and hasattr(old_rt, "close"):
        old_rt.close()

    messages = conv.get("messages", [])
    new_messages = []
    skip_next_assistant = False
    for m in messages:
        if skip_next_assistant and m.get("role") == "assistant":
            skip_next_assistant = False
            continue
        if (m.get("role") == "user" and m.get("display") == "runtime"):
            parsed_check = _s._parse_chat_input(m.get("content", ""))
            if parsed_check.get("function") == func_name:
                skip_next_assistant = True
                continue
        new_messages.append(m)
    conv["messages"] = new_messages
    _s._set_active_head(session_id, new_messages[-1]["id"] if new_messages else None)

    conv["function_trees"] = [
        ft for ft in conv.get("function_trees", [])
        if ft.get("name") != func_name and ft.get("path") != func_name
    ]

    msg_id = str(uuid.uuid4())[:8]
    original_content = cmd.get("original_content", text)

    _s._append_msg(conv, {
        "role": "user",
        "id": msg_id,
        "content": text,
        "original_content": original_content,
        "display": "runtime",
        "timestamp": time.time(),
    })

    await ws.send_text(json.dumps({
        "type": "chat_ack",
        "data": {"session_id": session_id, "msg_id": msg_id},
    }))

    parsed = _s._parse_chat_input(text)
    print(f"[retry] text={text[:200]}")
    print(f"[retry] parsed={parsed}")
    if parsed["action"] == "run":
        threading.Thread(
            target=_s._execute_in_context,
            args=(session_id, msg_id, "run"),
            kwargs={"func_name": parsed["function"], "kwargs": parsed["kwargs"],
                    "thinking_effort": thinking_effort,
                    "exec_thinking_effort": exec_thinking_effort},
            daemon=True,
        ).start()
    else:
        _s._broadcast_chat_response(session_id, msg_id, {
            "type": "error",
            "content": f"Could not parse retry command: {text[:100]}",
            "function": func_name,
            "display": "runtime",
        })


async def handle_switch_attempt(ws, cmd: dict):
    """Swap the visible result among stored attempts for a function call."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    func_name = cmd.get("function")
    attempt_idx = cmd.get("attempt_index", 0)
    conv = _s._sessions.get(session_id)
    if not conv:
        return
    messages = conv.get("messages", [])
    msg_idx = None
    target_msg = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if (m.get("role") == "assistant"
                and m.get("type") == "result"
                and m.get("function") == func_name
                and "attempts" in m):
            target_msg = m
            msg_idx = i
            break

    if target_msg and 0 <= attempt_idx < len(target_msg["attempts"]):
        old_idx = target_msg.get("current_attempt", 0)
        attempts = target_msg["attempts"]

        subsequent_now = messages[msg_idx + 1:]
        if old_idx < len(attempts):
            attempts[old_idx]["subsequent_messages"] = subsequent_now

        target_msg["current_attempt"] = attempt_idx
        target_msg["content"] = attempts[attempt_idx]["content"]

        restored = attempts[attempt_idx].get("subsequent_messages", [])
        new_msgs_for_attempt = messages[:msg_idx + 1] + restored
        conv["messages"] = new_msgs_for_attempt
        _s._set_active_head(
            session_id,
            new_msgs_for_attempt[-1]["id"] if new_msgs_for_attempt else None,
        )

        selected_tree = attempts[attempt_idx].get("tree")
        if selected_tree:
            func_trees = conv.get("function_trees", [])
            for ti, ft in enumerate(func_trees):
                if ft.get("name") == func_name or ft.get("path") == func_name:
                    func_trees[ti] = selected_tree
                    break

        _s._save_session(session_id)
        await ws.send_text(json.dumps({
            "type": "attempt_switched",
            "data": {
                "function": func_name,
                "attempt_index": attempt_idx,
                "content": attempts[attempt_idx]["content"],
                "tree": attempts[attempt_idx].get("tree"),
                "total": len(attempts),
                "subsequent_messages": restored,
            },
        }, default=str))


async def handle_set_conversation_channel(ws, cmd: dict):
    """Bind (or unbind) a conversation to a chat channel + account.

    Enforces 1:1 ownership: stealing a (channel, account) slot evicts
    any prior owner back to local. Persists the binding to SessionDB
    when the conv already has a row.
    """
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    ch = (cmd.get("channel") or "").strip().lower() or None
    acct_id = (cmd.get("account_id") or "").strip() or None
    peer = (cmd.get("peer") or "").strip() or None
    peer_display = (cmd.get("peer_display") or "").strip() or None
    ok = False
    err = None
    if not session_id:
        err = "session_id required"
    else:
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
        if conv is None:
            err = f"unknown conversation {session_id!r}"
        elif ch is None and (acct_id or peer):
            err = "channel must be set when account_id / peer is set"
        else:
            evicted_ids: list[str] = []
            if ch:
                from openprogram.agent.session_db import default_db
                db_pre = default_db()
                db_owners = set(db_pre.sessions_with_binding(ch, acct_id))
                with _s._sessions_lock:
                    mem_owners = {
                        oid for oid, o in _s._sessions.items()
                        if o.get("channel") == ch and o.get("account_id") == acct_id
                    }
                candidates = (db_owners | mem_owners) - {session_id}
                for oid in candidates:
                    with _s._sessions_lock:
                        other = _s._sessions.get(oid)
                        if other is not None:
                            other["channel"] = None
                            other["account_id"] = None
                            other["peer"] = None
                            other["peer_display"] = None
                    try:
                        if db_pre.get_session(oid) is not None:
                            db_pre.update_session(
                                oid,
                                channel=None,
                                account_id=None,
                                peer=None,
                                peer_display=None,
                            )
                    except Exception as ex:
                        _s._log(f"[set_conversation_channel] evict {oid} db: {ex}")
                    evicted_ids.append(oid)

            conv["channel"] = ch
            conv["account_id"] = acct_id if ch else None
            conv["peer"] = peer if ch else None
            if peer_display is not None:
                conv["peer_display"] = peer_display if ch else None
            try:
                from openprogram.agent.session_db import default_db
                db = default_db()
                if db.get_session(session_id) is not None:
                    db.update_session(
                        session_id,
                        channel=conv["channel"],
                        account_id=conv["account_id"],
                        peer=conv["peer"],
                        peer_display=conv.get("peer_display"),
                    )
                ok = True
            except Exception as e:
                err = f"persist failed: {type(e).__name__}: {e}"

            for oid in evicted_ids:
                try:
                    await ws.send_text(json.dumps({
                        "type": "session_channel_updated",
                        "data": {
                            "session_id": oid,
                            "ok": True,
                            "channel": None,
                            "account_id": None,
                            "peer": None,
                            "evicted_by": session_id,
                        },
                    }, default=str))
                except Exception:
                    pass
    await ws.send_text(json.dumps({
        "type": "session_channel_updated",
        "data": {
            "session_id": session_id,
            "ok": ok,
            "channel": ch,
            "account_id": acct_id,
            "peer": peer,
            "error": err,
        },
    }, default=str))


async def handle_compact(ws, cmd: dict):
    """Manual /compact entry point — user-initiated compaction.

    Frontend sends ``{action: "compact", session_id, keep_recent_tokens?}``.
    We delegate to ``dispatcher.trigger_compaction`` which walks the full
    ``engine.compact`` pipeline (LLM summary, DAG re-parent, event
    broadcast).
    """
    from openprogram.webui import server as _s
    from openprogram.agent.dispatcher import trigger_compaction

    session_id = cmd.get("session_id")
    if not session_id:
        await ws.send(json.dumps({
            "type": "chat_response",
            "data": {"type": "error",
                     "content": "compact: missing session_id"},
        }))
        return

    conv = _s._get_or_create_session(session_id)
    agent_id = conv.get("agent_id") or "main"
    keep_recent_tokens = cmd.get("keep_recent_tokens")
    if keep_recent_tokens is not None:
        try:
            keep_recent_tokens = int(keep_recent_tokens)
        except (TypeError, ValueError):
            keep_recent_tokens = None

    def _emit(envelope: dict) -> None:
        # Re-shape to the standard chat-response wire frame and
        # broadcast so every connected client sees compaction progress.
        if envelope.get("type") == "chat_response":
            _s._broadcast_chat_response(
                session_id, "compact", envelope.get("data") or {},
            )

    # Compaction is a blocking sync call (it runs an LLM under the hood
    # via its own event loop). Run it off the WS loop so the websocket
    # stays responsive.
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: trigger_compaction(
                session_id,
                agent_id=agent_id,
                on_event=_emit,
                keep_recent_tokens=keep_recent_tokens,
            ),
        )
    except Exception as e:  # noqa: BLE001
        _s._broadcast_chat_response(session_id, "compact", {
            "type": "error",
            "content": f"compact failed: {type(e).__name__}: {e}",
        })


ACTIONS = {
    "chat": handle_chat,
    "retry_node": handle_retry_node,
    "retry_overwrite": handle_retry_overwrite,
    "switch_attempt": handle_switch_attempt,
    "set_conversation_channel": handle_set_conversation_channel,
    "compact": handle_compact,
}
