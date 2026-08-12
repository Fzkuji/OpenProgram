from __future__ import annotations

import asyncio
import json
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.functions.agentics.ask_user import ask_user, set_ask_user
from openprogram.webui import server


class _SequenceWS:
    def __init__(self, incoming, *, focused_session_id: str | None = None) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []
        self.accepted = False
        self._focused_session_id = focused_session_id

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive_text(self) -> str:
        if not self.incoming:
            raise WebSocketDisconnect(1000)
        item = self.incoming.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def isolated_websocket_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})
    with server._follow_up_lock:
        server._follow_up_queues.clear()
    set_ask_user(None)
    yield
    set_ask_user(None)
    with server._follow_up_lock:
        server._follow_up_queues.clear()


def test_handler_failure_isolated_and_next_ping_runs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(ws, cmd) -> None:
        raise KeyError("private payload detail")

    monkeypatch.setattr(server, "WS_ACTIONS", {"bad": fail})
    ws = _SequenceWS([
        json.dumps({"action": "bad", "session_id": "s1", "secret": "do-not-echo"}),
        "ping",
        WebSocketDisconnect(1000),
    ])

    asyncio.run(server._websocket_handler(ws))

    action_error = next(frame for frame in ws.sent if frame["type"] == "action_error")
    assert action_error["data"] == {
        "action": "bad",
        "session_id": "s1",
        "code": "handler_error",
        "error": "action failed",
    }
    assert ws.sent[-1] == {"type": "pong"}
    assert "do-not-echo" not in json.dumps(ws.sent)
    assert "private payload detail" not in json.dumps(ws.sent)
    assert "action='bad'" in caplog.text


def test_invalid_json_does_not_close_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "WS_ACTIONS", {})
    ws = _SequenceWS(["{invalid", "ping", WebSocketDisconnect(1000)])

    asyncio.run(server._websocket_handler(ws))

    assert ws.sent[-1] == {"type": "pong"}


class _DisconnectWhenReadyWS(_SequenceWS):
    def __init__(self, ready: threading.Event, session_id: str) -> None:
        super().__init__([], focused_session_id=session_id)
        self.ready = ready

    async def receive_text(self) -> str:
        assert await asyncio.to_thread(self.ready.wait, 1)
        raise WebSocketDisconnect(1000)


def _start_follow_up(session_id: str):
    ready = threading.Event()
    result: list[object] = []

    def run() -> None:
        with server._web_follow_up(session_id, "m1", "fn"):
            ready.set()
            result.append(ask_user("continue?"))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return ready, result, thread


def test_last_focused_disconnect_releases_follow_up_with_none() -> None:
    ready, result, thread = _start_follow_up("s1")
    ws = _DisconnectWhenReadyWS(ready, "s1")

    asyncio.run(server._websocket_handler(ws))
    thread.join(1)

    assert not thread.is_alive(), "disconnect must not leave a 300 second wait"
    assert result == [None]
    with server._follow_up_lock:
        assert "s1" not in server._follow_up_queues


def test_disconnect_preserves_wait_when_another_connection_is_focused() -> None:
    ready, result, thread = _start_follow_up("s1")
    other = _SequenceWS([], focused_session_id="s1")
    server._ws_connections.append(other)
    ws = _DisconnectWhenReadyWS(ready, "s1")

    asyncio.run(server._websocket_handler(ws))
    thread.join(0.05)
    assert thread.is_alive()

    with server._follow_up_lock:
        queue = server._follow_up_queues["s1"]
    queue.put_nowait("answered elsewhere")
    thread.join(1)
    assert result == ["answered elsewhere"]


def test_empty_string_remains_a_real_follow_up_answer() -> None:
    ready, result, thread = _start_follow_up("s1")
    assert ready.wait(1)
    with server._follow_up_lock:
        queue = server._follow_up_queues["s1"]
    queue.put_nowait("")
    thread.join(1)

    assert result == [""]
