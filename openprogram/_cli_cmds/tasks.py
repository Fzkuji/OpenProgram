"""Read-only CLI task resource views."""
from __future__ import annotations

import json


def task_resource_payload(
    *, session_id: str | None = None, task_id: str | None = None,
) -> dict:
    from openprogram.agent.task import get_runner

    runner = get_runner()
    if task_id is not None:
        task = runner.get_task(task_id)
        view = runner.get_task_resource_view(task_id) if task is not None else None
        return {"task": view.to_dict() if view is not None else None}
    tasks = runner.list_tasks(session_id, limit=50)
    return {
        "tasks": [
            view.to_dict()
            for task in tasks
            if (view := runner.get_task_resource_view(task.id)) is not None
        ],
    }


def _format_view(view: dict) -> str:
    capacity = view.get("capacity") or {}
    budget = view.get("budget") or {}
    lines = [
        f"{view.get('task_id', '?')}  {view.get('status', '?')}  "
        f"resource={view.get('resource_state', '?')}",
        f"  reason={view.get('reason_code') or '-'} "
        f"retryable={str(bool(view.get('retryable'))).lower()}",
        f"  capacity={json.dumps(capacity, ensure_ascii=False, sort_keys=True)}",
        f"  budget={json.dumps(budget, ensure_ascii=False, sort_keys=True)}",
    ]
    return "\n".join(lines)


def _print_payload(payload: dict, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    views = payload.get("tasks")
    if views is None:
        views = [payload["task"]] if payload.get("task") is not None else []
    print("\n\n".join(_format_view(view) for view in views) or "No tasks.")
    return 0


def _cmd_tasks_list(session_id: str | None = None, *, as_json: bool = False) -> int:
    return _print_payload(
        task_resource_payload(session_id=session_id), as_json=as_json,
    )


def _cmd_tasks_get(task_id: str, *, as_json: bool = False) -> int:
    return _print_payload(task_resource_payload(task_id=task_id), as_json=as_json)
