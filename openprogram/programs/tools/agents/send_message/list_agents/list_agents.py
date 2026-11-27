"""list_agents — see which agents exist to talk to.

Discovery for branch-to-branch communication: an agent's conversation
is stored as a branch in the session DAG, so "which agents can I talk
to" = "which branches exist". By default only the current session's
branches are shown (that is where spawned agents live); scope="all"
widens to every session, most recently active first.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html (C2).
"""
from __future__ import annotations

from openprogram.programs._host.runtime import function
from openprogram.programs.tools.agents.send_message.shared import (
    _current_session,
    _db,
    _last_text,
)


_DESCRIPTION = (
    "List the agents you can talk to. An agent's conversation is stored "
    "as a branch in the session DAG; each line gives a ready-to-use "
    "`to=\"SID:HEAD\"` address for send_message, the branch name (if "
    "any — a «name» works directly as `to` too), the scheduled task the "
    "branch works on (if any), busy/idle status, and "
    "turn count / approximate size so you can pick a sensible "
    "`max_chars` before read_conversation. Default scope=\"session\" "
    "shows only the current session's branches — the agents spawned "
    "here. Pass scope=\"all\" to find agents in OTHER sessions: every "
    "session is listed, most recently active first, without previews "
    "(limit defaults to 20). The session view also lists running and queued "
    "work with its execution_id. Archived agents (close_agent / "
    "agent(archive_when_done=true)) are hidden from both; "
    "scope=\"archived\" lists exactly them — still readable with "
    "read_conversation, no longer addressable. To CREATE a new agent, "
    "use the `agent` tool instead."
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
        from openprogram.runtime.conversation.run_control import is_turn_running
        return bool(is_turn_running(session_id))
    except Exception:
        return None


def _branch_task(db, sid: str, head: str) -> str:
    """The scheduled task this branch is bound to, in words; empty if none."""
    try:
        task_id = (db.get_branch_meta(sid, head) or {}).get("scheduled_task_id")
        if not task_id:
            return ""
        from openprogram.programs.tools.jobs.cron.remind import next_cron
        from openprogram.runtime.scheduler import service
        import datetime as dt

        task = service.get_task(str(task_id))
        if task is None:
            return "deleted"
        parts = [str(task.get("title") or task_id), str(task.get("cron") or task.get("run_at") or task.get("type") or "")]
        if not task.get("enabled", True):
            parts.append("paused")
        elif task.get("cron"):
            upcoming = next_cron(str(task["cron"]), dt.datetime.now())
            if upcoming:
                parts.append(f"next {upcoming:%Y-%m-%d %H:%M}")
        return ", ".join(part for part in parts if part)
    except Exception:
        return ""


_WORK_ROWS = 20


def _work_lines(session_id: str) -> list[str]:
    """The agent work this session started that has not ended (what
    list_jobs showed before the five-tool set), newest first."""
    try:
        from openprogram.runtime.jobs.store import list_jobs
        from openprogram.runtime.jobs.types import is_terminal
        jobs = [j for j in list_jobs(session_id, limit=200, lock_timeout=2.0)
                if not is_terminal(j.status)][:_WORK_ROWS]
    except Exception:
        return []
    if not jobs:
        return []
    out = ["", "Running or queued work (close_agent stops one; read_conversation reads it):"]
    for job in jobs:
        # An agent's name is shown as it is addressed, spaces as written
        # (agent_name keeps it on one line); a prompt is folded to one.
        subject = (job.label or " ".join((job.subject or job.prompt or "").split()))[:70]
        out.append(f"  - execution_id={job.id} [{job.status.value}] {subject}")
    return out


def _session_lines(
    db, r: dict, cur: str, *, preview: bool, archived: bool = False,
) -> tuple[list[str], int]:
    """Render one session header + its branch lines; returns (lines,
    n_branches). ``archived`` flips the view: False (default) lists the
    live branch tips, True lists the archived branches — read off the
    archive flag rather than filtered out of the live tips, so a branch
    a merge absorbed is still listed once it is archived."""
    sid = r.get("id", "?")
    mark = "  ← current session" if sid == cur else ""
    title = r.get("title") or "(untitled)"
    sess_agent = r.get("agent_id") or "?"
    busy = _turn_running(sid)
    status = "" if busy is None else (" [busy]" if busy else " [idle]")
    lines = [f"{sid}  [{sess_agent}]  {title}{status}{mark}"]
    try:
        if archived:
            branches = db.list_archived_branches(sid) or []
        else:
            branches = [b for b in db.list_branches(sid) or []
                        if not b.get("archived")]
    except Exception:
        branches = []
    # Spawned agents go by the name they were spawned with, even once
    # their branch is renamed or, finished, retired from the tip list.
    agents: dict[str, str] = {}
    if not archived:
        from openprogram.runtime.conversation.peer_agents.addressing import (
            agent_tips,
        )
        for agent_label, tip in agent_tips(sid):
            agents.setdefault(tip, agent_label)
        listed = {b.get("head_msg_id") for b in branches}
        branches = branches + [
            {"head_msg_id": tip, "name": (db.get_branch_meta(sid, tip) or {}).get("name")}
            for tip in agents if tip not in listed
        ]
    for b in branches:
        head = b.get("head_msg_id", "?")
        name = b.get("name")
        label = f" «{name}»" if name else ""
        if head in agents and agents[head] != name:
            label += f" (agent «{agents[head]}»)"
        task = _branch_task(db, sid, head)
        if task:
            label += f" [scheduled task: {task}]"
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
    from openprogram.runtime.events import emit_safe
    if scope not in ("session", "all", "archived"):
        return (
            f'[list_agents error] unknown scope {scope!r} — use "session" '
            '(this session\'s branches, the default), "all" (every '
            'session), or "archived" (this session\'s retired branches).'
        )
    db = _db()
    cur = _current_session()

    if scope != "all":
        # "session" (default) lists the live branches; "archived" lists
        # exactly the retired ones, same per-branch format.
        archived_scope = scope == "archived"
        if not cur:
            return ('[list_agents error] no active session context — '
                    'use scope="all" to list every session')
        try:
            rows = [r for r in db.list_sessions(limit=10_000, include_archived=True)
                    if r.get("id") == cur]
        except Exception as e:  # noqa: BLE001
            return f"[list_agents error] {type(e).__name__}: {e}"
        if not rows:
            return "(current session not found)"
        lines, total_branches = _session_lines(
            db, rows[0], cur, preview=True, archived=archived_scope)
        emit_safe(
            "agents.listed", "agent",
            {"sessions": 1, "branches": total_branches},
        )
        if archived_scope:
            header = (
                f"{total_branches} archived branch(es) in this session — "
                "no longer addressable (send_message / agent(to=) refuse "
                "them); read_conversation still reads them, "
                'agent(start_from="SID:MSG_ID") still forks them:'
            )
        else:
            header = (
                f"{total_branches} branch(es) in this session — pass a `to` "
                "below to send_message (a «name» works directly as `to` too); "
                'scope="all" lists other sessions:'
            )
            lines += _work_lines(cur)
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
    """List talkable agents: this session's branches ("session",
    default), every session ("all"), or the retired ones
    ("archived")."""
    return _list_agents_impl(scope=scope, limit=limit,
                             agent_id=agent_id, source=source)
