"""Session lifecycle WS actions: delete / clear / load / search / list / follow_up_answer."""
from __future__ import annotations

import json
import time


async def handle_delete_session(ws, cmd: dict):
    from openprogram.webui import server as _s
    from openprogram.webui import persistence as _persist

    session_id = cmd.get("session_id")
    if not session_id:
        return
    # Snapshot agent_id BEFORE popping. `_delete_session_files`
    # otherwise looks up the conv in `_sessions` to find the
    # agent_id and falls back to a filesystem scan — which silently
    # misses sessions whose conv_dir is gone or never existed,
    # leaving the DB row behind and resurrecting the conversation
    # on the next page load.
    with _s._sessions_lock:
        conv = _s._sessions.pop(session_id, None)
    agent_id = (conv or {}).get("agent_id") if conv else None
    if conv:
        if conv.get("runtime") and hasattr(conv["runtime"], "close"):
            conv["runtime"].close()
        _s._cleanup_session_resources(session_id, conv)
    if agent_id:
        try:
            _persist.delete_session(agent_id, session_id)
        except Exception as e:
            _s._log(f"[delete_session] {session_id}: {e}")
    else:
        _s._delete_session_files(session_id)


async def handle_clear_sessions(ws, cmd: dict):
    from openprogram.webui import server as _s
    from openprogram.webui import persistence as _persist

    # Snapshot the full (session_id, agent_id) pairs BEFORE wiping
    # `_sessions`. `_s._delete_session_files` resolves `agent_id` from
    # `_sessions.get(...)` first; if we cleared the dict first that
    # lookup returns None and the function falls through to a
    # best-effort filesystem scan — which silently misses every
    # session that wasn't backed by a conv_dir, so the DB row sticks
    # around and the conversation reappears on refresh.
    with _s._sessions_lock:
        agent_id_by_session: dict[str, str | None] = {
            sid: conv.get("agent_id")
            for sid, conv in _s._sessions.items()
        }
        for conv in _s._sessions.values():
            if conv.get("runtime") and hasattr(conv["runtime"], "close"):
                conv["runtime"].close()
        _s._sessions.clear()
    # Also collect any session IDs that exist only in the DB (never
    # hydrated into `_sessions` this run) so a "clear all" really
    # nukes everything the sidebar shows on next page load.
    try:
        for agent_id, sid in _persist.list_sessions():
            agent_id_by_session.setdefault(sid, agent_id)
    except Exception:
        pass

    for cid, agent_id in agent_id_by_session.items():
        _s._follow_up_queues.pop(cid, None)
        with _s._running_tasks_lock:
            _s._running_tasks.pop(cid, None)
        # Prefer the snapshotted agent_id so the DB row + on-disk
        # conv_dir both get nuked atomically. If we don't have one,
        # fall back to the legacy resolve-by-scan path.
        if agent_id:
            try:
                _persist.delete_session(agent_id, cid)
            except Exception as e:
                _s._log(f"[clear_sessions] delete {cid}: {e}")
        else:
            _s._delete_session_files(cid)


async def handle_load_session(ws, cmd: dict):
    """Hydrate a session: linear chain under HEAD + full DAG snapshot + running-task probe."""
    from openprogram.webui import server as _s
    session_id = cmd.get("session_id")
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id)
    if conv:
        from openprogram.contextgit import (
            deepest_leaf,
            head_or_tip,
            linear_history,
            sibling_index,
            siblings as _siblings,
        )
        from openprogram.agent.session_db import default_db as _db_for_load
        from openprogram.webui.persistence import aggregate_tool_messages
        _db_load = _db_for_load()
        try:
            raw_msgs = _db_load.get_messages(conv["id"]) or []
            # Fold standalone role="tool" rows into their parent assistant's
            # tool_calls[] so the chat UI sees the same shape on refresh
            # as it does on live WS stream.
            all_msgs = aggregate_tool_messages(raw_msgs)
        except Exception:
            all_msgs = conv.get("messages", []) or []
            raw_msgs = all_msgs
        try:
            _sess_for_load = _db_load.get_session(conv["id"]) or {}
            _persisted_head = _sess_for_load.get("head_id")
        except Exception:
            _persisted_head = None
        head = _persisted_head or head_or_tip(conv, all_msgs)
        # If the persisted head points at a row that aggregation just
        # folded away (e.g. a role="tool" child of an assistant whose
        # turn never reached step 6's ``update_session(head_id=...)``
        # — common after a worker restart mid-turn), walk up the raw
        # parent_id chain until we hit a row that survived
        # aggregation. Without this the linear_history call below
        # returns [] and the page renders the empty Welcome screen
        # despite the DAG clearly having history.
        if head:
            agg_ids = {m.get("id") for m in all_msgs}
            if head not in agg_ids:
                raw_by_id = {m.get("id"): m for m in raw_msgs}
                cur = head
                hops = 0
                while cur and cur not in agg_ids and hops < 100:
                    parent = (raw_by_id.get(cur) or {}).get("parent_id")
                    if not parent or parent == cur:
                        cur = None
                        break
                    cur = parent
                    hops += 1
                if cur and cur in agg_ids:
                    head = cur
        chain = linear_history(all_msgs, head) if head else list(all_msgs)
        conv["messages"] = chain
        conv["head_id"] = head
        shown = []
        for m in chain:
            mid = m.get("id")
            idx, total = sibling_index(all_msgs, mid)
            prev_id = next_id = None
            if total > 1:
                sibs = _siblings(all_msgs, mid)
                ids = [s.get("id") for s in sibs]
                i = ids.index(mid) if mid in ids else -1
                if i > 0:
                    prev_id = deepest_leaf(all_msgs, ids[i - 1])
                if 0 <= i < len(ids) - 1:
                    next_id = deepest_leaf(all_msgs, ids[i + 1])
            shown.append({
                **m,
                "sibling_index": idx,
                "sibling_total": total,
                "prev_sibling_id": prev_id,
                "next_sibling_id": next_id,
            })

        tree_data = {}  # tree Context retired — execution trace lives in SessionDB DAG nodes
        try:
            from openprogram.agent.session_db import default_db
            full_msgs = default_db().get_messages(conv["id"])
        except Exception:
            full_msgs = all_msgs
        from openprogram.webui.graph_layout import annotate_graph
        graph = []
        for m in full_msgs:
            content = m.get("content") or ""
            preview = content.strip().replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "…"
            graph.append({
                "id": m.get("id"),
                "parent_id": m.get("parent_id"),
                "caller": m.get("caller") or "",
                "role": m.get("role"),
                "function": m.get("function"),
                "display": m.get("display"),
                "preview": preview,
                "created_at": m.get("created_at"),
            })
        # Compute (depth, lane) server-side so the frontend renders
        # parallel branches correctly without re-deriving topology.
        # ``annotate_graph`` filters microcompact synthetic nodes
        # (summary_*/k_*) — reassign so the WS payload ships only
        # the DAG-visible subset.
        graph = annotate_graph(graph, head)
        from openprogram.agent.session_config import load_session_run_config
        run_cfg = load_session_run_config(conv["id"])
        await ws.send_text(json.dumps({
            "type": "session_loaded",
            "data": {
                "id": conv["id"],
                "title": conv["title"],
                "messages": shown,
                "graph": graph,
                "head_id": head,
                "context_tree": tree_data,
                "provider_info": _s._get_provider_info(session_id),
                "context_stats": conv.get("_last_context_stats"),
                "channel": conv.get("channel"),
                "account_id": conv.get("account_id"),
                "peer": conv.get("peer"),
                "peer_display": conv.get("peer_display"),
                "source": conv.get("source"),
                "settings": {
                    "tools_enabled": run_cfg.tools_enabled,
                    "tools_override": run_cfg.tools_override,
                    "thinking_effort": run_cfg.thinking_effort,
                    "permission_mode": run_cfg.permission_mode,
                },
                "run_active": _s._is_run_active(conv["id"]),
            },
        }, default=str))
        # Zombie-task guards: no active runtime registered OR last event
        # >5 min ago → treat as dead, drop the task entry.
        with _s._running_tasks_lock:
            task_info = _s._running_tasks.get(session_id)
        if task_info and not _s._has_active_runtime(session_id):
            with _s._running_tasks_lock:
                _s._running_tasks.pop(session_id, None)
            task_info = None
        if task_info:
            _now = time.time()
            _started = task_info.get("started_at", _now)
            _last_evt_ts = task_info.get("last_event_at", _started)
            if (_now - _started > 300) and (_now - _last_evt_ts > 300):
                with _s._running_tasks_lock:
                    _s._running_tasks.pop(session_id, None)
                task_info = None
        if task_info:
            # Live partial-tree snapshot retired with the tree-Context
            # event system. The DAG nodes the function has produced so
            # far are already queryable via the GraphStore.
            await ws.send_text(json.dumps({
                "type": "running_task",
                "data": {
                    "session_id": session_id,
                    "msg_id": task_info["msg_id"],
                    "func_name": task_info["func_name"],
                    "started_at": task_info["started_at"],
                    "display_params": task_info.get("display_params", ""),
                    "partial_tree": None,
                    "stream_events": task_info.get("stream_events", []),
                },
            }, default=str))
    else:
        await ws.send_text(json.dumps({
            "type": "session_loaded",
            "data": {
                "id": session_id,
                "title": "New conversation",
                "context_tree": {},
                "provider_info": _s._get_provider_info(),
                "settings": {},
            },
        }, default=str))


async def handle_follow_up_answer(ws, cmd: dict):
    """User answered a follow-up question from a running function."""
    from openprogram.webui import server as _s
    fq_session_id = cmd.get("session_id", "")
    answer = cmd.get("answer", "")
    with _s._follow_up_lock:
        fq = _s._follow_up_queues.get(fq_session_id)
    if fq is not None:
        fq.put(answer)


async def handle_search_messages(ws, cmd: dict):
    """FTS-backed search across past sessions."""
    from openprogram.webui import server as _s
    query = (cmd.get("query") or "").strip()
    agent_id_filter = cmd.get("agent_id") or None
    limit = int(cmd.get("limit") or 50)
    if not query:
        await ws.send_text(json.dumps({
            "type": "search_results",
            "data": {"query": query, "results": [], "total": 0},
        }, default=str))
        return
    try:
        from openprogram.agent.session_db import default_db
        hits = default_db().search_messages(
            query, agent_id=agent_id_filter, limit=limit,
        )
    except Exception as e:
        _s._log(f"[search] failed: {e}")
        hits = []
    results = []
    for h in hits:
        content = h.get("content") or ""
        preview = content.strip().replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "…"
        results.append({
            "session_id": h.get("session_id"),
            "session_title": h.get("session_title"),
            "session_source": h.get("session_source"),
            "message_id": h.get("id"),
            "role": h.get("role"),
            "preview": preview,
            "content": content,
            "timestamp": h.get("timestamp"),
        })
    await ws.send_text(json.dumps({
        "type": "search_results",
        "data": {"query": query, "results": results, "total": len(results)},
    }, default=str))


async def handle_list_sessions(ws, cmd: dict):
    """Snapshot webui's in-memory sessions + per-agent sessions on disk."""
    from openprogram.webui import server as _s
    conv_list: list[dict] = []
    with _s._sessions_lock:
        for cid, conv in _s._sessions.items():
            runtime = conv.get("runtime")
            session_id = getattr(runtime, "_session_id", None) if runtime else None
            preview = None
            msgs = conv.get("messages") or []
            for m in reversed(msgs):
                if m.get("role") == "user":
                    c = m.get("content") or ""
                    if isinstance(c, str) and c.strip():
                        preview = c.strip().replace("\n", " ")
                        if len(preview) > 80:
                            preview = preview[:77] + "…"
                        break
            conv_list.append({
                "id": cid,
                "title": conv.get("title", "Untitled"),
                "created_at": conv.get("created_at"),
                "has_session": session_id is not None,
                "agent_id": conv.get("agent_id"),
                "source": conv.get("source"),
                "peer_display": conv.get("peer_display"),
                "channel": conv.get("channel"),
                "account_id": conv.get("account_id"),
                "peer": conv.get("peer"),
                "preview": preview,
            })
    seen_ids = {row["id"] for row in conv_list if row.get("id")}
    try:
        from openprogram.agent.session_db import default_db
        for srow in default_db().list_sessions(limit=10_000):
            sid = srow["id"]
            if sid in seen_ids:
                for row in conv_list:
                    if row.get("id") == sid:
                        if not row.get("source") and srow.get("source"):
                            row["source"] = srow["source"]
                        if not row.get("peer_display") and srow.get("peer_display"):
                            row["peer_display"] = srow["peer_display"]
                        if not row.get("channel") and srow.get("channel"):
                            row["channel"] = srow["channel"]
                        if not row.get("account_id") and srow.get("account_id"):
                            row["account_id"] = srow["account_id"]
                        break
                continue
            seen_ids.add(sid)
            preview = default_db().latest_user_text(sid)
            if preview:
                preview = preview.strip().replace("\n", " ")
                if len(preview) > 80:
                    preview = preview[:77] + "…"
            conv_list.append({
                "id": sid,
                "title": srow.get("title") or sid,
                "created_at": srow.get("created_at") or 0,
                "has_session": False,
                "agent_id": srow.get("agent_id"),
                "source": srow.get("source"),
                "peer_display": srow.get("peer_display"),
                "channel": srow.get("channel"),
                "account_id": srow.get("account_id"),
                "peer": srow.get("peer") or srow.get("peer_id"),
                "preview": preview,
            })
    except Exception:
        pass

    def _is_empty_placeholder(row: dict) -> bool:
        if row.get("preview"):
            return False
        t = (row.get("title") or "").strip()
        return t in ("", "New conversation", "Untitled")

    conv_list = [r for r in conv_list if not _is_empty_placeholder(r)]
    conv_list.sort(key=lambda c: c.get("created_at") or 0, reverse=True)
    await ws.send_text(json.dumps({
        "type": "sessions_list", "data": conv_list,
    }, default=str))


ACTIONS = {
    "delete_session": handle_delete_session,
    "clear_sessions": handle_clear_sessions,
    "load_session": handle_load_session,
    "follow_up_answer": handle_follow_up_answer,
    "search_messages": handle_search_messages,
    "list_sessions": handle_list_sessions,
}
