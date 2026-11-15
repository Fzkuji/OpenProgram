"""Single-host, cross-process lock serializing a session's Goal admission and mutations."""
from __future__ import annotations

import errno
import hashlib
from contextlib import contextmanager

from openprogram.host._platform import locking


@contextmanager
def goal_owner(store, session_id: str):
    """Try once; an OS-released lock never needs stale-PID guessing or polling.

    The lock is separate from snapshot locks so answering and editing remain
    asynchronous. The file goes with its conversation
    (``session_lock.remove_session_lock_files``); an owner whose file was
    removed between its open and its lock takes the one at the path.
    """
    from openprogram.state.store.session.session_lock import lock_file_current

    root = store.root_path.resolve() / ".goal-locks"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{hashlib.sha256(session_id.encode()).hexdigest()}.lock"
    while True:
        with path.open("a+", encoding="utf-8") as handle:
            try:
                locking.flock(handle.fileno(), locking.LOCK_EX | locking.LOCK_NB)
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                yield False
                return
            try:
                if not lock_file_current(handle, path):
                    continue
                yield True
                return
            finally:
                locking.flock(handle.fileno(), locking.LOCK_UN)
