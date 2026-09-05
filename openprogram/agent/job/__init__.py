"""Async job lifecycle — explicit Job entity, worker pool, cancel.

See ``docs/design/runtime/async-job-lifecycle.md`` for the full design.

Public surface:

  * :class:`Job` / :class:`JobStatus` — entity + state machine
  * :func:`get_runner` — process-wide singleton JobRunner
  * :class:`JobRunner` — thread-pool executor with cancel + persistence

Status transitions (one-way):

  pending → queued → running → completed
                            ↘ cancelled
                            ↘ errored

The runner submits work to a ``ThreadPoolExecutor`` and keeps a
parallel ``threading.Event`` per job for cancel signalling. Each
state transition writes a row to ``<session>/jobs.json`` and rides
the session git commit machinery (commit message: ``job: <id>
<status>``).
"""
from __future__ import annotations

from openprogram.agent.job.types import (
    Job,
    JobStatus,
    TERMINAL_STATUSES,
    is_terminal,
    can_transition,
)
from openprogram.agent.job.runner import JobRunner, get_runner

__all__ = [
    "Job",
    "JobStatus",
    "TERMINAL_STATUSES",
    "is_terminal",
    "can_transition",
    "JobRunner",
    "get_runner",
    "ExecutionSnapshot",
    "EventCursor",
    "JobResourceDTO",
]
