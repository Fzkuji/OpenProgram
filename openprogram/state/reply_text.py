"""A reply's flat text: its text passages, read in order.

A reply is a list of blocks (text, thinking, tool calls, steering messages).
The chat draws the blocks; everything else reads one string: the stored
row's ``content``, ``TurnResult.final_text``, CLI ``--print``, a channel
message, a search hit, a title, a copied message.

One rule builds that string, wherever it is built:

* text blocks that follow one another are one passage and are joined as
  they are, byte for byte (a provider splits one stream of text into
  blocks for citations, and a response continued after its output limit
  goes on mid-sentence);
* two passages that a shown block came between (a tool call, a steering
  message, reasoning with text) are separated by a blank line, unless the
  text on either side of the boundary already has whitespace there.

The separator depends only on text already written and on the first
character of the passage that follows, so a reply's flat text only ever
grows at its end while it streams: the stream tap stores the separator as
part of the appended tail, and names it on the frame of the delta that
opens the passage (``separator``) for clients that build the text from
deltas (:func:`stream_text`).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

#: What separates two passages a shown block came between.
PASSAGE_BREAK = "\n\n"


def passage_separator(before: str, after: str) -> str:
    """What goes between ``before`` and a passage starting with ``after``
    when a shown block came between them."""
    if not before or not after or before[-1].isspace() or after[0].isspace():
        return ""
    return PASSAGE_BREAK


def _kind_and_text(block: Any) -> tuple[Any, str]:
    read = block.get if isinstance(block, Mapping) else lambda name: getattr(block, name, None)
    kind, text = read("type"), read("text")
    if kind == "thinking" and not text:
        # A provider's own reasoning block keeps its text under ``thinking``.
        text = read("thinking")
    return kind, text if isinstance(text, str) else ""


class ReplyText:
    """The flat text of the blocks added so far."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._last = ""
        self._interrupted = False

    def add(self, block: Any) -> str:
        """Take the next block of the reply; returns what it appended.

        ``block`` is a stored block (``{"type", "text"}``) or a provider
        content block. Reasoning with no text is not shown, so it does not
        end a passage.
        """
        kind, text = _kind_and_text(block)
        if kind != "text":
            if kind != "thinking" or text:
                self.interrupt()
            return ""
        if not text:
            return ""
        added = (passage_separator(self._last, text) if self._interrupted else "") + text
        self._parts.append(added)
        self._last = text
        self._interrupted = False
        return added

    def interrupt(self) -> None:
        """A shown block that is not text came next."""
        self._interrupted = True

    def extend(self, blocks: Iterable[Any]) -> "ReplyText":
        for block in blocks:
            self.add(block)
        return self

    @property
    def text(self) -> str:
        if len(self._parts) > 1:
            self._parts = ["".join(self._parts)]
        return self._parts[0] if self._parts else ""


def reply_text(blocks: Iterable[Any] | str | None) -> str:
    """The flat text of a reply's blocks, or of one response's content."""
    if isinstance(blocks, str):
        return blocks
    return ReplyText().extend(blocks or ()).text


def opening_separator(blocks: list[dict], index: int, opening: str) -> str:
    """The separator before text block ``index`` when it opens with ``opening``.

    Empty unless the block starts a passage after a shown block: the stream
    tap asks this for the first text a block receives.
    """
    before = ReplyText().extend(blocks[:index])
    return passage_separator(before._last, opening) if before._interrupted else ""


def stream_text(event: Mapping) -> str:
    """The flat text a ``text`` stream event adds: its separator, then its delta."""
    text = event.get("text")
    if not isinstance(text, str) or not text:
        return ""
    separator = event.get("separator")
    return (separator if isinstance(separator, str) else "") + text
