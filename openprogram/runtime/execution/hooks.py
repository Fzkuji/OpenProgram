"""Engine hooks: how the control plane reaches the engines built on it.

The control plane never imports an engine. Where it must start or ask one
(activate an Agent attempt, admit a restart turn, reconcile permission waits,
settle a session's Goal, read Job records), the engine registers a callable
here when its package is imported: ``openprogram.runtime.conversation``
registers the Agent and Goal hooks and ``openprogram.runtime.jobs`` the Job
hooks. Programs never register here: the Goal tool loads on first use, later
than startup recovery, so the conversation engine that runs Goal turns
registers for it and imports it when the hook is first called.

A required hook that nobody registered means no such engine runs in this
process; looking it up raises :class:`EngineNotRegistered` instead of
skipping the work. An optional hook is one whose absence is itself the
answer: without the Jobs package loaded there is no live Job runner, without
a process runner there is no subprocess to signal, and without the
conversation engine there is no chat turn whose terminal outcome a Goal
accounts.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Awaitable, Callable, ContextManager, Mapping, Protocol


class EngineNotRegistered(RuntimeError):
    """A control-plane path needs an engine that this process did not load."""

    code = "engine_not_registered"

    def __init__(self, hook: str, provider: str):
        self.hook = hook
        self.provider = provider
        super().__init__(
            f"execution hook {hook!r} is not registered; import {provider} "
            "in this process before using it"
        )


class JobRecords(Protocol):
    """Job records as the run-control layer reads and settles them.

    Jobs keep their own projection (``jobs.json`` per session) beside the
    canonical execution. Run control only needs to find a Job, list a
    session's Jobs, settle its status and reach the live runner.
    """

    def load(self, session_id: str, job_id: str) -> Any | None: ...

    def list(self, session_id: str) -> list[Any]: ...

    def is_terminal(self, job: Any) -> bool: ...

    def is_waiting(self, job: Any) -> bool:
        """True while the Job has not started (pending or queued)."""
        ...

    def update_status(self, session_id: str, job_id: str, status: str | None, **fields: Any) -> Any:
        """Write ``status`` (a status value, or ``None`` to keep it) and ``fields``."""
        ...

    def live_job(self, job_id: str) -> Any | None:
        """The runner's in-memory Job, when a runner exists in this process."""
        ...

    def cancel_live(self, job_id: str, *, reason: str) -> Any | None:
        """Cancel through the live runner; ``None`` when there is no runner."""
        ...


class LiveOwnerSignals(Protocol):
    """Signals for the live processes that run an execution."""

    def request_graceful_stop(self, session_id: str, execution_id: str) -> None: ...

    def kill(self, session_id: str, execution_id: str) -> bool:
        """Kill the execution's subprocesses; True when one was killed."""
        ...


class SessionGoals(Protocol):
    """A session's Goal as restart reconciliation must respect it."""

    def owner(self, store: Any, session_id: str) -> ContextManager[bool]:
        """Try once to take the session's Goal owner lock; yields True when held.

        A live Goal controller in another process holds it; reconciliation
        skips that session instead of rewriting state under it.
        """
        ...

    def pause_for_restart(self, store: Any, session_id: str) -> bool:
        """Pause a Goal whose run died with the previous worker; True when paused."""
        ...


Activator = Callable[[Any, Any], Awaitable[Any]]
ActivatorFactory = Callable[[Any, Any], Activator]
RecoveryTurnStarter = Callable[[Any, Any, Any], "str | None"]
PermissionWaitReconciler = Callable[..., Awaitable[None]]
JobRunnerLookup = Callable[[Any], Any]
TurnRequestFields = Callable[[], frozenset[str]]
TerminalSettler = Callable[[Any, Any], None]
OwnerlessRunCloser = Callable[[Any, Any], None]


@dataclass(frozen=True)
class _Slot:
    name: str
    provider: str
    required: bool


_SLOTS = {
    slot.name: slot
    for slot in (
        # (execution store, control service) -> async activator(attempt, activation)
        _Slot("agent_activator", "openprogram.runtime.conversation", True),
        # (store, execution, restart event) -> outcome or None
        _Slot("recovery_turn_starter", "openprogram.runtime.conversation", True),
        # async (session_id, *, service) -> None
        _Slot("permission_wait_reconciler", "openprogram.runtime.conversation", True),
        # () -> field names a stored Job turn request may carry
        _Slot("turn_request_fields", "openprogram.runtime.jobs", True),
        _Slot("job_records", "openprogram.runtime.jobs", True),
        # (execution store) -> the live JobRunner bound to it, or None
        _Slot("job_runner", "openprogram.runtime.jobs", False),
        _Slot("live_owner_signals", "openprogram.runtime.conversation", False),
        # (execution store, terminal execution) -> None; settles the chat
        # Goal that owned the execution (usage, next turn)
        _Slot("terminal_settler", "openprogram.runtime.conversation", False),
        # (execution store, execution ended with no turn running) -> None
        _Slot("ownerless_run_closer", "openprogram.runtime.conversation", False),
        # (execution store, cut-off execution no restart will continue) -> None
        _Slot("restart_abandoned", "openprogram.runtime.conversation", False),
        _Slot("session_goals", "openprogram.runtime.conversation", True),
        # Activity's roster facts: .facts(job) -> {"agent_status", "usage"},
        # .outgoing(session_id, jobs) -> unanswered sends to other conversations,
        # .roster(session_id, jobs) -> {"agents": {job id: facts}, "outgoing"}
        _Slot("agent_roster_facts", "openprogram.runtime.jobs", False),
    )
}
_registered: dict[str, Any] = {}
_lock = RLock()


def register(name: str, value: Any) -> None:
    """Install ``value`` for hook ``name``; a later registration replaces it."""
    if name not in _SLOTS:
        raise KeyError(f"unknown execution hook {name!r}")
    with _lock:
        _registered[name] = value


def unregister(name: str) -> None:
    with _lock:
        _registered.pop(name, None)


def registered(name: str) -> Any | None:
    """The hook's value, or ``None``; use for optional hooks."""
    if name not in _SLOTS:
        raise KeyError(f"unknown execution hook {name!r}")
    with _lock:
        return _registered.get(name)


def require(name: str) -> Any:
    """The hook's value; raise :class:`EngineNotRegistered` when missing."""
    value = registered(name)
    if value is None:
        slot = _SLOTS[name]
        raise EngineNotRegistered(name, slot.provider)
    return value


def snapshot() -> Mapping[str, Any]:
    """The current registrations, for tests that restore them."""
    with _lock:
        return dict(_registered)


def restore(values: Mapping[str, Any]) -> None:
    with _lock:
        _registered.clear()
        _registered.update(values)


# Typed accessors used by the control plane.

def agent_activator(executions: Any, service: Any) -> Activator:
    factory: ActivatorFactory = require("agent_activator")
    return factory(executions, service)


def start_recovery_turn(store: Any, execution: Any, event: Any) -> str | None:
    starter: RecoveryTurnStarter = require("recovery_turn_starter")
    return starter(store, execution, event)


async def reconcile_permission_waits(session_id: str, *, service: Any) -> None:
    reconciler: PermissionWaitReconciler = require("permission_wait_reconciler")
    await reconciler(session_id, service=service)


def turn_request_fields() -> frozenset[str]:
    provider: TurnRequestFields = require("turn_request_fields")
    return provider()


def job_records() -> JobRecords:
    return require("job_records")


def job_runner(store: Any) -> Any | None:
    lookup: JobRunnerLookup | None = registered("job_runner")
    return None if lookup is None else lookup(store)


def live_owner_signals() -> LiveOwnerSignals | None:
    return registered("live_owner_signals")


def settle_terminal(executions: Any, execution: Any) -> None:
    """Run the engine's settlement for an execution that reached a terminal status."""
    settler: TerminalSettler | None = registered("terminal_settler")
    if settler is not None:
        settler(executions, execution)


def close_ownerless_run(executions: Any, execution: Any) -> None:
    """Tell the engine an execution ended where no turn was left to report it."""
    closer: OwnerlessRunCloser | None = registered("ownerless_run_closer")
    if closer is not None:
        closer(executions, execution)


def restart_abandoned(executions: Any, execution: Any) -> None:
    """Tell the engine no restart will continue this cut-off execution."""
    notify: OwnerlessRunCloser | None = registered("restart_abandoned")
    if notify is not None:
        notify(executions, execution)


def session_goals() -> SessionGoals:
    return require("session_goals")
