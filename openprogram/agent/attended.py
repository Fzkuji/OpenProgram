"""Attended / unattended mode — whether the agent may interrupt the user.

A long run is either *attended* (a human is watching and can answer questions)
or *unattended* (the user stepped away — "I'm asleep, don't ask me"). The
control is deliberately blunt: in unattended mode the agent is not given the
user-question tool, so it cannot ask. It then does its best with what it has
(genuine uncertainty is left for a later model-driven pass), rather than
blocking on an unanswerable prompt.

Why a tool-deny rather than a runtime fallback: a function can request ANY
toolset (default, full, explicit tools=). Gating at the policy layer — adding
the ask tool to the ``deny`` set during tool resolution — means it doesn't
matter which toolset a function picks. One choke point, no per-function audit.

PER-SESSION: a single worker process hosts many sessions (web/TUI), so the
mode is keyed by session_id; a global toggle would let one session's
"unattended" silence another's questions. A process-wide default covers
callers with no session in scope (and is what a bare CLI run uses).

DEFAULT IS UNATTENDED: a bare run / background run has nobody watching, so the
safe default is "don't ask". Call ``set_attended(True)`` (optionally with a
session) to allow questions.
"""
from __future__ import annotations

import threading

# The user-facing question tool. Denying it = the agent cannot prompt the user.
# (clarify.py registers under this same name; runtime.ask is reached only
# through this tool, so this one name covers the ask path.)
ASK_TOOLS = ("ask_user_question",)

_default = False           # process-wide default when no session is in scope
_by_session: dict[str, bool] = {}
_lock = threading.Lock()


def set_attended(value: bool, session_id: "str | None" = None) -> None:
    """Set whether the agent may ask the user. With a session_id, sets it for
    that session only; without, sets the process-wide default."""
    global _default
    with _lock:
        if session_id:
            _by_session[session_id] = bool(value)
        else:
            _default = bool(value)


def is_attended(session_id: "str | None" = None) -> bool:
    """True when the agent may ask. Falls back to the process default when the
    session has no explicit setting."""
    with _lock:
        if session_id and session_id in _by_session:
            return _by_session[session_id]
        return _default


def clear_session(session_id: str) -> None:
    """Drop a session's override (it falls back to the default)."""
    with _lock:
        _by_session.pop(session_id, None)


def denied_ask_tools(session_id: "str | None" = None) -> list[str]:
    """Tool names to subtract during resolution when unattended (empty when
    attended). Folded into the tool-policy ``deny`` set so it applies no
    matter which toolset a function requested."""
    return [] if is_attended(session_id) else list(ASK_TOOLS)
