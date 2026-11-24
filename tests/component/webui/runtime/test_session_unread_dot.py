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
        self.rows: dict[str, dict] = {}

    def update_session(self, session_id, **fields):
        self.updates.append((session_id, fields))
        self.rows.setdefault(session_id, {}).update(fields)

    def get_session(self, session_id):
        return self.rows.get(session_id)


class _Turns:
    """Root chat executions of one conversation, each with its own user message."""

    def __init__(self, *running):
        self.running = {execution_id: status for execution_id, status in running}
        self.ended: set[str] = set()

    def end(self, execution_id):
        self.running.pop(execution_id)
        self.ended.add(execution_id)

    def list_nonterminal(self, *, session_id):
        return [types.SimpleNamespace(execution_id=execution_id, session_id=session_id, status=status,
                                      parent_execution_id=None)
                for execution_id, status in self.running.items()]

    def get_execution(self, execution_id):
        from openprogram.runtime.execution.model import ExecutionStatus
        status = ExecutionStatus.COMPLETED if execution_id in self.ended else self.running[execution_id]
        return types.SimpleNamespace(execution_id=execution_id, status=status)

    def get_agent_turn_input(self, _execution_id):
        return {"kind": "chat", "request": {"source": "web"}}

    def get_execution_input(self, execution_id):
        return types.SimpleNamespace(user_message_id=execution_id + "_user")


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


def test_a_turn_that_ends_while_another_runs_leaves_the_conversation_running(connections, monkeypatch):
    """A short turn (a background-command notice, a turn on another
    branch) ends while another turn of the conversation still runs: the
    conversation is not done until that one is. A failure is still said."""
    from openprogram.runtime.conversation.turn_runtime import turn_writer

    sockets, frames = connections
    sockets.append(_WS(focused="s1"))
    db = _DB()
    asked = []
    monkeypatch.setattr(turn_writer, "_another_turn_runs",
                        lambda session_id, own_msg_id: asked.append((session_id, own_msg_id)) or True)

    turn_writer.mark_session_finished(db, "s1", own_msg_id="notice_user")
    turn_writer.mark_session_finished(db, "s1", failed=True, own_msg_id="notice_user")

    assert asked == [("s1", "notice_user")]
    assert [fields["status"] for _sid, fields in db.updates] == ["running", "failed"]
    assert frames[0]["data"]["status"] == "running"


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


def test_mark_all_read_clears_each_listed_conversation_without_moving_focus(connections, monkeypatch):
    """The sidebar Activity's "Mark all as read" clears dot and news of every
    listed conversation, once each, and leaves the socket's focus alone."""
    from openprogram.interfaces.server.ws_actions.session import ACTIONS
    from openprogram.runtime.conversation.always_on import pending
    import openprogram.runtime.context.session_db as session_db

    _, frames = connections
    updates: list = []
    seen: list[str] = []
    monkeypatch.setattr(session_db, "default_db", lambda: types.SimpleNamespace(
        update_session=lambda sid, **fields: updates.append((sid, fields))))
    monkeypatch.setattr(pending, "mark_seen", lambda sid: seen.append(sid) or False)
    ws = _WS(focused="open")

    asyncio.run(ACTIONS["mark_sessions_read"](ws, {"session_ids": ["a", "b", "a", "", 3]}))

    assert ws._focused_session_id == "open"
    assert updates == [("a", {"unread": False}), ("b", {"unread": False})]
    assert seen == ["a", "b"]
    assert frames == [{"type": "session_updated", "data": {"id": sid, "unread": False}} for sid in ("a", "b")]


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

    # The head only goes forward: a later turn written under this reply keeps it.
    assert db.updates == [("s1", {"status": "failed", "unread": True, "head_id": "reply-1",
                                  "head_forward_only": True})]


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


def _ended(execution_id, status_name="COMPLETED"):
    from openprogram.runtime.execution.model import ExecutionStatus
    return types.SimpleNamespace(execution_id=execution_id, session_id="s1", parent_execution_id=None,
                                 status=getattr(ExecutionStatus, status_name), status_version=9)


def test_two_turns_ending_together_leave_the_conversation_done(connections, monkeypatch):
    """Each of two turns ends while the other's execution is still running,
    so each leaves ``running``; once both executions have ended, the event
    effect of the last one settles the conversation as done."""
    from openprogram.runtime import execution as execution_package
    from openprogram.runtime.context import session_db
    from openprogram.runtime.conversation.turn_runtime import turn_writer
    from openprogram.runtime.execution.event_effects import ExecutionEventEffects
    from openprogram.runtime.execution.model import ExecutionStatus

    sockets, frames = connections
    sockets.append(_WS(focused="s1"))
    db = _DB()
    store = _Turns(("a", ExecutionStatus.RUNNING), ("b", ExecutionStatus.RUNNING))
    monkeypatch.setattr(execution_package, "default_store", lambda: store)
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    effects = ExecutionEventEffects.__new__(ExecutionEventEffects)
    effects.store = store

    turn_writer.mark_session_finished(db, "s1", own_msg_id="a_user")
    turn_writer.mark_session_finished(db, "s1", own_msg_id="b_user")
    assert db.rows["s1"]["status"] == "running"

    store.end("a")
    effects._settle_session_status(_ended("a"))
    assert db.rows["s1"]["status"] == "running"
    store.end("b")
    effects._settle_session_status(_ended("b"))

    assert db.rows["s1"]["status"] == "done"
    assert frames[-1] == {"type": "session_updated", "data": {"id": "s1", "status": "done"}}


def test_a_queued_turn_stopped_before_it_ran_does_not_leave_the_conversation_running(connections, monkeypatch):
    """A notice turn ends while a Goal continuation waits in the queue, so
    the conversation stays running; the queued turn is then stopped before
    it ever starts and writes no ending of its own."""
    from openprogram.runtime import execution as execution_package
    from openprogram.runtime.context import session_db
    from openprogram.runtime.conversation.turn_runtime import turn_writer
    from openprogram.runtime.execution.event_effects import ExecutionEventEffects
    from openprogram.runtime.execution.model import ExecutionStatus

    _sockets, _frames = connections
    db = _DB()
    store = _Turns(("notice", ExecutionStatus.RUNNING), ("goal", ExecutionStatus.QUEUED))
    monkeypatch.setattr(execution_package, "default_store", lambda: store)
    monkeypatch.setattr(session_db, "default_db", lambda: db)
    effects = ExecutionEventEffects.__new__(ExecutionEventEffects)
    effects.store = store

    turn_writer.mark_session_finished(db, "s1", own_msg_id="notice_user")
    store.end("notice")
    effects._settle_session_status(_ended("notice"))
    assert db.rows["s1"]["status"] == "running"

    store.end("goal")
    effects._settle_session_status(_ended("goal", "CANCELLED"))
    assert db.rows["s1"]["status"] == "done"


def test_a_failed_conversation_is_not_settled_as_done(monkeypatch):
    from openprogram.runtime.execution.projections.foreground import settle_session_status

    db = _DB()
    db.rows["s1"] = {"status": "failed"}
    assert not settle_session_status(db, _Turns(), "s1")
    assert db.rows["s1"]["status"] == "failed"
