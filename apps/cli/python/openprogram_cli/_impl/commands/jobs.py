"""Read-only CLI job resource views."""
from __future__ import annotations

import json


def job_resource_payload(
    *, session_id: str | None = None, job_id: str | None = None,
) -> dict:
    from openprogram.agent.job import get_runner

    runner = get_runner()
    if job_id is not None:
        job = runner.get_job(job_id)
        view = runner.get_job_resource_view(job_id) if job is not None else None
        return {"job": view.to_dict() if view is not None else None}
    jobs = runner.list_jobs(session_id, limit=50)
    return {
        "jobs": [
            view.to_dict()
            for job in jobs
            if (view := runner.get_job_resource_view(job.id)) is not None
        ],
    }


def _format_view(view: dict) -> str:
    capacity = view.get("capacity") or {}
    budget = view.get("budget") or {}
    lines = [
        f"{view.get('execution_id') or view.get('job_id', '?')}  {view.get('status', '?')}  "
        f"resource={view.get('resource_state', '?')}",
        f"  reason={view.get('reason_code') or '-'} "
        f"retryable={str(bool(view.get('retryable'))).lower()}",
        f"  capacity={json.dumps(capacity, ensure_ascii=False, sort_keys=True)}",
        f"  budget={json.dumps(budget, ensure_ascii=False, sort_keys=True)}",
        f"  cursor={json.dumps(view.get('event_cursor') or {}, ensure_ascii=False, sort_keys=True)}",
    ]
    return "\n".join(lines)


def _print_payload(payload: dict, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    views = payload.get("jobs")
    if views is None:
        views = [payload["job"]] if payload.get("job") is not None else []
    print("\n\n".join(_format_view(view) for view in views) or "No jobs.")
    return 0


def _cmd_jobs_list(session_id: str | None = None, *, as_json: bool = False) -> int:
    return _print_payload(
        job_resource_payload(session_id=session_id), as_json=as_json,
    )


def _cmd_jobs_get(job_id: str, *, as_json: bool = False) -> int:
    return _print_payload(job_resource_payload(job_id=job_id), as_json=as_json)
