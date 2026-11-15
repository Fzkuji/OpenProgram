"""A turn that ends while nobody looks at its conversation leaves a dot.

Blue (unread, done) for a finished turn, red (unread, failed) for a failed
one, none when a client has the conversation open. Only ``mark_session_read``
says a client has it open; loading a session does not.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest


class _DB:
    def __init__(self):
        self.updates: list[tuple[str, dict]] = []

    def update_session(self, session_id, **fields):
        self.updates.append((session_id, fields))


class _WS:
    def __init__(self, focused=None):
        self._focused_session_id = focused
        self.sent: list[dict] = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def connections(monkeypatch):
    from openprogram.interfaces.server import server as _s

    frames: list[dict] = []
    monkeypatch.setattr(_s, "_ws_connections", [])
    monkeypatch.setattr(_s, "_broadcast", lambda msg, **kw: frames.append(json.loads(msg)))
    return _s._ws_connections, frames


@pytest.mark.parametrize("failed, status", [(False, "done"), (True, "failed")])
def test_turn_end_marks_unread_when_nobody_looks(connections, failed, status):
    from openprogram.runtime.conversation.turn_runtime.turn_writer import mark_session_finished

    sockets, frames = connections
    sockets.append(_WS(focused="other"))
    db = _DB()

    mark_session_finished(db, "s1", failed=failed)

    assert db.updates == [("s1", {"status": status, "unread": True})]
    assert frames == [{"type": "session_updated",
                       "data": {"id": "s1", "status": status, "unread": True}}]


def test_a_quiet_turn_end_leaves_the_dot_alone(connections):
    from openprogram.runtime.conversation.turn_runtime.turn_writer import mark_session_finished

    sockets, frames = connections
    sockets.append(_WS(focused="other"))
    db = _DB()

    mark_session_finished(db, "s1", quiet=True)
    mark_session_finished(db, "s1", quiet=True, failed=True)

    assert db.updates == [("s1", {"status": "done"}), ("s1", {"status": "failed", "unread": True})]
    assert frames[0] == {"type": "session_updated", "data": {"id": "s1", "status": "done"}}


def test_turn_end_on_screen_is_not_unread(connections):
    from openprogram.runtime.conversation.turn_runtime.turn_writer import mark_session_finished

    sockets, frames = connections
    sockets.append(_WS(focused="s1"))
    db = _DB()

    mark_session_finished(db, "s1")

    assert db.updates == [("s1", {"status": "done", "unread": False})]
    assert frames[-1]["data"] == {"id": "s1", "status": "done", "unread": False}


def _read(ws, session_id, db_updates, monkeypatch):
    from openprogram.interfaces.server.ws_actions.session import handle_mark_session_read
    from openprogram.runtime.conversation.always_on import pending
    import openprogram.runtime.context.session_db as session_db

    monkeypatch.setattr(session_db, "default_db", lambda: types.SimpleNamespace(
        update_session=lambda sid, **fields: db_updates.append((sid, fields))))
    seen: list[str] = []
    monkeypatch.setattr(pending, "mark_seen", lambda sid: seen.append(sid) or False)
    asyncio.run(handle_mark_session_read(ws, {"session_id": session_id}))
    return seen


def test_turn_end_in_a_hidden_window_is_unread(connections):
    from openprogram.interfaces.server.ws_actions.session import handle_set_window_hidden
    from openprogram.runtime.conversation.turn_runtime.turn_writer import mark_session_finished

    sockets, _ = connections
    ws = _WS(focused="s1")
    sockets.append(ws)
    asyncio.run(handle_set_window_hidden(ws, {"hidden": True}))
    db = _DB()

    mark_session_finished(db, "s1")

    assert ws._focused_session_id == "s1"
    assert db.updates == [("s1", {"status": "done", "unread": True})]

    asyncio.run(handle_set_window_hidden(ws, {"hidden": False}))
    mark_session_finished(db, "s1")

    assert db.updates[-1] == ("s1", {"status": "done", "unread": False})


def test_a_second_visible_window_keeps_it_on_screen(connections):
    from openprogram.runtime.conversation.turn_runtime.turn_writer import mark_session_finished

    sockets, _ = connections
    hidden = _WS(focused="s1")
    hidden._window_hidden = True
    sockets.extend([hidden, _WS(focused="s1")])
    db = _DB()

    mark_session_finished(db, "s1")

    assert db.updates == [("s1", {"status": "done", "unread": False})]


def test_a_hidden_window_keeps_focus_but_clears_nothing(connections, monkeypatch):
    _, frames = connections
    ws = _WS(focused="other")
    ws._window_hidden = True
    updates: list = []

    seen = _read(ws, "s1", updates, monkeypatch)

    assert ws._focused_session_id == "s1"
    assert (updates, seen, frames) == ([], [], [])


def test_a_visible_window_still_clears_dot_and_news(connections, monkeypatch):
    _, frames = connections
    updates: list = []

    seen = _read(_WS(), "s1", updates, monkeypatch)

    assert updates == [("s1", {"unread": False})]
    assert seen == ["s1"]
    assert frames == [{"type": "session_updated", "data": {"id": "s1", "unread": False}}]


def test_failed_turn_records_failure_with_unread(connections):
    from openprogram.runtime.conversation.turn_runtime.turn_writer import TurnWriter

    db = _DB()
    writer = TurnWriter.__new__(TurnWriter)
    writer.db = db
    writer.req = types.SimpleNamespace(session_id="s1")
    writer.advance = True

    writer.record_failure("reply-1")

    assert db.updates == [("s1", {"status": "failed", "unread": True, "head_id": "reply-1"})]


def test_loading_a_session_does_not_mark_it_viewed(monkeypatch):
    from openprogram.interfaces.server.ws_actions.session import handle_load_session
    import openprogram.runtime.context.session_db as session_db

    monkeypatch.setattr(
        session_db, "default_db",
        lambda: types.SimpleNamespace(get_session=lambda sid: None),
    )
    ws = _WS(focused="visible")

    asyncio.run(handle_load_session(ws, {"session_id": "missing", "request_id": "r1"}))

    assert ws._focused_session_id == "visible"
    assert ws.sent == [{"type": "operation_error", "data": {
        "action": "load_session", "code": "session_not_found",
        "session_id": "missing", "request_id": "r1",
    }}]
