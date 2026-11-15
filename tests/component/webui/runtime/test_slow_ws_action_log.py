"""A WebSocket action that holds its connection is logged by name.

Actions on one connection run in order, so a slow one delays every later
frame on it, a chat send included: a New chat's first message was once
acknowledged 20 seconds late with nothing in the logs to say what held it.
"""
from __future__ import annotations

import asyncio
import logging

from openprogram.interfaces.server import server  # noqa: F401 - loads the dispatch module in order
from openprogram.interfaces.server.server_runtime import websocket_dispatch


class _FakeWS:
    scope: dict = {"state": {}}

    async def send_text(self, text):  # pragma: no cover - not reached
        raise AssertionError(text)


def _run(monkeypatch, caplog, threshold: float):
    from openprogram.interfaces.server import server

    calls = []

    async def slow(ws, cmd):
        calls.append(cmd["action"])
        await asyncio.sleep(0.01)

    monkeypatch.setitem(server.WS_ACTIONS, "probe_slow_action", slow)
    monkeypatch.setattr(websocket_dispatch, "SLOW_ACTION_SECONDS", threshold)
    monkeypatch.setattr(
        "openprogram.operations.self_update.verification.ui_checks.permits_ws_command",
        lambda ws, cmd: True)
    with caplog.at_level(logging.WARNING, logger=websocket_dispatch.__name__):
        asyncio.run(server._handle_ws_command(
            _FakeWS(), {"action": "probe_slow_action", "session_id": "local_s"}))
    assert calls == ["probe_slow_action"]
    return [r.getMessage() for r in caplog.records if r.name == websocket_dispatch.__name__]


def test_slow_action_names_itself_and_its_session(monkeypatch, caplog):
    messages = _run(monkeypatch, caplog, threshold=0.0)
    assert any("probe_slow_action" in m and "local_s" in m for m in messages), messages


def test_quick_action_logs_nothing(monkeypatch, caplog):
    assert _run(monkeypatch, caplog, threshold=60.0) == []
