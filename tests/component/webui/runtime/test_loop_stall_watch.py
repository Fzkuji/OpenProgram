"""A Worker that stops answering leaves the code it was in, once per stall."""
from __future__ import annotations

import logging

from openprogram.interfaces.server.server_runtime import loop_stall


class _Loop:
    def __init__(self):
        self.pending = []

    def call_soon_threadsafe(self, callback):
        self.pending.append(callback)

    def answer(self):
        for callback in self.pending:
            callback()
        self.pending.clear()


def _watch(loop, now):
    return loop_stall.LoopStallWatch(
        loop, 7, clock=lambda: now[0], stack=lambda thread: f"busy.py:3 work (thread {thread})",
        others=lambda thread: "writer in store.py:9 fold")


def test_a_late_answer_is_logged_once_with_where_the_loop_is(caplog):
    loop, now = _Loop(), [0.0]
    watch = _watch(loop, now)
    with caplog.at_level(logging.WARNING, logger=loop_stall.__name__):
        watch.check()
        now[0] = 0.5
        watch.check()
        assert not caplog.records, "half a second is not a stall"
        now[0] = 1.25
        watch.check()
        now[0] = 4.0
        watch.check()
        assert [r.getMessage() for r in caplog.records] == [
            "the Worker has not answered for 1.2s; it is in: busy.py:3 work (thread 7); "
            "other threads: writer in store.py:9 fold",
        ]
        loop.answer()
        watch.check()
        assert caplog.records[-1].getMessage() == "the Worker answers again"
        assert len(loop.pending) == 1, "the next question is asked in the same round"


def test_a_loop_that_answers_in_time_logs_nothing(caplog):
    loop, now = _Loop(), [0.0]
    watch = _watch(loop, now)
    with caplog.at_level(logging.WARNING, logger=loop_stall.__name__):
        for step in range(40):
            now[0] = step * 0.25
            watch.check()
            loop.answer()
    assert not caplog.records


def test_the_stack_names_the_calls_of_the_thread_asked_for():
    import threading

    assert "test_the_stack_names_the_calls" in loop_stall.loop_stack(threading.get_ident())
    assert loop_stall.loop_stack(-1) == "(thread gone)"


def test_other_threads_are_named_only_while_they_work():
    import threading

    started, release, done = threading.Event(), threading.Event(), []

    def fold():
        started.set()
        while not done:
            pass

    waiting = threading.Thread(target=release.wait, name="sleeper")
    working = threading.Thread(target=fold, name="folder")
    waiting.start()
    working.start()
    started.wait()
    try:
        seen = loop_stall.busy_threads(-1)
    finally:
        done.append(True)
        release.set()
        waiting.join()
        working.join()
    assert "folder in test_loop_stall_watch.py" in seen
    assert "sleeper" not in seen
