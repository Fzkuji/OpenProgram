"""Side work a finished turn starts beside itself is held in a process that
exits when its turn ends.

A title, a branch name and topic sorting are each a model request on a
thread of their own. ``openprogram --print`` holds them (``side_work.held``)
so none is cut off mid-request when the command's process shuts down.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.conversation import side_work, topics
from openprogram.runtime.conversation.turn_runtime import titles

SID = "side-work"


class _Recorder:
    """Stands in for ``threading``: records each thread asked for."""

    def __init__(self):
        self.started: list[str] = []

        recorder = self

        class Thread:
            def __init__(self, target=None, args=(), name=None, daemon=None):
                self.name = name or getattr(target, "__name__", "thread")

            def start(self):
                recorder.started.append(self.name)

        self.Thread = Thread


@pytest.fixture
def db(tmp_path):
    store = SessionDB(tmp_path / "sessions")
    store.create_session(SID, "main", title="")
    store.append_message(SID, {"id": "u0", "role": "user", "content": "Rename my photos by date",
                               "timestamp": 1.0, "predecessor": None})
    store.append_message(SID, {"id": "a0", "role": "assistant", "content": "Renamed 42 files.",
                               "timestamp": 2.0, "predecessor": "u0"})
    store.set_head(SID, "a0")
    return store


def test_held_side_work_is_allowed_again_after_the_block():
    assert side_work.allowed()
    with side_work.held():
        assert not side_work.allowed()
        with side_work.held():
            assert not side_work.allowed()
        assert not side_work.allowed()
    assert side_work.allowed()


def test_a_held_title_keeps_the_cut_title_and_starts_no_request(db, monkeypatch):
    threads = _Recorder()
    monkeypatch.setattr(titles, "threading", SimpleNamespace(Thread=threads.Thread))

    with side_work.held():
        titles._maybe_auto_title(db, SID, db.get_session(SID), "Rename my photos by date",
                                 "Renamed 42 files.")

    assert threads.started == []
    assert db.get_session(SID)["title"] == "Rename my photos by date"

    titles._maybe_auto_title(db, SID, {"extra_meta": {}}, "Rename my photos by date",
                             "Renamed 42 files.")
    assert len(threads.started) == 1


def test_a_held_branch_name_starts_no_request(monkeypatch):
    threads = _Recorder()
    monkeypatch.setattr(titles, "threading", SimpleNamespace(Thread=threads.Thread))
    store = SimpleNamespace(get_branch_meta=lambda *_: {}, bump_branch_turns=lambda *_: 1)

    with side_work.held():
        titles.maybe_auto_name_branch(store, SID, "a0")
    assert threads.started == []

    titles.maybe_auto_name_branch(store, SID, "a0")
    assert len(threads.started) == 1


def test_held_topic_sorting_starts_nothing_and_leaves_the_session_free(monkeypatch):
    threads = _Recorder()
    monkeypatch.setattr(topics, "threading", SimpleNamespace(Thread=threads.Thread,
                                                             Lock=topics.threading.Lock))
    monkeypatch.setattr(topics, "_running", set())

    with side_work.held():
        topics.sort_after_turn(SID)
    assert threads.started == []
    assert SID not in topics._running

    topics.sort_after_turn(SID)
    assert threads.started == ["topic-sorter"]


def test_a_held_turn_hands_nothing_to_memory(monkeypatch):
    """Past the threshold the hand-off is a model run inside the turn; a
    ``--print`` command leaves it to the idle watcher."""
    from openprogram.runtime.conversation import turn_runtime

    offered: list[str] = []

    class Backend:
        def write(self, *, session_id):
            offered.append(session_id)

    monkeypatch.setattr("openprogram.state.memory.get_backend", Backend)

    with side_work.held():
        turn_runtime._memory_write(SID)
    assert offered == []

    turn_runtime._memory_write(SID)
    assert offered == [SID]
