"""Global running-work snapshot — GET /api/running.

Aggregates the three in-flight populations the worker already tracks:

  turn     — foreground chat turns (server._running_tasks)
  job      — background jobs across ALL sessions (JobRunner.list_jobs)
  process  — background shells started by the `process` tool

Read-only; the right-sidebar Running panel polls this.
"""
from __future__ import annotations

import time

from fastapi.responses import JSONResponse


def _collect() -> list[dict]:
    items: list[dict] = []

    from openprogram.webui import server as _s
    with _s._running_tasks_lock:
        tasks = {sid: dict(t) for sid, t in _s._running_tasks.items()}
    for sid, task in tasks.items():
        items.append({
            "kind": "turn",
            "id": task.get("execution_id") or f"{task.get('msg_id')}_reply",
            "session_id": sid,
            "label": task.get("func_name") or "chat",
            "status": "cancelling" if task.get("cancelling") else "running",
            "started_at": task.get("started_at"),
        })

    try:
        from openprogram.agent.job.runner import get_runner
        from openprogram.agent.job.types import JobStatus
        jobs = get_runner().list_jobs(None, status_filter={
            JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING,
        })
        for job in jobs:
            items.append({
                "kind": "job",
                "id": job.id,
                "session_id": job.parent_session_id,
                "label": job.label or job.subject or (job.prompt or "")[:80],
                "status": job.status.value,
                "started_at": job.started_at or job.queued_at or job.created_at,
            })
    except Exception:
        pass

    try:
        from openprogram.programs.tools.files import process as _proc
        with _proc._LOCK:
            sessions = list(_proc._SESSIONS.values())
        for sess in sessions:
            if sess.proc.poll() is not None:
                continue
            items.append({
                "kind": "process",
                "id": sess.id,
                "session_id": None,
                "label": sess.command,
                "status": "running",
                "started_at": sess.started_at,
                "pid": sess.proc.pid,
            })
    except Exception:
        pass

    items.sort(key=lambda item: item.get("started_at") or 0, reverse=True)
    return items


def register(app):
    @app.get("/api/running")
    async def api_running():
        return JSONResponse(content={
            "items": _collect(),
            "now": time.time(),
        })
