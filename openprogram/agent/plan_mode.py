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
# Sessions whose plan flag was set by the permission TIER ("Plan mode"
# picked in the web chip / TUI), not by the LLM's enter_plan_mode tool.
# Tracked separately so leaving the tier only clears tier-entered plan —
# an LLM-entered plan still requires the approved exit_plan_mode call.
_by_tier: set[str] = set()
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
        _by_tier.discard(session_id)


def sync_tier(session_id: str, plan_tier: bool) -> None:
    """Reconcile the flag with the turn's permission tier.

    Called by the execute path once per turn with ``plan_tier =
    (effective_permission == "plan")``. Tier plan → set the flag (write
    tools hidden + plan system prompt, same as enter_plan_mode). Tier
    left plan → clear the flag ONLY if the tier set it; a plan the LLM
    entered via enter_plan_mode still exits through the approved
    exit_plan_mode call. While the tier stays "plan", an exit_plan_mode
    mid-turn lasts until the next turn re-syncs — switching the tier off
    plan (chip / shift+tab) is the durable way out.
    """
    if not session_id:
        return
    with _lock:
        if plan_tier:
            _active.add(session_id)
            _by_tier.add(session_id)
        elif session_id in _by_tier:
            _by_tier.discard(session_id)
            _active.discard(session_id)


def current() -> bool:
    """Convenience: ``True`` iff the current contextvar session is in
    plan mode. Tools running on the dispatcher's loop can call this
    without arguments.
    """
    return is_plan_mode(current_session_id.get())
