"""What the runtime knows about the execution that is running right now.

Models and state sit below the runtime and never import it. Some of their
code still needs a fact only the runtime has: the session or execution
bound to this thread, whether a turn is running, the saved Agents, the
claimed Job's resource context. The runtime registers one provider per
fact here when it loads (``openprogram.runtime.lower_layers``); the
functions below ask for it.

Without a registered provider the answer is what it is in a process where
the runtime never ran: no session, no execution, no turn and no Job. The
two facts with no such neutral answer refuse instead: an unregistered
:func:`session_has_live_work` reports live work (migration must fail
closed), and :func:`agent_registry` raises.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_providers: dict[str, Callable[..., Any]] = {}

FACTS = frozenset({
    "current_session_id",
    "current_execution_id",
    "turn_running",
    "session_has_live_work",
    "agent_registry",
    "session_chat_model",
    "current_job_resource_context",
    "current_job_operation_timeout",
    "record_current_job_activity",
    "forget_session_runs",
    "turn_branch",
    "unfinished_jobs",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the runtime's provider for each named fact."""
    unknown = set(providers) - FACTS
    if unknown:
        raise ValueError(f"unknown execution facts: {sorted(unknown)}")
    _providers.update(providers)


def _provider(name: str) -> Callable[..., Any] | None:
    return _providers.get(name)


def current_session_id() -> str | None:
    """The session bound to the current worker context, or None."""
    provider = _provider("current_session_id")
    return provider() if provider else None


def current_execution_id() -> str | None:
    """The execution bound to the current worker context, or None."""
    provider = _provider("current_execution_id")
    return provider() if provider else None


def turn_running(session_id: str) -> bool:
    """Whether a turn is in flight on ``session_id`` in this process."""
    provider = _provider("turn_running")
    return bool(provider(session_id)) if provider else False


def forget_session_runs(session_id: str) -> None:
    """Drop a deleted session's run rows from memory; nothing to drop if no runtime ran."""
    provider = _provider("forget_session_runs")
    if provider:
        provider(session_id)


def session_has_live_work(session_id: str) -> bool:
    """Whether any execution, chat turn or Job is still live on the session.

    Unknown is unsafe to migrate through, so an unregistered provider
    answers True.
    """
    provider = _provider("session_has_live_work")
    return bool(provider(session_id)) if provider else True


def agent_registry() -> Any:
    """The saved-Agent registry (``get``, ``get_default``, ``list_all`` …)."""
    provider = _provider("agent_registry")
    if provider is None:
        raise RuntimeError(
            "No Agent registry is registered; the runtime registers one "
            "when openprogram.runtime is imported."
        )
    return provider()


def session_chat_model(session_id: str | None) -> tuple[str | None, str | None]:
    """The model pinned on a session, or (None, None)."""
    provider = _provider("session_chat_model")
    return provider(session_id) if provider else (None, None)


def current_job_resource_context() -> Any:
    """The claimed Job's resource governance context, or None."""
    provider = _provider("current_job_resource_context")
    return provider() if provider else None


def current_job_operation_timeout(default: float | None, *, preemptibility: str) -> float | None:
    """Seconds the claimed Job allows one operation, or ``default``."""
    provider = _provider("current_job_operation_timeout")
    return provider(default, preemptibility=preemptibility) if provider else default


def record_current_job_activity(activity_kind: str) -> bool:
    """Record progress on the claimed Job; False when there is none."""
    provider = _provider("record_current_job_activity")
    return bool(provider(activity_kind)) if provider else False


def turn_branch(session_id: str) -> str | None:
    """The branch of the turn in flight in this context, when that turn is
    one of ``session_id``'s; None otherwise."""
    provider = _provider("turn_branch")
    return provider(session_id) if provider else None


def unfinished_jobs(session_id: str) -> list[tuple[str, str, str]]:
    """``(id, name, status)`` of each background Job on ``session_id`` that
    has not finished; none if no runtime ran."""
    provider = _provider("unfinished_jobs")
    return list(provider(session_id)) if provider else []


__all__ = [
    "FACTS",
    "agent_registry",
    "current_execution_id",
    "current_job_operation_timeout",
    "current_job_resource_context",
    "current_session_id",
    "record_current_job_activity",
    "register",
    "session_chat_model",
    "session_has_live_work",
    "turn_branch",
    "turn_running",
    "unfinished_jobs",
]
