"""A plugin's hooks: shell commands subscribed to bus events.

The manifest's ``hooks`` is a mapping, inline or in a JSON file, in either
of two shapes:

* OpenProgram's, the same as the ``hooks`` key of ``config.json``:
  ``{"tool.before": [{"command": "...", "timeout": 30}]}``;
* Claude Code's ``hooks/hooks.json``: ``{"hooks": {"PreToolUse":
  [{"matcher": "Bash|Write", "hooks": [{"type": "command", "command":
  "${CLAUDE_PLUGIN_ROOT}/check.sh"}]}]}}``. Its events map onto bus events
  (:data:`CLAUDE_EVENTS`); ``matcher`` is a regular expression over the
  tool name; ``${CLAUDE_PLUGIN_ROOT}`` is the plugin directory.

Each command follows the protocol of ``events/shell_hooks.py`` (the event
as JSON on stdin; on a gate event exit 2 denies with stderr as the reason)
and is subscribed through the plugin's ``ProgramContext``, so disabling
the plugin unsubscribes it. A transform event cannot take a shell hook.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from openprogram.runtime.events.registry import EVENTS

_log = logging.getLogger(__name__)

#: Claude Code hook events and the bus events they subscribe to.
CLAUDE_EVENTS: dict[str, str] = {
    "PreToolUse": "tool.before",
    "PostToolUse": "tool.after",
    "UserPromptSubmit": "chat.before_send",
    "Stop": "turn.stop",
    "SessionStart": "session.start",
}


@dataclass(frozen=True)
class CommandHook:
    event: str
    command: str
    timeout: float = 60.0
    matcher: str = ""


def _timeout(entry: dict) -> float:
    try:
        return float(entry.get("timeout") or 60)
    except (TypeError, ValueError):
        return 60.0


def command_hooks(value: Any, root: str = "") -> list[CommandHook]:
    """The command hooks a manifest's ``hooks`` value declares."""
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("hooks"), dict):
        value = value["hooks"]
    found: list[CommandHook] = []
    for event, entries in value.items():
        bus_event = CLAUDE_EVENTS.get(event, event)
        if bus_event not in EVENTS:
            _log.warning("plugin hook for unknown event %r skipped", event)
            continue
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("hooks")
            commands = nested if isinstance(nested, list) else [entry]
            for item in commands:
                if not isinstance(item, dict) or item.get("type", "command") != "command":
                    continue
                command = item.get("command")
                if not isinstance(command, str) or not command.strip():
                    continue
                if root:
                    command = command.replace("${CLAUDE_PLUGIN_ROOT}", root)
                found.append(CommandHook(bus_event, command, _timeout(item),
                                         str(entry.get("matcher") or "")))
    return found


def _matches(hook: CommandHook, event) -> bool:
    if not hook.matcher or hook.matcher == "*":
        return True
    tool = str((getattr(event, "payload", None) or {}).get("tool") or "")
    try:
        return re.fullmatch(hook.matcher, tool) is not None
    except re.error:
        return hook.matcher == tool


def mount(ctx, hook: CommandHook) -> None:
    """Subscribe one command hook through the plugin's context."""
    from openprogram.runtime.events.shell_hooks import make_shell_gate, make_shell_notifier
    kind = EVENTS[hook.event].kind
    if kind == "transform":
        _log.warning("plugin %s: %s rewrites a value; a shell hook cannot, skipped",
                     ctx.name, hook.event)
        return
    if kind == "gate":
        gate = make_shell_gate(hook.command, hook.timeout)
        ctx.gate(hook.event, lambda event: gate(event) if _matches(hook, event) else None)
    else:
        notify = make_shell_notifier(hook.command, hook.timeout)
        ctx.on(hook.event, lambda event: notify(event) if _matches(hook, event) else None)


__all__ = ["CLAUDE_EVENTS", "CommandHook", "command_hooks", "mount"]
