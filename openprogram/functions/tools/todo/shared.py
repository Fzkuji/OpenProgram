"""Storage layer for the todo planning board.

Session-scoped: todos live in ``<session-repo>/todos.json``, the same
placement pattern as ``inbox.json`` (agent/inbox.py). Each entry:

    id          short incrementing string ("1", "2", …)
    subject     required title
    description optional details
    status      pending | in_progress | completed
    owner       optional free-text claimant
    blocked_by  list of todo ids this entry waits on
    created_at / updated_at  unix timestamps
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

STATUSES = ("pending", "in_progress", "completed")

# ponytail: one global lock; per-session locks if todo traffic ever matters
_lock = threading.Lock()


def current_session_id() -> Optional[str]:
    try:
        from openprogram.agent.run_control import _current_session_id
        return _current_session_id.get(None)
    except Exception:
        return None


def todos_path(session_id: str) -> Optional[Path]:
    """Path to the session's todos.json, or None when the session repo
    doesn't exist."""
    from openprogram.agent.session_db import default_db
    sdir = default_db()._session_dir(session_id)  # noqa: SLF001 — same as inbox.py
    if not sdir.exists():
        return None
    return sdir / "todos.json"


def load(session_id: str) -> list[dict[str, Any]]:
    path = todos_path(session_id)
    if path is None or not path.exists():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    todos = blob.get("todos") if isinstance(blob, dict) else None
    return todos if isinstance(todos, list) else []


def save(session_id: str, todos: list[dict[str, Any]]) -> None:
    path = todos_path(session_id)
    if path is None:
        raise ValueError(f"session {session_id!r} not found")
    payload = {"version": 1, "todos": todos}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def next_id(todos: list[dict[str, Any]]) -> str:
    highest = 0
    for t in todos:
        try:
            highest = max(highest, int(t.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def lock() -> threading.Lock:
    return _lock
