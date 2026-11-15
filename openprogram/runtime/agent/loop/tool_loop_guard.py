"""Stop a turn whose tool rounds keep coming back the same.

A model can loop across rounds: it asks for one tool call, gets one
result, and asks for the same call again. Each round sends the whole
conversation again and changes nothing. Repeated *failures* are already
skipped by the tool executor after two (``failure_policy``); this guard
covers the successful loop: :data:`TOOL_LOOP_REPEATS` rounds in a row
that made the same calls with the same arguments and got the same results.

A round that got a different result (a poll whose answer moved, a file
that changed) is progress and starts the count again, as does anything
the user says mid-turn. A round whose calls are all waits (``sleep``,
``wait_for_ci``, or arguments that are only a duration) is never a loop:
the time it takes is the progress. Text an agent writes about the run
itself (elapsed time, wall time) is left out when results are compared.

After magpie's tool-loop guard (yetone/magpie, MIT).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from openprogram.models.providers.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCall,
    ToolResultMessage,
)
from openprogram.runtime.agent.loop.failure_policy import repeat_key

TOOL_LOOP_REPEATS = 8
"""Rounds in a row of the same calls and results that end the turn."""

_WAIT_WORDS = frozenset({"sleep", "wait", "delay", "pause", "poll"})
_DURATION_KEYS = frozenset({
    "duration", "duration_ms", "duration_s", "duration_seconds", "ms",
    "milliseconds", "seconds", "secs", "sec", "timeout", "timeout_ms",
    "delay", "delay_ms", "minutes",
})
# What a result says of the run rather than of what it found.
_RUN_NOISE = (
    (re.compile(r"(elapsed )[0-9.]+(\s*s)"), r"\1#\2"),
    (re.compile(r"(?m)^(Wall time:? )[0-9.]+( seconds?)"), r"\1#\2"),
    (re.compile(r'("duration_seconds"\s*:\s*)[0-9.eE+-]+'), r"\1#"),
)
_NOTICE_ARGS_CHARS = 120


@dataclass(frozen=True)
class ToolLoop:
    """The round a turn kept repeating, and how many times in a row."""

    calls: tuple[ToolCall, ...]
    rounds: int

    def called(self) -> str:
        names = []
        for call in self.calls:
            args = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, default=str)
            if len(args) > _NOTICE_ARGS_CHARS:
                args = args[:_NOTICE_ARGS_CHARS] + "…"
            names.append(f"{call.name} {args}")
        return "; ".join(names)

    def notice(self) -> str:
        return (
            f"Stopped: the agent made the same tool call {self.rounds} times in a row "
            f"and got the same result every time ({self.called()}). The model is "
            "stuck repeating it, so the turn ended here instead of spending more "
            "requests. Tell the agent how to go on and the conversation continues."
        )


class ToolLoopGuard:
    """Counts identical tool rounds within one agent turn."""

    def __init__(self, repeats: int = TOOL_LOOP_REPEATS) -> None:
        self._repeats = repeats
        self._last: str | None = None
        self._count = 0

    def reset(self) -> None:
        self._last = None
        self._count = 0

    def observe(
        self, message: AssistantMessage, results: list[ToolResultMessage]
    ) -> ToolLoop | None:
        """Record one finished round; the loop when it is the last straw."""
        calls = [block for block in message.content if isinstance(block, ToolCall)]
        if not calls or not results or all(_is_wait(call) for call in calls):
            self.reset()
            return None
        digest = _round_digest(calls, results)
        if digest == self._last:
            self._count += 1
        else:
            self._last, self._count = digest, 1
        if self._count >= self._repeats:
            return ToolLoop(calls=tuple(calls), rounds=self._count)
        return None


def _round_digest(calls: list[ToolCall], results: list[ToolResultMessage]) -> str:
    by_call: dict[str, list[str]] = {}
    for result in results:
        by_call.setdefault(result.tool_call_id, []).append(_result_digest(result))
    keys = []
    for call in calls:
        got = by_call.pop(call.id, [])
        keys.append(repeat_key(call.name, call.arguments) + "\0" + "\1".join(got))
    for got in by_call.values():  # results of no call in this round
        keys.append("\0\0" + "\1".join(got))
    hasher = hashlib.sha256()
    for key in sorted(keys):
        hasher.update(key.encode("utf-8", "surrogatepass") + b"\xff")
    return hasher.hexdigest()


def _result_digest(result: ToolResultMessage) -> str:
    parts = ["error" if result.is_error else "ok"]
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(_steady(block.text))
        elif isinstance(block, ImageContent):
            parts.append(block.mime_type + hashlib.sha256(block.data.encode()).hexdigest())
        else:
            parts.append(getattr(block, "type", "?") + hashlib.sha256(
                str(getattr(block, "data", "")).encode()).hexdigest())
    return "\0".join(parts)


def _steady(text: str) -> str:
    for pattern, replacement in _RUN_NOISE:
        text = pattern.sub(replacement, text)
    return text


def _name_words(name: str) -> list[str]:
    """``server__waitForCi`` → ``["wait", "for", "ci"]``: the tool's own name, in words."""
    for separator in ("__", ".", "/", ":"):
        name = name.rsplit(separator, 1)[-1]
    return [word.lower() for word in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name)]


def _is_wait(call: ToolCall) -> bool:
    if any(word in _WAIT_WORDS for word in _name_words(call.name)):
        return True
    args: Any = call.arguments
    return bool(args) and isinstance(args, dict) and all(
        key.lower() in _DURATION_KEYS and isinstance(value, (int, float))
        and not isinstance(value, bool)
        for key, value in args.items()
    )
