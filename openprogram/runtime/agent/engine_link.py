"""The link between the call core and the engine that runs turns on it.

The call core (``openprogram.runtime.agent``) makes model calls and runs the
tool loop. Some of what it does needs the engine above it: a persistent
``Agent.run_turn`` is a conversation turn on a saved Agent's profile and
tools, a tool loop asks whether the worker is stopping and tells a running
background job it is alive, a resumed tool call binds the question wait it
was admitted with, and a resumable loop checkpoints the runtime contract.
The core asks for these through the functions below and never imports the
engine. The conversation engine (``openprogram.runtime.conversation``)
registers its implementation with :func:`register` when it is imported; the
worker and the Server import it at start.

What sits beside or below the core is imported directly, not linked here:
the bound session and execution, nested-call admission and terminal marking
(``openprogram.runtime.execution.run_control``), approval gating and the
pre-approved wait id (``openprogram.runtime.permissions``).

With no engine registered (a plain library use of ``Agent`` / ``agent()``)
the worker is not stopping, there is no background job, binding a wait does
nothing, and ``Agent.run_turn`` / ``Agent.resume_turn`` raise
:class:`EngineUnavailable`.
"""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any, ContextManager, Protocol


class EngineUnavailable(RuntimeError):
    """A call needs the conversation engine and none is registered."""


class EngineLink(Protocol):
    """The operations the conversation engine implements for the call core."""

    # Persistent turns -----------------------------------------------------

    def run_turn(self, request: Any, *, on_event: Any, cancel_event: Any,
                 execution_context: Any) -> Any:
        """Run one persistent session turn for ``request`` and return its result."""

    def resume_turn(self, continuation: Any, *, on_event: Any, cancel_event: Any,
                    execution_context: Any) -> Any:
        """Resume a saved turn continuation without re-admitting it."""

    def agent_profile(self, agent_id: str) -> dict:
        """The saved profile a persistent turn of ``agent_id`` starts from."""

    def default_agent_profile(self) -> dict:
        """The saved profile of the default Agent, for a plain call's system prompt."""

    def resolve_tools(self, profile: dict, override: Any = None) -> Any:
        """The tools a saved Agent profile enables, narrowed by ``override``."""

    # The running turn -----------------------------------------------------

    def preapproved_wait(self, wait_id: str) -> ContextManager[None]:
        """Bind ``wait_id`` as the pre-approved wait for the enclosed tool call."""

    def worker_stopping(self) -> bool:
        """Whether the worker process is shutting down."""

    def record_job_activity(self, kind: str) -> None:
        """Tell the background job running this call that it is still alive."""

    def job_operation_timeout(self, declared: float | None) -> float | None:
        """The timeout one operation of the running background job may use."""

    # Continuation ---------------------------------------------------------

    def runtime_contract_snapshot(self, **fields: Any) -> dict:
        """The runtime contract a resumable tool loop checkpoints."""


_engine: EngineLink | Any | None = None


def register(engine: EngineLink | Any) -> None:
    """Make ``engine`` the engine of this process (the last one wins)."""
    global _engine
    _engine = engine


def unregister(engine: EngineLink | Any | None = None) -> None:
    """Drop the registered engine, or only ``engine`` when it is the current one."""
    global _engine
    if engine is None or _engine is engine:
        _engine = None


def current() -> EngineLink | Any | None:
    return _engine


def _required(operation: str) -> EngineLink | Any:
    if _engine is None:
        raise EngineUnavailable(
            f"{operation} needs the conversation engine; import "
            "openprogram.runtime.conversation (the worker and the Server do) "
            "before running persistent turns."
        )
    return _engine


def run_turn(request: Any, *, on_event: Any = None, cancel_event: Any = None,
             execution_context: Any = None) -> Any:
    return _required("Agent.run_turn").run_turn(
        request, on_event=on_event, cancel_event=cancel_event,
        execution_context=execution_context,
    )


def resume_turn(continuation: Any, *, on_event: Any = None, cancel_event: Any = None,
                execution_context: Any = None) -> Any:
    return _required("Agent.resume_turn").resume_turn(
        continuation, on_event=on_event, cancel_event=cancel_event,
        execution_context=execution_context,
    )


def agent_profile(agent_id: str) -> dict:
    return _required("Agent.run_turn").agent_profile(agent_id)


def default_agent_profile() -> dict:
    """The default Agent's profile; ``{}`` with no engine (no saved Agents)."""
    return _engine.default_agent_profile() if _engine is not None else {}


def resolve_tools(profile: dict, override: Any = None) -> Any:
    return _required("A saved Agent's tools").resolve_tools(profile, override=override)


def preapproved_wait(wait_id: str) -> ContextManager[None]:
    return _engine.preapproved_wait(wait_id) if _engine is not None else nullcontext()


def worker_stopping() -> bool:
    return bool(_engine.worker_stopping()) if _engine is not None else False


def raise_if_worker_stopping() -> None:
    if worker_stopping():
        from openprogram.models.providers.utils.errors import ExecInterrupt
        raise ExecInterrupt("worker_stopping")


def record_job_activity(kind: str) -> None:
    if _engine is not None:
        _engine.record_job_activity(kind)


def job_operation_timeout(declared: float | None) -> float | None:
    return _engine.job_operation_timeout(declared) if _engine is not None else declared


def runtime_contract_snapshot(**fields: Any) -> dict:
    return _required("A resumable tool loop").runtime_contract_snapshot(**fields)


__all__ = [
    "EngineLink", "EngineUnavailable", "register", "unregister", "current",
    "run_turn", "resume_turn", "agent_profile", "default_agent_profile", "resolve_tools",
    "preapproved_wait",
    "worker_stopping", "raise_if_worker_stopping",
    "record_job_activity", "job_operation_timeout",
    "runtime_contract_snapshot",
]
