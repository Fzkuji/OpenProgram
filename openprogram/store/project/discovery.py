"""Bounded, identity-based recovery for externally moved project directories."""
from __future__ import annotations

from collections import deque
import asyncio
import logging
import os
from pathlib import Path
import time

from . import project_store as projects

_SKIP = {"node_modules", "__pycache__", "venv", "build", "dist", "Library", "Trash"}


def _identity(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_dev}:{stat.st_ino}"


def _matches(path: Path, project) -> bool:
    if project.directory_identity and _identity(path) == project.directory_identity:
        return True
    footprint = path / '.openprogram' / 'sessions'
    return any((footprint / sid).is_dir() for sid in project.session_ids)


def discover_moved_projects(roots=None, *, max_directories=20000) -> list[str]:
    """Relocate unique matches only after a complete, bounded directory scan."""
    registered = projects.list_projects()
    missing = []
    for project in registered:
        if project.is_default or not project.path:
            continue
        path = Path(project.path)
        if path.is_dir():
            if not project.directory_identity:
                with projects._reg_lock:
                    current = projects.get_project(project.id)
                    if current and current.path == project.path and not current.directory_identity:
                        projects._upsert(current)
        else:
            missing.append(project)
    if not missing:
        return []
    if roots is None:
        roots = [Path(p.path).parent for p in registered if p.path and not p.is_default]
        roots += [Path.home() / name for name in ('Documents', 'Projects', 'Desktop', 'Downloads')]
    pending = deque((Path(root), 0) for root in roots if Path(root).is_dir())
    seen = set()
    candidates = {p.id: set() for p in missing}
    deadline = time.monotonic() + 10
    while pending:
        path, depth = pending.popleft()
        if path.is_symlink():
            continue
        canonical = path.resolve()
        if canonical in seen:
            continue
        if len(seen) >= max_directories or time.monotonic() >= deadline:
            return []
        seen.add(canonical)
        try:
            for project in missing:
                if _matches(canonical, project):
                    candidates[project.id].add(canonical)
            if depth < 6:
                with os.scandir(path) as entries:
                    pending.extend((Path(entry.path), depth + 1) for entry in entries
                                   if not entry.name.startswith('.') and entry.name not in _SKIP
                                   and entry.is_dir(follow_symlinks=False))
        except OSError:
            # An unreadable subtree could contain another copy.
            return []
    relocated = []
    for before in missing:
        matches = candidates[before.id]
        if len(matches) != 1:
            continue
        candidate = next(iter(matches))
        with projects._reg_lock:
            current = projects.get_project(before.id)
            if current is None or current.path != before.path or Path(current.path).exists():
                continue
            if any(p.id != before.id and p.path and Path(p.path).resolve() == candidate
                   for p in projects.list_projects()):
                continue
            try:
                if not candidate.is_dir() or not _matches(candidate, current):
                    continue
                projects.relocate_project(current.id, candidate)
                relocated.append(current.id)
            except OSError:
                continue
    return relocated


async def run_discovery(stop: asyncio.Event, notify) -> None:
    """The server owns this task and joins its off-loop scan at shutdown."""
    while not stop.is_set():
        try:
            if await asyncio.to_thread(discover_moved_projects):
                notify()
        except Exception:
            logging.getLogger(__name__).exception('Project folder discovery failed')
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass
