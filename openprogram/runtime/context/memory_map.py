"""The note that brings a changed memory map to the model beside a turn's message.

``MEMORY.md`` (its map, then its core) is the ``memory`` section of the
system prompt. A memory ingest rewrites it between turns; in the system
prompt that change would end the cached prefix of the whole conversation
behind it. So a conversation's system prompt keeps the memory section it was
recorded with, and when memory has changed since, the turn carries the
current text beside its message. This is one of the movable sections
``context.turn_context`` handles (``kept_sections``, ``note_to_send``); this
module only writes the memory section's note, whose text predates the other
sections' notes and is kept byte for byte so stored notes still match.
"""
from __future__ import annotations

SECTION = "memory"

_NOTE_OPEN = "<memory-map-update>"
_NOTE_CLOSE = "</memory-map-update>"
_NOTE_LEAD = (
    "MEMORY.md has changed since this conversation's system prompt was written. "
    "This is its current text; it replaces the memory map and core in the system "
    "prompt and any earlier update."
)


def note_for(section: str) -> str:
    """The note that tells the model the current memory section."""
    return f"{_NOTE_OPEN}\n{_NOTE_LEAD}\n\n{section}\n{_NOTE_CLOSE}"


__all__ = ["SECTION", "note_for"]
