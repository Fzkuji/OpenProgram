"""A turn pinned as a chapter is stored on its user message and loads back.

``set_message_chapter`` writes ``metadata.chapter`` on the user message
node, broadcasts ``message_chapter_updated`` to every connection and
refuses an assistant or unknown message; ``load_session`` carries the pin
as the row's ``chapterPinnedAt`` field.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from openprogram.interfaces.server.ws_actions import session as ws_session
from openprogram.interfaces.server.ws_errors import OperationError
from openprogram.state.store import SessionStore


class FakeWS:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


@pytest.fixture
def chat(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    store.create_session("s1", "main")
    store.append_message("s1", {"id": "u1", "role": "user", "content": "first question"})
    store.append_message("s1", {"id": "a1", "role": "assistant", "content": "first answer",
                                "predecessor": "u1"})
    store.append_message("s1", {"id": "u2", "role": "user", "content": "second question",
                                "predecessor": "a1"})
    frames: list[dict] = []
    monkeypatch.setattr(server, "_broadcast", lambda msg, **kw: frames.append(json.loads(msg)))
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(server, "refresh_context_stats", lambda _sid: None)
    with server._sessions_lock:
        server._sessions["s1"] = {"id": "s1"}
    yield store, frames
    with server._sessions_lock:
        server._sessions.pop("s1", None)


def _set(cmd: dict) -> FakeWS:
    ws = FakeWS()
    asyncio.run(ws_session.handle_set_message_chapter(ws, {"action": "set_message_chapter", **cmd}))
    return ws


def _metadata(store: SessionStore, node_id: str) -> dict:
    return next(n.metadata for n in store.get_nodes("s1") if n.id == node_id) or {}


def _loaded_rows() -> dict[str, dict]:
    from openprogram.interfaces.server.transcript import publisher

    ws = FakeWS()
    ws._history_protocol = 3
    asyncio.run(ws_session.handle_load_session(ws, {"session_id": "s1"}))
    publisher.release(ws)
    loaded = next(f for f in ws.frames if f["type"] == "session_loaded")
    return {row["id"]: row for turn in loaded["data"]["turns"] for row in turn["rows"]}


def test_the_action_is_registered():
    assert ws_session.ACTIONS["set_message_chapter"] is ws_session.handle_set_message_chapter


def test_pin_stores_the_chapter_on_the_user_message_and_broadcasts(chat):
    store, frames = chat

    _set({"session_id": "s1", "message_id": "u1", "pinned": True, "request_id": "r1"})

    chapter = _metadata(store, "u1")["chapter"]
    assert isinstance(chapter["pinned_at"], float) and chapter["pinned_at"] > 0
    assert frames == [{
        "type": "message_chapter_updated",
        "data": {"session_id": "s1", "message_id": "u1", "chapter": chapter,
                 "request_id": "r1", "action": "set_message_chapter"},
    }]
    # Nothing else on the node changes.
    assert "chapter" not in _metadata(store, "u2")


def test_unpin_writes_none(chat):
    store, frames = chat

    _set({"session_id": "s1", "message_id": "u1", "pinned": True})
    _set({"session_id": "s1", "message_id": "u1", "pinned": False})

    assert _metadata(store, "u1")["chapter"] is None
    assert frames[-1]["data"]["chapter"] is None


def test_the_pin_survives_a_reload_in_the_history_payload(chat):
    _set({"session_id": "s1", "message_id": "u2", "pinned": True})

    rows = _loaded_rows()

    assert rows["u2"]["chapterPinnedAt"] > 0
    assert rows["u1"].get("chapterPinnedAt") is None


@pytest.mark.parametrize("cmd, code", [
    ({"message_id": "a1", "pinned": True}, "not_a_user_message"),
    ({"message_id": "missing", "pinned": True}, "message_not_found"),
    ({"message_id": "u1", "pinned": "yes"}, "invalid_request"),
    ({"message_id": "", "pinned": True}, "invalid_request"),
])
def test_rejects_assistant_unknown_and_malformed_requests(chat, cmd, code):
    store, frames = chat

    with pytest.raises(OperationError) as raised:
        _set({"session_id": "s1", **cmd})

    assert raised.value.code == code
    assert frames == []
    assert "chapter" not in _metadata(store, "a1")

