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
#: With no checkpoint pending, the loop looks on its own only this often.
RESYNC_SECONDS = 300.0
#: Signalled when a manual edit makes a checkpoint pending.
TOPIC = "memory.checkpoint"


def mark_pending(root: Path) -> None:
    """Called under the workspace lock before installing the first edit."""
    from openprogram.state.store.session.files.atomic_file import atomic_write_text
    path = runtime_dir(root) / "manual-checkpoint.json"
    if path.exists():
        return
    # Preserve the exact pre-edit state, even if it was written outside the UI.
    git_commit_state(root, "memory: before manual editing")
    atomic_write_text(path, json.dumps({"due": time.time() + INTERVAL_SECONDS}))
    from openprogram.host._platform import changes
    changes.signal(TOPIC)


def checkpoint(root: Path, *, force: bool = False) -> str | None:
    path = runtime_dir(root) / "manual-checkpoint.json"
    if not path.is_file():
        return None
    with workspace_write_lock(root):
        if not path.is_file():
            return None
        due = json.loads(path.read_text(encoding="utf-8"))["due"]
        if not force and time.time() < due:
            return None
        revision = git_commit_state(root, "memory: manual editing checkpoint")
        path.unlink(missing_ok=True)
        return revision


def pending_due(root: Path) -> float | None:
    """When the pending checkpoint is due (``time.time()``), or None."""
    try:
        return float(json.loads((runtime_dir(root) / "manual-checkpoint.json").read_text(encoding="utf-8"))["due"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


async def run_checkpoints(stop: asyncio.Event) -> None:
    """The server owns and joins this task; pending deadlines survive restarts.

    It sleeps until the pending checkpoint is due; a manual edit that makes
    one pending wakes it (``TOPIC``)."""
    from openprogram.host._platform import changes
    from . import is_enabled, store

    loop = asyncio.get_running_loop()
    wake = asyncio.Event()

    def poke(_keys: tuple[str, ...]) -> None:
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:  # the loop is closing
            pass

    unsubscribe = changes.subscribe(TOPIC, poke)
    try:
        while not stop.is_set():
            wake.clear()
            due = None
            try:
                if is_enabled():
                    root = store.root()
                    await asyncio.to_thread(checkpoint, root)
                    due = await asyncio.to_thread(pending_due, root)
            except Exception:
                logging.getLogger(__name__).exception("Memory checkpoint failed")
            timeout = RESYNC_SECONDS if due is None else min(RESYNC_SECONDS, max(0.0, due - time.time()) + 0.5)
            tasks = {asyncio.ensure_future(stop.wait()), asyncio.ensure_future(wake.wait())}
            try:
                await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
    finally:
        unsubscribe()
