"""list_sessions / list_branches — see what's out there to talk to.

The discovery side of branch-to-branch communication: before you can
``message_branch(target="SID:HEAD")`` you need to know which sessions and
branches exist and how to address them. Both tools return human-readable
listings whose lines double as ready-to-use ``target`` values.

Design: docs/design/runtime/agent-collaboration.md (C2).
"""
from __future__ import annotations

from openprogram.functions._runtime import function


def _db():
    from openprogram.agent.session_db import default_db
    return default_db()


def _current_session() -> str | None:
    try:
        from openprogram.webui._pause_stop import _current_session_id
        return _current_session_id.get(None)
    except Exception:
        return None


def _last_text(session_id: str, *, head_id: str | None = None) -> str:
    """Short preview = the latest message's text on a session/branch."""
    try:
        msgs = _db().get_messages(session_id, limit=8) or []
    except Exception:
        return ""
    # If a head is given, prefer that node's own content.
    if head_id:
        for m in msgs:
            if m.get("id") == head_id:
                return _clip(m.get("content"))
    for m in reversed(msgs):
        c = m.get("content")
        if c:
            return _clip(c)
    return ""


def _clip(text, n: int = 70) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return (s[: n - 1] + "…") if len(s) > n else s


_LIST_SESSIONS_DESC = (
    "List the sessions in the system so you can see other agents/work and "
    "address them. Each line shows the session id, title, agent, last "
    "activity, and a one-line preview of its latest message. The current "
    "session is marked. Use a session id (or the id:head from "
    "list_branches) as the `target` of message_branch to talk to it."
)


def _list_sessions_impl(limit: int = 50, agent_id: str = "", source: str = "") -> str:
    from openprogram.agent.event_bus import emit_safe
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


@function(name="list_sessions", description=_LIST_SESSIONS_DESC, toolset=["core"])
def list_sessions(limit: int = 50, agent_id: str = "", source: str = "") -> str:
    """List sessions (id, title, agent, last activity, preview)."""
    return _list_sessions_impl(limit=limit, agent_id=agent_id, source=source)


_LIST_BRANCHES_DESC = (
    "List the branches of a session (defaults to the current session). "
    "Each line gives a `SID:HEAD` you can pass directly as message_branch's "
    "`target` to message that branch, plus its name (if any) and a preview "
    "of its tip. Use this to find the exact branch to talk to before "
    "calling message_branch."
)


def _list_branches_impl(session_id: str = "") -> str:
    from openprogram.agent.event_bus import emit_safe
    sid = (session_id or "").strip() or _current_session()
    if not sid:
        return (
            "[list_branches error] no session_id given and no current "
            "session — pass a session_id (see list_sessions)."
        )
    db = _db()
    try:
        branches = db.list_branches(sid)
    except Exception as e:  # noqa: BLE001
        return f"[list_branches error] {type(e).__name__}: {e}"

    emit_safe("branches.listed", "agent", {"session": sid, "count": len(branches)})
    if not branches:
        return f"(session {sid} has no branches)"

    lines = [f"{len(branches)} branch(es) in {sid} — pass a `target` below to message_branch:"]
    for b in branches:
        head = b.get("head_msg_id", "?")
        name = b.get("name")
        preview = _last_text(sid, head_id=head)
        label = f" «{name}»" if name else ""
        lines.append(
            f"- target={sid}:{head}{label}"
            + (f"\n    “{preview}”" if preview else "")
        )
    return "\n".join(lines)


@function(name="list_branches", description=_LIST_BRANCHES_DESC, toolset=["core"])
def list_branches(session_id: str = "") -> str:
    """List a session's branches as ready-to-use `SID:HEAD` targets."""
    return _list_branches_impl(session_id=session_id)
