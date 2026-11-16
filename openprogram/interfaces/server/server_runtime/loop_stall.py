"""Record what the Worker was doing when it stopped answering.

Every WebSocket action is served from one event loop. While that loop is
busy in a single call, every action waits together: a conversation opens
late, the file tree fills late, a send is acknowledged late, and the slow
action log names all of them without naming the cause. A thread outside
the loop asks it to answer four times a second; when an answer is a second
late, the log gets the code the loop thread is in at that moment, and how
long the wait lasted once it ends.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from typing import Callable

_log = logging.getLogger(__name__)

STALL_SECONDS = 1.0
PING_SECONDS = 0.25
_FRAMES = 12
_THREADS = 8
# Innermost calls of a thread that is waiting, not working.
_IDLE_CALLS = frozenset({"wait", "_worker", "select", "get", "accept", "readinto", "recv"})


def loop_stack(thread_id: int) -> str:
    """The innermost calls of one thread, outermost first."""
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return "(thread gone)"
    calls = traceback.extract_stack(frame)[-_FRAMES:]
    return " > ".join(f"{call.filename.rsplit('/', 1)[-1]}:{call.lineno} {call.name}"
                      for call in calls)


def busy_threads(loop_thread_id: int) -> str:
    """Where the other threads are, for a loop that waits without running.

    A loop thread that sits in its selector while its answer is late is
    not the one working: another thread holds the interpreter. Each other
    thread is named with its innermost call.
    """
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    me = threading.get_ident()
    calls = []
    for thread_id, frame in sys._current_frames().items():
        if thread_id in (loop_thread_id, me):
            continue
        code = frame.f_code
        if code.co_name in _IDLE_CALLS:
            continue
        calls.append(f"{names.get(thread_id, thread_id)} in "
                     f"{code.co_filename.rsplit('/', 1)[-1]}:{frame.f_lineno} {code.co_name}")
    return "; ".join(sorted(calls)[:_THREADS]) or "none"


class LoopStallWatch:
    """Ping one loop from a thread and log each answer that comes late."""

    def __init__(self, loop: asyncio.AbstractEventLoop, loop_thread_id: int, *,
                 clock: Callable[[], float] = time.monotonic,
                 stack: Callable[[int], str] = loop_stack,
                 others: Callable[[int], str] = busy_threads) -> None:
        self._loop = loop
        self._thread_id = loop_thread_id
        self._clock = clock
        self._stack = stack
        self._others = others
        self._asked_at: float | None = None
        self._reported = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _answer(self) -> None:
        self._asked_at = None

    def check(self) -> None:
        """One round: note a late answer, close a finished stall, ask again."""
        now = self._clock()
        asked_at = self._asked_at
        if asked_at is not None:
            if not self._reported and now - asked_at >= STALL_SECONDS:
                self._reported = True
                _log.warning(
                    "the Worker has not answered for %.1fs; it is in: %s; other threads: %s",
                    now - asked_at, self._stack(self._thread_id), self._others(self._thread_id))
            return
        if self._reported:
            self._reported = False
            _log.warning("the Worker answers again")
        self._asked_at = now
        try:
            self._loop.call_soon_threadsafe(self._answer)
        except RuntimeError:  # the loop is closed: nothing left to watch
            self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(PING_SECONDS):
            self.check()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="openprogram-loop-stall-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def watch_running_loop() -> LoopStallWatch:
    """Start watching the loop this is called from."""
    watch = LoopStallWatch(asyncio.get_running_loop(), threading.get_ident())
    watch.start()
    return watch
