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
    """The session's main working directory, or ``None``.

    The main directory is the bound project's path; an unbound session
    uses the default project (whose path is the user's home, and what
    the composer chip shows for ad-hoc chats). Returning ``None`` and
    letting the caller fall back to ``os.getcwd()`` would leak the
    SERVER's launch directory into the chat, so the resolution is
    explicit at every step.

    A bound project whose directory is gone resolves to ``None``: the
    turn falls back to the session's own ``workdir/`` (see
    ``apply_default_workdir``) and never silently borrows the default
    project's home directory in its place. ``project_path_missing``
    tells the two apart so the UI can show the repair affordance.

    Resolved fresh on every call (no caching) so a project relocation
    takes effect from the next turn on."""
    proj = _main_project(session_id)
    if proj is None or not proj.path:
        return None
    p = Path(proj.path).expanduser()
    return p if p.is_dir() else None


def _main_project(session_id: str):
    """The session's main project record (bound, else the default)."""
    if not session_id:
        return None
    try:
        from openprogram.store import project_store as _projects
        return (_projects.project_for_session(session_id)
                or _projects.get_default_project())
    except Exception:
        return None


def project_path_missing(session_id: str) -> Optional[str]:
    """The main project's path when that directory does not exist, else
    ``None``. The single source of truth for the "project directory is
    missing" warning state shipped to the UI."""
    proj = _main_project(session_id)
    if proj is None or not proj.path:
        return None
    p = Path(proj.path).expanduser()
    return None if p.is_dir() else proj.path


def apply_default_workdir(runtime, session_id: str) -> Optional[Path]:
    """Point ``runtime`` at this session's default cwd.

    Resolution order: the session's main project path, falling back to
    the session repo's ``workdir/`` — which is also what a project whose
    directory has gone missing falls back to. A no-op when:
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
