"""Auto mode's review of a screen, kept for the conversation.

Auto reviews a call from its arguments. For a screen those say which
application the agent wants to operate and for what request; they say
nothing about one click over the next, whose arguments are an element index
or two pixels. So the first call to an application (or a VM, or the user's
own browser) is reviewed, and an allowing review stands for that screen for
the rest of the conversation, as a grant the user gives in the asking modes
does. What the agent may do there without asking the user is the tool's
confirmation policy, which the model reads with the target's guide.

The grant is keyed like the permission rule of the same screen
(``computer_use_agent(google chrome)``), bound to the conversation, its
owner, the version of its permissions and the rules that name the tool:
switching mode, or adding or removing a rule for the tool, starts over.
Rules are matched before it on every call, so a deny or ask rule always
wins. It lives in this process only, so a restarted worker reviews the
screen once more.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_granted: dict[tuple, set[str]] = {}
_MAX_CONVERSATIONS = 512


def _conversation(req, name: str) -> tuple | None:
    session_id = getattr(req, "session_id", None)
    if not session_id:
        return None
    from openprogram.runtime.permissions.policy import rules_for_tool
    return (session_id, getattr(req, "principal_id", None), getattr(req, "_permission_version", 0),
            rules_for_tool(getattr(req, "permission_rules", None), name))


def _screen(name: str, args) -> str | None:
    from openprogram.runtime.permissions.rule import parse_command
    return parse_command(name, args) if isinstance(args, dict) else None


def held(req, name: str, args) -> bool:
    """Whether an earlier review of this conversation allowed the screen this call operates."""
    conversation, screen = _conversation(req, name), _screen(name, args)
    if conversation is None or screen is None:
        return False
    with _lock:
        return screen in _granted.get(conversation, ())


def grant(req, name: str, args) -> None:
    """Keep an allowing review of this call's screen for the conversation."""
    conversation, screen = _conversation(req, name), _screen(name, args)
    if conversation is None or screen is None:
        return
    with _lock:
        if conversation not in _granted and len(_granted) >= _MAX_CONVERSATIONS:
            _granted.clear()
        _granted.setdefault(conversation, set()).add(screen)


def forget(session_id: str | None = None) -> None:
    """Drop the kept reviews of one conversation, or of all."""
    with _lock:
        if session_id is None:
            _granted.clear()
            return
        for conversation in [key for key in _granted if key[0] == session_id]:
            del _granted[conversation]


__all__ = ["forget", "grant", "held"]
