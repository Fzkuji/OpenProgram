"""Time to first token: how long a streamed reply took to begin.

Two marks, from sending the request: the reply's first content of any kind
(text, reasoning or a tool call), and its first visible text. The first is
how long the provider took to start; the second also counts the model's
thinking. Read in one place for every provider (``stream._metered``) and
written onto the reply's :class:`Usage`, from where the usage ledger keeps
it per request.

After magpie's TTFT (yetone/magpie, MIT).
"""
from __future__ import annotations

import time
from typing import Any

from openprogram.models.providers.types import AssistantMessage

_CONTENT = frozenset({
    "text_delta", "thinking_delta", "toolcall_start", "toolcall_delta", "servertool_start",
})


def _kind(event: Any) -> str | None:
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", None)


class FirstToken:
    """Watches one reply's events from the moment its request is sent."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self.first_token_ms: int | None = None
        self.first_text_ms: int | None = None

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def see(self, event: Any) -> None:
        if self.first_text_ms is not None:
            return
        kind = _kind(event)
        if kind not in _CONTENT:
            return
        elapsed = self._elapsed_ms()
        if self.first_token_ms is None:
            self.first_token_ms = elapsed
        if kind == "text_delta":
            self.first_text_ms = elapsed

    def stamp(self, event: Any) -> None:
        """Write the marks onto the reply a terminal event carries, whatever its shape."""
        if self.first_token_ms is None:
            return
        kind = _kind(event)
        field = "message" if kind == "done" else "error" if kind == "error" else None
        if field is None:
            return
        final = event.get(field) if isinstance(event, dict) else getattr(event, field, None)
        if isinstance(final, AssistantMessage):
            final.usage.first_token_ms = self.first_token_ms
            final.usage.first_text_ms = self.first_text_ms
        elif isinstance(final, dict):
            usage = final.setdefault("usage", {})
            if isinstance(usage, dict):
                usage["first_token_ms"] = self.first_token_ms
                usage["first_text_ms"] = self.first_text_ms
