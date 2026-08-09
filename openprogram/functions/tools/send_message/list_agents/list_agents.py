"""list_agents — see which agents exist to talk to.

Discovery for branch-to-branch communication: an agent's conversation
is stored as a branch in the session DAG, so "which agents can I talk
to" = "which sessions exist, and which branches does each one have".
One call lists them all, grouped by session; every line gives a
``to="SID:HEAD"`` address ready for send_message.

Design: docs/reference/design/runtime/agent-collaboration.md (C2).
"""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.functions.tools.send_message.shared import (
    _current_session,
    _db,
    _last_text,
)


_DESCRIPTION = (
    "List the agents you can talk to. An agent's conversation is stored "
    "as a branch in the session DAG, so the output is grouped by "
    "session, one line per branch: its name (if any), a ready-to-use "
    "`to=\"SID:HEAD\"` address for send_message, its busy/idle status, "
    "and a preview of its tip. A named branch can also be addressed by "
    "name alone: send_message(to=\"<name>\"). Use this before "
    "send_message to find the exact agent to talk to; to CREATE a new "
    "agent, use the `agent` tool instead."
)


def _turn_running(session_id: str) -> bool | None:
    """Busy probe; None = unknown (status omitted from the line)."""
    try:
        from openprogram.agent.run_control import is_turn_running
        return bool(is_turn_running(session_id))
    except Exception:
        return None


def _list_agents_impl(limit: int = 50, agent_id: str = "", source: str = "") -> str:
    from openprogram.events import emit_safe
    db = _db()
    try:
        rows = db.list_sessions(
            limit=max(1, int(limit)),
            agent_id=(agent_id or None),
            source=(source or None),
        )
    except Exception as e:  # noqa: BLE001
        return f"[list_agents error] {type(e).__name__}: {e}"

    cur = _current_session()
    if not rows:
        return "(no sessions)"

    lines: list[str] = []
    total_branches = 0
    for r in rows:
        sid = r.get("id", "?")
        mark = "  ← current session" if sid == cur else ""
        title = r.get("title") or "(untitled)"
        sess_agent = r.get("agent_id") or "?"
        busy = _turn_running(sid)
        status = "" if busy is None else (" [busy]" if busy else " [idle]")
        lines.append(f"{sid}  [{sess_agent}]  {title}{status}{mark}")
        try:
            branches = db.list_branches(sid) or []
        except Exception:
            branches = []
        total_branches += len(branches)
        for b in branches:
            head = b.get("head_msg_id", "?")
            name = b.get("name")
            preview = _last_text(sid, head_id=head)
            label = f" «{name}»" if name else ""
            lines.append(
                f"  - to={sid}:{head}{label}"
                + (f"\n      “{preview}”" if preview else "")
            )
        if not branches:
            lines.append("  (no branches)")

    emit_safe(
        "agents.listed", "agent",
        {"sessions": len(rows), "branches": total_branches},
    )
    header = (
        f"{len(rows)} session(s), {total_branches} branch(es) — pass a "
        "`to` below to send_message (a «name» works directly as `to` too):"
    )
    return "\n".join([header, *lines])


@function(name="list_agents", description=_DESCRIPTION, toolset=["core"])
def list_agents(limit: int = 50, agent_id: str = "", source: str = "") -> str:
    """List talkable agents: sessions and their branches as `to` targets."""
    return _list_agents_impl(limit=limit, agent_id=agent_id, source=source)
