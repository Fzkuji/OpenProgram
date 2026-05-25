"""Plan-mode session flag — process-wide, in-memory.

A session is "in plan mode" when the LLM has called ``enter_plan_mode``
and has not yet successfully called ``exit_plan_mode``. While the flag
is set:

  * The dispatcher hides write/mutate tools from the LLM (see
    ``apply_tool_policy(source="plan", ...)``).
  * The system prompt gets a plan-mode reminder appended.

The flag is held in a module-level dict keyed by ``session_id``, not
persisted to SessionDB. Rationale: plan mode is a within-conversation
working state, not a long-lived attribute of the conversation. A worker
restart resets every session to non-plan-mode — the LLM can simply re-
enter plan mode on the next turn if it still wants to plan.

Tools read/write the flag via :func:`is_plan_mode`, :func:`enter`,
:func:`exit`. The current session_id is set by the dispatcher at turn
start through :data:`current_session_id` (a contextvar), so tools
running on the asyncio loop can recover it without args plumbing.
"""
from __future__ import annotations

import contextvars
import threading
from typing import Optional


_active: set[str] = set()
_lock = threading.Lock()


# Set by ``process_user_turn`` at the start of every turn so tools can
# recover the session_id without us threading it through every signature.
current_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openprogram_plan_mode_session_id", default=None,
)


def is_plan_mode(session_id: Optional[str]) -> bool:
    if not session_id:
        return False
    with _lock:
        return session_id in _active


def enter(session_id: str) -> None:
    if not session_id:
        return
    with _lock:
        _active.add(session_id)


def exit(session_id: str) -> None:  # noqa: A003 — mirrors the tool name
    if not session_id:
        return
    with _lock:
        _active.discard(session_id)


def current() -> bool:
    """Convenience: ``True`` iff the current contextvar session is in
    plan mode. Tools running on the dispatcher's loop can call this
    without arguments.
    """
    return is_plan_mode(current_session_id.get())
