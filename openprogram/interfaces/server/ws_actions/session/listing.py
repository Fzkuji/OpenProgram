"""The sidebar session list: build, broadcast and reply."""
from __future__ import annotations

import json


def _project_name_map() -> tuple[dict[str, str], str]:
    """``(session_id → project name, default/home name)``.

    Lets the sidebar group conversations by project with the home
    folder as the catch-all — every conversation gets a project name, so
    "group by project" never produces an "Ungrouped" bucket. Reads the
    registry once (not per-conversation).
    """
    try:
        from openprogram.state.store.project import project_store as _ps
        default_name = _ps.get_default_project().name or "Home"
        m: dict[str, str] = {}
        for p in _ps.list_projects():
            if p.is_default:
                continue
            for sid in (p.session_ids or []):
                m[sid] = p.name
        return m, default_name
    except Exception:
        return {}, ""


def build_sessions_list() -> list[dict]:
    """Build the sidebar session list payload from the registry.

    Shared by ``handle_list_sessions`` (per-client reply) and the
    fn-form REST path (broadcast on new-session creation), so both
    produce identical rows.
    """
    from openprogram.runtime.context.session_db import default_db
    _proj_map, _default_proj = _project_name_map()

    rows = default_db().list_sessions(limit=10_000, include_archived=True)
    conv_list: list[dict] = []
    for row in rows:
        sid = row.get("id", "")
        title = (row.get("title") or "").strip()
        preview = (row.get("preview") or "").strip()
        if title in ("", "New conversation", "Untitled") and preview:
            title = preview
        if not title and not preview:
            continue
        conv_list.append({
            "id": sid,
            "title": title or sid,
            "created_at": row.get("created_at") or 0,
            "updated_at": row.get("updated_at") or row.get("created_at") or 0,
            "agent_id": row.get("agent_id"),
            "source": row.get("source"),
            "peer_display": row.get("peer_display"),
            "channel": row.get("channel"),
            "account_id": row.get("account_id"),
            "environment_id": row.get("environment_id"),
            "peer": row.get("peer_id"),
            "preview": preview or None,
            "pinned": bool(row.get("pinned")),
            "archived": bool(row.get("archived")),
            "group": row.get("group") or "",
            "status": row.get("status") or "",
            "unread": bool(row.get("unread")),
            "sort_at": row.get("sort_at") or 0,
            "sort_since": row.get("sort_since") or 0,
            "project": _proj_map.get(sid, _default_proj),
        })
    conv_list.sort(key=lambda c: c.get("updated_at") or 0, reverse=True)
    return conv_list


def broadcast_sessions_list() -> None:
    """Broadcast the current session list to every connected client."""
    from openprogram.interfaces.server import server as _s
    _s._broadcast(json.dumps({
        "type": "sessions_list", "data": build_sessions_list(),
    }, default=str))


async def handle_list_sessions(ws, cmd: dict):
    """List sessions from registry (pure memory, no disk I/O)."""
    await ws.send_text(json.dumps({
        "type": "sessions_list", "data": build_sessions_list(),
    }, default=str))
