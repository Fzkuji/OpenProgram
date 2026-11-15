"""Bounded cross-platform project lookup on startup and explicit access."""
from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from pathlib import Path

from . import identity, project_store as projects
from .location import reconcile_registered_projects, refresh_project_location

_log = logging.getLogger(__name__)
_SKIP = {"node_modules", "vendor", "venv", "build", "dist", "target", "__pycache__"}


class MarkerScan:
    """One bounded walk for project markers, shared by every lookup.

    The walk does not depend on which project is looked for, so a pass over
    many missing projects walks once and each of them is one dictionary
    lookup. It runs at the first lookup, never when nothing is missing.

    Limits count all directory entries, including files, to bound large flat
    directories. Symbolic links and hidden directories are never traversed.
    A walk that hit its limit or an unreadable folder finds nothing.
    """

    def __init__(self, roots=None, *, max_directories: int = 4000):
        self._roots = roots
        self._max = max_directories
        self._found: dict[str, set[Path]] | None = None
        self._walked = False

    def folder_for(self, directory_identity: str) -> Path | None:
        """The one folder carrying this marker, or no match."""
        if not self._walked:
            self._walked = True
            self._found = self._walk()
        matches = (self._found or {}).get(directory_identity) or ()
        return next(iter(matches)) if len(matches) == 1 else None

    def _walk(self) -> dict[str, set[Path]] | None:
        if self._max < 1:
            return None
        roots = self._roots
        if roots is None:
            roots = {Path(p.path).expanduser().parent for p in projects.list_projects()
                     if not p.is_default and p.path}
        home = Path.home().resolve()
        selected = set()
        for item in roots:
            root = Path(item).expanduser()
            if root.is_symlink():
                continue
            root = root.resolve()
            # A moved parent no longer exists at its recorded path. Use its
            # nearest surviving ancestor, without expanding into a home scan.
            while not root.is_dir() and root.parent != root:
                root = root.parent
            if root == home or root in home.parents:
                continue
            if root.is_dir():
                selected.add(root)
        queue = deque((root, 0) for root in sorted(selected))
        visited = set()
        found: dict[str, set[Path]] = {}
        entries = 0
        while queue:
            folder, depth = queue.popleft()
            if folder in visited:
                continue
            visited.add(folder)
            if depth >= 3:
                marker = identity.read_marker(folder)
                if marker:
                    found.setdefault(marker, set()).add(folder)
                continue
            marked = False
            try:
                with os.scandir(folder) as children:
                    for child in children:
                        entries += 1
                        if entries > self._max:
                            return None
                        if child.name == ".openprogram":
                            marked = True
                        if child.name.startswith(".") or child.name in _SKIP:
                            continue
                        if child.is_dir(follow_symlinks=False):
                            queue.append((Path(child.path), depth + 1))
            except OSError:
                return None
            # Only a folder that has the metadata directory can carry a marker.
            if marked:
                marker = identity.read_marker(folder)
                if marker:
                    found.setdefault(marker, set()).add(folder)
        return found


def find_project_folder(project, *, roots=None, max_directories: int = 4000,
                        scan: MarkerScan | None = None) -> Path | None:
    """Return one marker match in a completed bounded search, or no match."""
    if not identity.is_portable(project) or max_directories < 1:
        return None
    if scan is None:
        scan = MarkerScan(roots, max_directories=max_directories)
    return scan.folder_for(project.directory_identity)


def discover_moved_projects(roots=None, *, max_directories=4000) -> list[str]:
    return reconcile_registered_projects(roots=roots, max_directories=max_directories)


async def run_discovery(stop: asyncio.Event, notify) -> None:
    """Startup reconciliation; later access checks run at their public entry."""
    try:
        moved = await asyncio.to_thread(reconcile_registered_projects)
        if moved:
            notify()
    except Exception:
        _log.exception("Project location reconciliation failed")
    await stop.wait()
