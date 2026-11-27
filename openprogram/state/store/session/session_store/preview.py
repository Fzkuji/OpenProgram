"""A chat's preview line: the words of its latest user message, as the
chat lists show them under the title.

A message can carry wrapping that is not words the person wrote: the
envelope OpenProgram sends an outside agent the owner's words in, and the
``<pasted_content>`` tags an imported Claude conversation keeps around
pasted text. The preview shows the words alone, on one line, cut at 80
characters."""

from __future__ import annotations

import re

from openprogram.state.authority import unwrap_owner_input

_PASTED_TAG = re.compile(r"</?pasted_content\b[^>]*>?")
_SPACES = re.compile(r"\s+")


def message_preview(text: str) -> str:
    """The preview line for a user message's text (also safe to apply to
    a preview already made: it changes nothing then)."""
    words = unwrap_owner_input(str(text or ""))
    words = _PASTED_TAG.sub(" ", words)
    words = _SPACES.sub(" ", words).strip()
    return (words[:77] + "…") if len(words) > 80 else words
