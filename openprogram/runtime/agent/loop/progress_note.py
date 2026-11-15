"""Ask for a word of progress after a long stretch of tool rounds with nothing to read.

A model working through many tool calls can go a long time without writing
anything the user sees. The system prompt asks for a line every few calls;
this is the harness's half of it: after :data:`QUIET_ROUNDS` rounds in a row
whose response was tool calls only, the loop adds one note to this turn's
context asking for a few words, and counts again from there. Text or shown
thinking in a response is something to read and starts the count again.

The note is only for a turn somebody is watching; the caller says so
(``AgentLoopConfig.progress_notes``). It is added after the round's tool
results and nothing earlier is changed, so what the provider cached stays valid.
"""
from __future__ import annotations

from openprogram.models.providers.types import AssistantMessage, TextContent, ThinkingContent

QUIET_ROUNDS = 6
"""Tool rounds in a row with nothing shown before the loop asks for a word."""

NOTE = ("Note from the runtime, not from the user: the user has seen no text from you for several steps. "
        "Before your next tool call, say in a few words what you are doing, then carry on with the task.")


def _shown(message: AssistantMessage) -> bool:
    for block in message.content:
        if isinstance(block, TextContent) and (block.text or "").strip():
            return True
        if isinstance(block, ThinkingContent) and (block.thinking or "").strip():
            return True
    return False


class QuietStretch:
    """Counts tool rounds with nothing shown; says when a note is due."""

    def __init__(self, rounds: int = QUIET_ROUNDS):
        self._rounds = rounds
        self._quiet = 0

    def reset(self) -> None:
        self._quiet = 0

    def observe(self, message: AssistantMessage) -> str | None:
        """The note to add after this round's tool results, or ``None``."""
        if _shown(message):
            self._quiet = 0
            return None
        self._quiet += 1
        if self._quiet < self._rounds:
            return None
        self._quiet = 0
        return NOTE
