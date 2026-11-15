"""Reading the Goal is on every model request's path, so it must see
another process's write without re-reading the session's whole history."""
from __future__ import annotations

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixtures, bound at module level so pytest collects them here.
runtime = _test_chat_goal.runtime


def test_goal_read_sees_another_store_without_rebuilding_history(runtime, monkeypatch, tmp_path):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.state.store.session.memory_index import SessionMemoryIndex
    goals, _chat, _store = runtime
    db = goals._db()
    assert goals.load_goal("goal-chat")["text"] == "finish the task"
    db.get_session("goal-chat")

    rebuilds = []
    original = SessionMemoryIndex.rebuild
    def counted(self, *args, **kwargs):
        rebuilds.append(1)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SessionMemoryIndex, "rebuild", counted)

    for _ in range(5):
        assert goals.load_goal("goal-chat")["text"] == "finish the task"
    assert rebuilds == [], "a Goal read must not re-read the history"

    # Another process (its own store on the same directory) changes the Goal.
    other = SessionDB(tmp_path / "sessions")
    changed = dict(other.get_session("goal-chat")["extra_meta"]["goal"])
    changed["text"] = "ship the fix"
    other.update_session("goal-chat", goal=changed)
    rebuilds.clear()  # the other store's own first load
    assert goals.load_goal("goal-chat")["text"] == "ship the fix"
    assert rebuilds == []
    other.close()


def test_missing_session_has_no_goal(runtime):
    goals, _chat, _store = runtime
    assert goals.load_goal("no-such-session") is None
    assert goals._db().read_disk_meta("../escape") is None
