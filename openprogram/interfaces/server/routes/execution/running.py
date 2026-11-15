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

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse

_log = logging.getLogger(__name__)


def _canonical_snapshot(execution_id: str) -> dict | None:
    """Read the complete public snapshot for one running item."""
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.public import execution_snapshot

    store = default_store()
    execution = store.get_execution(execution_id)
    if execution is None:
        return None
    resource = None
    job = None
    try:
        from openprogram.runtime.jobs.runner import runner_for_execution_store

        runner = runner_for_execution_store(store)
        if runner is not None:
            view = runner.get_job_resource_view(execution_id)
            resource = view.resource if view is not None else None
            job = runner.get_job(execution_id)
    except Exception:
        # The job runner is optional; the snapshot omits resource/job without
        # it.
        _log.debug("job view unavailable for execution %s", execution_id, exc_info=True)
    return execution_snapshot(
        execution,
        store=store,
        resource=resource,
        job_id=getattr(job, "id", None),
        job=job,
    ).to_dict()


def _collect() -> list[dict]:
    items: list[dict] = []

    try:
        from openprogram.runtime.execution.event_effects import list_running_executions

        for projection in list_running_executions():
            payload = projection.payload
            execution = payload.get("execution") or {}
            snapshot = payload.get("snapshot") or _canonical_snapshot(
                projection.execution_id,
            )
            ui = payload.get("ui") or {}
            item = {
                "kind": "execution",
                "id": projection.execution_id,
                "execution_id": projection.execution_id,
                "session_id": projection.session_id,
                "label": ui.get("label") or "execution",
                "status": projection.status,
                "started_at": execution.get("created_at"),
            }
            if snapshot is not None:
                item.update({
                    "snapshot": snapshot,
                    "capabilities": snapshot.get("capabilities") or {},
                    "resource": snapshot.get("resource"),
                    "event_cursor": payload["event_cursor"],
                })
            items.append(item)
    except Exception:
        # Each source of running items is listed independently; one failing
        # must not empty the list.
        _log.debug("canonical executions not listed", exc_info=True)

    try:
        # 进行中的 chat 轮次（含每个会话分支的后台 execution）。这是
        # "正在运行的程序" 的顶层视角；tool/process 是它的细粒度补充。
        from openprogram.interfaces.server import server as _srv
        with _srv._running_tasks_lock:
            tasks = [(sid, None, dict(t)) for sid, t in _srv._running_tasks.items()]
            # A turn on a branch that is not selected holds its own slot.
            tasks += [(sid, branch_id, dict(t))
                      for (sid, branch_id), t in _srv._branch_running_tasks.items()]
        for sid, branch_id, task in tasks:
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
                **({"branch_id": branch_id} if branch_id else {}),
            })
    except Exception:
        # Each source of running items is listed independently; one failing
        # must not empty the list.
        _log.debug("running chat turns not listed", exc_info=True)

    try:
        # Import the loop package by name: it holds the table of running tool calls.
        import importlib
        _loop = importlib.import_module("openprogram.runtime.agent.loop")
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
        # Each source of running items is listed independently; one failing
        # must not empty the list.
        _log.debug("running tool calls not listed", exc_info=True)

    try:
        from openprogram.runtime.jobs.runner import get_runner
        from openprogram.runtime.jobs.types import JobStatus
        jobs = get_runner().list_jobs(None, status_filter={
            JobStatus.PENDING, JobStatus.QUEUED, JobStatus.RUNNING,
        })
        for job in jobs:
            if any(i.get("execution_id") == job.id for i in items):
                continue
            canonical = None
            try:
                from openprogram.runtime.execution import default_store
                from openprogram.runtime.execution.public import execution_snapshot

                execution = default_store().get_execution(job.id)
                if execution is not None:
                    view = get_runner().get_job_resource_view(job.id)
                    canonical = execution_snapshot(
                        execution,
                        store=default_store(),
                        resource=view.resource if view is not None else None,
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
        # Each source of running items is listed independently; one failing
        # must not empty the list.
        _log.debug("background jobs not listed", exc_info=True)

    from openprogram.host.processes import ProcessStore
    from openprogram.host.processes.store import ACTIVE, public_record
    for record in ProcessStore().list():
        if record["status"] in ACTIVE:
            items.append({**public_record(record), "kind": "process", "label": record["command"]})

    items.sort(key=lambda item: item.get("started_at") or 0, reverse=True)
    return items


def register(app):
    from . import processes
    from openprogram.host.resources.terminal import register_routes
    processes.register(app)
    register_routes(app)

    @app.get("/api/running")
    def api_running(request: Request):
        from ..self_updates import require_owner
        from openprogram.operations.self_update.control.projection import ProjectionAccessError
        from openprogram.operations.self_update.control.projection import running_status
        from openprogram.operations.self_update.store import SelfUpdateStore
        from openprogram.operations.self_update.types import SelfUpdateError

        try:
            require_owner(request)
        except ProjectionAccessError:
            return JSONResponse({"error": "owner authentication required"}, status_code=403)
        items = _collect()
        update_error = None
        try:
            for update in running_status(SelfUpdateStore()):
                items.append(dict(kind="self_update", id=update["update_id"],
                    session_id=update["session_id"], label="self-update", status=update["phase"],
                    started_at=update["created_at"], update=update))
        except (SelfUpdateError, ValueError, OSError, KeyError, TypeError):
            update_error = "self-update snapshot unavailable"
        return JSONResponse(content={
            "items": items,
            "now": time.time(),
            "self_update_error": update_error,
        }, headers={"Cache-Control": "no-store"})
