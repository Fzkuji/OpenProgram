"""Shared current-location resolution. No periodic or HOME scans."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from . import identity
from . import native
from . import project_store as projects

_log = logging.getLogger(__name__)

AVAILABLE = "available"
MISSING = "missing"
REPLACED = "replaced"
MIGRATING = "migrating"
PENDING = "pending"
ERROR = "error"


def evaluate_project_location(project) -> str:
    """Return location_state without rewriting identity."""
    if project is None or project.is_default:
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
        if identity.path_is_replacement(project, path):
            return REPLACED
        return AVAILABLE
    resolved = identity.resolve_bookmark_path(project)
    if resolved is not None:
        return AVAILABLE
    return MISSING


def refresh_project_location(project_id: str) -> str:
    """Startup/access/event reconcile for one project. No recursive search."""
    project = projects.get_project(project_id)
    if project is None or project.is_default:
        return AVAILABLE
    recorded = getattr(project, "location_state", "") or ""
    if recorded in {MIGRATING, PENDING, ERROR}:
        return recorded
    path = Path(project.path).expanduser() if project.path else None
    if path is not None and path.is_dir():
        if identity.path_is_replacement(project, path):
            _set_state(project, REPLACED)
            return REPLACED
        if recorded in {MISSING, REPLACED}:
            _set_state(project, AVAILABLE)
        return AVAILABLE
    resolved = identity.resolve_bookmark_path(project)
    if resolved is not None:
        try:
            projects.relocate_project(
                project.id, resolved, expected_path=project.path,
                require_identity=True)
            return AVAILABLE
        except projects.ProjectStoreError as exc:
            _log.info("bookmark relocate refused for %s: %s", project.id, exc)
            _set_state(project, MISSING)
            return MISSING
    _set_state(project, MISSING)
    return MISSING


def reconcile_registered_projects() -> list[str]:
    """Resolve bookmarks / identity for registered projects only."""
    moved = []
    for project in projects.list_projects():
        if project.is_default or not project.path:
            continue
        before = project.path
        state = refresh_project_location(project.id)
        current = projects.get_project(project.id)
        if current and current.path != before and state == AVAILABLE:
            moved.append(project.id)
    return moved


def bound_execution_state(project) -> str | None:
    """None when new bound-project tasks may start; otherwise a block reason."""
    if project is None or getattr(project, "is_default", False):
        return None
    state = evaluate_project_location(project)
    if state == AVAILABLE:
        path = Path(project.path).expanduser() if project.path else None
        if path is None or not path.is_dir() or identity.path_is_replacement(project, path):
            return REPLACED if path is not None and path.is_dir() else MISSING
        return None
    return state


def _set_state(project, state: str) -> None:
    if getattr(project, "location_state", "") == state:
        return
    try:
        projects.set_location_state(project.id, state)
    except Exception:
        _log.debug("location state not persisted for %s", project.id, exc_info=True)


def watch_paths_for_projects() -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for project in projects.list_projects():
        if project.is_default or not project.path:
            continue
        folder = Path(project.path).expanduser()
        for candidate in (folder, folder.parent):
            try:
                key = candidate.resolve() if candidate.exists() else candidate
            except OSError:
                key = candidate
            if key in seen:
                continue
            seen.add(key)
            paths.append(candidate)
    volumes = Path("/Volumes")
    if volumes.is_dir() and volumes not in seen:
        paths.append(volumes)
    return paths


class LocationObserver:
    """Native events plus one startup reconcile. No idle retry loop."""

    def __init__(self, notify: Callable[[], None] | None = None):
        self._notify = notify or (lambda: None)
        self._native: native.NativePathObserver | None = None
        self._pending: set[str] = set()
        self._lock = __import__("threading").Lock()

    def start(self) -> None:
        moved = reconcile_registered_projects()
        if moved:
            self._notify()
        paths = watch_paths_for_projects()
        self._native = native.NativePathObserver(self._on_native_paths)
        self._native.start(paths)

    def stop(self) -> None:
        if self._native is not None:
            self._native.stop()
            self._native = None

    def refresh(self) -> None:
        """Rebuild native subscriptions after registry/path changes."""
        old = self._native
        if old is not None:
            old.stop()
        observer = native.NativePathObserver(self._on_native_paths)
        self._native = observer
        observer.start(watch_paths_for_projects())

    def _on_native_paths(self, changed: list[str]) -> None:
        # Ordinary file edits under a project do not start recovery:
        # native flags already exclude content-only watches. Coalesce
        # to registered projects whose path or ancestor matches.
        touched = []
        changed_paths = [Path(item) for item in changed]
        for project in projects.list_projects():
            if project.is_default or not project.path:
                continue
            folder = Path(project.path).expanduser()
            for event_path in changed_paths:
                try:
                    if folder == event_path or folder in event_path.parents or event_path in folder.parents:
                        touched.append(project.id)
                        break
                    if str(event_path).startswith("/Volumes") or event_path.name == "Volumes":
                        touched.append(project.id)
                        break
                except OSError:
                    continue
        moved = []
        for project_id in dict.fromkeys(touched):
            before = projects.get_project(project_id)
            state = refresh_project_location(project_id)
            after = projects.get_project(project_id)
            if before and after and (before.path != after.path or state != AVAILABLE):
                moved.append(project_id)
        if moved or touched:
            self._notify()
