"""Project location recovery without periodic or HOME scans.

Kept as the historical import path. Startup and native events live in
``location.py``. ``discover_moved_projects`` only reconciles registered
projects (bookmark / inode), never walks HOME.
"""
from __future__ import annotations

import asyncio
import logging

from .location import (
    LocationObserver,
    reconcile_registered_projects,
    refresh_project_location,
)

_log = logging.getLogger(__name__)


def discover_moved_projects(roots=None, *, max_directories=20000) -> list[str]:
    """Identity/bookmark reconcile only. ``roots`` is ignored.

    The unused arguments remain so older tests that patched this
    function keep the same signature. Recursive directory search was
    removed; callers that need a folder walk must not add one back.
    """
    del roots, max_directories
    return reconcile_registered_projects()


async def run_discovery(stop: asyncio.Event, notify) -> None:
    """Native observer owned by the server. Stops without idle retries."""
    observer = LocationObserver(notify)
    try:
        await asyncio.to_thread(observer.start)
        await stop.wait()
    except Exception:
        _log.exception("Project location observer failed")
        if not stop.is_set():
            await stop.wait()
    finally:
        await asyncio.to_thread(observer.stop)
