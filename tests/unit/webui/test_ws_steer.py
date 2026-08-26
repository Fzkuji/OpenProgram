from __future__ import annotations

import asyncio
import json

from openprogram.webui.ws_actions import runtime


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.frames.append(json.loads(raw))


def test_steer_after_turn_end_returns_not_running(monkeypatch) -> None:
    from openprogram.webui import server

    monkeypatch.setattr(server, "_is_run_active", lambda session_id: False)
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "openprogram.agent.steering.push",
        lambda session_id, message: pushed.append((session_id, message)) or True,
    )
    ws = _WS()

    asyncio.run(runtime.handle_steer(ws, {
        "session_id": "finished",
        "message": "late",
        "request_id": "request-1",
    }))

    assert pushed == []
    assert ws.frames == [{
        "type": "steer_ack",
        "data": {
            "session_id": "finished",
            "request_id": "request-1",
            "result": "not_running",
            "queued": False,
            "message": "late",
        },
    }]


def test_steer_after_chat_sweep_returns_not_running(monkeypatch) -> None:
    from openprogram.webui import server

    monkeypatch.setattr(server, "_is_run_active", lambda session_id: True)
    monkeypatch.setattr(server, "_running_tasks", {
        "closing": {"func_name": "_chat", "msg_id": "m1"},
    })
    pushed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "openprogram.agent.steering.push",
        lambda session_id, message: pushed.append((session_id, message)) or True,
    )
    ws = _WS()

    asyncio.run(runtime.handle_steer(ws, {
        "session_id": "closing",
        "message": "too late",
        "request_id": "request-2",
    }))

    assert pushed == []
    assert ws.frames[0]["data"]["result"] == "not_running"


def test_steer_ack_accepts_registered_chat(tmp_path, monkeypatch) -> None:
    from openprogram.agent import steering
    from openprogram.webui import server

    inbox = tmp_path / "steering"
    inbox.mkdir()
    monkeypatch.setattr(steering, "_steer_dir", lambda session_id: inbox)
    monkeypatch.setattr(server, "_is_run_active", lambda session_id: True)
    steering.begin_accepting("active")
    ws = _WS()
    try:
        asyncio.run(runtime.handle_steer(ws, {
            "session_id": "active",
            "message": "change direction",
            "request_id": "request-3",
        }))
    finally:
        pending = steering.close_and_drain("active")

    assert ws.frames[0]["data"]["result"] == "accepted"
    assert pending == ["change direction"]
