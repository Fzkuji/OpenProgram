"""Shared portable location resolution. No native APIs or idle scans."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from . import identity
from . import project_store as projects

_log = logging.getLogger(__name__)
_migration_attempted: set[str] = set()
_migration_deferred: set[str] = set()
_migration_lock = threading.Lock()

AVAILABLE = "available"
MISSING = "missing"
REPLACED = "replaced"
MIGRATING = "migrating"
PENDING = "pending"
ERROR = "error"


def evaluate_project_location(project) -> str:
    """Return location_state without rewriting identity.

    A folder on another machine is never looked for here: it is available,
    and a turn that finds it gone says so from that machine."""
    if project is None or getattr(project, "is_default", False) or not projects.folder_is_here(project):
        return AVAILABLE
    state = getattr(project, "location_state", "") or ""
    if state in {MIGRATING, PENDING, ERROR}:
        if state == MIGRATING:
            return MIGRATING
        if state == PENDING:
            return PENDING
        if state == ERROR:
            return ERROR
    path = Path(project.path).expanduser() if project.path else None
    if path is not None and path.is_dir():
        if not getattr(project, "directory_identity", "") and not (
                getattr(project, "native_bookmark", "") or ""):
            return PENDING
        if identity.path_is_replacement(project, path):
            return REPLACED
        return AVAILABLE
    return MISSING


def refresh_project_location(project_id: str, *, roots=None, max_directories: int = 4000,
                             scan=None) -> str:
    """Reconcile on startup/access, with bounded portable marker lookup.

    ``scan`` is the walk a pass over many projects shares (``MarkerScan``).
    """
    project = projects.get_project(project_id)
    if project is None or getattr(project, "is_default", False) or not projects.folder_is_here(project):
        return AVAILABLE
    recorded = getattr(project, "location_state", "") or ""
    if recorded in {MIGRATING, PENDING, ERROR}:
        # Legacy records cannot be migrated safely until explicit Locate has
        # captured the directory identity.
        if not getattr(project, "directory_identity", "") and not (
                getattr(project, "native_bookmark", "") or ""):
            _set_state(project, PENDING, "directory identity unavailable")
            return recorded
        with _migration_lock:
            if project_id in _migration_attempted:
                if project_id not in _migration_deferred:
                    return recorded
                if _project_has_live_writers(project):
                    return recorded
                _migration_attempted.discard(project_id)
                _migration_deferred.discard(project_id)
            _migration_attempted.add(project_id)
        settled = False
        try:
            from openprogram.state.store.session.files.migration import run_project_migration
            from openprogram.state.store.session.session_store import default_store
            result = run_project_migration(project_id, default_store(), timeout=2.0)
            if isinstance(result, dict) and any(
                    value == "deferred" for value in result.values()):
                with _migration_lock:
                    _migration_deferred.add(project_id)
            # Nothing is left to migrate: the folder check below decides,
            # or a project whose sessions all moved stays pending forever.
            settled = isinstance(result, dict) and all(
                value in {"done", "deleted"} for value in result.values())
            refreshed = projects.get_project(project_id)
            if refreshed is not None:
                project = refreshed
                recorded = getattr(project, "location_state", "") or ""
                if recorded == AVAILABLE:
                    return AVAILABLE
        except Exception:
            _log.debug("location migration retry failed for %s", project_id,
                       exc_info=True)
        if not settled:
            return recorded
    path = Path(project.path).expanduser() if project.path else None
    if path is not None and path.is_dir() and identity.captured_identity_matches(project, path):
        if not identity.is_portable(project):
            try:
                projects._upsert(project, capture_identity=True)
            except projects.ProjectStoreError as exc:
                _set_state(project, PENDING, str(exc))
                return PENDING
        if recorded != AVAILABLE:
            _set_state(project, AVAILABLE)
        return AVAILABLE
    if identity.is_portable(project):
        from .discovery import find_project_folder
        resolved = find_project_folder(project, roots=roots, max_directories=max_directories,
                                       scan=scan)
        if resolved is not None:
            try:
                projects.relocate_project(project.id, resolved, expected_path=project.path,
                                          require_identity=True)
                return AVAILABLE
            except projects.ProjectStoreError as exc:
                _log.info("portable relocate refused for %s: %s", project.id, exc)
                current = projects.get_project(project.id)
                if current and current.location_state in {MIGRATING, PENDING, ERROR}:
                    return current.location_state
    state = REPLACED if path is not None and path.is_dir() else MISSING
    if not getattr(project, "directory_identity", "") and state == REPLACED:
        state = PENDING
    _set_state(project, state)
    return state


def _project_has_live_writers(project) -> bool:
    """Avoid repeating a bounded migration wait while its writers are live."""
    try:
        from openprogram.state.store.session.files.migration import _live_jobs
        return any(_live_jobs(session_id) for session_id in
                   (getattr(project, "session_ids", []) or []))
    except Exception:
        return True


def reconcile_registered_projects(*, roots=None, max_directories: int = 4000) -> list[str]:
    """Resolve portable markers for registered projects only.

    Runs behind a reply or at start-up, never on a path somebody waits on.
    A project whose folder is where the registry says costs one folder
    check and one marker read; every missing one shares a single bounded
    walk, so a registry full of deleted folders walks once, not once each.
    It holds the registry only for the moment a changed state is written.
    """
    from .discovery import MarkerScan
    scan = MarkerScan(roots, max_directories=max_directories)
    moved = []
    for project in projects.list_projects():
        if getattr(project, "is_default", False) or not project.path or not projects.folder_is_here(project):
            continue
        before = project.path
        state = refresh_project_location(project.id, roots=roots,
                                         max_directories=max_directories, scan=scan)
        current = projects.get_project(project.id)
        if current and current.path != before and state == AVAILABLE:
            moved.append(project.id)
    return moved


def bound_execution_state(project) -> str | None:
    """None when new bound-project tasks may start; otherwise a block reason."""
    if project is None or getattr(project, "is_default", False) or not projects.folder_is_here(project):
        return None
    state = evaluate_project_location(project)
    if state == AVAILABLE:
        path = Path(project.path).expanduser() if project.path else None
        if path is None or not path.is_dir() or identity.path_is_replacement(project, path):
            return REPLACED if path is not None and path.is_dir() else MISSING
        return None
    return state


def _set_state(project, state: str, error: str = "") -> None:
    if getattr(project, "location_state", "") == state:
        return
    try:
        projects.set_location_state(project.id, state, error=error)
    except Exception:
        _log.debug("location state not persisted for %s", project.id, exc_info=True)
