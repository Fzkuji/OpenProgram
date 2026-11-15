"""Slash commands a surface answers itself, without a model turn.

The Worker's chat command and ``openprogram --print`` take typed text the
same way: text that names a registered builtin command (``/commit-message``,
``/goal``) runs that command here; ``/compact`` compacts the conversation;
``/task`` and ``/merge`` become a model turn marked with its
``slash_action``; anything else, an unknown ``/name`` included, is sent to
the model as written. Only the delivery is the surface's own (a frame, a
line on stdout).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass
class LocalOutcome:
    """What a local command did.

    ``text`` is its answer for the person. ``send_text`` replaces the typed
    text and goes on as a model turn. ``starts_goal`` marks that turn's
    message as the one that sets a Goal (``/goal <objective>``): it is written
    on the user message, which then stands for ``text`` on a surface that
    draws messages. ``invoke`` names an Agent method the command asks the
    surface to run (``/goal``).
    """

    command: str
    text: str = ""
    send_text: str | None = None
    starts_goal: bool = False
    invoke: dict | None = None


def resolve(text: str, session_id: str):
    """The registered local command ``text`` names, or ``None``."""
    if not text.startswith("/"):
        return None
    try:
        from openprogram.runtime.commands.dispatch import invoke

        resolved = invoke(text, session_id=session_id)
    except Exception:  # noqa: BLE001 - not a command this surface can run
        return None
    if resolved.ok and resolved.kind == "local" and callable(resolved.local_handler):
        return resolved
    return None


def run(resolved, session_id: str, *, branch_id: str | None = None) -> LocalOutcome:
    """Run a command :func:`resolve` found; a failing command answers with
    its error instead of raising. ``branch_id`` names the branch the command
    was sent on when it is not the selected one."""
    context = {"session_id": session_id, **({"branch_id": branch_id} if branch_id else {})}
    try:
        out = resolved.local_handler(context, resolved.raw_args) or {}
    except Exception as exc:  # noqa: BLE001 - answered, not raised
        out = {"text": f"/{resolved.command_name} failed: {type(exc).__name__}: {exc}"}
    invoke = out.get("invoke")
    send_text = out.get("send_text")
    return LocalOutcome(
        command=resolved.command_name,
        text=str(out.get("text") or ""),
        send_text=str(send_text) if send_text else None,
        starts_goal=bool(send_text) and out.get("starts_goal") is True,
        invoke=invoke if isinstance(invoke, dict) else None,
    )


def compact_args(text: str) -> dict | None:
    """``/compact [KEEP_RECENT_TOKENS]`` as its arguments, ``None`` for any
    other text."""
    head, _, rest = text.strip().partition(" ")
    if head.lower() != "/compact":
        return None
    rest = rest.strip()
    try:
        return {"keep_recent_tokens": int(rest)} if rest else {}
    except ValueError:
        return None


def compact(session_id: str, *, agent_id: str, keep_recent_tokens: int | None = None,
            cancel_event: threading.Event | None = None,
            on_event: Callable[[dict], None] | None = None) -> dict:
    """Compact ``session_id`` as the Web UI's ``/compact`` does
    (``turn_runtime.trigger_compaction``)."""
    from openprogram.runtime.conversation.turn_runtime import trigger_compaction

    return trigger_compaction(session_id, agent_id=agent_id, on_event=on_event,
                              keep_recent_tokens=keep_recent_tokens, cancel_event=cancel_event)


def slash_fields(parsed: Mapping[str, Any]) -> dict:
    """The request fields a parsed ``/task`` or ``/merge`` adds
    (``interfaces/server/chat_input.parse_chat_input``); ``slash_action`` is
    ``None`` for any other text, so ``interaction`` keeps the authority's
    value."""
    action = parsed.get("action")
    return {
        "slash_action": action if action in {"spawn", "merge"} else None,
        "structured_output": (
            {
                "prompt": parsed.get("prompt") or "",
                "label": parsed.get("label") or "",
                "context": parsed.get("context") or "inherit",
                "wait": parsed.get("wait", True),
            }
            if action == "spawn" else {
                "sub_sessions": parsed.get("sub_sessions") or [],
                "message": parsed.get("message") or "",
            }
            if action == "merge" else None
        ),
    }


__all__ = ["LocalOutcome", "compact", "compact_args", "resolve", "run", "slash_fields"]
