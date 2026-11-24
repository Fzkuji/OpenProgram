"""Say where a running Worker spends its time, when asked to.

A Worker that is busy while nobody uses it cannot be measured from outside:
its own log names slow actions and stalls, not steady background work. An
empty file ``logs/sample-threads`` under the state folder asks for a sample.
The Worker then looks at every thread fifty times a second for half a
minute and writes the calls it found them in, most frequent first, to
``logs/thread-sample.txt``. Threads that are only waiting are left out.

The request is seen by its file event (``host/_platform/changes.watch``);
only where file events cannot be had is the folder looked at every
:data:`POLL_SECONDS`.
"""
from __future__ import annotations

import collections
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable

_log = logging.getLogger(__name__)

REQUEST_NAME = "sample-threads"
RESULT_NAME = "thread-sample.txt"
SAMPLE_SECONDS = 30.0
SAMPLE_INTERVAL = 0.02
#: Only where file events cannot be had.
POLL_SECONDS = 2.0
#: A look even without an event, in case one was lost.
RESYNC_SECONDS = 300.0
_LISTED = 40
_OUTER_CALLS = 2
_INNER_CALLS = 4
# Innermost calls of a thread that is waiting, not working.
_IDLE_CALLS = frozenset({
    "wait", "_worker", "select", "get", "accept", "readinto", "recv", "sleep", "join",
    "_wait_for_tstate_lock", "read", "_do_waitpid",
})


def _own_calls(frame) -> str | None:
    """The product's calls on one thread's stack: where it started and where it is."""
    calls = []
    while frame is not None:
        code = frame.f_code
        if "openprogram" in code.co_filename:
            calls.append(f"{code.co_filename.rsplit('/', 1)[-1]}:{code.co_name}")
        frame = frame.f_back
    if not calls:
        return None
    calls.reverse()
    if len(calls) <= _OUTER_CALLS + _INNER_CALLS:
        return " > ".join(calls)
    return " > ".join([*calls[:_OUTER_CALLS], "…", *calls[-_INNER_CALLS:]])


def _cpu_reader() -> Callable[[int], float | None]:
    """How to read the processor time one thread has used, on this system.

    Where a thread stands at each look says where it waits as much as where
    it works; its processor time says which threads did the work.
    """
    clock_of = getattr(time, "pthread_getcpuclockid", None)
    if clock_of is not None:
        def read(ident: int) -> float | None:
            try:
                return time.clock_gettime(clock_of(ident))
            except (OSError, ValueError):
                return None
        return read
    if sys.platform != "darwin":
        return lambda _ident: None
    import ctypes
    import ctypes.util

    class _Info(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int) for name in (
            "user_seconds", "user_microseconds", "system_seconds", "system_microseconds",
            "cpu_usage", "policy", "run_state", "flags", "suspend_count", "sleep_time")]

    try:
        lib = ctypes.CDLL(ctypes.util.find_library("c"))
        lib.pthread_mach_thread_np.argtypes = [ctypes.c_void_p]
        lib.pthread_mach_thread_np.restype = ctypes.c_uint
        lib.thread_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p,
                                    ctypes.POINTER(ctypes.c_uint)]
        lib.thread_info.restype = ctypes.c_int
    except (OSError, AttributeError, TypeError):
        return lambda _ident: None

    def read(ident: int) -> float | None:
        info = _Info()
        count = ctypes.c_uint(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_int))
        # 3 is THREAD_BASIC_INFO.
        if lib.thread_info(lib.pthread_mach_thread_np(ident), 3, ctypes.byref(info),
                           ctypes.byref(count)) != 0:
            return None
        return (info.user_seconds + info.system_seconds
                + (info.user_microseconds + info.system_microseconds) / 1e6)
    return read


def take_sample(seconds: float = SAMPLE_SECONDS, interval: float = SAMPLE_INTERVAL, *,
                frames: Callable[[], dict] = sys._current_frames,
                sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic,
                cpu: Callable[[int], float | None] | None = None,
                names: Callable[[], dict[int, str]] | None = None) -> str:
    """Sample every other thread for ``seconds`` and describe what they did.

    The sample ends by the clock: a busy Worker gives this thread fewer
    turns, so a fixed number of looks would run for minutes.
    """
    me = threading.get_ident()
    found: collections.Counter[str] = collections.Counter()
    rounds = 0
    cpu = cpu or _cpu_reader()
    names = names or (lambda: {t.ident: t.name for t in threading.enumerate() if t.ident})
    # Processor time of each thread: at its first look and at its latest.
    # A thread that starts during the sample is counted from its start.
    first: dict[int, float] = {}
    latest: dict[int, float] = {}
    named: dict[int, str] = {}
    where: dict[int, collections.Counter[str]] = {}
    until = clock() + seconds
    while rounds == 0 or clock() < until:
        rounds += 1
        named.update(names())
        for thread_id, frame in frames().items():
            if thread_id == me:
                continue
            used = cpu(thread_id)
            if used is not None:
                first.setdefault(thread_id, used if rounds == 1 else 0.0)
                latest[thread_id] = used
            if frame.f_code.co_name in _IDLE_CALLS:
                continue
            calls = _own_calls(frame)
            if calls:
                found[calls] += 1
                where.setdefault(thread_id, collections.Counter())[calls] += 1
        sleep(interval)
    lines = [f"{rounds} looks at every thread over {seconds:g} s; each line is the share of looks "
             f"a thread was there. A thread that sleeps or waits on the network inside one call "
             f"is listed at that call too."]
    for calls, count in found.most_common(_LISTED):
        lines.append(f"{100 * count / rounds:5.1f}%  {calls}")
    if not found:
        lines.append("no thread was working in any look")
    worked = sorted(((latest[i] - first[i], i) for i in latest), reverse=True)
    worked = [(used, i) for used, i in worked if used >= 0.01]
    if worked:
        lines.append("")
        lines.append(f"Processor time by thread, {sum(used for used, _ in worked):.2f} s in all; "
                     f"with the call each was seen in most:")
        for used, ident in worked[:_LISTED]:
            seen = where.get(ident)
            lines.append(f"{used:6.2f} s  {named.get(ident, '?')}"
                         + (f"  {seen.most_common(1)[0][0]}" if seen else ""))
    return "\n".join(lines) + "\n"


class SampleRequests:
    """Take a sample each time the request file appears."""

    def __init__(self, logs: Path, *, sample: Callable[[], str] = take_sample,
                 request_name: str = REQUEST_NAME, result_name: str = RESULT_NAME) -> None:
        self._request = logs / request_name
        self._result = logs / result_name
        self._sample = sample
        self._stop = threading.Event()
        #: Signalled when the request file is written; named after it.
        self.topic = f"sample_request.{request_name}"
        self._listener = None

    def check(self) -> bool:
        """Answer a waiting request; True when one was answered."""
        try:
            self._request.unlink()
        except OSError:
            return False
        try:
            self._result.write_text(self._sample(), encoding="utf-8")
        except OSError:
            _log.warning("the sample could not be written to %s", self._result, exc_info=True)
        return True

    def _run(self) -> None:
        from openprogram.host._platform import changes

        # A request left before the Worker started is answered at once, so
        # the sample covers what it does while it starts.
        self.check()
        while not self._stop.is_set():
            changes.wait_for(self._listener, resync=RESYNC_SECONDS, poll=POLL_SECONDS)
            if not self._stop.is_set():
                self.check()

    def start(self) -> None:
        from openprogram.host._platform import changes

        changes.watch(self._request.parent, self.topic, names={self._request.name})
        self._listener = changes.listen(self.topic)
        threading.Thread(target=self._run, name="openprogram-thread-sample", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()


def watch_sample_requests() -> SampleRequests:
    """Start answering sample requests under this profile's ``logs`` folder."""
    from openprogram.state.paths import get_state_dir

    logs = get_state_dir() / "logs"
    requests = SampleRequests(logs)
    requests.start()
    from openprogram.interfaces.server.server_runtime import memory_sample
    # The memory sample is one look, answered by the same kind of watcher;
    # it stops with the Worker (a daemon thread), like this one.
    SampleRequests(logs, sample=memory_sample.take_sample,
                   request_name=memory_sample.REQUEST_NAME,
                   result_name=memory_sample.RESULT_NAME).start()
    return requests
