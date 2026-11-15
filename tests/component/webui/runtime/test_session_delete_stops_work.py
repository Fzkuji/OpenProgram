"""Deleting a session stops the work still running in it.

A deleted conversation must not keep a turn writing into its removed repo,
approval prompts open in other tabs, or queued messages waiting to start.
"""
from __future__ import annotations

import asyncio
import json
import types

import openprogram.runtime.execution as execution_pkg
import openprogram.runtime.execution.waits as waits_module
import openprogram.runtime.execution.run_control.cancellation as cancellation
import openprogram.runtime.conversation.inbox as inbox


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _execution(execution_id, parent=None):
    return types.SimpleNamespace(execution_id=execution_id, parent_execution_id=parent)


def test_stop_session_work_cancels_roots_waits_slot_and_inbox(monkeypatch):
    from openprogram.interfaces.server import server as _s

    session_id = "deleted-running"
    cancelled: list[tuple[str, str]] = []
    waits_cancelled: list[str] = []
    cleared: list[tuple[str, str]] = []
    events: list[dict] = []

    store = types.SimpleNamespace(
        list_nonterminal=lambda session_id: [
            _execution("root"), _execution("child", parent="root"),
        ],
    )

    class _Waits:
        def __init__(self, _store):
            pass

        def list_open(self, *, session_id):
            return [types.SimpleNamespace(execution_id="orphan-wait")]

        def cancel_execution(self, execution_id):
            waits_cancelled.append(execution_id)

    monkeypatch.setattr(execution_pkg, "default_store", lambda: store)
    monkeypatch.setattr(waits_module, "DurableWaitStore", _Waits)
    monkeypatch.setattr(
        cancellation, "_cancel_canonical_execution",
        lambda execution, *, reason_code: cancelled.append(
            (execution.execution_id, reason_code)),
    )
    monkeypatch.setattr(
        inbox, "clear",
        lambda sid, *, reason: cleared.append((sid, reason)) or 0,
    )
    monkeypatch.setattr(
        _s, "_emit_running_task_event", lambda sid, **kw: events.append(kw),
    )
    with _s._running_tasks_lock:
        _s._running_tasks[session_id] = {"msg_id": "m1", "execution_id": "other"}

    result = _s._stop_session_work(session_id, reason="deleted")

    assert result == ["root"]
    assert cancelled == [("root", "cancel.session")]
    assert waits_cancelled == ["orphan-wait"]
    assert cleared == [(session_id, "deleted")]
    with _s._running_tasks_lock:
        assert session_id not in _s._running_tasks
    assert not _s._has_active_runtime(session_id)
    assert events[-1] == {"cleared_msg_id": "m1", "cleared_execution_id": "other"}


def test_delete_session_stops_work_before_removing_the_repo(monkeypatch):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.session import handle_delete_session
    import openprogram.runtime.context.session_db as session_db

    order: list[str] = []
    broadcasts: list[dict] = []
    monkeypatch.setattr(
        _s, "_stop_session_work",
        lambda sid, *, reason: order.append(f"stop:{sid}") or [],
    )
    monkeypatch.setattr(
        session_db, "default_db",
        lambda: types.SimpleNamespace(
            delete_session=lambda sid: order.append(f"delete:{sid}"),
        ),
    )
    monkeypatch.setattr(_s, "_broadcast", lambda msg: broadcasts.append(json.loads(msg)))

    asyncio.run(handle_delete_session(_FakeWS(), {"session_id": "s1"}))

    assert order == ["stop:s1", "delete:s1"]
    assert broadcasts == [{"type": "session_deleted", "session_id": "s1"}]


def test_delete_session_failure_resends_the_real_list(monkeypatch):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.session import handle_delete_session
    import openprogram.interfaces.server.ws_actions.session.lifecycle as lifecycle
    import openprogram.runtime.context.session_db as session_db

    listed: list[object] = []
    broadcasts: list[dict] = []
    monkeypatch.setattr(_s, "_stop_session_work", lambda sid, *, reason: [])

    def _fail(_sid):
        raise OSError("repo busy")

    monkeypatch.setattr(
        session_db, "default_db",
        lambda: types.SimpleNamespace(delete_session=_fail),
    )
    monkeypatch.setattr(_s, "_broadcast", lambda msg: broadcasts.append(json.loads(msg)))

    async def _list(ws, cmd):
        listed.append(ws)

    monkeypatch.setattr(lifecycle, "handle_list_sessions", _list)
    ws = _FakeWS()

    asyncio.run(handle_delete_session(ws, {"session_id": "s1"}))

    assert listed == [ws]
    assert broadcasts == []


def test_clear_sessions_stops_every_session(monkeypatch):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.session import handle_clear_sessions
    import openprogram.interfaces.server.ws_actions.session.lifecycle as lifecycle
    import openprogram.runtime.context.session_db as session_db

    stopped: list[str] = []
    deleted: list[str] = []
    monkeypatch.setattr(
        _s, "_stop_session_work",
        lambda sid, *, reason: stopped.append(sid) or [],
    )
    monkeypatch.setattr(
        session_db, "default_db",
        lambda: types.SimpleNamespace(
            list_sessions=lambda **kw: [{"id": "a"}, {"id": "b"}],
            delete_session=lambda sid: deleted.append(sid),
        ),
    )

    async def _list(ws, cmd):
        return None

    monkeypatch.setattr(lifecycle, "handle_list_sessions", _list)
    with _s._sessions_lock:
        saved = dict(_s._sessions)
        _s._sessions.clear()
    try:
        asyncio.run(handle_clear_sessions(_FakeWS(), {}))
    finally:
        with _s._sessions_lock:
            _s._sessions.update(saved)

    assert sorted(stopped) == ["a", "b"]
    assert sorted(deleted) == ["a", "b"]
