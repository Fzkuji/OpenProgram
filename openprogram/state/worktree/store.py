"""Worktree persistence — single ``~/.openprogram/worktrees.json``.

Worktrees are cross-session resources (a plan agent can hand one
off to a sub-job, the user can keep a worktree across session
restarts). So persistence lives in a single profile-scoped file,
NOT inside any one session repo. Schema::

  {"version": 1,
   "worktrees": {<worktree_id>: <Worktree.to_dict>, ...}}

Concurrency: a single ``threading.Lock`` guards every read /
write. The file is small (one short JSON row per active worktree)
so locking the whole file rather than per-row is acceptable.

Atomic writes use the ``tmp + replace`` pattern, identical to
``openprogram.runtime.jobs.store._write_raw``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from openprogram.state import paths
from openprogram.state.worktree.types import Worktree, WorktreeStatus
from openprogram.host._platform.filesystem import replace_file


_FILE_LOCK = threading.Lock()


def _store_path() -> Path:
    return paths.get_state_dir() / "worktrees.json"


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
    changed = False
    for row in rows.values():
        if isinstance(row, dict) and "parent_task" in row:
            if "parent_job" not in row:
                row["parent_job"] = row["parent_task"]
            row.pop("parent_task", None)
            changed = True
    if changed:
        _write_raw(rows)
    return rows


def _write_raw(rows: dict[str, dict[str, Any]]) -> None:
    paths.ensure_state_dir()
    path = _store_path()
    payload = {"version": 1, "worktrees": rows}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    replace_file(tmp, path)


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
    parent_job: Optional[str] = None,
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
        if parent_job is not None and wt.parent_job != parent_job:
            continue
        out.append(wt)
    out.sort(key=lambda w: w.created_at or 0, reverse=True)
    return out


_CURRENT = object()


def current_branch_for(session_id: str) -> Optional[str]:
    """The branch a worktree lookup is for: the running turn's own branch
    when the turn is in this conversation, else the selected branch.
    ``None`` when neither is known (no store, no branch recorded)."""
    try:
        from openprogram.state import execution_facts
        branch = execution_facts.turn_branch(session_id)
    except Exception:
        branch = None
    if branch:
        return branch
    try:
        from openprogram.state.store.current import current_store
        return (current_store().get_session(session_id) or {}).get("active_branch_id")
    except Exception:
        return None


def find_active_for_session(session_id: str, branch_id: Any = _CURRENT) -> Optional[Worktree]:
    """The ACTIVE worktree a branch of the session runs in.

    A worktree created for one branch (``parent_branch``) serves that
    branch only; a conversation-wide one (no ``parent_branch``) serves
    every branch that has none of its own. ``branch_id`` defaults to the
    current turn's branch, else the selected one. A worktree bound to a
    job (a sub-agent's, a self-update repair candidate) is that job's and
    never a branch's working directory, even though the session owns it."""
    if branch_id is _CURRENT:
        branch_id = current_branch_for(session_id)
    rows = list_worktrees(
        status_filter={WorktreeStatus.ACTIVE},
        parent_session=session_id,
    )
    rows = [wt for wt in rows if wt.parent_job is None]
    if branch_id:
        own = [wt for wt in rows if wt.parent_branch == branch_id]
        if own:
            return own[0]
    shared = [wt for wt in rows if not wt.parent_branch]
    return shared[0] if shared else None


def active_at_path(path: Optional[str]) -> Optional[Worktree]:
    """The ACTIVE worktree whose directory is ``path``, if any."""
    if not path:
        return None
    import os
    want = os.path.realpath(path)
    for wt in list_worktrees(status_filter={WorktreeStatus.ACTIVE}):
        if wt.worktree_path and os.path.realpath(wt.worktree_path) == want:
            return wt
    return None


def find_for_job(job_id: str) -> Optional[Worktree]:
    rows = list_worktrees(parent_job=job_id)
    return rows[0] if rows else None


__all__ = [
    "save_worktree",
    "load_worktree",
    "delete_worktree",
    "list_worktrees",
    "find_active_for_session",
    "current_branch_for",
    "active_at_path",
    "find_for_job",
]
