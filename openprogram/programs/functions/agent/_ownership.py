"""Job ownership check shared by job_output / job_stop.

read_conversation can read any branch, so any agent can learn any
job_id — without this check any agent could wait on or cancel work it
never dispatched. A job may be managed by:

  * the session that dispatched it (``caller_session_id``, or
    ``parent_session_id`` for same-session spawns), or
  * an ancestor on the job chain — the current job is an ancestor of
    the target via ``parent_job_id``, or the current session dispatched
    one of the target's ancestors (the chain the cascading cancel walks).

No session context at all (a user / UI call, not an agent turn) is not
gated — the human owns everything.
"""
from __future__ import annotations

from typing import Optional


def check_job_ownership(job_id: str, tool: str) -> Optional[str]:
    """Return an error string when the current session may not manage
    ``job_id``; None when allowed (including unknown jobs — the tool
    reports those itself)."""
    try:
        from openprogram.agent.run_control import _current_session_id
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    if not sid:
        return None  # user / UI call — no agent identity to gate on
    from openprogram.agent.job import get_runner
    runner = get_runner()
    job = runner.get_job(job_id)
    if job is None:
        return None
    try:
        from openprogram.agent.job.runner import _current_job_id
        cur_tid = _current_job_id.get()
    except Exception:
        cur_tid = None

    node = job
    seen: set[str] = set()
    for _ in range(64):
        if sid in (node.parent_session_id, node.caller_session_id):
            return None
        pid = node.parent_job_id
        if not pid or pid in seen:
            break
        if pid == cur_tid:
            return None
        seen.add(pid)
        parent = runner.get_job(pid)
        if parent is None:
            break
        node = parent
    return f"[{tool} error] job {job_id} was not dispatched by this session"


__all__ = ["check_job_ownership"]
