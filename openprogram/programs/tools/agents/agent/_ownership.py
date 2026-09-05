"""Ownership checks for background execution resource tools."""
from __future__ import annotations

from typing import Optional


def check_job_ownership(job_id: str, tool: str) -> Optional[str]:
    """Return an error when the current agent may not manage ``job_id``."""
    try:
        from openprogram.agent.run_control import _current_session_id
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    if not sid:
        return None
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
