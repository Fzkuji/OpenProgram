"""Retention for the session file-recovery tree.

Per-turn directories and prompt checkpoints are capped separately. Directories
that hold intents, their locks, checkpoints and blobs are not turn directories
and never count against or fall under the turn cap. Turn ids removed here are
listed in ``evicted-turns.json`` so a plan can say the backup is gone.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import manifest
from .paths import session_backup_root
from .prompt_checkpoints import BLOB_DIRECTORY, CHECKPOINT_DIRECTORY, PromptCheckpoints


MAX_TURNS = 100
MAX_CHECKPOINTS = 100
EVICTED_FILE = "evicted-turns.json"
_NOT_TURN_DIRECTORIES = {"intents", "intent-locks", CHECKPOINT_DIRECTORY, BLOB_DIRECTORY}
_TERMINAL_INTENTS = {"committed", "rolled_back", "recovery_required", "aborted"}


def turn_directories(root: Path) -> list[Path]:
    return [
        path for path in root.iterdir()
        if path.is_dir() and path.name not in _NOT_TURN_DIRECTORIES
    ]


def evicted_turns(session_dir: Path) -> set[str]:
    path = session_backup_root(Path(session_dir)) / EVICTED_FILE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(item) for item in value} if isinstance(value, list) else set()


def evict_old(
    session_dir: Path,
    max_turns: int = MAX_TURNS,
    max_checkpoints: int = MAX_CHECKPOINTS,
) -> int:
    """Remove the oldest turn directories and checkpoints beyond the caps.

    Returns the count of turn directories removed.
    """
    root = session_backup_root(Path(session_dir))
    if not root.exists():
        return 0
    removed = _evict_turns(root, session_dir, max_turns)
    _evict_checkpoints(session_dir, max_checkpoints)
    return removed


def _evict_turns(root: Path, session_dir: Path, max_turns: int) -> int:
    directories = turn_directories(root)
    if len(directories) <= max_turns:
        return 0
    directories.sort(key=lambda path: path.stat().st_mtime)
    evicted = evicted_turns(session_dir)
    count = 0
    for directory in directories[: len(directories) - max_turns]:
        try:
            shutil.rmtree(directory)
        except OSError:
            continue
        evicted.add(directory.name)
        count += 1
    if count:
        manifest.save(root / EVICTED_FILE, sorted(evicted))
    return count


def _evict_checkpoints(session_dir: Path, max_checkpoints: int) -> None:
    checkpoints = PromptCheckpoints(Path(session_dir))
    with checkpoints._lock():
        rows = checkpoints.all()
        if len(rows) <= max_checkpoints:
            return
        for row in rows[: len(rows) - max_checkpoints]:
            checkpoints._path(str(row["user_msg_id"])).unlink(missing_ok=True)
        _sweep_blobs(checkpoints, rows[len(rows) - max_checkpoints:])


def _sweep_blobs(checkpoints: PromptCheckpoints, kept: list[dict]) -> None:
    referenced = {
        str(entry.get("digest") or "").removeprefix("sha256:")
        for row in kept
        for entry in (row.get("files") or {}).values()
        if entry.get("kind") == "regular"
    }
    referenced |= _blobs_in_open_intents(checkpoints.root / "intents")
    if not checkpoints.blobs.exists():
        return
    for blob in checkpoints.blobs.iterdir():
        if blob.name not in referenced and not blob.name.startswith("."):
            blob.unlink(missing_ok=True)


def _blobs_in_open_intents(directory: Path) -> set[str]:
    names: set[str] = set()
    if not directory.exists():
        return names
    for path in directory.glob("*.json"):
        try:
            intent = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if intent.get("status") in _TERMINAL_INTENTS:
            continue
        for action in intent.get("actions") or []:
            for side in ("target", "rollback", "expected_current"):
                blob = (action.get(side) or {}).get("blob_path")
                if blob:
                    names.add(Path(str(blob)).name)
    return names
