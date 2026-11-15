"""Plan-mode session flag — process-wide, in-memory.

The flag only decides the plan-mode note: beside the message of the
conversation's own turn (:func:`note_for`), or at the end of the prompt of
a turn with a prompt of its own. Whether a turn is in Plan, for refusing
tools, is read from the turn's permission mode alone, so nothing a model
does can restrict a conversation whose selector says otherwise.

The flag is held in a module-level set keyed by ``(session_id, branch_id)``,
not persisted to SessionDB: each branch has its own permission, Plan
included, so a plan entered on one branch leaves its siblings alone. Rationale: plan mode is a within-conversation
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


# Keys are ``(session_id, branch_id)``: each branch of a conversation has its
# own permission, and Plan is part of it. A flag set outside a turn (no
# branch known) has ``branch_id`` None and covers only callers that name none.
_active: set[tuple[str, Optional[str]]] = set()
# Sessions whose plan flag was set by the permission TIER ("Plan mode"
# picked in the web chip / TUI), not by the LLM's enter_plan_mode tool.
# Tracked separately so leaving the tier only clears tier-entered plan —
# an LLM-entered plan still requires the approved exit_plan_mode call.
_by_tier: set[tuple[str, Optional[str]]] = set()
_lock = threading.Lock()


# Set by ``process_user_turn`` at the start of every turn so tools can
# recover the session_id without us threading it through every signature.
current_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "openprogram_plan_mode_session_id", default=None,
)


def _key(session_id: str, branch_id: Optional[str]) -> tuple[str, Optional[str]]:
    """The flag's key; the branch defaults to the running turn's own."""
    if branch_id is None:
        from openprogram.runtime.agent.turn_request import get_turn_request
        req = get_turn_request()
        if getattr(req, "session_id", None) == session_id:
            branch_id = getattr(req, "branch_id", None)
    return session_id, branch_id


def is_plan_mode(session_id: Optional[str], branch_id: Optional[str] = None) -> bool:
    if not session_id:
        return False
    key = _key(session_id, branch_id)
    with _lock:
        return key in _active


def enter(session_id: str, branch_id: Optional[str] = None) -> None:
    if not session_id:
        return
    key = _key(session_id, branch_id)
    with _lock:
        _active.add(key)


def exit(session_id: str, branch_id: Optional[str] = None) -> None:  # noqa: A003 — mirrors the tool name
    if not session_id:
        return
    key = _key(session_id, branch_id)
    with _lock:
        _active.discard(key)
        _by_tier.discard(key)


def sync_tier(session_id: str, plan_tier: bool, branch_id: Optional[str] = None) -> None:
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
    key = _key(session_id, branch_id)
    with _lock:
        if plan_tier:
            _active.add(key)
            _by_tier.add(key)
        elif key in _by_tier:
            _by_tier.discard(key)
            _active.discard(key)


def current() -> bool:
    """Convenience: ``True`` iff the current contextvar session is in
    plan mode. Tools running on the dispatcher's loop can call this
    without arguments.
    """
    return is_plan_mode(turn_session_id())


# The plan-mode note beside a turn's message --------------------------------
#
# The plan-mode instructions are never in the conversation's system prompt,
# so entering or leaving Plan rebuilds nothing. They go beside the message of
# the conversation's own turn (``context.turn_context`` attachment
# ``plan_mode``) on Claude Code's schedule: the full note on entering, then
# one note every :data:`NOTE_EVERY` turns, every fifth of them full and the
# rest short, the full note again when no earlier note is in view (after a
# compaction), and one exit note on leaving.

#: Turns between plan-mode notes (Claude Code's ``PLAN_MODE_ATTACHMENT_CONFIG``).
NOTE_EVERY = 5
#: Every this-many-th note is the full one (the 1st, 6th, 11th …).
FULL_EVERY = 5

SPARSE_TEXT = (
    "<plan-mode>\n"
    "Still in plan mode: read only, change nothing. Send the plan with "
    "`exit_plan_mode(plan=...)`. The full plan-mode instructions are in the "
    "earlier plan-mode note.\n"
    "</plan-mode>"
)
EXIT_TEXT = (
    "<plan-mode>\n"
    "Plan mode has ended. The plan-mode limits no longer apply; the "
    "conversation's normal permissions do.\n"
    "</plan-mode>"
)


def full_text() -> str:
    from openprogram.runtime.context.components import _PLAN_MODE_TEXT

    return _PLAN_MODE_TEXT


def note_for(in_plan: bool, view: Optional[dict]) -> tuple[str, str]:
    """``(text, kind)`` of the plan-mode note this turn sends, ``("", "")``
    for none. ``kind`` is ``full``, ``sparse`` or ``exit``.

    ``view`` says what the request's rendered history shows of earlier
    notes (``turn_context.turn_view``): ``last`` (the kind of the last note
    in view, or None), ``turns_since`` (turns from that note's to this one)
    and ``sent_since_exit`` (notes since the last exit note). None means the
    render did not say, and the turn is treated as having no note in view.
    """
    state = (view or {}).get("plan_mode") or {}
    last = state.get("last")
    if not in_plan:
        return (EXIT_TEXT, "exit") if last in ("full", "sparse") else ("", "")
    if last in (None, "exit"):
        return full_text(), "full"
    if int(state.get("turns_since") or 0) < NOTE_EVERY:
        return "", ""
    number = int(state.get("sent_since_exit") or 0) + 1
    if number % FULL_EVERY == 1:
        return full_text(), "full"
    return SPARSE_TEXT, "sparse"


def turn_session_id() -> Optional[str]:
    """The conversation the running turn belongs to.

    The variable bound at turn start does not reach a tool body that runs
    after an approval resumed the turn in another context, so the bound
    turn request and the execution's session stand in for it.
    """
    session_id = current_session_id.get()
    if session_id:
        return session_id
    try:
        from openprogram.runtime.agent.turn_request import get_turn_request
        request = get_turn_request()
        if request is not None and getattr(request, "session_id", None):
            return request.session_id
    except Exception:  # noqa: BLE001 - no bound request
        pass
    try:
        from openprogram.runtime.execution.run_control import get_current_session_id
        return get_current_session_id() or None
    except Exception:  # noqa: BLE001 - no bound execution
        return None
