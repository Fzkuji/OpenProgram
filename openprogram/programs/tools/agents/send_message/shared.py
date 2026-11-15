"""Shared helpers for the send_message tool family.

Session/branch enumeration used by the discovery tool
(``list_agents``).
"""
from __future__ import annotations


def _db():
    from openprogram.runtime.context.session_db import default_db
    return default_db()


def _current_session() -> str | None:
    try:
        from openprogram.runtime.execution.run_control.binding import (
            _current_session_id,
        )
        return _current_session_id.get(None)
    except Exception:
        return None


def _last_text(session_id: str, *, head_id: str | None = None) -> str:
    """Short preview = the latest message's text on a session/branch.

    With ``head_id`` the preview comes from that branch's own chain only:
    a branch whose tip has no text yet (a helper stopped mid-turn) shows
    its latest text before the tip, never another branch's message."""
    try:
        if head_id:
            msgs = _db().get_branch(session_id, head_id) or []
        else:
            msgs = _db().get_messages(session_id, limit=8) or []
    except Exception:
        return ""
    for m in reversed(msgs):
        c = m.get("content")
        if c:
            return _clip(c)
    return ""


def _clip(text, n: int = 70) -> str:
    s = str(text or "").replace("\n", " ").strip()
    return (s[: n - 1] + "…") if len(s) > n else s
