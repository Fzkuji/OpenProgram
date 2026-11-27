"""The scheduler, as the conversation runtime reaches it.

The scheduler sits above the conversations: it fires reminders and
scheduled tasks into them and settles a scheduled run when its turn ends.
The conversation runtime never imports it; this module is the link between
them and imports no part. What it still needs from it
arrives here: a resumed or ownerless turn that ended is offered to the
scheduler, which finishes it when it is a task's run; an ended run's pages
close; the always-on inbox names the scheduled tasks its reports came from;
a report pops up as a system notification when no desktop App shows it; and
the always-on ticker stops unattended runs past their time limit and keeps
looking while one is open. The
runtime's assembly (``openprogram.runtime.lower_layers``) registers one
provider for each when the runtime loads; each imports its scheduler module
when it is called, so a test that replaces a scheduler function is what the
conversation reaches.

Without a registered provider each is what it is in a process with no
scheduler: no run is a task's, no page closes, there are no tasks, nobody
is notified and no run is under a limit.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

LINKS = frozenset({
    "run_ended",
    "close_run_pages",
    "scheduled_tasks",
    "notify_unless_shown",
    "stop_overdue",
    "limited_runs_open",
})

_providers: dict[str, Callable[..., Any]] = {}


def register(**providers: Callable[..., Any]) -> None:
    """Install the scheduler's provider for each named link."""
    unknown = set(providers) - LINKS
    if unknown:
        raise ValueError(f"unknown scheduler links: {sorted(unknown)}")
    _providers.update(providers)


def run_ended(executions: Any, execution: Any, result: Any) -> None:
    """A turn this Worker continued or closed has stopped (``result`` is None
    when it stopped with no turn running): finish it if it is a task's run."""
    provider = _providers.get("run_ended")
    if provider is not None:
        provider(executions, execution, result)


def close_run_pages(execution_id: str) -> None:
    """The pages an ended run opened close with it."""
    provider = _providers.get("close_run_pages")
    if provider is not None:
        provider(execution_id)


def scheduled_tasks() -> list[dict[str, Any]]:
    """Every scheduled task."""
    provider = _providers.get("scheduled_tasks")
    return list(provider()) if provider is not None else []


def notify_unless_shown(notice_id: str, title: str, message: str,
                        unless: Callable[[], bool] | None = None) -> None:
    """Post a system notification unless a desktop App says it showed it;
    ``unless`` is asked when the wait ends."""
    provider = _providers.get("notify_unless_shown")
    if provider is not None:
        provider(notice_id, title, message, unless=unless)


def stop_overdue(now: float) -> list[str]:
    """Stop the unattended runs past their time limit; their execution ids."""
    provider = _providers.get("stop_overdue")
    return list(provider(now) or []) if provider is not None else []


def limited_runs_open() -> bool:
    """Whether any unfinished run has a time limit, so :func:`stop_overdue`
    has something to look at."""
    provider = _providers.get("limited_runs_open")
    return bool(provider()) if provider is not None else False


__all__ = [
    "LINKS", "close_run_pages", "limited_runs_open", "notify_unless_shown", "register",
    "run_ended", "scheduled_tasks", "stop_overdue",
]
