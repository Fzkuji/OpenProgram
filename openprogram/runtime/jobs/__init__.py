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
state transition rewrites ``jobs.json``, a line in the session's log.
"""
from __future__ import annotations

from openprogram.runtime.jobs.types import (
    Job,
    JobStatus,
    TERMINAL_STATUSES,
    is_terminal,
    can_transition,
)
from openprogram.runtime.jobs.runner import JobRunner, get_runner

# Register the Job hooks with the execution control plane.
from openprogram.runtime.jobs import execution_hooks as _execution_hooks  # noqa: E402,F401

__all__ = [
    "Job",
    "JobStatus",
    "TERMINAL_STATUSES",
    "is_terminal",
    "can_transition",
    "JobRunner",
    "get_runner",
]
