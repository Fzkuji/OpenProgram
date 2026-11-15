"""Search within the shown conversation and across past sessions."""
from __future__ import annotations

import json

from .load import _session_io


async def handle_search_session(ws, cmd: dict) -> None:
    """Find text in the conversation this connection is showing.

    Each match's ``id`` is a row of the branch, which a page read accepts as
    ``history_around``.
    """
    from openprogram.interfaces.server.transcript import reader
    from openprogram.interfaces.server.transcript.turn_index import get_view
    from openprogram.interfaces.server.ws_errors import OperationError
    from openprogram.runtime.context.session_db import default_db
    session_id = cmd.get("session_id")
    query = cmd.get("query")
    if not isinstance(query, str) or len(query) > _SEARCH_QUERY_MAX:
        raise OperationError("invalid_request", scope="session")
    db = default_db()
    if not await _session_io(db.get_session, session_id):
        raise OperationError("invalid_request", scope="session")
    view = await _session_io(get_view, db, session_id)
    matches = await _session_io(reader.search, db, view, query.strip())
    await ws.send_text(json.dumps({"type": "session_search_results", "data": {
        "id": session_id, "query": query, "matches": matches, "total": sum(m["count"] for m in matches),
        "request_id": cmd.get("request_id"), "action": "search_session",
    }}, ensure_ascii=False, default=str))


_SEARCH_QUERY_MAX = 500


async def handle_search_messages(ws, cmd: dict):
    """FTS-backed search across past sessions."""
    from openprogram.interfaces.server import server as _s
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
        from openprogram.runtime.context.session_db import default_db
        hits = await _session_io(
            default_db().search_messages,
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
