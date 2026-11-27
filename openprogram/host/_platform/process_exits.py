"""Learning that a process ended the moment it ends.

A process that has to show it is alive (a command's supervisor, the update
controller, a migrating Worker) would otherwise write a heartbeat for its
whole life, and whoever reads it would wait out a staleness deadline to
notice it stopped. Here the operating system says so instead:

* macOS and BSD: ``kqueue`` with ``EVFILT_PROC``/``NOTE_EXIT``.
* Linux: a ``pidfd`` (``os.pidfd_open``), readable once the process exits,
  in one ``epoll``.
* Windows: the process handle, signalled on exit, in one
  ``WaitForMultipleObjects`` (up to 63 at once beside its wake event).

One thread serves every watched process. Where none of these can be had (an
old Linux kernel, more than 63 processes on Windows, a handle the Worker may
not open) the process is looked at every ``FALLBACK_SECONDS`` instead.

A PID can be reused. The process is first held by the operating system (a
registered event, a pidfd, a handle), and only then is its birth identity
compared with the one recorded: a process that already differs is reported
as ended at once, and from then on the held process cannot be confused with
another.

An exit is announced through :func:`announce_exits`, which ``changes``
fills with its :func:`signal(topic, key)
<openprogram.host._platform.changes.signal>`. As with file events, the
thread starts only when the Worker calls :func:`start` (through
``changes.start_watching``); in the CLI and in tests a watch is only kept.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import select
import sys
import threading
import time
from typing import Callable

_log = logging.getLogger(__name__)

#: How often a process is looked at where the operating system cannot say
#: it ended.
FALLBACK_SECONDS = 10.0


@dataclass(frozen=True)
class _Watch:
    pid: int
    identity: str | None
    topic: str
    key: str
    identify: Callable[[int], str | None]


_lock = threading.Lock()
_watches: set[_Watch] = set()
_queued: list[_Watch] = []
_thread: threading.Thread | None = None
_waker: "_Backend | None" = None
_running = False
_announce: Callable[[str, str], None] | None = None


def _start_token(pid: int) -> str | None:
    from .processes import process_start_token
    return process_start_token(pid)


def watch_process(pid: int, identity: str | None, topic: str, key: str = "", *,
                  identify: Callable[[int], str | None] | None = None) -> None:
    """Signal ``topic`` (with ``key``) once process ``pid`` ends.

    ``identity`` is the birth identity recorded for it, as ``identify(pid)``
    returns it (``process_start_token`` by default); a process with another
    identity under that PID counts as ended. ``None`` skips the check.
    Watching the same process for the same topic and key again changes
    nothing.
    """
    entry = _Watch(int(pid), identity, topic, key, identify or _start_token)
    with _lock:
        if entry in _watches:
            return
        _watches.add(entry)
        _queued.append(entry)
        waker = _waker
    if waker is not None:
        waker.wake()


def watched() -> int:
    """How many processes are watched now."""
    with _lock:
        return len(_watches)


def start() -> None:
    """Begin following every watched process, before or after. The Worker
    calls this once, through ``changes.start_watching``."""
    global _thread, _waker, _running
    with _lock:
        if _running:
            return
        _running = True
        backend = _backend()
        _waker = backend
        _queued[:] = list(_watches)
        _thread = threading.Thread(target=_run, args=(backend,), name="process-exits", daemon=True)
        thread = _thread
    thread.start()


def stop() -> None:
    """End the thread (the Worker is stopping); the watches are kept."""
    global _thread, _waker, _running
    with _lock:
        thread, backend = _thread, _waker
        _thread, _waker, _running = None, None, False
    if backend is not None:
        backend.wake()
    if thread is not None:
        thread.join(timeout=2.0)


def announce_exits(announce: Callable[[str, str], None]) -> None:
    """Have ``announce(topic, key)`` say that a watched process ended.
    ``changes`` installs its :func:`signal <openprogram.host._platform.changes.signal>`
    when it loads; process exits are one of the sources it gathers."""
    global _announce
    _announce = announce


def _ended(entry: _Watch) -> None:
    with _lock:
        _watches.discard(entry)
    if _announce is not None:
        _announce(entry.topic, entry.key)


def _same(entry: _Watch) -> bool:
    """Whether ``entry``'s process still runs under its recorded identity."""
    if entry.identity is None:
        from .processes import process_alive
        return process_alive(entry.pid)
    try:
        return entry.identify(entry.pid) == entry.identity
    except Exception:  # noqa: BLE001 - an identity that cannot be read is not a live match
        _log.debug("identity of %s not read", entry.pid, exc_info=True)
        return False


def _run(backend: "_Backend") -> None:
    fallback: set[_Watch] = set()
    next_look = time.monotonic() + FALLBACK_SECONDS
    try:
        while True:
            with _lock:
                if _waker is not backend:
                    return
                queued, _queued[:] = list(_queued), []
            for entry in queued:
                held = backend.add(entry)
                if held == GONE:
                    _ended(entry)
                elif not _same(entry):
                    backend.discard(entry)
                    _ended(entry)
                elif held == UNHELD:
                    fallback.add(entry)
            timeout = None
            if fallback:
                now = time.monotonic()
                if now >= next_look:
                    for entry in [e for e in fallback if not _same(e)]:
                        fallback.discard(entry)
                        _ended(entry)
                    next_look = now + FALLBACK_SECONDS
                timeout = max(0.0, next_look - time.monotonic())
            for entry in backend.wait(timeout):
                _ended(entry)
    except Exception:  # noqa: BLE001 - a broken watcher leaves processes to the fallback look
        _log.warning("process exit events failed; ended processes are no longer announced", exc_info=True)
    finally:
        backend.close()


#: What :meth:`_Backend.add` found: the process is held, already gone, or
#: cannot be held here and is looked at every ``FALLBACK_SECONDS``.
HELD, GONE, UNHELD = "held", "gone", "unheld"


class _Backend:
    """How one platform holds processes and waits for any to end."""

    def add(self, entry: _Watch) -> str:
        """Hold the process: :data:`HELD`, :data:`GONE` or :data:`UNHELD`."""
        return UNHELD

    def discard(self, entry: _Watch) -> None:
        pass

    def wait(self, timeout: float | None) -> list[_Watch]:
        """Entries whose process ended, or none once ``timeout`` passed or
        :meth:`wake` was called."""
        raise NotImplementedError

    def wake(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _Looking(_Backend):
    """No exit events here: every process is looked at on the fallback interval."""

    def __init__(self) -> None:
        self._woken = threading.Event()

    def wait(self, timeout: float | None) -> list[_Watch]:
        self._woken.wait(timeout)
        self._woken.clear()
        return []

    def wake(self) -> None:
        self._woken.set()


class _Pipe(_Backend):
    """A wake pipe beside the operating system's own wait (POSIX)."""

    def __init__(self) -> None:
        self._read, self._write = os.pipe()
        os.set_blocking(self._read, False)
        os.set_blocking(self._write, False)
        self._by_pid: dict[int, set[_Watch]] = {}

    def wake(self) -> None:
        try:
            os.write(self._write, b"x")
        except (BlockingIOError, OSError):
            pass  # already woken, or closing

    def _drain(self) -> None:
        try:
            while os.read(self._read, 512):
                pass
        except (BlockingIOError, OSError):
            pass

    def _hold(self, entry: _Watch) -> bool:
        """Whether the pid was not held already."""
        known = self._by_pid.setdefault(entry.pid, set())
        known.add(entry)
        return len(known) == 1

    def _release(self, entry: _Watch) -> bool:
        """Whether nothing holds the pid any more."""
        known = self._by_pid.get(entry.pid)
        if known is None:
            return False
        known.discard(entry)
        if known:
            return False
        del self._by_pid[entry.pid]
        return True

    def _gone(self, pid: int) -> list[_Watch]:
        return list(self._by_pid.pop(pid, ()))

    def close(self) -> None:
        for fd in (self._read, self._write):
            try:
                os.close(fd)
            except OSError:
                pass


class _Kqueue(_Pipe):
    def __init__(self) -> None:
        super().__init__()
        self._queue = select.kqueue()
        self._queue.control([select.kevent(self._read, select.KQ_FILTER_READ, select.KQ_EV_ADD)], 0)

    def add(self, entry: _Watch) -> str:
        if not self._hold(entry):
            return HELD
        try:
            self._queue.control([select.kevent(
                entry.pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                select.KQ_NOTE_EXIT)], 0)
        except ProcessLookupError:
            # Gone, or a zombie that already exited.
            self._release(entry)
            return GONE
        except OSError:
            self._release(entry)
            return UNHELD
        return HELD

    def discard(self, entry: _Watch) -> None:
        if self._release(entry):
            try:
                self._queue.control([select.kevent(entry.pid, select.KQ_FILTER_PROC, select.KQ_EV_DELETE)], 0)
            except OSError:
                pass

    def wait(self, timeout: float | None) -> list[_Watch]:
        ended: list[_Watch] = []
        for event in self._queue.control(None, 64, timeout):
            if event.filter == select.KQ_FILTER_READ:
                self._drain()
            elif event.filter == select.KQ_FILTER_PROC and event.fflags & select.KQ_NOTE_EXIT:
                ended.extend(self._gone(event.ident))
        return ended

    def close(self) -> None:
        self._queue.close()
        super().close()


class _Pidfd(_Pipe):
    def __init__(self) -> None:
        super().__init__()
        self._poll = select.epoll()
        self._poll.register(self._read, select.EPOLLIN)
        self._fds: dict[int, int] = {}  # pidfd -> pid

    def add(self, entry: _Watch) -> str:
        if not self._hold(entry):
            return HELD
        try:
            fd = os.pidfd_open(entry.pid)
        except ProcessLookupError:
            self._release(entry)
            return GONE
        except OSError:
            self._release(entry)
            return UNHELD
        self._fds[fd] = entry.pid
        self._poll.register(fd, select.EPOLLIN)
        return HELD

    def _close_pid(self, pid: int) -> None:
        for fd in [fd for fd, known in self._fds.items() if known == pid]:
            del self._fds[fd]
            try:
                self._poll.unregister(fd)
            except OSError:
                pass
            os.close(fd)

    def discard(self, entry: _Watch) -> None:
        if self._release(entry):
            self._close_pid(entry.pid)

    def wait(self, timeout: float | None) -> list[_Watch]:
        ended: list[_Watch] = []
        for fd, _mask in self._poll.poll(-1 if timeout is None else timeout):
            if fd == self._read:
                self._drain()
                continue
            pid = self._fds.get(fd)
            if pid is not None:
                self._close_pid(pid)
                ended.extend(self._gone(pid))
        return ended

    def close(self) -> None:
        for fd in list(self._fds):
            os.close(fd)
        self._fds.clear()
        self._poll.close()
        super().close()


class _WindowsHandles(_Backend):
    #: WaitForMultipleObjects takes 64 handles; one is the wake event.
    LIMIT = 63

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, ctypes.c_void_p]
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes = [wintypes.HANDLE]
        kernel.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                                  wintypes.BOOL, wintypes.DWORD]
        kernel.WaitForMultipleObjects.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel = kernel
        self._handle_type = wintypes.HANDLE
        self._event = kernel.CreateEventW(None, False, False, None)
        if not self._event:
            raise OSError(ctypes.get_last_error(), "CreateEventW failed")
        self._held: dict[int, tuple[int, set[_Watch]]] = {}  # pid -> (handle, entries)

    def add(self, entry: _Watch) -> str:
        known = self._held.get(entry.pid)
        if known is not None:
            known[1].add(entry)
            return HELD
        if len(self._held) >= self.LIMIT:
            return UNHELD
        synchronize, query_limited = 0x00100000, 0x1000
        handle = self._kernel.OpenProcess(synchronize | query_limited, False, entry.pid)
        if not handle:
            # ERROR_INVALID_PARAMETER: no such process.
            return GONE if self._ctypes.get_last_error() == 87 else UNHELD
        self._held[entry.pid] = (handle, {entry})
        return HELD

    def discard(self, entry: _Watch) -> None:
        known = self._held.get(entry.pid)
        if known is None:
            return
        known[1].discard(entry)
        if not known[1]:
            self._kernel.CloseHandle(known[0])
            del self._held[entry.pid]

    def wait(self, timeout: float | None) -> list[_Watch]:
        pids = list(self._held)
        handles = (self._handle_type * (len(pids) + 1))(self._event, *(self._held[p][0] for p in pids))
        infinite = 0xFFFFFFFF
        milliseconds = infinite if timeout is None else int(timeout * 1000)
        result = self._kernel.WaitForMultipleObjects(len(pids) + 1, handles, False, milliseconds)
        if 1 <= result <= len(pids):
            pid = pids[result - 1]
            handle, entries = self._held.pop(pid)
            self._kernel.CloseHandle(handle)
            return list(entries)
        if result == 0xFFFFFFFF:
            raise OSError(self._ctypes.get_last_error(), "WaitForMultipleObjects failed")
        return []

    def wake(self) -> None:
        self._kernel.SetEvent(self._event)

    def close(self) -> None:
        for handle, _entries in self._held.values():
            self._kernel.CloseHandle(handle)
        self._held.clear()
        self._kernel.CloseHandle(self._event)


def _backend() -> _Backend:
    """The best way this machine has to learn a process ended."""
    try:
        if sys.platform == "win32":
            return _WindowsHandles()
        if hasattr(select, "kqueue") and hasattr(select, "KQ_FILTER_PROC"):
            return _Kqueue()
        if hasattr(os, "pidfd_open") and hasattr(select, "epoll"):
            probe = os.pidfd_open(os.getpid())
            os.close(probe)
            return _Pidfd()
    except OSError:
        _log.warning("process exit events are not available; ended processes are looked for", exc_info=True)
    return _Looking()


__all__ = ["FALLBACK_SECONDS", "start", "stop", "watch_process", "watched"]
