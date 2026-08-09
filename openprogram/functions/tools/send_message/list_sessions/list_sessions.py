"""list_sessions — see which sessions exist to talk to.

The cross-session half of discovery for branch-to-branch
communication: before you can ``send_message(to="SID:HEAD")`` you need
to know which sessions exist and how to address them.

Design: docs/design/runtime/agent-collaboration.md (C2).
"""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.functions.tools.send_message.shared import (
    _current_session,
    _db,
    _last_text,
)


_DESCRIPTION = (
    "List the sessions in the system so you can see other agents/work and "
    "address them. Each line shows the session id, title, agent, last "
    "activity, and a one-line preview of its latest message. The current "
    "session is marked. Use a session id (or the id:head from "
    "list_branches) as the `to` of send_message to talk to it."
)


def _list_sessions_impl(limit: int = 50, agent_id: str = "", source: str = "") -> str:
    from openprogram.events import emit_safe
    db = _db()
    try:
        rows = db.list_sessions(
            limit=max(1, int(limit)),
            agent_id=(agent_id or None),
            source=(source or None),
        )
    except Exception as e:  # noqa: BLE001
        return f"[list_sessions error] {type(e).__name__}: {e}"

    cur = _current_session()
    emit_safe("sessions.listed", "agent", {"count": len(rows)})
    if not rows:
        return "(no sessions)"

    lines = [f"{len(rows)} session(s):"]
    for r in rows:
        sid = r.get("id", "?")
        mark = "  ← current" if sid == cur else ""
        title = r.get("title") or "(untitled)"
        agent = r.get("agent_id") or "?"
        preview = _last_text(sid)
        lines.append(
            f"- {sid}  [{agent}]  {title}{mark}"
            + (f"\n    “{preview}”" if preview else "")
        )
    return "\n".join(lines)


@function(name="list_sessions", description=_DESCRIPTION, toolset=["core"])
def list_sessions(limit: int = 50, agent_id: str = "", source: str = "") -> str:
    """List sessions (id, title, agent, last activity, preview)."""
    return _list_sessions_impl(limit=limit, agent_id=agent_id, source=source)
