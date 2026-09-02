"""Global running-work snapshot — GET /api/running.

Aggregates the in-flight populations the worker already tracks:

  run      — active chat turns / background executions per session
             (server._running_tasks; execution_id distinguishes branches)
  tool     — tool calls executing right now (bash, execute_code, …)
  job      — background jobs across ALL sessions (JobRunner.list_jobs)
  process  — background shells started by the `process` tool

Read-only; the right-sidebar Running panel polls this.
"""
from __future__ import annotations

import time

from fastapi.responses import JSONResponse


def _collect() -> list[dict]:
    items: list[dict] = []

    try:
        from openprogram.execution.projections import list_running_execution_projections

        for projection in list_running_execution_projections():
            payload = projection.payload
            execution = payload.get("execution") or {}
            snapshot = payload.get("snapshot") or execution
            ui = payload.get("ui") or {}
            item = {
                "kind": "execution",
                "id": projection.execution_id,
                "execution_id": projection.execution_id,
                "session_id": projection.session_id,
                "label": ui.get("label") or "execution",
                "status": projection.status,
                "started_at": snapshot.get("created_at"),
            }
            if payload.get("snapshot"):
                item.update({
                    "snapshot": snapshot,
                    "capabilities": snapshot.get("capabilities") or {},
                    "resource": snapshot.get("resource"),
                    "event_cursor": payload.get("event_cursor") or {
                        "execution_id": projection.execution_id,
                        "next_sequence": projection.event_sequence + 1,
                        "snapshot_status_version": snapshot.get("status_version"),
                    },
                })
            items.append(item)
    except Exception:
        pass

    try:
        # 进行中的 chat 轮次（含每个会话分支的后台 execution）。这是
        # "正在运行的程序" 的顶层视角；tool/process 是它的细粒度补充。
        from openprogram.webui import server as _srv
        with _srv._running_tasks_lock:
            tasks = {sid: dict(t) for sid, t in _srv._running_tasks.items()}
        for sid, task in tasks.items():
            execution_id = task.get("execution_id")
            if execution_id and any(i.get("execution_id") == execution_id for i in items):
                continue
            items.append({
                "kind": "run",
                "id": task.get("execution_id") or "",
                "session_id": sid,
                "execution_id": task.get("execution_id"),
                "label": task.get("func_name") or "chat",
                "status": "running",
                "started_at": task.get("started_at"),
            })
    except Exception:
        pass

    try:
        # openprogram.agent re-exports the agent_loop *function* under the
        # same name, shadowing the module — import the module explicitly.
        import importlib
        _loop = importlib.import_module("openprogram.agent.agent_loop")
        with _loop.RUNNING_TOOL_CALLS_LOCK:
            calls = {cid: dict(c) for cid, c in _loop.RUNNING_TOOL_CALLS.items()}
        for cid, call in calls.items():
            items.append({
                "kind": "tool",
                "id": cid,
                "session_id": call.get("session_id"),
                "execution_id": call.get("execution_id"),
                "label": call.get("label") or call.get("tool_name") or "tool",
                "tool_name": call.get("tool_name"),
                "status": "running",
                "started_at": call.get("started_at"),
            })
    except Exception:
        pass

    try:
        from openprogram.agent.job.runner import get_runner
        from openprogram.agent.job.types import JobStatus
        jobs = get_runner().list_jobs(None, status_filter={
            JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING,
        })
        for job in jobs:
            if any(i.get("execution_id") == job.id for i in items):
                continue
            canonical = None
            try:
                from openprogram.execution import default_store
                from openprogram.execution.public import execution_snapshot

                execution = default_store().get_execution(job.id)
                if execution is not None:
                    view = get_runner().get_job_resource_view(job.id)
                    canonical = execution_snapshot(
                        execution,
                        store=default_store(),
                        resource=view.to_dict() if view is not None else None,
                        job_id=job.id,
                        job=job,
                    ).to_dict()
            except Exception:
                canonical = None
            if canonical is not None:
                items.append({
                    "kind": "execution",
                    "id": job.id,
                    "execution_id": job.id,
                    "session_id": job.parent_session_id,
                    "label": job.label or job.subject or (job.prompt or "")[:80],
                    "status": canonical.get("status", job.status.value),
                    "started_at": job.started_at or job.queued_at or job.created_at,
                    "snapshot": canonical,
                    "capabilities": canonical.get("capabilities") or {},
                    "resource": canonical.get("resource"),
                    "event_cursor": {
                        "execution_id": job.id,
                        "next_sequence": int(canonical.get("event_sequence") or 0) + 1,
                        "snapshot_status_version": canonical.get("status_version"),
                    },
                })
                continue
            items.append({
                "kind": "execution",
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
