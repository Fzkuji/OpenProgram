"""Job persistence — one ``jobs.json`` document per session, kept in its log.

Schema:

  {"jobs": {<job_id>: <Job.to_dict>, ...}, "version": 1}

The store is intentionally per-session: jobs always belong to one
session (design D6), and every write is one line of that session's log,
so the log replays the job lifecycle. No cross-session queries — UI's
"list all running jobs" enumerates sessions and asks each store.

Concurrency: every public method takes the lock for the session (one
``threading.Lock`` per session_id). Inside the lock we read → mutate →
write. The runner submits state transitions from worker threads; the
lock serialises them.

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
from typing import Any, Callable, Optional

from openprogram.host._platform import locking

from openprogram.runtime.jobs.types import (
    Job,
    JobStatus,
    is_terminal,
    can_transition,
)
import logging

_log = logging.getLogger(__name__)


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
def _session_file_lock(path):
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        locking.flock(handle.fileno(), locking.LOCK_EX)
        try:
            yield
        finally:
            locking.flock(handle.fileno(), locking.LOCK_UN)


def _jobs_path(session_id: str):
    """The session's jobs.json document, or None if the session
    doesn't exist (e.g. the session was deleted)."""
    from openprogram.state.store import default_store
    store = default_store()
    sdir = store._session_dir(session_id)  # noqa: SLF001 — re-read after session lock
    if not sdir.exists():
        return None
    return store.state_file(session_id, "jobs.json")


def _load_raw(path) -> dict[str, dict[str, Any]]:
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


def _write_raw(path, jobs: dict[str, dict[str, Any]]) -> None:
    payload = {"version": 1, "jobs": jobs}
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


def _ensure_session(session_id: str):
    """Materialise the session and return its jobs.json document. Returns
    None if the session can't be opened — caller should treat as
    "job store unavailable" and degrade gracefully."""
    from openprogram.state.store import default_store
    store = default_store()
    pair = store._open(session_id, create_if_missing=True)  # noqa: SLF001
    if pair is None:
        return None
    session_log, _ = pair
    session_log._ensure_init()  # noqa: SLF001 — the job needs a session log
    return store.state_file_at(session_log.path, "jobs.json")


def save_job(
    session_id: str,
    job: Job,
    *,
    _mirror: bool = True,
) -> None:
    """Idempotent write — overwrites the entry for ``job.id``."""
    from openprogram.state.store.session.session_lock import session_interprocess_lock
    with session_interprocess_lock(session_id):
        path = _ensure_session(session_id)
        if path is None:
            return
        with _session_lock(session_id):
            with _session_file_lock(path):
                jobs = _load_raw(path)
                jobs[job.id] = job.to_dict()
                _write_raw(path, jobs)
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
        # Only the Job's own row carries its unsent notice.
        replace(job, parent_session_id=caller_session_id, notice_pending=None),
        _mirror=False,
    )


def settle_notice(session_id: str, job_id: str) -> None:
    """The parent's notice for this Job is queued (or not owed): clear the mark."""
    from openprogram.state.store.session.session_lock import session_interprocess_lock
    with session_interprocess_lock(session_id):
        path = _jobs_path(session_id)
        if path is None or not path.exists():
            return
        with _session_lock(session_id):
            with _session_file_lock(path):
                jobs = _load_raw(path)
                row = jobs.get(job_id)
                if not row or not row.get("notice_pending"):
                    return
                row["notice_pending"] = None
                _write_raw(path, jobs)


def load_job(session_id: str, job_id: str) -> Optional[Job]:
    from openprogram.state.store.session.session_lock import session_interprocess_lock
    with session_interprocess_lock(session_id):
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
    lock_timeout: Optional[float] = None,
) -> list[Job]:
    """Return jobs in this session, newest first (by created_at desc).

    With ``lock_timeout``, raises :class:`TimeoutError` if another process
    keeps the session locked that long; by default it waits for the lock.
    """
    from openprogram.state.store.session.session_lock import session_interprocess_lock
    with session_interprocess_lock(session_id, timeout=lock_timeout):
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
    *,
    expected_status: Optional[JobStatus] = None,
    **fields: Any,
) -> Optional[Job]:
    """Atomic state transition + extra-field stamp.

    Validates the (from, to) edge against ``can_transition``. Raises
    ``ValueError`` on illegal transitions so callers can't accidentally
    revive a terminal job.

    ``fields`` overrides any Job attribute — typical use is
    ``head_id=...``, ``result_text=...``, ``error=...``, plus the
    timestamp fields the runner stamps explicitly.

    With ``expected_status``, a stale caller receives the current row without
    changing any fields. The comparison shares the write lock so a progress
    stamp cannot race a terminal transition and try to resurrect the job.
    """
    from openprogram.state.store.session.session_lock import session_interprocess_lock
    with session_interprocess_lock(session_id):
        path = _ensure_session(session_id)
        if path is None:
            return None
        result = _update_job_status_locked(
            session_id, path, job_id, new_status,
            expected_status=expected_status, fields=fields)
    if result is None:
        return None
    mirror_linked_job_to_caller(result)
    return result


def _update_job_status_locked(
    session_id: str, path, job_id: str, new_status: JobStatus,
    *, expected_status: Optional[JobStatus], fields: dict[str, Any],
) -> Optional[Job]:
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
            if expected_status is not None and t.status != expected_status:
                return t
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
                t.status = new_status
                # Time-stamp the transition.
                now = time.time()
                if new_status == JobStatus.QUEUED and t.queued_at is None:
                    t.queued_at = now
                elif new_status == JobStatus.RUNNING and t.started_at is None:
                    t.started_at = now
                elif is_terminal(new_status) and t.completed_at is None:
                    t.completed_at = now
                if is_terminal(new_status):
                    # The parent still has to hear this ended; settled once
                    # its notice is queued (jobs/handoff.py).
                    from openprogram.runtime.jobs.handoff import this_process
                    t.notice_pending = this_process()
                if new_status == JobStatus.CANCELLED and t.cancel_requested_at is None:
                    t.cancel_requested_at = now
                for k, v in fields.items():
                    if hasattr(t, k):
                        setattr(t, k, v)
                jobs[job_id] = t.to_dict()
                _write_raw(path, jobs)
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
    from openprogram.state.store import default_store
    store = default_store()
    if not store.root_path.exists():
        return 0
    count = 0
    linked_updates: list[Job] = []
    from openprogram.state.store.session.placement import iter_session_dirs
    for sid, sdir in iter_session_dirs(store.root_path):
        path = store.state_file_at(sdir, "jobs.json")
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
