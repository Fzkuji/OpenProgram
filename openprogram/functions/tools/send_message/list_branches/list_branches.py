"""list_branches — see which branches a session has to talk to.

The per-session half of discovery for branch-to-branch communication:
each line gives a ``SID:HEAD`` (and name, if any) that doubles as a
ready-to-use ``to`` value for ``send_message``.

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
    "List the branches of a session (defaults to the current session). "
    "Each line gives a `SID:HEAD` you can pass directly as send_message's "
    "`to` to message that branch, plus its name (if any) and a preview "
    "of its tip. A named branch can also be addressed by name alone: "
    "send_message(to=\"<name>\"). Use this to find the exact branch to "
    "talk to before calling send_message."
)


def _list_branches_impl(session_id: str = "") -> str:
    from openprogram.events import emit_safe
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

    lines = [
        f"{len(branches)} branch(es) in {sid} — pass a `to` below to "
        "send_message (a «name» works directly as `to` too):"
    ]
    for b in branches:
        head = b.get("head_msg_id", "?")
        name = b.get("name")
        preview = _last_text(sid, head_id=head)
        label = f" «{name}»" if name else ""
        lines.append(
            f"- to={sid}:{head}{label}"
            + (f"\n    “{preview}”" if preview else "")
        )
    return "\n".join(lines)


@function(name="list_branches", description=_DESCRIPTION, toolset=["core"])
def list_branches(session_id: str = "") -> str:
    """List a session's branches as ready-to-use `SID:HEAD` targets."""
    return _list_branches_impl(session_id=session_id)
