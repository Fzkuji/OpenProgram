from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import re
import threading

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.agent.authority import owner_authority
from openprogram.programs.workflow.ask_user import ask_user, set_ask_user
from openprogram.webui import server
from openprogram.webui.ws_errors import operation_error_frame


class _SequenceWS:
    def __init__(self, incoming, *, focused_session_id: str | None = None) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []
        self.accepted = False
        self._focused_session_id = focused_session_id
        self.scope = {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef"),
            },
        }

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


def _assert_operation_error_data(
    data: dict,
    *,
    action: str,
    request_id: str,
    code: str,
    message: str,
) -> None:
    assert set(data) == {
        "error_id",
        "request_id",
        "scope",
        "code",
        "message",
        "action",
        "session_id",
        "operation_id",
        "retryable",
        "severity",
        "correlation_id",
        "occurred_at",
    }
    assert re.fullmatch(r"err_[0-9a-f]{32}", data["error_id"])
    assert re.fullmatch(r"corr_[0-9a-f]{32}", data["correlation_id"])
    assert data["occurred_at"].endswith("Z")
    occurred_at = datetime.fromisoformat(data["occurred_at"].replace("Z", "+00:00"))
    assert occurred_at.utcoffset() == timedelta(0)
    assert data["action"] == action
    assert data["session_id"] == "s1"
    assert data["request_id"] == request_id
    assert data["scope"] == "session"
    assert data["code"] == code
    assert data["message"] == message
    assert data["operation_id"] is None
    assert data["retryable"] is False
    assert data["severity"] == "error"


def test_handler_failure_isolated_and_next_ping_runs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(ws, cmd) -> None:
        raise KeyError("private payload detail")

    monkeypatch.setattr(server, "WS_ACTIONS", {"bad": fail})
    ws = _SequenceWS([
        json.dumps({
            "action": "bad",
            "session_id": "s1",
            "request_id": "request-1",
            "secret": "do-not-echo",
        }),
        "ping",
        WebSocketDisconnect(1000),
    ])

    asyncio.run(server._websocket_handler(ws))

    operation_error = next(
        frame for frame in ws.sent if frame["type"] == "operation_error"
    )
    _assert_operation_error_data(
        operation_error["data"],
        action="bad",
        request_id="request-1",
        code="handler_error",
        message="Action failed",
    )
    assert ws.sent[-1] == {"type": "pong"}
    assert "do-not-echo" not in json.dumps(ws.sent)
    assert "private payload detail" not in json.dumps(ws.sent)
    assert "action='bad'" in caplog.text


def test_handler_error_rejects_non_string_metadata(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(ws, cmd) -> None:
        raise TypeError("failed")

    monkeypatch.setattr(server, "WS_ACTIONS", {"bad": fail})
    ws = _SequenceWS([
        json.dumps({
            "action": "bad",
            "session_id": {"secret": "do-not-log"},
            "request_id": {"secret": "do-not-log-request"},
        }),
        json.dumps({"action": {"secret": "do-not-log-action"}}),
        json.dumps({
            "action": "missing",
            "session_id": {"secret": "do-not-log-id"},
            "request_id": "x" * 129,
        }),
        WebSocketDisconnect(1000),
    ])

    asyncio.run(server._websocket_handler(ws))

    operation_error = next(
        frame for frame in ws.sent if frame["type"] == "operation_error"
    )
    assert operation_error["data"]["session_id"] is None
    assert operation_error["data"]["request_id"] is None
    assert ws.sent[-2]["data"]["action"] is None
    assert ws.sent[-1]["data"]["session_id"] is None
    assert ws.sent[-1]["data"]["request_id"] is None
    assert "do-not-log" not in json.dumps(ws.sent)
    assert "do-not-log" not in caplog.text
    assert "do-not-log" not in capsys.readouterr().out


def test_invalid_json_does_not_close_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "WS_ACTIONS", {})
    ws = _SequenceWS(["{invalid", "ping", WebSocketDisconnect(1000)])

    asyncio.run(server._websocket_handler(ws))

    assert ws.sent[-1] == {"type": "pong"}


def test_unknown_action_returns_correlated_operation_error() -> None:
    ws = _SequenceWS([
        json.dumps({
            "action": "removed_action",
            "session_id": "s1",
            "request_id": "request-2",
        }),
        "ping",
        WebSocketDisconnect(1000),
    ])

    asyncio.run(server._websocket_handler(ws))

    assert ws.sent[-2]["type"] == "operation_error"
    _assert_operation_error_data(
        ws.sent[-2]["data"],
        action="removed_action",
        request_id="request-2",
        code="unknown_action",
        message="Unknown action",
    )
    assert ws.sent[-1] == {"type": "pong"}


@pytest.mark.parametrize("field", ["action", "session_id", "request_id"])
@pytest.mark.parametrize("invalid", ["x" * 129, "line\u0085break", "line\nbreak"])
def test_operation_error_rejects_unsafe_metadata(field: str, invalid: str) -> None:
    command = {
        "action": "bad",
        "session_id": "s1",
        "request_id": "x" * 128,
        field: invalid,
    }

    data = operation_error_frame(command, code="handler_error")["data"]

    assert data[field] is None
    if field != "request_id":
        assert data["request_id"] == "x" * 128


def test_handler_transport_disconnect_ends_connection_without_operation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def disconnect(ws, cmd) -> None:
        raise WebSocketDisconnect(1001)

    monkeypatch.setattr(server, "WS_ACTIONS", {"disconnect": disconnect})
    ws = _SequenceWS([
        json.dumps({"action": "disconnect", "session_id": "s1"}),
        "ping",
    ])

    asyncio.run(server._websocket_handler(ws))

    assert all(
        frame["type"] not in {"operation_error", "action_error", "pong"}
        for frame in ws.sent
    )


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


def _start_repeated_follow_up(session_id: str):
    ready = threading.Event()
    result: list[object] = []

    def run() -> None:
        with server._web_follow_up(session_id, "m1", "fn"):
            ready.set()
            result.append((ask_user("first?"), ask_user("second?")))

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


def test_disconnect_releases_repeated_follow_ups_with_none() -> None:
    ready, result, thread = _start_repeated_follow_up("s1")
    ws = _DisconnectWhenReadyWS(ready, "s1")

    asyncio.run(server._websocket_handler(ws))
    thread.join(0.1)
    remained_blocked = thread.is_alive()
    if remained_blocked:
        with server._follow_up_lock:
            queue = server._follow_up_queues.get("s1")
        if queue is not None:
            queue.put_nowait("test cleanup")
        thread.join(1)

    assert not remained_blocked, "later questions must not restart the 300 second wait"
    assert result == [(None, None)]


def test_cancel_releases_repeated_follow_ups_with_none() -> None:
    ready, result, thread = _start_repeated_follow_up("s1")
    assert ready.wait(1)
    with server._follow_up_lock:
        queue = server._follow_up_queues["s1"]
    queue.put_nowait({"_cancelled": True})

    thread.join(0.1)
    remained_blocked = thread.is_alive()
    if remained_blocked:
        queue.put_nowait("test cleanup")
        thread.join(1)

    assert not remained_blocked, "later questions must not restart after cancellation"
    assert result == [(None, None)]


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
