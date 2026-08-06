"""turn.stop gate — hook-driven continuation after a finished turn.

``process_user_turn`` calls :func:`continue_stop_hook_turns` after the
goal loop has released the turn. The gate receives a ``turn.stop`` event;
a deny reason launches one more continuation turn (built like a goal
continuation: ``dataclasses.replace`` with ``source="hook_continue"`` and
``INHERIT_PARENT``), then the goal judgment and the gate run again on the
new result. Head movement stays with the normal TurnWriter path inside
``run_turn`` — this module never touches session heads.

Runaway protection: at most :data:`MAX_HOOK_CONTINUATIONS` hook-driven
turns per ``process_user_turn`` call; ``payload["stop_hook_active"]`` is
True on every ask after the first so a hook can tell it already forced a
continuation. Failed or cancelled turns return without asking the gate.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable, Optional

from openprogram.agent.dispatcher.types import INHERIT_PARENT

_log = logging.getLogger(__name__)

MAX_HOOK_CONTINUATIONS = 10
LAST_TEXT_MAX_CHARS = 4000


def _is_cancelled(session_id: str, cancel_event) -> bool:
    if cancel_event is not None and getattr(cancel_event, "is_set",
                                            lambda: False)():
        return True
    try:
        from openprogram.agent.run_control import is_cancelled
        return is_cancelled(session_id)
    except Exception:
        return False


def continue_stop_hook_turns(
    req: Any,
    result: Any,
    *,
    run_turn: Callable,
    goal_continue: Optional[Callable] = None,
    on_event: Optional[Callable] = None,
    cancel_event: Any = None,
) -> Any:
    """Ask the ``turn.stop`` gate; while denied (and under budget), run one
    more turn via ``run_turn`` (the dispatcher's single-turn primitive),
    re-judge the goal via ``goal_continue``, and ask again. Returns the
    LAST turn's result."""
    from openprogram.events import get_event_bus, make_event

    prev_req = req
    for used in range(MAX_HOOK_CONTINUATIONS + 1):
        if getattr(result, "failed", False):
            return result
        if _is_cancelled(prev_req.session_id, cancel_event):
            return result
        outcome = get_event_bus().emit_gate(make_event(
            "turn.stop", "system",
            payload={
                "session_id": prev_req.session_id,
                "user_msg_id": getattr(result, "user_msg_id", None),
                "assistant_msg_id": getattr(result, "assistant_msg_id", None),
                "last_text": (getattr(result, "final_text", "") or
                              "")[:LAST_TEXT_MAX_CHARS],
                "stop_hook_active": used > 0,
            },
            metadata={"session": prev_req.session_id},
        ))
        if outcome.allowed:
            return result
        if used >= MAX_HOOK_CONTINUATIONS:
            _log.warning(
                "turn.stop gate still denying after %d hook continuations "
                "for session %s; stopping anyway",
                MAX_HOOK_CONTINUATIONS, prev_req.session_id)
            return result

        reason = "; ".join(outcome.reasons) or "hook denied the stop"
        next_req = replace(
            prev_req,
            user_text=f"[hook] {reason}。继续。",
            source="hook_continue",
            user_msg_id=None,
            user_already_persisted=False,
            branch_from=INHERIT_PARENT,
            history_override=None,
            attachments=None,
            spawn_caller=None,
        )
        result = run_turn(next_req, on_event=on_event,
                          cancel_event=cancel_event)
        prev_req = next_req
        if goal_continue is not None and not getattr(result, "failed", False):
            try:
                result = goal_continue(next_req, result, run_turn=run_turn,
                                       on_event=on_event,
                                       cancel_event=cancel_event)
            except Exception:
                _log.warning("goal continuation after hook turn failed for "
                             "session %s", next_req.session_id, exc_info=True)
    return result
