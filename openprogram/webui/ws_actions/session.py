"""Session lifecycle WS actions: delete / clear / load / search / list / follow_up_answer."""
from __future__ import annotations

import json
import time


def _annotate_spawn_origin(graph: list[dict]) -> None:
    """Attach ``spawned_from`` to each ``source=agent_spawn`` user msg
    that's the root of a sub-branch.

    The field is a dict ``{caller_id, caller_branch, caller_session_id,
    label}`` pointing at the main-lane turn that produced the sub
    branch (so the frontend can render a "Spawned from" card with a
    Switch button mirroring the main-lane AttachCard).

    Discovery: scan attach pointer nodes for ``attach_ref`` → sub
    branch tip. Walk parent_id back from the tip to reach the
    sub-branch root. The attach node's own ``parent_id`` (= the main
    LLM reply that ran the task() tool) is the caller id we record.
    """
    by_id = {n.get("id"): n for n in graph if n.get("id")}
    # Build conv_children from parent_id
    conv_children: dict[str, list[str]] = {}
    for n in graph:
        p = n.get("parent_id")
        if p:
            conv_children.setdefault(p, []).append(n.get("id") or "")
    for attach in graph:
        if attach.get("function") != "attach":
            continue
        tip = attach.get("attach_ref")
        caller = attach.get("parent_id") or attach.get("caller")
        if not tip or tip not in by_id or not caller:
            continue
        # Walk parent_id up from the tip to the sub-branch's
        # ``source=agent_spawn`` root.
        cur: str | None = tip
        hops = 0
        sub_root = None
        seen: set[str] = set()
        while cur and cur not in seen and hops < 500:
            seen.add(cur)
            hops += 1
            n = by_id.get(cur) or {}
            if (n.get("source") == "agent_spawn"
                    and n.get("role") == "user"):
                sub_root = n
                break
            p = n.get("parent_id")
            if not p:
                break
            cur = p
        if not sub_root:
            continue
        sub_root["spawned_from"] = {
            "caller_id": caller,
            "label": (attach.get("attach_label") or "").strip() or None,
        }


async def handle_delete_session(ws, cmd: dict):
    from openprogram.webui import server as _s
    from openprogram.webui import persistence as _persist

    session_id = cmd.get("session_id")
    if not session_id:
        return
    # Capture agent_id BEFORE popping. `_delete_session_files`
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

    # Capture the full (session_id, agent_id) pairs BEFORE wiping
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
        # Prefer the captured agent_id so the DB row + on-disk
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
    """Hydrate a session: linear chain under HEAD + full DAG dump + running-task probe."""
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
        # Splice attach pointer rows (function="attach") into the
        # displayed chain. They hang off a parent message via
        # called_by — not on the conv chain itself — so
        # linear_history skips them, but the chat needs them inline
        # as standalone AttachCard rows.
        chain_ids = {m.get("id") for m in chain}
        attach_by_parent: dict[str, list[dict]] = {}
        for m in all_msgs:
            if m.get("function") != "attach":
                continue
            # Skip pointers already in the chain — older writes set
            # both parent_id and called_by, which made attach pointers
            # appear as conv children too. New writes only set
            # called_by; this guard keeps old data from doubling up.
            if m.get("id") in chain_ids:
                continue
            parent = m.get("called_by") or m.get("parent_id") or ""
            if parent and parent in chain_ids:
                attach_by_parent.setdefault(parent, []).append(m)
        if attach_by_parent:
            spliced: list[dict] = []
            for m in chain:
                spliced.append(m)
                extras = attach_by_parent.get(m.get("id"), [])
                extras.sort(key=lambda x: x.get("timestamp") or 0)
                spliced.extend(extras)
            chain = spliced
        # Splice runtime-block placeholder rows written by the
        # dispatcher's @agentic_function wrapper. They hang off the
        # assistant reply that called the tool via parent_id but are
        # not on the conv chain itself (the chain's head is the
        # assistant reply, not its runtime child). The chat needs
        # each placeholder as a standalone RuntimeBlock row right
        # after its owning assistant reply.
        chain_ids = {m.get("id") for m in chain}
        runtime_by_parent: dict[str, list[dict]] = {}
        for m in all_msgs:
            if m.get("type") != "status" or m.get("display") != "runtime":
                continue
            if m.get("id") in chain_ids:
                continue
            parent = m.get("parent_id") or ""
            if parent and parent in chain_ids:
                runtime_by_parent.setdefault(parent, []).append(m)
        if runtime_by_parent:
            spliced2: list[dict] = []
            for m in chain:
                spliced2.append(m)
                extras = runtime_by_parent.get(m.get("id"), [])
                extras.sort(key=lambda x: x.get("timestamp") or 0)
                spliced2.extend(extras)
            chain = spliced2
        conv["messages"] = chain
        conv["head_id"] = head
        from openprogram.agent.session_db import default_db as _ddb
        from openprogram.webui.ws_actions.branch import (
            _attach_info as _ainfo, _attach_embed_stats as _astats,
        )
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
            # Enrich attach pointer rows with embed stats so the
            # AttachCard can render "EMBEDS N messages · M tokens"
            # without a follow-up round trip. Cost = one O(1)
            # commit-file read per attach pointer.
            enriched = {**m,
                "sibling_index": idx,
                "sibling_total": total,
                "prev_sibling_id": prev_id,
                "next_sibling_id": next_id,
            }
            if m.get("function") == "attach":
                _ref, _man, _src = _ainfo(m)
                if _src:
                    _n, _tok = _astats(_ddb(), conv["id"], _src)
                    attach_dict = dict(m.get("attach") or {})
                    attach_dict.setdefault("source_commit_id", _src)
                    if _n is not None:
                        attach_dict["embed_count"] = _n
                    if _tok is not None:
                        attach_dict["embed_tokens"] = _tok
                    enriched["attach"] = attach_dict
            shown.append(enriched)

        tree_data = {}  # tree Context retired — execution trace lives in SessionDB DAG nodes
        try:
            full_msgs = _ddb().get_messages(conv["id"])
        except Exception:
            full_msgs = all_msgs
        from openprogram.webui.graph_layout import annotate_graph
        graph = []
        for m in full_msgs:
            content = m.get("content") or ""
            preview = content.strip().replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "…"
            from openprogram.webui.ws_actions.branch import (
                _extract_tool_input,
                _extract_function_name,
                _extract_tool_is_error,
                _extract_llm_meta,
                _extract_attach_label,
            )
            input_str = _extract_tool_input(m)
            _aref, _amanual, _asrc_commit = _ainfo(m)
            _aembed_n, _aembed_tok = _astats(_ddb(), conv["id"], _asrc_commit)
            graph.append({
                "id": m.get("id"),
                "parent_id": m.get("parent_id"),
                "caller": m.get("caller") or "",
                "role": m.get("role"),
                "function": m.get("function"),
                "display": m.get("display"),
                "source": m.get("source"),
                "preview": preview,
                "input": input_str,
                "name": _extract_function_name(m),
                "is_error": _extract_tool_is_error(m),
                "llm": _extract_llm_meta(m),
                "created_at": m.get("created_at"),
                "attach_ref": _aref,
                "attach_manual": _amanual,
                "attach_label": _extract_attach_label(m),
                "attach_source_commit_id": _asrc_commit,
                "attach_embed_count": _aembed_n,
                "attach_embed_tokens": _aembed_tok,
            })
        # Compute (depth, lane) server-side so the frontend renders
        # parallel branches correctly without re-deriving topology.
        # ``annotate_graph`` filters microcompact synthetic nodes
        # (summary_*/k_*) — reassign so the WS payload ships only
        # the DAG-visible subset.
        graph = annotate_graph(graph, head)

        # Reverse-link each spawned sub-branch's root user msg back
        # to the main-lane turn that produced it, so the frontend
        # can render a "Spawned from: <branch>" card at the top of
        # the sub branch (mirror of the AttachCard on main).
        _annotate_spawn_origin(graph)
        # Mirror the spawned_from annotation onto ``shown`` (what the
        # chat list renders), keyed by id.
        _spawn_by_id = {
            n["id"]: n["spawned_from"]
            for n in graph if n.get("spawned_from")
        }
        for m in shown:
            sf = _spawn_by_id.get(m.get("id"))
            if sf:
                m["spawned_from"] = sf
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
            # Live partial-tree dump retired with the tree-Context
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
    """List webui's in-memory sessions + per-agent sessions on disk."""
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
