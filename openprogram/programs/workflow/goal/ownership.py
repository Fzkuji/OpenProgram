"""Single-host, cross-process ownership for a session's Goal controller."""
from __future__ import annotations

import errno
import hashlib
from contextlib import contextmanager
from functools import wraps

from openprogram import _compat as locking


@contextmanager
def goal_owner(store, session_id: str):
    """Try once; an OS-released lock never needs stale-PID guessing or polling.

    The lock is separate from snapshot locks so answering and editing remain
    asynchronous. Keep the file in place: unlinking it permits two owners.
    """
    root = store.root_path.resolve() / ".goal-locks"
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(session_id.encode()).hexdigest()
    with (root / f"{name}.lock").open("a+") as handle:
        try:
            locking.flock(handle.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            yield False
            return
        try:
            yield True
        finally:
            locking.flock(handle.fileno(), locking.LOCK_UN)


def exclusive_goal(function):
    @wraps(function)
    def run(*args, **kwargs):
        from openprogram.agentic_programming.function import current_session_id
        import openprogram.programs.workflow.goal as goal_pkg

        session_id = current_session_id()
        if not session_id:
            return function(*args, **kwargs)
        with goal_owner(goal_pkg._db(), session_id) as acquired:
            if not acquired:
                raise ValueError("A Goal controller is already executing for this session")
            return function(*args, **kwargs)
    return run
