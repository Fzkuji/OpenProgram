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


def project_workdir_for(session_id: str) -> Optional[Path]:
    """The session's bound NON-DEFAULT project path, or ``None``.

    Resolved fresh on every call (no caching) so a mid-chat
    ``set_session_project`` changes the default cwd from the next
    turn on. The default project never counts — ad-hoc sessions keep
    the historical session-workdir behaviour."""
    if not session_id:
        return None
    try:
        from openprogram.store import project_store as _projects
        proj = _projects.project_for_session(session_id)
        if proj is not None and not proj.is_default and proj.path:
            p = Path(proj.path).expanduser()
            if p.is_dir():
                return p
    except Exception:
        pass
    return None


def apply_default_workdir(runtime, session_id: str) -> Optional[Path]:
    """Point ``runtime`` at this session's default cwd.

    Resolution order: the session's bound (non-default) project path,
    falling back to the session repo's ``workdir/``. A no-op when:
      * runtime is None,
      * the session has no resolvable workdir,
      * the runtime lacks ``set_workdir``.

    Returns the path that was applied (or ``None`` when no-op). The
    caller may want to surface the path in a debug log; we don't log
    here to keep the helper coupling-free.
    """
    if runtime is None or not session_id:
        return None
    wd = project_workdir_for(session_id) or session_workdir_for(session_id)
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
