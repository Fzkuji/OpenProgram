"""Task persistence — one ``tasks.json`` file per session repo.

Lives at ``<session-repo>/tasks.json`` (i.e. ``<state>/sessions/<id>/``,
or inside a project-bound session's repo) so it rides the
same git history as ``meta.json`` and ``context/``. Schema:

  {"tasks": {<task_id>: <Task.to_dict>, ...}, "version": 1}

The store is intentionally per-session: tasks always belong to one
session (design D6), and putting them in the session repo means a
``git log`` on that repo replays the task lifecycle. No cross-session
queries — UI's "list all running tasks" enumerates sessions and asks
each store.

Concurrency: every public method takes the file lock for the session
(one ``threading.Lock`` per session_id). Inside the lock we read →
mutate → write → commit. The runner submits state transitions from
worker threads; the lock serialises them.

Persistence is *idempotent* on Task.id — re-saving the same task just
overwrites the dict entry. Status transitions are validated against
``can_transition`` and raise ``ValueError`` on illegal moves so a
buggy code path can't smuggle ``completed → running``.

Crash recovery (D12): ``reconcile_orphans()`` walks every session and
flips any non-terminal task to ``errored`` with
``error="worker died before completion"``. Called once at process
startup.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from openprogram.agent.task.types import (
    Task,
    TaskStatus,
    is_terminal,
    can_transition,
)


_locks: dict[str, threading.Lock] = {}
_locks_master = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _locks_master:
        lk = _locks.get(session_id)
        if lk is None:
            lk = threading.Lock()
            _locks[session_id] = lk
        return lk


def _tasks_path(session_id: str) -> Optional[Path]:
    """Path to the session's tasks.json, or None if the session repo
    doesn't exist (e.g. the session was deleted)."""
    from openprogram.store import default_store
    store = default_store()
    sdir = store._session_dir(session_id)  # noqa: SLF001 — intentional
    if not sdir.exists():
        return None
    return sdir / "tasks.json"


def _load_raw(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(blob, dict):
        return {}
    tasks = blob.get("tasks")
    if not isinstance(tasks, dict):
        return {}
    return tasks


def _write_raw(path: Path, tasks: dict[str, dict[str, Any]]) -> None:
    payload = {"version": 1, "tasks": tasks}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    tmp.replace(path)


def _commit(session_id: str, message: str) -> None:
    """Best-effort: ride the same commit machinery the dispatcher uses
    at turn-end. Failures are swallowed because tasks.json on disk is
    already correct; the git commit just records the transition."""
    try:
        from openprogram.store import default_store
        default_store().commit_turn(session_id, message)
    except Exception:
        pass


def _ensure_session(session_id: str) -> Optional[Path]:
    """Materialise the session's repo (so tasks.json lives next to a
    real meta.json / .git) and return the tasks.json path. Returns
    None if the session can't be opened — caller should treat as
    "task store unavailable" and degrade gracefully."""
    from openprogram.store import default_store
    store = default_store()
    pair = store._open(session_id, create_if_missing=True)  # noqa: SLF001
    if pair is None:
        return None
    git, _ = pair
    git._ensure_init()  # noqa: SLF001 — task store needs a real repo
    return git.path / "tasks.json"


def save_task(session_id: str, task: Task, *, commit_message: Optional[str] = None) -> None:
    """Idempotent write — overwrites the entry for ``task.id``."""
    path = _ensure_session(session_id)
    if path is None:
        return
    with _session_lock(session_id):
        tasks = _load_raw(path)
        tasks[task.id] = task.to_dict()
        _write_raw(path, tasks)
    msg = commit_message or f"task: {task.id} {task.status.value}"
    _commit(session_id, msg)


def load_task(session_id: str, task_id: str) -> Optional[Task]:
    path = _tasks_path(session_id)
    if path is None or not path.exists():
        return None
    with _session_lock(session_id):
        tasks = _load_raw(path)
    row = tasks.get(task_id)
    if not row:
        return None
    try:
        return Task.from_dict(row)
    except Exception:
        return None


def list_tasks(
    session_id: str,
    *,
    status_filter: Optional[set[TaskStatus]] = None,
    limit: Optional[int] = None,
) -> list[Task]:
    """Return tasks in this session, newest first (by created_at desc)."""
    path = _tasks_path(session_id)
    if path is None or not path.exists():
        return []
    with _session_lock(session_id):
        rows = _load_raw(path)
    out: list[Task] = []
    for row in rows.values():
        try:
            t = Task.from_dict(row)
        except Exception:
            continue
        if status_filter and t.status not in status_filter:
            continue
        out.append(t)
    out.sort(key=lambda x: x.created_at or 0, reverse=True)
    if limit is not None:
        out = out[:limit]
    return out


def update_task_status(
    session_id: str,
    task_id: str,
    new_status: TaskStatus,
    **fields: Any,
) -> Optional[Task]:
    """Atomic state transition + extra-field stamp.

    Validates the (from, to) edge against ``can_transition``. Raises
    ``ValueError`` on illegal transitions so callers can't accidentally
    revive a terminal task.

    ``fields`` overrides any Task attribute — typical use is
    ``head_id=...``, ``result_text=...``, ``error=...``, plus the
    timestamp fields the runner stamps explicitly.
    """
    path = _ensure_session(session_id)
    if path is None:
        return None
    with _session_lock(session_id):
        tasks = _load_raw(path)
        row = tasks.get(task_id)
        if not row:
            return None
        try:
            t = Task.from_dict(row)
        except Exception:
            return None
        if t.status == new_status:
            # No-op transition is OK (idempotent caller). Still apply
            # field updates if any.
            for k, v in fields.items():
                if hasattr(t, k):
                    setattr(t, k, v)
            tasks[task_id] = t.to_dict()
            _write_raw(path, tasks)
            return t
        if not can_transition(t.status, new_status):
            raise ValueError(
                f"illegal task transition {t.status.value} → "
                f"{new_status.value} (task {task_id})"
            )
        old_status = t.status
        t.status = new_status
        # Time-stamp the transition.
        now = time.time()
        if new_status == TaskStatus.QUEUED and t.queued_at is None:
            t.queued_at = now
        elif new_status == TaskStatus.RUNNING and t.started_at is None:
            t.started_at = now
        elif is_terminal(new_status) and t.completed_at is None:
            t.completed_at = now
        if new_status == TaskStatus.CANCELLED and t.cancel_requested_at is None:
            t.cancel_requested_at = now
        for k, v in fields.items():
            if hasattr(t, k):
                setattr(t, k, v)
        tasks[task_id] = t.to_dict()
        _write_raw(path, tasks)
    _commit(session_id, f"task: {task_id} {old_status.value}→{new_status.value}")
    return t


def reconcile_orphans() -> int:
    """Walk every session repo, flip non-terminal tasks → errored.

    Called once at process startup (server.py / dispatcher entry).
    Returns the number of tasks reconciled.
    """
    from openprogram.store import default_store
    store = default_store()
    if not store.root_path.exists():
        return 0
    count = 0
    for sdir in sorted(store.root_path.iterdir()):
        if not sdir.is_dir():
            continue
        sid = sdir.name
        path = sdir / "tasks.json"
        if not path.exists():
            continue
        with _session_lock(sid):
            try:
                rows = _load_raw(path)
            except Exception:
                continue
            mutated = False
            for tid, row in list(rows.items()):
                try:
                    t = Task.from_dict(row)
                except Exception:
                    continue
                if is_terminal(t.status):
                    continue
                old = t.status
                t.status = TaskStatus.ERRORED
                t.completed_at = time.time()
                t.error = "worker died before completion"
                rows[tid] = t.to_dict()
                mutated = True
                count += 1
                # Per-task git commit would be noisy on startup with
                # many orphans; aggregate them under one commit below.
                _ = old  # quiet linter
            if mutated:
                _write_raw(path, rows)
        if path.exists():
            _commit(sid, f"task: reconcile orphans (startup)")
    return count


__all__ = [
    "save_task",
    "load_task",
    "list_tasks",
    "update_task_status",
    "reconcile_orphans",
]
