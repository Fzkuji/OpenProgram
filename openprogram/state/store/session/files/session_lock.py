"""The conversation lock: one per conversation, reentrant in a process.

Fork children, CLI, worker, rewind and migration share one flock file per
conversation id under the application state root. Path-based locks are not
sufficient because placement can change and subprocesses write
independently.

One lock order, everywhere:

1. the conversation's process lock (:func:`conversation_lock`, an RLock
   shared by every store and helper in this process),
2. the conversation's flock (:func:`session_interprocess_lock`),
3. inner locks: the cached index's locks, the store's maps, registry
   files, the rewind intent and workspace locks.

:func:`session_interprocess_lock` takes step 1 itself before step 2, so a
thread holding the flock always holds the process lock: no thread in this
process can hold one half and wait for the other. The flock is held through
one descriptor per conversation per process (``flock`` is per open file
description, so a second descriptor in the same process would wait on the
first); nested acquisitions count on that holder. Both waits are bounded:
past the deadline :class:`SessionLockTimeout` names the conversation and
the holder instead of blocking a Worker thread forever. Nothing is held
while waiting on a model, the network, or another conversation's lock.

Nothing waits for it on the Worker's event loop: that thread serves every
connection, so a request whose work reads or writes a conversation runs
that work in a worker thread, and a held conversation delays that request
only. The server names its loop (:func:`watch_event_loop`); an
acquisition on it is logged once per call site
(:func:`_note_event_loop_acquisition`) so a new one is found and moved.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from openprogram.host._platform import locking


# How long a writer waits for a conversation before giving up. A holder
# does file work only (no model or network waits), so this is far longer
# than any legitimate hold and short enough that a stuck conversation
# surfaces as an error instead of a hung Worker.
DEFAULT_WAIT = 60.0
_POLL = 0.02


_log = logging.getLogger(__name__)
_loop_sites: set[tuple] = set()
_worker_loop: Optional[Callable[[], Optional[asyncio.AbstractEventLoop]]] = None


def watch_event_loop(
    current: Optional[Callable[[], Optional[asyncio.AbstractEventLoop]]],
) -> None:
    """Name the loop no conversation lock should be waited for on.

    ``current`` returns the Worker's event loop (or ``None`` before it
    runs); the server hands it down when it loads, since the store sits
    below it and never imports it. ``None`` stops watching."""
    global _worker_loop
    _worker_loop = current


def _on_worker_event_loop() -> bool:
    """Whether this thread is running the Worker's event loop."""
    current = _worker_loop
    if current is None:
        return False
    loop = asyncio._get_running_loop()
    return loop is not None and current() is loop


def _note_event_loop_acquisition(session_id: str) -> None:
    """Log, once per call site, a conversation lock taken on the event loop."""
    stack = [frame for frame in traceback.extract_stack()[:-1]
             if not frame.filename.endswith(("session_lock.py", "contextlib.py"))]
    site = tuple((frame.filename, frame.lineno) for frame in stack[-6:])
    if site in _loop_sites:
        return
    _loop_sites.add(site)
    _log.warning(
        "conversation %s locked on the event loop; run this in a worker thread:\n%s",
        session_id, "".join(traceback.format_list(stack[-6:])),
    )


class SessionLockTimeout(TimeoutError):
    """The conversation stayed locked past the wait."""

    def __init__(self, session_id: str, waited: float, holder: str) -> None:
        super().__init__(
            f"conversation {session_id} is locked by {holder}; gave up after {waited:.1f}s"
        )
        self.session_id = session_id
        self.waited = waited
        self.holder = holder


def _safe(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _lock_dir(root: str | Path | None = None) -> Path:
    from openprogram.state.paths import get_state_dir
    base = Path(root) if root is not None else Path(get_state_dir()) / "projects"
    path = base / ".locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_lock_path(session_id: str, root: str | Path | None = None) -> Path:
    return _lock_dir(root) / f"{_safe(session_id)}.lock"


_bases: dict[str, str] = {}


def _lock_key(session_id: str, root: str | Path | None) -> str:
    """The lock file's real path, without touching the disk after the first call."""
    from openprogram.state.paths import get_state_dir
    base = str(root) if root is not None else os.path.join(str(get_state_dir()), "projects")
    real = _bases.get(base)
    if real is None:
        real = os.path.realpath(os.path.expanduser(base))
        _bases[base] = real
    return os.path.join(real, ".locks", f"{_safe(session_id) or '_'}.lock")


class ConversationLock:
    """This process's lock for one conversation, and its flock holder record."""

    def __init__(self, session_id: str, path: str) -> None:
        self.session_id = session_id
        self.path = path
        self._mutex = threading.RLock()
        self._owner: Optional[int] = None
        self._owner_name = ""
        self._depth = 0
        self._handle = None
        self._flock_depth = 0

    def _holder(self) -> str:
        if self._owner is not None:
            return f"thread {self._owner_name!r} in this process"
        return "another process"

    def acquire(self, *, timeout: Optional[float] = DEFAULT_WAIT, blocking: bool = True) -> None:
        start = time.monotonic()
        if self._owner != threading.get_ident() and _on_worker_event_loop():
            _note_event_loop_acquisition(self.session_id)
        if not blocking:
            if not self._mutex.acquire(blocking=False):
                raise BlockingIOError(f"conversation {self.session_id} is locked")
        elif not self._mutex.acquire(timeout=-1 if timeout is None else max(0.0, timeout)):
            raise SessionLockTimeout(self.session_id, time.monotonic() - start, self._holder())
        if self._depth == 0:
            current = threading.current_thread()
            self._owner, self._owner_name = current.ident, current.name
        self._depth += 1

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner, self._owner_name = None, ""
        self._mutex.release()

    def held_by_current_thread(self) -> bool:
        return self._owner == threading.get_ident()

    def held(self) -> bool:
        return self._owner is not None

    def __enter__(self) -> "ConversationLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    # The flock half; called only while this thread holds the mutex.
    def _flock(self, deadline: Optional[float], blocking: bool, start: float) -> None:
        if self._flock_depth:
            self._flock_depth += 1
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        while True:
            handle = open(self.path, "a+", encoding="utf-8")
            try:
                while True:
                    try:
                        locking.flock(handle.fileno(), locking.LOCK_EX | locking.LOCK_NB)
                        break
                    except BlockingIOError:
                        if not blocking:
                            raise
                        if deadline is not None and time.monotonic() >= deadline:
                            raise SessionLockTimeout(
                                self.session_id, time.monotonic() - start, "another process",
                            ) from None
                        time.sleep(_POLL)
                current = lock_file_current(handle, self.path)
            except BaseException:
                handle.close()
                raise
            if current:
                break
            # The file was removed while this waited (its conversation was
            # deleted, or a sweep took an idle file): the lock just taken is
            # on a file nobody else can open. Take the one at the path.
            handle.close()
        self._handle = handle
        self._flock_depth = 1

    def _unflock(self) -> None:
        self._flock_depth -= 1
        if self._flock_depth:
            return
        handle, self._handle = self._handle, None
        try:
            locking.flock(handle.fileno(), locking.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


def lock_file_current(handle, path: str | Path) -> bool:
    """Whether ``path`` still names the file ``handle`` has open.

    A lock file can be removed while a process waits for it. The waiter then
    locks a file no path leads to, and the next opener gets a new one: two
    holders. Every acquisition checks this after locking and opens the file
    again when it fails, which is what makes removing a lock file safe.
    """
    try:
        mine, named = os.fstat(handle.fileno()), os.stat(path)
    except OSError:
        return False
    return (mine.st_dev, mine.st_ino) == (named.st_dev, named.st_ino)


def remove_lock_file(path: str | Path) -> bool:
    """Remove a lock file nobody holds. True when it is gone.

    The file is locked first, so one that is held stays. A process that
    opened it before the removal finds out when it gets the lock
    (:func:`lock_file_current`) and opens the path again. Windows refuses to
    remove a file that is open, so there it is removed after this handle
    closes, and stays when another process has it open.
    """
    try:
        handle = open(path, "r+", encoding="utf-8")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        try:
            locking.flock(handle.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        except OSError:
            return False
        try:
            if not lock_file_current(handle, path):
                return False
            try:
                os.unlink(path)
                return True
            except OSError:
                pass
        finally:
            locking.flock(handle.fileno(), locking.LOCK_UN)
    finally:
        handle.close()
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def session_lock_files(session_id: str, root: str | Path | None = None) -> list[Path]:
    """Every lock file kept for one conversation under the store root."""
    directory = _lock_dir(root)
    safe = _safe(session_id)
    return [
        directory / f"{safe}.lock",
        directory / f".operations-{safe}.lock",
        directory / f".migration-{session_id}.lock",
        directory.parent / ".goal-locks" / f"{hashlib.sha256(session_id.encode()).hexdigest()}.lock",
    ]


def remove_session_lock_files(session_id: str, root: str | Path | None = None) -> None:
    """A conversation was deleted: its lock files go with it.

    Called by the thread that holds the conversation's lock, after the
    delete mark is written. The held file is unlinked in place; a waiter
    that gets it next sees the removal, opens the path again and finds the
    conversation deleted. What cannot be removed now (a file another
    process has open on Windows) is left to :func:`sweep_lock_files`.
    """
    held, *others = session_lock_files(session_id, root)
    try:
        os.unlink(held)
    except OSError:
        pass
    for path in others:
        remove_lock_file(path)


def stale_lock_files(root: str | Path, known_session_ids) -> list[Path]:
    """The lock files under ``root`` of conversations the store does not have.

    A lock file is made the first time anything locks an id, including a
    probe of an id nobody created and a late writer of a deleted
    conversation, so files outlive what they guarded. Files of
    ``known_session_ids`` and the store's own registry locks are not
    listed. Only names are read; nothing is opened.
    """
    root = Path(root)
    known = set(known_session_ids)
    safe = {_safe(session_id) for session_id in known}
    hashed = {hashlib.sha256(session_id.encode()).hexdigest() for session_id in known}
    stale: list[Path] = []
    for directory, keep in ((root / ".locks", None), (root / ".goal-locks", hashed)):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".lock"):
                continue
            stem = name[:-len(".lock")]
            if keep is not None:
                kept = stem in keep
            elif stem.startswith(".operations-"):
                kept = stem[len(".operations-"):] in safe
            elif stem.startswith(".migration-") and stem != ".migration-journal":
                kept = stem[len(".migration-"):] in known
            else:
                kept = stem.startswith(".") or stem in safe
            if not kept:
                stale.append(directory / name)
    return stale


def sweep_lock_files(paths) -> int:
    """Remove the idle ones of ``paths`` (:func:`stale_lock_files`); a file
    that is held stays. Returns how many were removed.

    Safe beside running conversations, a listed file's own included: an
    idle lock file holds nothing, and whoever locks its id next makes it
    again (:func:`lock_file_current`).
    """
    return sum(1 for path in paths if remove_lock_file(path))


_registry: dict[str, ConversationLock] = {}
_registry_lock = threading.Lock()


def conversation_lock(session_id: str, root: str | Path | None = None) -> ConversationLock:
    """The process-wide lock for ``session_id`` under ``root`` (default: the state root).

    ``with conversation_lock(sid):`` serializes threads of this process
    (bounded by :data:`DEFAULT_WAIT`); :func:`session_interprocess_lock`
    takes it and then the flock.
    """
    key = _lock_key(str(session_id or ""), root)
    with _registry_lock:
        lock = _registry.get(key)
        if lock is None:
            lock = ConversationLock(str(session_id or ""), key)
            _registry[key] = lock
        return lock


def _reset_after_fork() -> None:
    # A child starts with no holders: the parent's threads are gone, and a
    # flock it inherited through a descriptor stays the parent's to release.
    global _registry, _registry_lock
    _registry = {}
    _registry_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


@contextmanager
def session_interprocess_lock(
    session_id: str,
    *,
    timeout: float | None = None,
    blocking: bool = True,
    reentrant: bool = True,
    root: str | Path | None = None,
) -> Iterator[None]:
    """Exclusive lock on one conversation across threads and processes.

    Takes the conversation's process lock, then its flock. Reentrant in the
    thread that holds it (``reentrant`` is accepted for old callers; every
    acquisition is reentrant). After acquiring, callers must re-read session
    placement. ``timeout`` is seconds, :data:`DEFAULT_WAIT` when omitted; on
    expiry raises :class:`SessionLockTimeout` (a :class:`TimeoutError`)
    without taking the lock. Non-blocking mode raises
    :class:`BlockingIOError`.
    """
    del reentrant
    if not session_id or session_id in {".", ".."}:
        raise ValueError("session_id is required")
    lock = conversation_lock(session_id, root)
    wait = DEFAULT_WAIT if timeout is None else max(0.0, timeout)
    start = time.monotonic()
    deadline = start + wait
    lock.acquire(timeout=wait, blocking=blocking)
    try:
        lock._flock(deadline, blocking, start)
        try:
            yield
        finally:
            lock._unflock()
    finally:
        lock.release()


def session_lock_available(
    session_id: str, *, root: str | Path | None = None,
) -> bool:
    """True when no thread or process currently holds the conversation lock."""
    if conversation_lock(session_id, root).held():
        return False
    try:
        with session_interprocess_lock(session_id, blocking=False, root=root):
            return True
    except BlockingIOError:
        return False


@contextmanager
def registry_file_lock(root: str | Path, name: str, *, timeout: float = 15.0) -> Iterator[None]:
    """Lock a cross-session registry while it is read-modify-written."""
    directory = Path(root) / ".locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f".{name}.lock"
    deadline = time.monotonic() + timeout
    while True:
        handle = path.open("a+", encoding="utf-8")
        try:
            while True:
                try:
                    locking.flock(handle.fileno(), locking.LOCK_EX | locking.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"registry lock busy: {name}")
                    time.sleep(0.05)
            if lock_file_current(handle, path):
                break
        except BaseException:
            handle.close()
            raise
        # Removed while this waited (a deleted conversation's operations
        # lock): take the file at the path instead.
        handle.close()
    try:
        yield
    finally:
        try:
            locking.flock(handle.fileno(), locking.LOCK_UN)
        finally:
            handle.close()
