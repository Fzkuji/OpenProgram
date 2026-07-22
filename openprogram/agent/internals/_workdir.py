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
    """The session's project path (bound project, else the default
    project), or ``None``.

    Resolved fresh on every call (no caching) so a mid-chat
    ``set_session_project`` changes the default cwd from the next
    turn on. The default project counts too — its path is the user's
    home, and that's what the topbar chip shows for ad-hoc sessions.
    Falling through to ``os.getcwd()`` would leak the SERVER's launch
    directory (the OpenProgram repo) into every chat, which is exactly
    the bug this exists to fix."""
    if not session_id:
        return None
    try:
        from openprogram.store import project_store as _projects
        proj = _projects.project_for_session(session_id)
        if proj is None:
            proj = _projects.get_default_project()
        if proj is not None and proj.path:
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
