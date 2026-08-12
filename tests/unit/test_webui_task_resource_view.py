from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openprogram.agent.task.types import TaskStatus
from openprogram.webui.ws_actions import task as ws_task


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


class _View:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": "running",
            "resource_state": "active",
            "reason_code": None,
        }


class _Runner:
    def __init__(self) -> None:
        self.task = SimpleNamespace(
            id="t1", parent_session_id="s1", status=TaskStatus.RUNNING,
            to_dict=lambda: {
                "id": "t1", "parent_session_id": "s1", "status": "running",
            },
        )

    def list_tasks(self, session_id, status_filter=None, limit=None):
        return [self.task]

    def get_task(self, task_id):
        return self.task if task_id == "t1" else None

    def cancel_task(self, task_id, reason=None):
        return self.task if task_id == "t1" else None

    def get_task_resource_view(self, task_id):
        return _View(task_id)


@pytest.mark.parametrize(
    ("handler", "command", "frame_type", "resource_path"),
    [
        (ws_task.handle_list_tasks, {"session_id": "s1"}, "tasks_list", ("tasks", 0)),
        (ws_task.handle_get_task, {"task_id": "t1"}, "task", ("task",)),
        (
            ws_task.handle_cancel_task,
            {"task_id": "t1"},
            "cancel_task_result",
            (),
        ),
    ],
)
def test_web_task_surfaces_embed_canonical_resource_view(
    monkeypatch, handler, command, frame_type, resource_path,
) -> None:
    runner = _Runner()
    monkeypatch.setattr("openprogram.agent.task.get_runner", lambda: runner)
    ws = _WS()

    asyncio.run(handler(ws, command))

    frame = next(item for item in ws.frames if item["type"] == frame_type)
    payload = frame["data"]
    for key in resource_path:
        payload = payload[key]
    assert payload["resource"] == runner.get_task_resource_view("t1").to_dict()
