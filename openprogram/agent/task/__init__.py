"""Async task lifecycle — explicit Task entity, worker pool, cancel.

See ``docs/design/runtime/async-task-lifecycle.md`` for the full design.

Public surface:

  * :class:`Task` / :class:`TaskStatus` — entity + state machine
  * :func:`get_runner` — process-wide singleton TaskRunner
  * :class:`TaskRunner` — thread-pool executor with cancel + persistence

Status transitions (one-way):

  pending → queued → running → completed
                            ↘ cancelled
                            ↘ errored

The runner submits work to a ``ThreadPoolExecutor`` and keeps a
parallel ``threading.Event`` per task for cancel signalling. Each
state transition writes a row to ``<session>/tasks.json`` and rides
the session git commit machinery (commit message: ``task: <id>
<status>``).
"""
from __future__ import annotations

from openprogram.agent.task.types import (
    Task,
    TaskStatus,
    TERMINAL_STATUSES,
    is_terminal,
    can_transition,
)
from openprogram.agent.task.runner import TaskRunner, get_runner

__all__ = [
    "Task",
    "TaskStatus",
    "TERMINAL_STATUSES",
    "is_terminal",
    "can_transition",
    "TaskRunner",
    "get_runner",
]
