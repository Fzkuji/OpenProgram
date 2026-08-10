"""The TurnRequest in force for the current execution context.

An ``@agentic_function`` body running ``runtime.exec()`` builds its own
inner ``AgentSession``. That session's tools used to be handed to the
agent loop raw — no approval wrapper, no authority, no hard constraints —
so an agent spawned from inside a program was effectively unsupervised
while the identical tool on the outer loop was gated.

The outer request is what carries the non-downgradable part of the
execution context (source, permission mode, authority tier, principal),
so the dispatcher binds it here and the runtime reads it back to derive
an inner request via ``runtime_authority``. Nothing bound (a library-only
``Runtime()`` with no dispatcher above it) means no inner gating, which
is the same position as before for that path.
"""
from __future__ import annotations

import contextvars
from typing import Any, Optional

current_turn_request: contextvars.ContextVar[Optional[Any]] = (
    contextvars.ContextVar("openprogram_current_turn_request", default=None)
)


def set_turn_request(req: Any):
    return current_turn_request.set(req)


def get_turn_request() -> Optional[Any]:
    return current_turn_request.get(None)


def reset_turn_request(token) -> None:
    try:
        current_turn_request.reset(token)
    except ValueError:
        pass


def inner_turn_request(source: str) -> Optional[Any]:
    """Derive the request an inner AgentSession's tools are gated by.

    Inherits the outer request's authority through ``runtime_authority``
    (which pins ``interaction="non-interactive"`` and the runtime speaker),
    and keeps source/permission_mode/permission_rules so neither the hard
    constraints nor a deny rule can be dropped by descending a level.
    """
    outer = get_turn_request()
    if outer is None:
        return None
    from openprogram.agent.authority import runtime_authority
    from openprogram.agent.dispatcher import TurnRequest

    authority = runtime_authority(outer, source)
    if not authority:
        return None
    return TurnRequest(
        session_id=getattr(outer, "session_id", "") or "",
        user_text="",
        agent_id=getattr(outer, "agent_id", "main") or "main",
        # A nested program cannot widen the outer source. agent_spawn and
        # cron stay themselves so their hard-constraint sets keep applying.
        source=getattr(outer, "source", "web") or "web",
        permission_mode=getattr(outer, "permission_mode", None),
        permission_rules=getattr(outer, "permission_rules", None),
        additional_working_dirs=list(
            getattr(outer, "additional_working_dirs", None) or ()
        ),
        **authority,
    )


__all__ = [
    "current_turn_request", "set_turn_request", "get_turn_request",
    "reset_turn_request", "inner_turn_request",
]
