"""Async task WS actions — spawn / list / get / cancel.

Wire shape, all messages JSON envelopes::

  spawn:
    in   {"action": "spawn_task", "session_id": "...",
          "prompt": "...", "agent_id": "main",
          "parent_msg_id": "...", "label": "alpha",
          "context": "inherit"|"clean"}
    out  {"type": "spawn_task_result",
          "data": {"task_id", "session_id", "status",
                   "parent_msg_id"}}

  list:
    in   {"action": "list_tasks", "session_id": "..." | null,
          "status_filter": ["running", ...]?, "limit": 50?}
    out  {"type": "tasks_list",
          "data": {"session_id"?, "tasks": [<task_dict>, ...]}}

  get:
    in   {"action": "get_task", "task_id": "..."}
    out  {"type": "task",
          "data": {"task": <task_dict>|null}}

  cancel:
    in   {"action": "cancel_task", "task_id": "..."}
    out  {"type": "cancel_task_result",
          "data": {"task_id", "status"}}

Mutating operations also broadcast a ``task_status`` envelope
(via the runner) so other clients tail-following the session
see the transition.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


def _serialise(task) -> dict[str, Any]:
    """Convert a Task to the WS payload shape."""
    d = task.to_dict()
    # Strip oversize prompt blob — the UI doesn't need the full text
    # in list_tasks, only the subject. spawn / get respect their own
    # caller's choice (we keep prompt in the dict).
    return d


async def handle_spawn_task(ws, cmd: dict) -> None:
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
            "type": "spawn_task_result",
            "data": {
                "task_id": None,
                "session_id": session_id,
                "status": "errored",
                "parent_msg_id": parent_msg_id,
                "error": "session_id and prompt are required",
            },
        }, default=str))
        return

    if context_mode == "inherit" and not parent_msg_id:
        await ws.send_text(json.dumps({
            "type": "spawn_task_result",
            "data": {
                "task_id": None,
                "session_id": session_id,
                "status": "errored",
                "parent_msg_id": parent_msg_id,
                "error": "parent_msg_id is required when context='inherit'",
            },
        }, default=str))
        return

    from openprogram.agent.task import get_runner
    runner = get_runner()

    def _submit() -> str:
        return runner.spawn_task(
            session_id=session_id,
            prompt=prompt,
            agent_id=agent_id,
            subject=(prompt.splitlines()[0] if prompt else "task")[:60],
            description=prompt,
            context_mode=context_mode,
            parent_msg_id=parent_msg_id,
            label=label,
        )

    loop = asyncio.get_event_loop()
    task_id = await loop.run_in_executor(None, _submit)
    cur = runner.get_task(task_id)
    payload = {
        "task_id": task_id,
        "session_id": session_id,
        "status": (cur.status.value if cur else "pending"),
        "parent_msg_id": parent_msg_id,
    }
    await ws.send_text(json.dumps({
        "type": "spawn_task_result",
        "data": payload,
    }, default=str))


async def handle_list_tasks(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip() or None
    sf = cmd.get("status_filter") or None
    limit = cmd.get("limit")
    if not isinstance(limit, int):
        limit = None
    from openprogram.agent.task import get_runner
    from openprogram.agent.task.types import TaskStatus
    status_filter = None
    if isinstance(sf, list) and sf:
        try:
            status_filter = {TaskStatus(s) for s in sf}
        except ValueError:
            status_filter = None
    runner = get_runner()

    def _read():
        return runner.list_tasks(session_id, status_filter=status_filter, limit=limit)

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _read)
    await ws.send_text(json.dumps({
        "type": "tasks_list",
        "data": {
            "session_id": session_id,
            "tasks": [_serialise(t) for t in rows],
        },
    }, default=str))


async def handle_get_task(ws, cmd: dict) -> None:
    task_id = (cmd.get("task_id") or "").strip()
    if not task_id:
        await ws.send_text(json.dumps({
            "type": "task",
            "data": {"task": None, "error": "task_id is required"},
        }, default=str))
        return
    from openprogram.agent.task import get_runner
    runner = get_runner()

    def _read():
        return runner.get_task(task_id)

    loop = asyncio.get_event_loop()
    t = await loop.run_in_executor(None, _read)
    await ws.send_text(json.dumps({
        "type": "task",
        "data": {"task": _serialise(t) if t else None},
    }, default=str))


async def handle_cancel_task(ws, cmd: dict) -> None:
    task_id = (cmd.get("task_id") or "").strip()
    reason = cmd.get("reason") or None
    if not task_id:
        await ws.send_text(json.dumps({
            "type": "cancel_task_result",
            "data": {"task_id": None, "status": None,
                      "error": "task_id is required"},
        }, default=str))
        return
    from openprogram.agent.task import get_runner
    runner = get_runner()

    def _cancel():
        return runner.cancel_task(task_id, reason=reason)

    loop = asyncio.get_event_loop()
    t = await loop.run_in_executor(None, _cancel)
    await ws.send_text(json.dumps({
        "type": "cancel_task_result",
        "data": {
            "task_id": task_id,
            "status": (t.status.value if t else None),
        },
    }, default=str))


ACTIONS = {
    "spawn_task": handle_spawn_task,
    "list_tasks": handle_list_tasks,
    "get_task": handle_get_task,
    "cancel_task": handle_cancel_task,
}
