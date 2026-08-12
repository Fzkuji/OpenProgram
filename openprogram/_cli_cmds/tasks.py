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


def _print_payload(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _cmd_tasks_list(session_id: str | None = None) -> int:
    return _print_payload(task_resource_payload(session_id=session_id))


def _cmd_tasks_get(task_id: str) -> int:
    return _print_payload(task_resource_payload(task_id=task_id))
