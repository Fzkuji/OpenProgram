"""Which watched processes count as ended, decided without a thread."""
from __future__ import annotations

import pytest

from openprogram.host._platform import changes, process_exits


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(process_exits, "_watches", set())
    monkeypatch.setattr(process_exits, "_queued", [])
    monkeypatch.setattr(process_exits, "_waker", None)


class _Backend(process_exits._Backend):
    """Holds what ``held`` says, and ends the run after ``rounds`` waits."""

    def __init__(self, held, exits=(), rounds=1):
        self.held, self.exits, self.rounds = held, list(exits), rounds
        self.discarded = []

    def add(self, entry):
        return self.held.get(entry.pid, process_exits.HELD)

    def discard(self, entry):
        self.discarded.append(entry.pid)

    def wait(self, timeout):
        self.rounds -= 1
        if self.rounds <= 0:
            process_exits._waker = None
        ended = [e for e in process_exits._watches if e.pid in self.exits]
        self.exits = []
        return ended

    def wake(self):
        pass


def _run(backend):
    process_exits._waker = backend
    process_exits._run(backend)


def test_watching_twice_keeps_one_watch_and_starts_nothing():
    process_exits.watch_process(10, "a", "topic", "k")
    process_exits.watch_process(10, "a", "topic", "k")
    assert process_exits.watched() == 1
    assert process_exits._waker is None


def test_a_process_reported_by_the_system_ends_its_watch():
    identities = {10: "a", 11: "b"}
    process_exits.watch_process(10, "a", "topic", "first", identify=identities.get)
    process_exits.watch_process(11, "b", "topic", "second", identify=identities.get)
    with changes.listen("topic") as listener:
        _run(_Backend({}, exits=[10]))
        assert listener.wait(0) == {"topic:first"}
    assert process_exits.watched() == 1


def test_a_reused_pid_or_a_gone_process_counts_as_ended_at_once():
    process_exits.watch_process(10, "a", "topic", "reused", identify=lambda pid: "someone else")
    process_exits.watch_process(11, "b", "topic", "gone", identify=lambda pid: "b")
    backend = _Backend({11: process_exits.GONE})
    with changes.listen("topic") as listener:
        _run(backend)
        assert listener.wait(0) == {"topic:reused", "topic:gone"}
    assert backend.discarded == [10], "a held process that is someone else is let go"
    assert process_exits.watched() == 0


def test_a_process_that_cannot_be_held_is_looked_at_on_the_fallback(monkeypatch):
    alive = {"yes": True}
    process_exits.watch_process(12, "c", "topic", "looked",
                                identify=lambda pid: "c" if alive["yes"] else None)
    clock = [0.0]
    monkeypatch.setattr(process_exits.time, "monotonic", lambda: clock[0])
    backend = _Backend({12: process_exits.UNHELD}, rounds=2)
    timeouts = []
    original = backend.wait

    def wait(timeout):
        timeouts.append(timeout)
        alive["yes"] = False
        clock[0] += process_exits.FALLBACK_SECONDS
        return original(timeout)

    backend.wait = wait
    with changes.listen("topic") as listener:
        _run(backend)
        assert listener.wait(0) == {"topic:looked"}
    assert timeouts[0] == process_exits.FALLBACK_SECONDS
