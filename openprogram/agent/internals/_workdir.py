"""Default chat-runtime workdir resolution.

Single seam between the session store (which owns ``workdir/`` inside
each session's git repo) and the runtime (which forwards a working
directory to subprocess-spawning providers via ``--cd`` and similar).

Why a tiny helper and not inline in execute_in_context: keeps the
fallback chain in one place when the override sites multiply (right
now /api/run sets its own work_dir; future sub-agent dispatch will
want to point a fresh runtime at a sub-agent's worktree).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def session_workdir_for(session_id: str) -> Optional[Path]:
    """Resolve the session's ``workdir/`` directory; ``None`` when the
    session has no git repo yet (first-turn race) or the store is
    misconfigured. Callers should treat ``None`` as "leave runtime cwd
    untouched"."""
    if not session_id:
        return None
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
    except Exception:
        return None
    try:
        return store.session_workdir(session_id)
    except Exception:
        return None


def apply_default_workdir(runtime, session_id: str) -> Optional[Path]:
    """Point ``runtime`` at this session's workdir/ as the default cwd.

    A no-op when:
      * runtime is None,
      * the session has no resolvable workdir,
      * the runtime lacks ``set_workdir``.

    Returns the path that was applied (or ``None`` when no-op). The
    caller may want to surface the path in a debug log; we don't log
    here to keep the helper coupling-free.
    """
    if runtime is None or not session_id:
        return None
    wd = session_workdir_for(session_id)
    if wd is None:
        return None
    set_workdir = getattr(runtime, "set_workdir", None)
    if not callable(set_workdir):
        return None
    try:
        set_workdir(str(wd))
    except Exception:
        return None
    return wd
