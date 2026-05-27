"""Worktree persistence — single ``~/.agentic/worktrees.json``.

Worktrees are cross-session resources (a plan agent can hand one
off to a sub-task, the user can keep a worktree across session
restarts). So persistence lives in a single profile-scoped file,
NOT inside any one session repo. Schema::

  {"version": 1,
   "worktrees": {<worktree_id>: <Worktree.to_dict>, ...}}

Concurrency: a single ``threading.Lock`` guards every read /
write. The file is small (one short JSON row per active worktree)
so locking the whole file rather than per-row is acceptable.

Atomic writes use the ``tmp + replace`` pattern, identical to
``openprogram.agent.task.store._write_raw``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from openprogram.paths import get_state_dir, ensure_state_dir
from openprogram.worktree.types import Worktree, WorktreeStatus


_FILE_LOCK = threading.Lock()


def _store_path() -> Path:
    return get_state_dir() / "worktrees.json"


def _load_raw() -> dict[str, dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(blob, dict):
        return {}
    rows = blob.get("worktrees")
    if not isinstance(rows, dict):
        return {}
    return rows


def _write_raw(rows: dict[str, dict[str, Any]]) -> None:
    ensure_state_dir()
    path = _store_path()
    payload = {"version": 1, "worktrees": rows}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_worktree(wt: Worktree) -> None:
    """Idempotent — overwrites the entry for ``wt.id``."""
    with _FILE_LOCK:
        rows = _load_raw()
        rows[wt.id] = wt.to_dict()
        _write_raw(rows)


def load_worktree(worktree_id: str) -> Optional[Worktree]:
    with _FILE_LOCK:
        rows = _load_raw()
    row = rows.get(worktree_id)
    if not row:
        return None
    try:
        return Worktree.from_dict(row)
    except Exception:
        return None


def delete_worktree(worktree_id: str) -> None:
    """Permanent removal. Manager keeps the entity around with status=
    ``discarded`` for audit; this function is for tests / explicit
    cleanup. Production paths should call ``manager.discard_worktree``
    which sets the status instead of deleting the row."""
    with _FILE_LOCK:
        rows = _load_raw()
        if worktree_id in rows:
            rows.pop(worktree_id, None)
            _write_raw(rows)


def list_worktrees(
    *,
    status_filter: Optional[set[WorktreeStatus]] = None,
    parent_session: Optional[str] = None,
    parent_task: Optional[str] = None,
) -> list[Worktree]:
    """Return all worktrees matching the filters, newest-first."""
    with _FILE_LOCK:
        rows = _load_raw()
    out: list[Worktree] = []
    for row in rows.values():
        try:
            wt = Worktree.from_dict(row)
        except Exception:
            continue
        if status_filter and wt.status not in status_filter:
            continue
        if parent_session is not None and wt.parent_session != parent_session:
            continue
        if parent_task is not None and wt.parent_task != parent_task:
            continue
        out.append(wt)
    out.sort(key=lambda w: w.created_at or 0, reverse=True)
    return out


def find_active_for_session(session_id: str) -> Optional[Worktree]:
    """Convenience: the first worktree in ACTIVE state bound to this
    session. (Manager keeps it to at most one — see Part 5 #4.)"""
    rows = list_worktrees(
        status_filter={WorktreeStatus.ACTIVE},
        parent_session=session_id,
    )
    return rows[0] if rows else None


def find_for_task(task_id: str) -> Optional[Worktree]:
    rows = list_worktrees(parent_task=task_id)
    return rows[0] if rows else None


__all__ = [
    "save_worktree",
    "load_worktree",
    "delete_worktree",
    "list_worktrees",
    "find_active_for_session",
    "find_for_task",
]
