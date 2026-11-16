"""Run one conversation's blocking store work off the event loop, in its lane.

A request handled on the Worker's event loop that reads or writes a
conversation waits for that conversation's lock (:mod:`session_lock`),
which a turn, a cold load or another process may hold for a while. Waiting
on the loop would stall every connection, so the work runs in a thread.
Waiting in the loop's thread pool unbounded would let one held
conversation take every thread: forty requests queued on it would leave
none for the next conversation's load.

:func:`run_for_conversation` bounds that per conversation. Each
conversation has a lane of :data:`LANE_WIDTH` threads of the lanes' pool;
a request beyond it waits on the loop (an ``asyncio`` wait, holding no
thread) for its turn in that lane, so a held conversation keeps at most
that many threads and the others keep the rest. A lane admits its
requests in the order they arrived.

The lanes of a loop share a pool of their own (:data:`POOL_SIZE`
threads), not the loop's default one: requests elsewhere that wait in
``asyncio.to_thread`` (on a conversation or anything else) can fill the
default pool without holding up a lane, and lanes never take the default
pool's threads. The pool lives while the loop has lane work and is shut
down when the last lane empties, so an idle loop, a closed one or a forked
child keeps no threads of it.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

#: Threads one conversation may occupy at once. Its store work is
#: serialised by its lock, so a wider lane would only park threads.
LANE_WIDTH = 2

#: Threads all lanes of a loop share. Each held conversation keeps at most
#: :data:`LANE_WIDTH` of them, so this many conversations would have to be
#: held at once, each with requests waiting, before another one waits.
POOL_SIZE = 64

# Keyed by the loop. Touched only on the loop's own thread, or after the
# loop closed.
_pools: dict[int, ThreadPoolExecutor] = {}


def _executor(loop: asyncio.AbstractEventLoop) -> ThreadPoolExecutor:
    pool = _pools.get(id(loop))
    if pool is None:
        pool = _pools[id(loop)] = ThreadPoolExecutor(
            max_workers=POOL_SIZE, thread_name_prefix="conversation-work")
    return pool


def _drop_lane(key: tuple[int, str]) -> None:
    """Forget an empty lane; the loop's pool ends with its last lane."""
    _lanes.pop(key, None)
    loop_id = key[0]
    if any(other[0] == loop_id for other in tuple(_lanes)):
        return
    pool = _pools.pop(loop_id, None)
    if pool is not None:
        pool.shutdown(wait=False)


def _forget_after_fork() -> None:
    # The child has none of the parent's threads; its first request starts
    # a pool of its own.
    _pools.clear()
    _lanes.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_forget_after_fork)


class _Lane:
    """One conversation's lane on one loop: threads in use and who waits."""

    __slots__ = ("busy", "waiting")

    def __init__(self) -> None:
        self.busy = 0
        self.waiting: deque[asyncio.Future] = deque()


# Keyed by (loop, conversation). Touched only on the loop's own thread.
_lanes: dict[tuple[int, str], _Lane] = {}


def _holds_a_conversation_lock() -> bool:
    from .files import session_lock

    with session_lock._registry_lock:
        locks = tuple(session_lock._registry.values())
    return any(lock.held_by_current_thread() for lock in locks)


def _release(loop: asyncio.AbstractEventLoop, key: tuple[int, str]) -> None:
    lane = _lanes.get(key)
    if lane is None:
        return
    lane.busy -= 1
    while lane.waiting:
        nxt = lane.waiting.popleft()
        if not nxt.done():
            lane.busy += 1
            nxt.set_result(None)
            return
    if lane.busy <= 0 and not lane.waiting:
        _drop_lane(key)


async def run_for_conversation(session_id: str | None, fn: Callable[..., T],
                               /, *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` in a thread of ``session_id``'s lane.

    Awaited on the event loop. The call keeps the caller's context
    variables, as :func:`asyncio.to_thread` does. Cancelling the awaiting
    task gives the lane back once the thread is done (or at once when no
    thread took the work yet), so a lane never runs more than
    :data:`LANE_WIDTH` threads.

    A caller whose thread already holds a conversation's lock runs ``fn``
    in place: the lock is reentrant only in its own thread, and handing the
    work to another one would have that thread wait for this one."""
    if _holds_a_conversation_lock():
        return fn(*args, **kwargs)
    loop = asyncio.get_running_loop()
    key = (id(loop), str(session_id or ""))
    lane = _lanes.get(key)
    if lane is None:
        lane = _lanes[key] = _Lane()
    if lane.busy >= LANE_WIDTH or lane.waiting:
        turn = loop.create_future()
        lane.waiting.append(turn)
        try:
            await turn
        except BaseException:
            if turn.done() and not turn.cancelled():
                # The lane was handed over just as this request gave up.
                _release(loop, key)
            else:
                try:
                    lane.waiting.remove(turn)
                except ValueError:
                    pass
                if not lane.busy and not lane.waiting:
                    _drop_lane(key)
            raise
    else:
        lane.busy += 1
    context = contextvars.copy_context()
    call = functools.partial(fn, *args, **kwargs)
    # The lane is given back once the thread is done with the work, or, when
    # the request is cancelled before a thread took it, at once.
    state = {"phase": "queued"}
    guard = threading.Lock()

    def give_back() -> None:
        try:
            loop.call_soon_threadsafe(_release, loop, key)
        except RuntimeError:
            # The loop is closed; nothing waits on its lanes any more.
            _drop_lane(key)

    def run() -> T:
        with guard:
            if state["phase"] == "abandoned":
                raise asyncio.CancelledError()
            state["phase"] = "running"
        try:
            return context.run(call)
        finally:
            give_back()

    try:
        future = loop.run_in_executor(_executor(loop), run)
    except BaseException:
        _release(loop, key)
        raise
    try:
        return await future
    except asyncio.CancelledError:
        with guard:
            queued = state["phase"] == "queued"
            if queued:
                state["phase"] = "abandoned"
        if queued:
            _release(loop, key)
        raise


__all__ = ["LANE_WIDTH", "POOL_SIZE", "run_for_conversation"]
