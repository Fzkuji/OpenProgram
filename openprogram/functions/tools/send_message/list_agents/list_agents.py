"""list_agents — see which agents exist to talk to.

Discovery for branch-to-branch communication: an agent's conversation
is stored as a branch in the session DAG, so "which agents can I talk
to" = "which branches exist". By default only the current session's
branches are shown (that is where spawned agents live); scope="all"
widens to every session, most recently active first.

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
    "as a branch in the session DAG; each line gives a ready-to-use "
    "`to=\"SID:HEAD\"` address for send_message, the branch name (if "
    "any — a «name» works directly as `to` too), busy/idle status, and "
    "turn count / approximate size so you can pick a sensible "
    "`max_chars` before read_conversation. Default scope=\"session\" "
    "shows only the current session's branches — the agents spawned "
    "here. Pass scope=\"all\" to find agents in OTHER sessions: every "
    "session is listed, most recently active first, without previews "
    "(limit defaults to 20). To CREATE a new agent, use the `agent` "
    "tool instead."
)


def _branch_stats(db, sid: str, head: str) -> str:
    """`— N turns, ~Xk chars` for one branch; empty on any failure."""
    try:
        chain = db.get_branch(sid, head) or []
        if not chain:
            return ""
        chars = sum(len(str(m.get("content") or "")) for m in chain)
        size = f"~{chars // 1000}k chars" if chars >= 1000 else "<1k chars"
        return f" — {len(chain)} turns, {size}"
    except Exception:
        return ""


def _turn_running(session_id: str) -> bool | None:
    """Busy probe; None = unknown (status omitted from the line)."""
    try:
        from openprogram.agent.run_control import is_turn_running
        return bool(is_turn_running(session_id))
    except Exception:
        return None


def _session_lines(db, r: dict, cur: str, *, preview: bool) -> tuple[list[str], int]:
    """Render one session header + its branch lines; returns (lines, n_branches)."""
    sid = r.get("id", "?")
    mark = "  ← current session" if sid == cur else ""
    title = r.get("title") or "(untitled)"
    sess_agent = r.get("agent_id") or "?"
    busy = _turn_running(sid)
    status = "" if busy is None else (" [busy]" if busy else " [idle]")
    lines = [f"{sid}  [{sess_agent}]  {title}{status}{mark}"]
    try:
        branches = db.list_branches(sid) or []
    except Exception:
        branches = []
    for b in branches:
        head = b.get("head_msg_id", "?")
        name = b.get("name")
        label = f" «{name}»" if name else ""
        stats = _branch_stats(db, sid, head)
        line = f"  - to={sid}:{head}{label}{stats}"
        if preview:
            tip = _last_text(sid, head_id=head)
            if tip:
                line += f"\n      “{tip}”"
        lines.append(line)
    if not branches:
        lines.append("  (no branches)")
    return lines, len(branches)


def _list_agents_impl(
    scope: str = "session",
    limit: int = 20,
    agent_id: str = "",
    source: str = "",
) -> str:
    from openprogram.events import emit_safe
    db = _db()
    cur = _current_session()

    if scope != "all":
        if not cur:
            return ('[list_agents error] no active session context — '
                    'use scope="all" to list every session')
        try:
            rows = [r for r in db.list_sessions(limit=10_000)
                    if r.get("id") == cur]
        except Exception as e:  # noqa: BLE001
            return f"[list_agents error] {type(e).__name__}: {e}"
        if not rows:
            return "(current session not found)"
        lines, total_branches = _session_lines(db, rows[0], cur, preview=True)
        emit_safe(
            "agents.listed", "agent",
            {"sessions": 1, "branches": total_branches},
        )
        header = (
            f"{total_branches} branch(es) in this session — pass a `to` "
            "below to send_message (a «name» works directly as `to` too); "
            'scope="all" lists other sessions:'
        )
        return "\n".join([header, *lines])

    # scope="all" — list_sessions returns most recently active first.
    try:
        rows = db.list_sessions(
            limit=max(1, int(limit)),
            agent_id=(agent_id or None),
            source=(source or None),
        )
    except Exception as e:  # noqa: BLE001
        return f"[list_agents error] {type(e).__name__}: {e}"
    if not rows:
        return "(no sessions)"

    lines: list[str] = []
    total_branches = 0
    for r in rows:
        s_lines, n = _session_lines(db, r, cur, preview=False)
        lines.extend(s_lines)
        total_branches += n

    emit_safe(
        "agents.listed", "agent",
        {"sessions": len(rows), "branches": total_branches},
    )
    header = (
        f"{len(rows)} session(s), {total_branches} branch(es), most "
        "recently active first — pass a `to` below to send_message "
        "(a «name» works directly as `to` too):"
    )
    return "\n".join([header, *lines])


@function(name="list_agents", description=_DESCRIPTION, toolset=["core"])
def list_agents(
    scope: str = "session",
    limit: int = 20,
    agent_id: str = "",
    source: str = "",
) -> str:
    """List talkable agents: this session's branches, or all sessions."""
    return _list_agents_impl(scope=scope, limit=limit,
                             agent_id=agent_id, source=source)
