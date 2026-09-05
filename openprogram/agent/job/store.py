"""Job persistence — one ``jobs.json`` file per session repo.

Lives at ``<session-repo>/jobs.json`` (i.e. ``<state>/sessions/<id>/``,
or inside a project-bound session's repo) so it rides the
same git history as ``meta.json`` and ``context/``. Schema:

  {"jobs": {<job_id>: <Job.to_dict>, ...}, "version": 1}

The store is intentionally per-session: jobs always belong to one
session (design D6), and putting them in the session repo means a
``git log`` on that repo replays the job lifecycle. No cross-session
queries — UI's "list all running jobs" enumerates sessions and asks
each store.

Concurrency: every public method takes the file lock for the session
(one ``threading.Lock`` per session_id). Inside the lock we read →
mutate → write → commit. The runner submits state transitions from
worker threads; the lock serialises them.

Persistence is *idempotent* on Job.id — re-saving the same job just
overwrites the dict entry. Status transitions are validated against
``can_transition`` and raise ``ValueError`` on illegal moves so a
buggy code path can't smuggle ``completed → running``.

Crash recovery (D12): ``reconcile_orphans()`` walks every session and
flips any non-terminal job to ``errored`` with
``error="worker died before completion"``. Called once at process
startup.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from openprogram import _compat as fcntl

from openprogram.agent.job.types import (
    Job,
    JobStatus,
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


@contextmanager
def _session_file_lock(path: Path):
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _jobs_path(session_id: str) -> Optional[Path]:
    """Path to the session's jobs.json, or None if the session repo
    doesn't exist (e.g. the session was deleted)."""
    from openprogram.store import default_store
    store = default_store()
    sdir = store._session_dir(session_id)  # noqa: SLF001 — intentional
    if not sdir.exists():
        return None
    path = sdir / "jobs.json"
    _migrate_legacy_file(path)
    return path


def _rename_legacy_keys(value: Any) -> Any:
    if isinstance(value, dict):
        migrated: dict[str, Any] = {}
        for key, item in value.items():
            new_key = key.replace("task_id", "job_id")
            new_value = _rename_legacy_keys(item)
            if new_key in migrated and migrated[new_key] != new_value:
                raise ValueError(f"conflicting legacy field: {new_key}")
            migrated[new_key] = new_value
        return migrated
    if isinstance(value, list):
        return [_rename_legacy_keys(item) for item in value]
    return value


def _migrate_legacy_file(path: Path) -> None:
    """Move a valid legacy tasks.json into jobs.json once."""
    legacy_path = path.with_name("tasks.json")
    with _session_file_lock(path):
        if not legacy_path.exists():
            return
        if path.exists():
            try:
                canonical = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return
            if isinstance(canonical, dict) and isinstance(canonical.get("jobs"), dict):
                legacy_path.unlink()
            return
        try:
            blob = json.loads(legacy_path.read_text(encoding="utf-8"))
            legacy_jobs = blob.get("tasks") if isinstance(blob, dict) else None
        except Exception:
            return
        if isinstance(legacy_jobs, dict):
            _write_raw(path, _rename_legacy_keys(legacy_jobs))
            legacy_path.unlink()


def _load_raw(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(blob, dict):
        return {}
    jobs = blob.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return jobs


def _write_raw(path: Path, jobs: dict[str, dict[str, Any]]) -> None:
    from openprogram.store.session.git_session import atomic_write_text

    payload = {"version": 1, "jobs": jobs}
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, default=str, indent=2),
    )


def _commit(session_id: str, message: str) -> None:
    """Best-effort: ride the same commit machinery the dispatcher uses
    at turn-end. Failures are swallowed because jobs.json on disk is
    already correct; the git commit just records the transition."""
    try:
        from openprogram.store import default_store
        default_store().commit_turn(session_id, message)
    except Exception:
        pass


def _ensure_session(session_id: str) -> Optional[Path]:
    """Materialise the session's repo (so jobs.json lives next to a
    real meta.json / .git) and return the jobs.json path. Returns
    None if the session can't be opened — caller should treat as
    "job store unavailable" and degrade gracefully."""
    from openprogram.store import default_store
    store = default_store()
    pair = store._open(session_id, create_if_missing=True)  # noqa: SLF001
    if pair is None:
        return None
    git, _ = pair
    git._ensure_init()  # noqa: SLF001 — job store needs a real repo
    path = git.path / "jobs.json"
    _migrate_legacy_file(path)
    return path


def save_job(
    session_id: str,
    job: Job,
    *,
    commit_message: Optional[str] = None,
    _mirror: bool = True,
) -> None:
    """Idempotent write — overwrites the entry for ``job.id``."""
    path = _ensure_session(session_id)
    if path is None:
        return
    with _session_lock(session_id):
        with _session_file_lock(path):
            jobs = _load_raw(path)
            jobs[job.id] = job.to_dict()
            _write_raw(path, jobs)
    msg = commit_message or f"job: {job.id} {job.status.value}"
    _commit(session_id, msg)
    if _mirror:
        mirror_linked_job_to_caller(job)


def mirror_linked_job_to_caller(job: Job) -> None:
    """Durably mirror cross-session linked status at its origin turn."""
    caller_session_id = job.caller_session_id
    if (
        job.relation != "linked"
        or not caller_session_id
        or caller_session_id == job.parent_session_id
        or not job.origin_turn_id
    ):
        return
    from dataclasses import replace

    save_job(
        caller_session_id,
        replace(job, parent_session_id=caller_session_id),
        commit_message=f"job link: {job.id} {job.status.value}",
        _mirror=False,
    )


def load_job(session_id: str, job_id: str) -> Optional[Job]:
    path = _jobs_path(session_id)
    if path is None or not path.exists():
        return None
    with _session_lock(session_id):
        with _session_file_lock(path):
            jobs = _load_raw(path)
    row = jobs.get(job_id)
    if not row:
        return None
    try:
        return Job.from_dict(row)
    except Exception:
        return None


def list_jobs(
    session_id: str,
    *,
    status_filter: Optional[set[JobStatus]] = None,
    limit: Optional[int] = None,
) -> list[Job]:
    """Return jobs in this session, newest first (by created_at desc)."""
    path = _jobs_path(session_id)
    if path is None or not path.exists():
        return []
    with _session_lock(session_id):
        with _session_file_lock(path):
            rows = _load_raw(path)
    out: list[Job] = []
    for row in rows.values():
        try:
            t = Job.from_dict(row)
        except Exception:
            continue
        if status_filter and t.status not in status_filter:
            continue
        out.append(t)
    out.sort(key=lambda x: x.created_at or 0, reverse=True)
    if limit is not None:
        out = out[:limit]
    return out


def update_job_status(
    session_id: str,
    job_id: str,
    new_status: JobStatus,
    **fields: Any,
) -> Optional[Job]:
    """Atomic state transition + extra-field stamp.

    Validates the (from, to) edge against ``can_transition``. Raises
    ``ValueError`` on illegal transitions so callers can't accidentally
    revive a terminal job.

    ``fields`` overrides any Job attribute — typical use is
    ``head_id=...``, ``result_text=...``, ``error=...``, plus the
    timestamp fields the runner stamps explicitly.
    """
    path = _ensure_session(session_id)
    if path is None:
        return None
    old_status: JobStatus | None = None
    with _session_lock(session_id):
        with _session_file_lock(path):
            jobs = _load_raw(path)
            row = jobs.get(job_id)
            if not row:
                return None
            try:
                t = Job.from_dict(row)
            except Exception:
                return None
            if t.status == new_status:
                # Non-terminal no-op transitions may refresh progress fields.
                # A terminal row is an immutable outcome: a retry or racing
                # cancellation must not rewrite its first reason/result.
                if not is_terminal(t.status):
                    for k, v in fields.items():
                        if hasattr(t, k):
                            setattr(t, k, v)
                    jobs[job_id] = t.to_dict()
                    _write_raw(path, jobs)
            elif not can_transition(t.status, new_status):
                raise ValueError(
                    f"illegal job transition {t.status.value} → "
                    f"{new_status.value} (job {job_id})"
                )
            else:
                old_status = t.status
                t.status = new_status
                # Time-stamp the transition.
                now = time.time()
                if new_status == JobStatus.QUEUED and t.queued_at is None:
                    t.queued_at = now
                elif new_status == JobStatus.RUNNING and t.started_at is None:
                    t.started_at = now
                elif is_terminal(new_status) and t.completed_at is None:
                    t.completed_at = now
                if new_status == JobStatus.CANCELLED and t.cancel_requested_at is None:
                    t.cancel_requested_at = now
                for k, v in fields.items():
                    if hasattr(t, k):
                        setattr(t, k, v)
                jobs[job_id] = t.to_dict()
                _write_raw(path, jobs)
    if old_status is not None:
        _commit(session_id, f"job: {job_id} {old_status.value}→{new_status.value}")
    mirror_linked_job_to_caller(t)
    return t


def reconcile_orphans(
    *,
    legacy_only: bool = False,
    on_reconciled: Optional[Callable[[Job], None]] = None,
) -> int:
    """Walk every session repo, flip non-terminal jobs → errored.

    Called once at process startup (server.py / dispatcher entry).
    ``legacy_only`` leaves Jobs with durable admission ids to the
    resource reconciler.
    ``on_reconciled`` receives each canonical recovered Job after its caller
    mirror is current; caller-side linked mirrors are not reported twice.
    Returns the number of jobs reconciled.
    """
    from openprogram.store import default_store
    store = default_store()
    if not store.root_path.exists():
        return 0
    count = 0
    linked_updates: list[Job] = []
    for sdir in sorted(store.root_path.iterdir()):
        if not sdir.is_dir():
            continue
        sid = sdir.name
        path = sdir / "jobs.json"
        _migrate_legacy_file(path)
        if not path.exists():
            continue
        with _session_lock(sid):
            with _session_file_lock(path):
                try:
                    rows = _load_raw(path)
                except Exception:
                    continue
                mutated = False
                for tid, row in list(rows.items()):
                    try:
                        t = Job.from_dict(row)
                    except Exception:
                        continue
                    if is_terminal(t.status):
                        continue
                    if legacy_only and t.admission_id:
                        continue
                    old = t.status
                    t.status = JobStatus.ERRORED
                    t.completed_at = time.time()
                    t.error = "worker died before completion"
                    t.reason_code = "error.worker_lost"
                    rows[tid] = t.to_dict()
                    linked_updates.append(t)
                    mutated = True
                    count += 1
                    # Per-job git commit would be noisy on startup with
                    # many orphans; aggregate them under one commit below.
                    _ = old  # quiet linter
                if mutated:
                    _write_raw(path, rows)
        if path.exists():
            _commit(sid, f"job: reconcile orphans (startup)")
    for job in linked_updates:
        mirror_linked_job_to_caller(job)
        # A cross-session linked job is also stored as a caller-side mirror
        # whose parent_session_id was rewritten to that caller. Notify only
        # for the canonical execution row; otherwise a recovery observer
        # would update the source attach once with the target session and a
        # second time with the mirror's source session.
        is_linked_mirror = bool(
            job.relation == "linked"
            and job.caller_session_id
            and job.caller_session_id == job.parent_session_id
        )
        if on_reconciled is not None and not is_linked_mirror:
            on_reconciled(job)
    return count


__all__ = [
    "save_job",
    "load_job",
    "list_jobs",
    "update_job_status",
    "reconcile_orphans",
    "mirror_linked_job_to_caller",
]
