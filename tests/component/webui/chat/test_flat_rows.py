"""Clients that are not the web read a conversation as plain id, role and content rows."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from openprogram.interfaces.server.ws_actions import session as ws_session
from openprogram.state.store import SessionStore

SESSION = "flat-chat"


class PlainSocket:
    """A socket that sends no history_version: the terminal CLI, worker_turn or a rehearsal."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    store.create_session(SESSION, "main")
    previous = None
    for turn in range(3):
        store.append_message(SESSION, {"id": f"u{turn}", "role": "user", "content": f"question {turn}",
                                       "predecessor": previous})
        store.append_message(SESSION, {"id": f"u{turn}_reply", "role": "assistant", "content": "",
                                       "predecessor": f"u{turn}",
                                       "blocks": [{"type": "text", "text": f"answer {turn}"}]})
        previous = f"u{turn}_reply"
    store.append_message(SESSION, {"id": "call-1", "role": "tool", "function": "read", "content": "tool body",
                                   "caller": "u0_reply"})
    store.update_session(SESSION, head_id=previous)
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(server, "refresh_context_stats", lambda _sid: None)
    with server._sessions_lock:
        server._sessions[SESSION] = {"id": SESSION}
    yield store
    with server._sessions_lock:
        server._sessions.pop(SESSION, None)


def _loaded(socket, cmd: dict | None = None) -> dict:
    asyncio.run(ws_session.handle_load_session(socket, {"session_id": SESSION, **(cmd or {})}))
    return next(frame["data"] for frame in socket.frames if frame["type"] == "session_loaded")


def test_a_plain_socket_gets_the_branch_as_id_role_and_content_rows(chat):
    loaded = _loaded(PlainSocket())

    assert loaded["messages"] == [
        row for turn in range(3) for row in (
            {"id": f"u{turn}", "role": "user", "content": f"question {turn}"},
            {"id": f"u{turn}_reply", "role": "assistant", "content": f"answer {turn}"},
        )
    ]
    assert "turns" not in loaded and loaded["graph"] == []


def test_a_plain_socket_cannot_page(chat):
    from openprogram.interfaces.server.ws_errors import OperationError

    with pytest.raises(OperationError, match="invalid_request"):
        asyncio.run(ws_session.handle_load_session(PlainSocket(), {"session_id": SESSION, "history_before": "u2"}))


def test_worker_turn_recovery_finds_its_reply_by_id(chat):
    from openprogram.interfaces.cli.worker_turn import _Relay

    loaded = _loaded(PlainSocket())
    relay = _Relay(SESSION, "request", None, None)
    relay.msg_id = "u1"

    turn = relay._reply_from_session(loaded)

    assert (turn.reply, turn.assistant_msg_id) == ("answer 1", "u1_reply")


def test_the_rehearsal_reads_the_conversation(chat, monkeypatch):
    from openprogram.operations.self_update.trial import rehearsal

    class Connection:
        def __init__(self) -> None:
            self.replies: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def send(self, text: str) -> None:
            socket = PlainSocket()
            asyncio.run(ws_session.handle_load_session(socket, json.loads(text)))
            self.replies = [json.dumps(frame) for frame in socket.frames]

        def recv(self, timeout=None) -> str:
            return self.replies.pop(0)

    monkeypatch.setattr("websockets.sync.client.connect", lambda *_args, **_kwargs: Connection())
    endpoint = SimpleNamespace(websocket_url="ws://unused", authorization_header="", origin="")

    messages = rehearsal.load_conversation(endpoint, SESSION)

    assert any("answer 2" in rehearsal._text_of(message) for message in messages)


def test_the_http_command_route_sends_the_newest_rows_within_its_budget(chat, monkeypatch):
    from openprogram.interfaces.server.routes.control import commands

    row_bytes = len(json.dumps({"id": "u2_reply", "role": "assistant", "content": "answer 2"}).encode())
    monkeypatch.setattr(commands, "HTTP_ROW_BYTES", 2 * row_bytes + 4)
    request = SimpleNamespace(scope={}, headers={}, app=None)

    result = asyncio.run(commands.invoke(request, "load_session", {"session_id": SESSION}))

    loaded = next(frame["data"] for frame in result["frames"] if frame["type"] == "session_loaded")
    assert [row["id"] for row in loaded["messages"]] == ["u2", "u2_reply"]
    assert commands.HTTP_ROW_BYTES < 4 * 1024 * 1024
