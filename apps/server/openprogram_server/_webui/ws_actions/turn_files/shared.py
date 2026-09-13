"""Shared configuration, state, and validators for turn-file actions."""
from __future__ import annotations

import threading
from pathlib import Path

_MAX_SCOPE_FILES = 10_000
_SCOPE_PAGE_SIZE = 100
_MAX_DIFF_BYTES = 512 * 1024
_MAX_DIFF_PAGE_BYTES = 256 * 1024
_MAX_DIFF_LINES = 200
_MAX_DIFF_LINE_BYTES = 64 * 1024
_REVIEW_CATEGORIES = {"All", "Code", "Tests", "Docs", "Large"}
_REVIEW_SORTS = {"path", "alpha", "category", "recent"}
_REVIEW_SCOPES = {"turn", "branch", "workspace"}
_MAX_REVIEW_SNAPSHOTS = 256
_MAX_REVIEW_SNAPSHOT_BYTES = 16 * 1024 * 1024
_MAX_REVIEW_SNAPSHOT_ITEMS = _MAX_SCOPE_FILES
_REVIEW_SNAPSHOT_TTL = 5 * 60
_MAX_REVIEW_CURSORS = 1024
_MAX_REVIEW_SNAPSHOT_TOMBSTONES = _MAX_REVIEW_SNAPSHOTS + _MAX_REVIEW_CURSORS
_MAX_REVIEW_TEXT_BYTES = 4096

_REVIEW_SNAPSHOTS: dict[str, dict] = {}
_REVIEW_CURSORS: dict[str, dict] = {}
_REVIEW_SNAPSHOT_EPOCHS: dict[str, int] = {}
_REVIEW_SNAPSHOT_NONCE = 0
_REVIEW_REGISTRY_LOCK = threading.RLock()


def _setting(name: str):
    return globals()[name]


def _project_root(session_id: str) -> Path | None:
    try:
        from openprogram.store.project.project_store import project_for_session

        project = project_for_session(session_id)
        if project and project.path:
            return Path(project.path).expanduser().resolve()
    except Exception:
        pass
    return None


def _valid_turn_id(turn_id: str) -> bool:
    return bool(
        turn_id
        and turn_id not in {".", ".."}
        and not any(ord(char) < 32 or 0x7f <= ord(char) <= 0x9f for char in turn_id)
        and not Path(turn_id).is_absolute()
        and Path(turn_id).name == turn_id
        and "/" not in turn_id
        and "\\" not in turn_id
    )
