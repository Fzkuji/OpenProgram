"""What usage metering needs from the Goal tool.

A chat Goal carries a token / cost budget, so the usage ledger checks the
Goal before a provider request starts and refreshes the Goal's usage after
one ends. Models sit below Programs and never import the Goal tool
(``openprogram.programs.tools.planning.goal``); this port owns the
vocabulary both sides share and the Goal tool fills in the behaviour when
it loads (``openprogram.programs._host.lower_layers``).

Owned here, so there is one definition of each:

* :class:`GoalConflictError`, :class:`GoalStateUnavailable` — the Goal's
  two refusals, raised by the ledger and the Goal tool alike, with the two
  conflicts that decide nothing about the Goal: :class:`GoalBusy` (the lock
  was held) and :class:`GoalVersionConflict` (the write met a newer Goal);
* :data:`turn_goal` — the Goal identity bound to the running chat turn.

Registered by the Goal tool, with the answer of a process where no Goal
tool ever loaded as the default: :func:`locked` holds nothing,
:func:`load_goal` finds no Goal, :func:`publish`,
:func:`refresh_usage` and :func:`accumulate_goal_usage` do nothing.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Optional


class GoalConflictError(ValueError):
    """A stale controller attempted to replace a newer Goal snapshot."""


class GoalBusy(GoalConflictError):
    """The session's Goal lock stayed held for the whole wait.

    Nothing about the Goal was decided: a caller that only keeps the Goal's
    books (usage, elapsed time, a finished turn's accounting) defers or
    retries, and never fails the reply it belongs to.
    """


class GoalVersionConflict(GoalConflictError):
    """A Goal write met a version newer than the one it read.

    The write changed nothing. A writer without the lock reads again and
    redoes its step.
    """


class GoalStateUnavailable(RuntimeError):
    """Persistent Goal state could not be read or committed safely."""


turn_goal: ContextVar[dict | None] = ContextVar("chat_goal_turn", default=None)

_providers: dict[str, Callable[..., Any]] = {}

BEHAVIOURS = frozenset({
    "locked",
    "publish",
    "refresh_usage",
    "load_goal",
    "accumulate_goal_usage",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the Goal tool's provider for each named behaviour."""
    unknown = set(providers) - BEHAVIOURS
    if unknown:
        raise ValueError(f"unknown Goal behaviours: {sorted(unknown)}")
    _providers.update(providers)


@contextmanager
def locked(session_id: str) -> Iterator[None]:
    """Hold the session's Goal lock for the block; :class:`GoalBusy` when it
    stays held for the whole wait."""
    provider = _providers.get("locked")
    if provider is None:
        yield
        return
    with provider(session_id):
        yield


def load_goal(session_id: str) -> Optional[dict]:
    """The session's Goal, read fresh, or None."""
    provider = _providers.get("load_goal")
    return provider(session_id) if provider else None


def publish(session_id: str, goal: dict) -> None:
    """Save ``goal`` and tell the session's watchers."""
    provider = _providers.get("publish")
    if provider:
        provider(session_id, goal)


def refresh_usage(session_id: str) -> None:
    """Project the ledger's usage onto the session's Goal."""
    provider = _providers.get("refresh_usage")
    if provider:
        provider(session_id)


def accumulate_goal_usage(session_id: str, goal: dict) -> None:
    """Add the usage recorded since the Goal's last boundary to ``goal``."""
    provider = _providers.get("accumulate_goal_usage")
    if provider:
        provider(session_id, goal)


__all__ = [
    "BEHAVIOURS",
    "GoalBusy",
    "GoalConflictError",
    "GoalStateUnavailable",
    "GoalVersionConflict",
    "accumulate_goal_usage",
    "load_goal",
    "locked",
    "publish",
    "refresh_usage",
    "register",
    "turn_goal",
]
