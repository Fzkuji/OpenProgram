"""What the runtime and Programs tell the host about the work running now.

``openprogram.host`` sits below the runtime and Programs and never imports
them. The bound session and execution already come down through
``state.execution_facts``; the remaining facts host code needs arrive here.
The runtime registers one provider per fact when it loads
(``openprogram.runtime.lower_layers``); the functions below ask for it.

Without a registered provider each answer is what it is in a process where
the runtime never ran: no execution records, no running call, no turn, no
tool policy, no conversation settings, nobody to tell.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_providers: dict[str, Callable[..., Any]] = {}

FACTS = frozenset({
    "execution",
    "conversation_execution_ids",
    "worker_stopping",
    "foreground_turn",
    "current_call_id",
    "current_tool_call_id",
    "turn_request_source",
    "allowed_tool_names",
    "session_workdir",
    "session_additional_working_dirs",
    "surface_context",
    "broadcast",
    "load_programs",
})


def register(**providers: Callable[..., Any]) -> None:
    """Install the runtime's provider for each named fact."""
    unknown = set(providers) - FACTS
    if unknown:
        raise ValueError(f"unknown runtime facts: {sorted(unknown)}")
    _providers.update(providers)


def _provider(name: str) -> Callable[..., Any] | None:
    return _providers.get(name)


def execution(execution_id: str) -> Any:
    """The stored execution record (``session_id``, ``status``,
    ``status_version`` …), or None when there is none."""
    provider = _provider("execution")
    return provider(execution_id) if provider else None


def conversation_execution_ids(session_id: str) -> list[str]:
    """Ids of the executions that belong to a conversation, its branches included."""
    provider = _provider("conversation_execution_ids")
    return list(provider(session_id)) if provider else []


def worker_stopping() -> bool:
    """Whether this worker is shutting down and admits no new work."""
    provider = _provider("worker_stopping")
    return bool(provider()) if provider else False


def foreground_turn(session_id: str) -> Any:
    """The cancellation token of the foreground turn running on a session
    (its ``execution_id`` names the run), or None between turns."""
    provider = _provider("foreground_turn")
    return provider(session_id) if provider else None


def current_call_id() -> str:
    """DAG node id of the Agent method running in this context, or ""."""
    provider = _provider("current_call_id")
    return (provider() or "") if provider else ""


def current_tool_call_id() -> str | None:
    """Provider tool-call id running in this context, or None."""
    provider = _provider("current_tool_call_id")
    return provider() if provider else None


def turn_request_source() -> str | None:
    """The ``source`` of the turn request bound to this context, or None."""
    provider = _provider("turn_request_source")
    return provider() if provider else None


def allowed_tool_names() -> set[str] | None:
    """The tool names this turn's resolved tool policy allows, or None when
    no policy is frozen (standalone trusted calls)."""
    provider = _provider("allowed_tool_names")
    return provider() if provider else None


def session_workdir(session_id: str) -> str | None:
    """The working directory a conversation's tools start in, or None."""
    provider = _provider("session_workdir")
    return provider(session_id) if provider else None


def session_additional_working_dirs(session_id: str) -> tuple[str, ...]:
    """The extra working directories a conversation was given."""
    provider = _provider("session_additional_working_dirs")
    return tuple(provider(session_id)) if provider else ()


def surface_context() -> dict | None:
    """The App surface the running turn came from (its window ids), or None."""
    provider = _provider("surface_context")
    return provider() if provider else None


def broadcast(frame: dict) -> None:
    """Send one ``{"type", "data"}`` frame to every connected client."""
    provider = _provider("broadcast")
    if provider:
        provider(frame)


def load_programs() -> None:
    """Load the built-in Programs, so their resource providers register."""
    provider = _provider("load_programs")
    if provider:
        provider()


__all__ = [
    "FACTS",
    "allowed_tool_names",
    "broadcast",
    "conversation_execution_ids",
    "current_call_id",
    "current_tool_call_id",
    "execution",
    "foreground_turn",
    "load_programs",
    "register",
    "session_additional_working_dirs",
    "session_workdir",
    "surface_context",
    "turn_request_source",
    "worker_stopping",
]
