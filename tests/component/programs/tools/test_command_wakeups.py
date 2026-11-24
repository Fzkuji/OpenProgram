"""A command's output, exit and controls are delivered when they happen:
the supervisor and the waiting tool call ring each other, nothing ticks."""
from __future__ import annotations

import os
import selectors
import threading
import time

import pytest

import openprogram.host.processes as processes
from openprogram.host import cancellation, sandbox
from openprogram.host.processes import channel
from openprogram.host.processes.store import ProcessStore

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX shell commands")


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(processes, "current_owner", lambda: ("conversation", None, None))
    monkeypatch.setattr(sandbox, "resolve_policy", lambda *a, **k: None)
    store = ProcessStore(tmp_path / "processes.db")
    yield store
    for record in store.list():
        if record["status"] in {"starting", "running", "stopping"}:
            processes.control(store, record["id"], "stop")


def _counting_reads(store, monkeypatch):
    reads = []
    take = store.take

    def counted(process_id):
        reads.append(time.monotonic())
        return take(process_id)

    monkeypatch.setattr(store, "take", counted)
    return reads


def _serve(bell, selector, until):
    """Run the doorbell's side of a supervisor loop until ``until()``."""
    poked = 0
    deadline = time.monotonic() + 5
    while not until() and time.monotonic() < deadline:
        for key, _ in selector.select(0.5):
            poked += bell.handle(key.fileobj)
    return poked


def test_the_doorbell_rings_subscribers_and_drops_a_wrong_token():
    bell = channel.Doorbell()
    selector = selectors.DefaultSelector()
    bell.register(selector)
    record = {"notify_port": bell.port, "notify_token": bell.token}
    try:
        stranger = channel.Watch({"notify_port": bell.port, "notify_token": "0" * 32})
        watch = channel.Watch(record)
        assert _serve(bell, selector, lambda: len(bell._subscribers) == 1 and not bell._opening) == 1
        bell.ring()
        assert watch.wait(2) is True and not watch.ended
        # The stranger was cut off without a ring.
        assert stranger.wait(2) is True and stranger.ended
        channel.poke(record)
        assert _serve(bell, selector, lambda: bool(bell._pokers)) == 1
        # Nothing rings: the wait lasts its timeout.
        started = time.monotonic()
        assert watch.wait(0.2) is False
        assert time.monotonic() - started >= 0.19
        threading.Timer(0.1, watch.wake).start()
        assert watch.wait(5) is True
    finally:
        bell.close()
    # The supervisor's end closes the connection: the waiter sees it ended.
    assert watch.wait(2) is True and watch.ended
    watch.close()
    stranger.close()


@posix_only
def test_a_commands_output_and_exit_reach_the_waiting_call_without_ticks(store, monkeypatch):
    from openprogram.programs.tools.files.bash import session

    record = processes.start("sleep 0.4; echo first; sleep 0.4; echo done", store=store)
    assert record["status"] == "running" and record["notify_port"]
    reads = _counting_reads(store, monkeypatch)
    started = time.monotonic()
    reply = session.collect(store, record["id"], 20_000, started)
    elapsed = time.monotonic() - started
    assert reply.record["status"] == "exited" and reply.output == "first\ndone\n"
    # The exit arrives when it happens, not at the yield time or a tick.
    assert 0.75 < elapsed < 1.6, elapsed
    # A read when the call starts, one per output ring, one at the exit and
    # the last read after it; a 50 ms tick would have read about 16 times.
    assert len(reads) <= 7, len(reads)


@posix_only
def test_a_stop_is_applied_at_once(store):
    record = processes.start("sleep 30", store=store)
    started = time.monotonic()
    after = processes.control(store, record["id"], "stop")
    assert after["status"] == "stopped"
    assert time.monotonic() - started < 1.5


@posix_only
def test_a_background_child_holds_the_command_until_it_ends(store):
    from openprogram.programs.tools.files.bash import session

    record = processes.start("(sleep 0.6; echo late) & echo early", store=store)
    started = time.monotonic()
    reply = session.collect(store, record["id"], 20_000, started)
    elapsed = time.monotonic() - started
    assert reply.output == "early\nlate\n"
    assert reply.record["status"] == "exited"
    assert 0.5 < elapsed < 1.5, elapsed


@posix_only
def test_a_stop_fired_for_the_turn_wakes_the_wait(store, monkeypatch):
    from openprogram.programs.tools.files.bash import session

    stop = threading.Event()
    fired = []

    def waker(callback):
        fired.append(callback)
        return lambda: fired.remove(callback)

    def check():
        if stop.is_set():
            raise cancellation.CancelledError("stopped")

    monkeypatch.setattr(cancellation, "_cancellation_waker", waker)
    monkeypatch.setattr(cancellation, "_cancellation_check", check)
    record = processes.start("sleep 30", store=store)

    def press_stop():
        stop.set()
        for callback in list(fired):
            callback()

    threading.Timer(0.3, press_stop).start()
    started = time.monotonic()
    reply = session.collect(store, record["id"], 20_000, started, stop_on_cancel=True)
    assert reply.record["status"] == "stopped"
    assert "Cancelled" in reply.note
    assert time.monotonic() - started < 2.0
    assert fired == []


@posix_only
def test_a_supervisor_killed_outright_ends_the_wait_as_unknown(store):
    import signal

    from openprogram.programs.tools.files.bash import session

    record = processes.start("sleep 30", store=store)
    threading.Timer(0.3, os.kill, (record["supervisor_pid"], signal.SIGKILL)).start()
    started = time.monotonic()
    reply = session.collect(store, record["id"], 20_000, started)
    assert reply.record["status"] == "unknown"
    assert time.monotonic() - started < 2.0
    # Its command lives on in its own group; end it.
    os.killpg(record["pid"], signal.SIGKILL)


def test_the_job_port_follower_wakes_per_notification_and_stops(monkeypatch):
    from openprogram.host._platform import process_ownership as ownership

    messages = [ownership._JOB_KEY, ownership._JOB_KEY]
    arrived = threading.Semaphore(0)
    posted, closed = [], []

    def next_message(port):
        if messages:
            return messages.pop(0)
        arrived.acquire()
        return posted[-1]

    def post(port, key):
        posted.append(key)
        arrived.release()

    monkeypatch.setattr(ownership._sys, "platform", "win32")
    monkeypatch.setattr(ownership, "_windows_job_completion_port", lambda job: 99)
    monkeypatch.setattr(ownership, "_windows_next_job_message", next_message)
    monkeypatch.setattr(ownership, "_windows_post_job_message", post)
    monkeypatch.setattr(ownership, "_windows_close_handle", closed.append)
    owner = ownership.ProcessTreeOwner()
    owner._job_handle = 7
    woken = threading.Semaphore(0)
    stop = owner.watch_members(woken.release)
    assert woken.acquire(timeout=2) and woken.acquire(timeout=2)
    stop()
    assert posted == [ownership._STOP_KEY] and closed == [99]
    owner._job_handle = None
    owner._finished = True

