"""Attended / unattended mode — whether the agent may interrupt the user.

A long run is either *attended* (a human is watching and can answer questions)
or *unattended* (the user stepped away — "I'm asleep, don't ask me"). The
control the user asked for is deliberately blunt: in unattended mode the agent
simply is not given the user-question tool, so it cannot ask. It then does its
best with what it has (and any genuine uncertainty is left for a later
model-driven pass to resolve), rather than blocking on an unanswerable prompt.

Why a tool-deny rather than a runtime fallback: a function can request ANY
toolset (default, full, an explicit tools= list). Gating at the policy layer —
adding the ask tool to the ``deny`` set during tool resolution — means it does
not matter which toolset a function picks; unattended always strips the ask
tool. One choke point, no per-function audit.

Scope: process-wide, per session. Set by the session's attended flag (CLI
flag, TUI toggle, web toggle — all write the same session state via the
worker). DEFAULT IS UNATTENDED: a bare CLI run has nobody watching, so the
safe default is "don't ask". Call ``set_attended(True)`` to allow questions.
"""
from __future__ import annotations

import threading

# The user-facing question tool. Denying it = the agent cannot prompt the user.
# (clarify.py registers under this same name; runtime.ask is reached only
# through this tool, so this one name covers the ask path.)
ASK_TOOLS = ("ask_user_question",)

_attended = False  # default: unattended (CLI / background runs have no watcher)
_lock = threading.Lock()


def set_attended(value: bool) -> None:
    """Set whether the agent may ask the user (True = attended/may ask)."""
    global _attended
    with _lock:
        _attended = bool(value)


def is_attended() -> bool:
    """True when a human is watching and the agent may ask questions."""
    return _attended


def denied_ask_tools() -> list[str]:
    """Tool names to subtract during resolution when unattended (empty when
    attended). Folded into the tool-policy ``deny`` set so it applies no
    matter which toolset a function requested."""
    return [] if _attended else list(ASK_TOOLS)
