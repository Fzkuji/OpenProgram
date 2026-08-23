"""turn.stop gate — hook-driven continuation after a finished turn.

``process_user_turn`` calls :func:`continue_stop_hook_turns` after a normal
turn. The gate receives a ``turn.stop`` event;
a deny reason launches one more continuation turn (built like a goal
continuation: ``dataclasses.replace`` with ``source="hook_continue"`` and
``INHERIT_PARENT``), then the gate runs again on the new result. Head
movement stays with the normal TurnWriter path inside
``run_turn`` — this module never touches session heads.

Runaway protection is the ``stop_hook_active`` flag protocol (same as
Claude Code / Codex stop hooks — no numeric cap): the payload carries
``stop_hook_active=True`` on every ask after the first, so a hook knows
it already forced a continuation and is expected to allow the stop
instead of looping forever. Failed or cancelled turns return without
asking the gate, and the user's interrupt reaches every continuation.
Goal Workflow rounds do not enter this top-level gate.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional

from openprogram.agent.dispatcher.types import INHERIT_PARENT

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
    on_event: Optional[Callable] = None,
    cancel_event: Any = None,
) -> Any:
    """Ask the ``turn.stop`` gate; while denied, run one more turn via
    ``run_turn`` (the dispatcher's single-turn primitive) and ask again.
    No numeric cap — the
    ``stop_hook_active`` flag tells the hook it already forced a
    continuation. Returns the LAST turn's result."""
    from openprogram.events import get_event_bus, make_event

    prev_req = req
    used = 0
    while True:
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

        reason = "; ".join(outcome.reasons) or "hook denied the stop"
        from openprogram.agent.authority import runtime_authority
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
            **runtime_authority(prev_req, "hook_continue"),
        )
        result = run_turn(next_req, on_event=on_event,
                          cancel_event=cancel_event)
        used += 1
        prev_req = next_req
