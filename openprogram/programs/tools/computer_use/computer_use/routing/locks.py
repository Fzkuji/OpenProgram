"""Who may operate a screen now: shared and exclusive locks that hold across the worker's processes.

A call asks its provider which keys it needs (``Provider.lock``): shared
keys, which any number of calls hold together, and exclusive keys, which one
call holds alone. Steps delivered to an application in the background share
the machine and take the application; a step that moves the real pointer
takes the machine.

Every key is a directory of lock files under the state directory, so a lock
held by one process of the worker keeps the others out, and a process that
dies gives its locks back (the operating system drops its file locks):

``q-*``      one ticket per waiting request, named by when it came and
             locked from its arrival for as long as it waits. A request goes
             to the gate only when no earlier ticket of another process is
             still locked (one process orders its own requests in memory), so
             requests from every process reach it in the order they came.
``gate``     held while a request is decided. The request on it keeps it
             for as long as it waits, so later requests stay behind it:
             first come, first served, and a waiting exclusive request goes
             before later shared ones.
``writer``   held by the exclusive holder.
``r-*``      one file per shared holder, locked while it holds.

Only exclusive file locks are used, since that is what every platform has.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import itertools
import os
from pathlib import Path
import threading
import time
from typing import Callable, Iterator

from openprogram.host._platform import locking

WAIT_SECONDS = 60.0
_POLL_SECONDS = 0.05

_serial = itertools.count(1)


class Busy(RuntimeError):
    """The key stayed held for the whole wait; nothing was sent."""

    def __init__(self, key: str, seconds: float) -> None:
        self.key, self.seconds = key, seconds
        super().__init__(f"{key} has been held for {seconds:g} s")


@dataclass(frozen=True)
class Locks:
    """The keys one call holds: ``shared`` with other calls, ``exclusive`` alone."""
    shared: tuple[str, ...] = ()
    exclusive: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.shared or self.exclusive)


def machine_key(instance: str) -> str:
    """The key of one machine instance: ``local``, a machine id, or an id with its conversation."""
    return f"machine/{instance}"


def application_key(instance: str, application: str) -> str:
    return f"application/{instance}/{application}"


def tab_key(instance: str, browser: str, tab: str) -> str:
    return f"tab/{instance}/{browser}/{tab}"


def _root() -> Path:
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "computer-use" / "locks"


def _folder(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    folder = _root() / digest
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _open(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def _try(fd: int) -> bool:
    try:
        locking.flock(fd, locking.LOCK_EX | locking.LOCK_NB)
    except (BlockingIOError, PermissionError):
        return False
    return True


def _release(fd: int) -> None:
    try:
        locking.flock(fd, locking.LOCK_UN)
    finally:
        os.close(fd)


# Requests of one process take their turn at a key in the order they came.
_queues_guard = threading.Lock()
_queues: dict[str, deque[int]] = {}


class _Wait:
    """One request's wait: up to the call's deadline, giving way when the call is cancelled."""

    def __init__(self, key: str, seconds: float, deadline: float, cancelled: Callable[[], None] | None) -> None:
        self.key, self.seconds, self.deadline, self.cancelled = key, seconds, deadline, cancelled

    def tick(self) -> None:
        if self.cancelled is not None:
            self.cancelled()
        if time.monotonic() >= self.deadline:
            raise Busy(self.key, self.seconds)
        time.sleep(_POLL_SECONDS)


@contextmanager
def _turn(key: str, wait: _Wait) -> Iterator[None]:
    ticket = next(_serial)
    with _queues_guard:
        _queues.setdefault(key, deque()).append(ticket)
    try:
        while True:
            with _queues_guard:
                if _queues[key][0] == ticket:
                    break
            wait.tick()
        yield
    finally:
        with _queues_guard:
            queue = _queues.get(key)
            if queue is not None:
                queue.remove(ticket)
                if not queue:
                    _queues.pop(key, None)


def _readers_gone(folder: Path) -> bool:
    """Whether no shared holder is left; the files of finished or dead holders are removed."""
    gone = True
    for path in folder.glob("r-*"):
        try:
            fd = _open(path)
        except OSError:
            continue
        if _try(fd):
            # Closed before it is removed: Windows does not remove a file that is still open.
            _release(fd)
            try:
                path.unlink()
            except OSError:
                pass
        else:
            os.close(fd)
            gone = False
    return gone


def _take_ticket(folder: Path, wait: _Wait) -> tuple[Path, int]:
    """This request's place in the key's queue across processes, locked for as long as it waits."""
    while True:
        path = folder / f"q-{time.time_ns():020d}-{os.getpid()}-{next(_serial)}"
        fd = _open(path)
        try:
            # Another waiter can find the ticket before it is locked, take it for a dead one's and remove it.
            while not _try(fd):
                wait.tick()
        except BaseException:
            os.close(fd)
            raise
        if os.fstat(fd).st_nlink:
            return path, fd
        _release(fd)


def _drop_ticket(path: Path, fd: int) -> None:
    # Unlocked and closed first, so Windows can remove it; else the next waiter does.
    _release(fd)
    try:
        path.unlink()
    except OSError:
        pass


def _earlier_waiting(folder: Path, own: Path) -> bool:
    """Whether a request that came before ``own`` still waits; the tickets of dead or finished ones are removed."""
    for path in sorted(folder.glob("q-*")):
        if path.name >= own.name:
            break
        if path.name.split("-")[2:3] == [str(os.getpid())]:
            # This process orders its own requests (``_turn``); one of them that came first is already ahead.
            continue
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue
        if _try(fd):
            _drop_ticket(path, fd)
        else:
            os.close(fd)
            return True
    return False


def _acquire(key: str, exclusive: bool, wait: _Wait) -> Callable[[], None]:
    """Take ``key``; return what gives it back."""
    folder = _folder(key)
    # Taken on arrival, before this process's own turn: a request queued behind another of its process
    # keeps its place against the requests of other processes.
    ticket = _take_ticket(folder, wait)
    queued = True
    try:
        with _turn(key, wait):
            gate = _open(folder / "gate")
            try:
                while _earlier_waiting(folder, ticket[0]) or not _try(gate):
                    wait.tick()
                # On the gate, the request holds its place there.
                queued = False
                _drop_ticket(*ticket)
                if exclusive:
                    writer = _open(folder / "writer")
                    try:
                        while not _try(writer):
                            wait.tick()
                        try:
                            while not _readers_gone(folder):
                                wait.tick()
                        except BaseException:
                            locking.flock(writer, locking.LOCK_UN)
                            raise
                    except BaseException:
                        os.close(writer)
                        raise
                    return lambda: _release(writer)
                # An exclusive holder is at work: wait for it holding the gate, so later requests stay behind.
                while True:
                    writer = _open(folder / "writer")
                    if _try(writer):
                        _release(writer)
                        break
                    os.close(writer)
                    wait.tick()
                path = folder / f"r-{os.getpid()}-{next(_serial)}"
                reader = _open(path)
                _try(reader)

                def release() -> None:
                    # Unlocked and closed first, so Windows can remove it; else the next exclusive request does.
                    _release(reader)
                    try:
                        path.unlink()
                    except OSError:
                        pass
                return release
            finally:
                _release(gate)
    finally:
        if queued:
            # A request that gave up leaves the queue.
            _drop_ticket(*ticket)


@contextmanager
def held(locks: Locks, *, cancelled: Callable[[], None] | None = None,
         seconds: float = WAIT_SECONDS) -> Iterator[None]:
    """Hold ``locks`` for one call.

    ``cancelled`` raises when the call was cancelled; it is asked while
    waiting. Keys are taken in one order everywhere (machines before what is
    inside them), so two calls never wait for each other. ``seconds`` bounds
    the whole wait, not each key: raises :class:`Busy` once it has passed.
    """
    deadline = time.monotonic() + seconds
    wanted = {key: True for key in locks.exclusive}
    for key in locks.shared:
        wanted.setdefault(key, False)
    releases: list[Callable[[], None]] = []
    try:
        for key in sorted(wanted, key=_order):
            releases.append(_acquire(key, wanted[key], _Wait(key, seconds, deadline, cancelled)))
        yield
    finally:
        for release in reversed(releases):
            try:
                release()
            except OSError:
                pass


def _order(key: str) -> tuple[int, str]:
    return (0 if key.startswith("machine/") else 1, key)


__all__ = ["Busy", "Locks", "WAIT_SECONDS", "application_key", "held", "machine_key", "tab_key"]
