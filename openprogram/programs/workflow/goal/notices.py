"""Transcript notices — system rows (``local_command`` envelope) for
goal events, and the terminal-status finisher every stop rule funnels
through."""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.workflow.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.workflow.goal as _goal

_log = logging.getLogger(__name__)


def _emit_goal_notice(session_id: str, content: str,
                      on_event: Optional[Callable] = None) -> None:
    """One system row in the transcript (``local_command`` envelope,
    webui broadcast; best-effort — absent server is a no-op). Callers
    inside a turn pass ``on_event`` so the row reaches that turn's own
    event stream as well as the broadcast."""
    payload = {
        "type": "local_command",
        "session_id": session_id,
        "content": content,
    }
    if on_event is not None:
        try:
            on_event({"type": "chat_response", "data": dict(payload)})
        except Exception:
            _log.debug("goal notice emit failed", exc_info=True)
    try:
        from openprogram.webui import server as _s
        _s._broadcast(json.dumps(
            {"type": "chat_response", "data": payload}, default=str))
    except Exception:
        pass


# Terminal statuses, and how each reads in the transcript.
_TERMINAL_LABELS = {
    "achieved": "已达成",
    "error": "已终止",
    "capped": "已达轮次上限",
}


def _finish(session_id: str, goal: dict, on_event: Optional[Callable]) -> None:
    try:
        _goal.save_goal(session_id, goal)
    except Exception:
        _log.warning("goal terminal write failed for session %s",
                     session_id, exc_info=True)
    _goal._emit_goal_update(on_event, session_id, goal)
    # The chip alone leaves a stopped run looking like the assistant went
    # silent mid-conversation — the reason is already written to the goal
    # state, so say it in the transcript too. ``waiting_user`` is excluded:
    # it emits its own question line and the run resumes.
    label = _goal._TERMINAL_LABELS.get(str(goal.get("status") or ""))
    if label:
        reason = str(goal.get("last_reason") or "").strip()
        _goal._emit_goal_notice(session_id,
                                f"[goal] {label}：{reason}" if reason
                                else f"[goal] {label}",
                                on_event)
