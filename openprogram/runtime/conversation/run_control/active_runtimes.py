"""Active exec runtimes, tracked so canonical cancellation can kill the CLI subprocess."""
from __future__ import annotations

import logging
import threading
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Active exec runtimes — keep track so canonical execution cancellation can
# kill the CLI subprocess.
# ---------------------------------------------------------------------------

_active_exec_runtimes: dict[tuple[str, str | None], Any] = {}
_active_exec_runtimes_lock = threading.Lock()


def register_active_runtime(
    session_id: str, rt: Any, *, execution_id: str | None = None,
) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes[(session_id, execution_id)] = rt


def unregister_active_runtime(
    session_id: str, *, execution_id: str | None = None,
) -> None:
    with _active_exec_runtimes_lock:
        _active_exec_runtimes.pop((session_id, execution_id), None)


def has_active_runtime(session_id: str) -> bool:
    """True iff a foreground runtime is registered for this session.

    Used as a zombie check against ``_running_tasks``: an entry there
    without a paired live runtime (process died, cleanup missed) is
    stale and should be treated as no-op.
    """
    with _active_exec_runtimes_lock:
        key = (session_id, None)
        if key not in _active_exec_runtimes:
            return False
        runtime = _active_exec_runtimes[key]
        # Foreground threads are registered before start. Only a thread that
        # has actually started can be known to have finished; preserve the
        # reservation handoff and opaque runtimes until explicit cleanup.
        is_alive = getattr(runtime, "is_alive", None)
        if getattr(runtime, "ident", None) is not None and callable(is_alive) and not is_alive():
            _active_exec_runtimes.pop(key)
            return False
        return True


def kill_active_runtime(
    session_id: str, *, execution_id: str | None = None,
) -> None:
    """Terminate the subprocess of the active exec runtime, if any."""
    with _active_exec_runtimes_lock:
        rt = _active_exec_runtimes.get((session_id, execution_id))
    if rt is None:
        return
    proc = getattr(rt, "_proc", None)
    if proc is None:
        return
    try:
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    # Process.kill raises OSError (ProcessLookupError once it exited).
                    _log.debug("exec runtime kill failed for session %s", session_id, exc_info=True)
    except Exception:
        # Stop path: the caller reports the kill as attempted either way.
        _log.warning("exec runtime not terminated for session %s", session_id, exc_info=True)
