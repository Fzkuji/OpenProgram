"""The conversation side of a Job, as the jobs machinery reaches it.

The runtime's parts are ordered: ``jobs`` sits below ``conversation`` and
never imports it. This module is the link between them; it imports no part.

Jobs are the durable background work: the store, the runner, the resource
governor and the notices that say a Job ended. The conversation part sits
above them: its turns start Jobs, wait on them and read their results. A
Job's own work is a conversation's work too, though: an Agent Job runs as
an agent turn, a process Job is killed through the conversation's process
runner, a Job is admitted under its conversation's settings, its card is a
node of the conversation it was started from, and a message held for a busy
agent waits in that conversation's inbox. The runtime's assembly
(``openprogram.runtime.lower_layers``) registers one provider for each of
these when the runtime loads; the functions below ask for it. Every provider
imports its conversation module when it is called, so a test that replaces a
function there is what the runner reaches.

:data:`LINKS` is the whole contract: registration fails for any other name.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

LINKS = frozenset({
    "agent_driver",
    "agent_capabilities",
    "agent_adapter",
    "kill_process",
    "unregister_cancel_event",
    "conversation_permission",
    "freeze_branch_request",
    "branch_holding",
    "inbox",
    "subscribe_idle",
    "attach_card",
    "write_attach_placeholder",
})

_providers: dict[str, Callable[..., Any]] = {}


def register(**providers: Callable[..., Any]) -> None:
    """Install the conversation's provider for each named link."""
    unknown = set(providers) - LINKS
    if unknown:
        raise ValueError(f"unknown conversation links: {sorted(unknown)}")
    _providers.update(providers)


def _provider(name: str) -> Callable[..., Any]:
    provider = _providers.get(name)
    if provider is None:
        raise RuntimeError(f"the conversation runtime has not registered {name!r}")
    return provider


# ── the agent turn a Job runs ─────────────────────────────────────────────

def agent_driver(executions: Any, **options: Any) -> Any:
    """The production driver that owns an Agent Job's canonical turn
    (``AgentProductionDriver(executions, **options)``)."""
    return _provider("agent_driver")(executions, **options)


def agent_capabilities(payload: Any) -> Any:
    """What a canonical agent turn with this input can do (pause, step, steer …)."""
    return _provider("agent_capabilities")(payload)


def agent_adapter(**options: Any) -> Any:
    """An adapter that admits and activates one agent turn the runtime starts
    itself (a Job's notice turn)."""
    return _provider("agent_adapter")(**options)


def kill_process(session_id: str, *, execution_id: str) -> bool:
    """Kill the subprocess a run in this conversation started; whether one went."""
    return bool(_provider("kill_process")(session_id, execution_id=execution_id))


def unregister_cancel_event(session_id: str, cancel_event: Any, *, execution_id: str) -> None:
    """Forget the cancel event registered for a run that never started."""
    _provider("unregister_cancel_event")(session_id, cancel_event, execution_id=execution_id)


# ── the conversation's settings ───────────────────────────────────────────

def conversation_permission(session_id: str, **options: Any) -> dict[str, Any]:
    """The permission mode and rules a turn sent in this conversation runs with."""
    return _provider("conversation_permission")(session_id, **options)


def freeze_branch_request(request: dict[str, Any]) -> dict[str, Any]:
    """The request with the settings of the branch it runs on written into it."""
    return _provider("freeze_branch_request")(request)


def branch_holding(session_id: str, message_id: Any) -> Any:
    """The branch of a conversation that holds this message."""
    return _provider("branch_holding")(session_id, message_id)


# ── messages and cards ───────────────────────────────────────────────────

def inbox() -> Any:
    """The inbox of messages queued for busy agents
    (``conversation.peer_agents.inbox``)."""
    return _provider("inbox")()


def subscribe_idle(callback: Callable[[], None]) -> Callable[[], None]:
    """Call ``callback`` whenever a conversation may have gone quiet; the
    function returned unsubscribes."""
    return _provider("subscribe_idle")(callback)


def attach_card(db: Any, session_id: str, node_id: Any) -> Any:
    """The node of a spawned agent's card in the conversation it was started from."""
    return _provider("attach_card")(db, session_id, node_id)


def write_attach_placeholder(**card: Any) -> Any:
    """Write the running card of a spawned agent; its node id, or None."""
    return _provider("write_attach_placeholder")(**card)


__all__ = [
    "LINKS", "agent_adapter", "agent_capabilities", "agent_driver", "attach_card",
    "branch_holding", "conversation_permission", "freeze_branch_request", "inbox",
    "kill_process", "register", "subscribe_idle", "unregister_cancel_event",
    "write_attach_placeholder",
]
