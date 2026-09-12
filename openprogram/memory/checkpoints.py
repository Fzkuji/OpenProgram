"""Five-minute history checkpoints for already-persisted manual edits."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from .management.transaction import git_commit_state, workspace_write_lock
from .workspace_layout import runtime_dir

INTERVAL_SECONDS = 300


def mark_pending(root: Path) -> None:
    """Called under the workspace lock before installing the first edit."""
    from openprogram.store.session.git_session import atomic_write_text
    path = runtime_dir(root) / "manual-checkpoint.json"
    if path.exists():
        return
    # Preserve the exact pre-edit state, even if it was written outside the UI.
    git_commit_state(root, "memory: before manual editing")
    atomic_write_text(path, json.dumps({"due": time.time() + INTERVAL_SECONDS}))


def checkpoint(root: Path, *, force: bool = False) -> str | None:
    path = runtime_dir(root) / "manual-checkpoint.json"
    if not path.is_file():
        return None
    with workspace_write_lock(root):
        if not path.is_file():
            return None
        due = json.loads(path.read_text())["due"]
        if not force and time.time() < due:
            return None
        revision = git_commit_state(root, "memory: manual editing checkpoint")
        path.unlink(missing_ok=True)
        return revision


async def run_checkpoints(stop: asyncio.Event) -> None:
    """The server owns and joins this task; pending deadlines survive restarts."""
    from . import is_enabled, store
    while not stop.is_set():
        try:
            if is_enabled():
                await asyncio.to_thread(checkpoint, store.root())
        except Exception:
            logging.getLogger(__name__).exception("Memory checkpoint failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=15)
        except TimeoutError:
            pass
