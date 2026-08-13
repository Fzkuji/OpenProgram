"""Async job WS actions — spawn / list / get / cancel.

Wire shape, all messages JSON envelopes::

  spawn:
    in   {"action": "spawn_job", "session_id": "...",
          "prompt": "...", "agent_id": "main",
          "parent_msg_id": "...", "label": "alpha",
          "context": "inherit"|"clean"}
    out  {"type": "spawn_job_result",
          "data": {"job_id", "session_id", "status",
                   "parent_msg_id"}}

  list:
    in   {"action": "list_jobs", "session_id": "..." | null,
          "status_filter": ["running", ...]?, "limit": 50?}
    out  {"type": "jobs_list",
          "data": {"session_id"?, "jobs": [<job_dict>, ...]}}

  get:
    in   {"action": "get_job", "job_id": "..."}
    out  {"type": "job",
          "data": {"job": <job_dict>|null}}

  cancel:
    in   {"action": "cancel_job", "job_id": "..."}
    out  {"type": "cancel_job_result",
          "data": {"job_id", "status"}}

Mutating operations also broadcast a ``job_status`` envelope
(via the runner) so other clients tail-following the session
see the transition.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def _serialise(job, runner) -> dict[str, Any]:
    """Convert a Job and its canonical resource view to the WS shape."""
    d = job.to_dict()
    view = runner.get_job_resource_view(job.id)
    d["resource"] = view.to_dict() if view is not None else None
    # Strip oversize prompt blob — the UI doesn't need the full text
    # in list_jobs, only the subject. spawn / get respect their own
    # caller's choice (we keep prompt in the dict).
    return d


async def handle_spawn_job(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    prompt = cmd.get("prompt") or ""
    agent_id = (cmd.get("agent_id") or "main").strip() or "main"
    parent_msg_id = (cmd.get("parent_msg_id") or "").strip() or None
    label_in = cmd.get("label")
    label = label_in.strip() if isinstance(label_in, str) else None
    if label == "":
        label = None
    raw_ctx = (cmd.get("context") or cmd.get("mode") or "inherit").strip().lower()
    context_mode = "clean" if raw_ctx in ("clean", "detached") else "inherit"

    if not session_id or not prompt:
        await ws.send_text(json.dumps({
            "type": "spawn_job_result",
            "data": {
                "job_id": None,
                "session_id": session_id,
                "status": "errored",
                "parent_msg_id": parent_msg_id,
                "error": "session_id and prompt are required",
            },
        }, default=str))
        return

    if context_mode == "inherit" and not parent_msg_id:
        await ws.send_text(json.dumps({
            "type": "spawn_job_result",
            "data": {
                "job_id": None,
                "session_id": session_id,
                "status": "errored",
                "parent_msg_id": parent_msg_id,
                "error": "parent_msg_id is required when context='inherit'",
            },
        }, default=str))
        return

    from openprogram.agent.job import get_runner
    runner = get_runner()

    def _submit() -> str:
        return runner.spawn_job(
            session_id=session_id,
            prompt=prompt,
            agent_id=agent_id,
            subject=(prompt.splitlines()[0] if prompt else "job")[:60],
            description=prompt,
            context_mode=context_mode,
            parent_msg_id=parent_msg_id,
            label=label,
        )

    from openprogram.agent.resource_governance import AdmissionRejected

    loop = asyncio.get_event_loop()
    try:
        job_id = await loop.run_in_executor(None, _submit)
    except AdmissionRejected as rejected:
        decision = rejected.decision
        await ws.send_text(json.dumps({
            "type": "spawn_job_result",
            "data": {
                "job_id": None,
                "session_id": session_id,
                "status": "rejected",
                "parent_msg_id": parent_msg_id,
                "reason_code": decision.reason_code,
                "retryable": decision.retryable,
                "limits": decision.effective_limits,
                "capacity": decision.capacity,
                "usage": decision.usage,
                "resource": None,
            },
        }, default=str))
        return
    cur = runner.get_job(job_id)
    view = runner.get_job_resource_view(job_id) if cur is not None else None
    payload = {
        "job_id": job_id,
        "session_id": session_id,
        "status": (cur.status.value if cur else "pending"),
        "parent_msg_id": parent_msg_id,
        "resource": view.to_dict() if view is not None else None,
    }
    await ws.send_text(json.dumps({
        "type": "spawn_job_result",
        "data": payload,
    }, default=str))


async def handle_list_jobs(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip() or None
    sf = cmd.get("status_filter") or None
    limit = cmd.get("limit")
    if not isinstance(limit, int):
        limit = None
    from openprogram.agent.job import get_runner
    from openprogram.agent.job.types import JobStatus
    status_filter = None
    if isinstance(sf, list) and sf:
        try:
            status_filter = {JobStatus(s) for s in sf}
        except ValueError:
            status_filter = None
    runner = get_runner()

    def _read():
        return runner.list_jobs(session_id, status_filter=status_filter, limit=limit)

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _read)
    await ws.send_text(json.dumps({
        "type": "jobs_list",
        "data": {
            "session_id": session_id,
            "jobs": [_serialise(t, runner) for t in rows],
        },
    }, default=str))


async def handle_get_job(ws, cmd: dict) -> None:
    job_id = (cmd.get("job_id") or "").strip()
    if not job_id:
        await ws.send_text(json.dumps({
            "type": "job",
            "data": {"job": None, "error": "job_id is required"},
        }, default=str))
        return
    from openprogram.agent.job import get_runner
    runner = get_runner()

    def _read():
        return runner.get_job(job_id)

    loop = asyncio.get_event_loop()
    t = await loop.run_in_executor(None, _read)
    await ws.send_text(json.dumps({
        "type": "job",
        "data": {"job": _serialise(t, runner) if t else None},
    }, default=str))


async def handle_cancel_job(ws, cmd: dict) -> None:
    job_id = (cmd.get("job_id") or "").strip()
    reason = cmd.get("reason") or None
    if not job_id:
        await ws.send_text(json.dumps({
            "type": "cancel_job_result",
            "data": {"job_id": None, "status": None,
                      "error": "job_id is required"},
        }, default=str))
        return
    from openprogram.agent.job import get_runner
    runner = get_runner()

    def _cancel():
        return runner.cancel_job(job_id, reason=reason)

    loop = asyncio.get_event_loop()
    t = await loop.run_in_executor(None, _cancel)
    view = runner.get_job_resource_view(job_id) if t else None
    await ws.send_text(json.dumps({
        "type": "cancel_job_result",
        "data": {
            "job_id": job_id,
            "status": (t.status.value if t else None),
            "resource": view.to_dict() if view is not None else None,
        },
    }, default=str))


ACTIONS = {
    "spawn_job": handle_spawn_job,
    "list_jobs": handle_list_jobs,
    "get_job": handle_get_job,
    "cancel_job": handle_cancel_job,
}
