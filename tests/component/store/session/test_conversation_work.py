"""A conversation's blocking work runs in its own lane of the loop's pool.

Requests waiting on one held conversation take at most ``LANE_WIDTH``
threads, in the order they came; the others queue on the event loop
holding no thread, so another conversation's work starts at once.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time

import pytest

from openprogram.state.store.session import conversation_work
from openprogram.state.store.session.conversation_work import LANE_WIDTH, run_for_conversation


def test_one_conversation_takes_at_most_its_lane_and_keeps_the_order():
    gates = [threading.Event() for _ in range(12)]
    running = 0
    peak = 0
    started: list[int] = []
    guard = threading.Lock()

    def work(index: int) -> int:
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
            started.append(index)
        gates[index].wait(5)
        with guard:
            running -= 1
        return index

    async def scenario():
        tasks = [asyncio.create_task(run_for_conversation("held", work, i)) for i in range(12)]
        await asyncio.sleep(0.1)
        busy_then = running
        other_start = time.monotonic()
        other = await run_for_conversation("other", lambda: "free")
        other_took = time.monotonic() - other_start
        for gate in gates:  # one finishes, the next in line starts
            gate.set()
            await asyncio.sleep(0.02)
        return busy_then, other, other_took, await asyncio.gather(*tasks)

    busy_then, other, other_took, results = asyncio.run(scenario())
    assert busy_then == LANE_WIDTH
    assert peak == LANE_WIDTH
    assert other == "free" and other_took < 0.5
    assert results == list(range(12))
    assert sorted(started[:LANE_WIDTH]) == list(range(LANE_WIDTH))
    assert started[LANE_WIDTH:] == list(range(LANE_WIDTH, 12))
    assert conversation_work._lanes == {}
    # The lanes' pool ended with its last lane: an idle loop keeps no thread.
    assert conversation_work._pools == {}


def test_a_cancelled_request_frees_its_place_only_once_its_thread_is_done():
    first_release = threading.Event()
    ran: list[str] = []

    def blocking(name: str) -> str:
        ran.append(name)
        if name.startswith("first"):
            first_release.wait(5)
        return name

    async def scenario():
        firsts = [asyncio.create_task(run_for_conversation("c", blocking, f"first-{i}"))
                  for i in range(LANE_WIDTH)]
        queued = asyncio.create_task(run_for_conversation("c", blocking, "queued"))
        last = asyncio.create_task(run_for_conversation("c", blocking, "last"))
        await asyncio.sleep(0.05)
        queued.cancel()
        firsts[0].cancel()
        await asyncio.sleep(0.1)
        # The cancelled first one still runs in its thread: the lane is full.
        assert ran == [f"first-{i}" for i in range(LANE_WIDTH)]
        first_release.set()
        assert await asyncio.wait_for(last, 5) == "last"
        await asyncio.gather(*firsts, return_exceptions=True)
        return queued.cancelled()

    assert asyncio.run(scenario())
    assert "queued" not in ran
    assert conversation_work._lanes == {}


def test_work_keeps_the_callers_context():
    var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="unset")

    async def scenario():
        var.set("caller")
        return await run_for_conversation("c", var.get)

    assert asyncio.run(scenario()) == "caller"


def test_a_thread_holding_a_conversation_lock_runs_the_work_in_place(tmp_path, monkeypatch):
    """Handing the work to another thread would have that thread wait for
    the lock this one holds."""
    from openprogram.state.store.session.files.session_lock import conversation_lock

    lock = conversation_lock("mine", tmp_path)

    async def scenario():
        with lock:
            return await run_for_conversation("other", threading.get_ident)

    assert asyncio.run(scenario()) == threading.get_ident()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork only")
def test_a_forked_child_still_runs_conversation_work():
    """A child inherits none of the parent's threads; work handed to a
    pool the parent started would never run."""
    asyncio.run(run_for_conversation("c", lambda: None))
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child reports through the pipe
        try:
            answer = asyncio.run(asyncio.wait_for(run_for_conversation("c", lambda: "ran"), 5))
            os.write(write, answer.encode())
        finally:
            os._exit(0)
    os.close(write)
    os.waitpid(pid, 0)
    with os.fdopen(read) as pipe:
        assert pipe.read() == "ran"


def test_a_full_default_pool_does_not_hold_up_a_lane():
    """Requests elsewhere waiting in ``asyncio.to_thread`` can fill the
    loop's default pool; a conversation's lane still starts at once, and
    lanes leave the default pool alone."""
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    lane_threads: list[str] = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
        stuck = [asyncio.create_task(asyncio.to_thread(release.wait, 5)) for _ in range(4)]
        await asyncio.sleep(0.05)
        start = time.monotonic()
        answer = await asyncio.wait_for(run_for_conversation(
            "other", lambda: lane_threads.append(threading.current_thread().name) or "ran"), 2)
        took = time.monotonic() - start
        release.set()
        await asyncio.gather(*stuck)
        return answer, took

    answer, took = asyncio.run(scenario())
    assert answer == "ran" and took < 0.5
    assert lane_threads[0].startswith("conversation-work")
    assert conversation_work._pools == {}
