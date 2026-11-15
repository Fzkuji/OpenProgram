"""Data types and validation shared by Topic Markdown modules."""

import re
from dataclasses import dataclass, field
from datetime import date


MEMORY_ID = r"mem_[A-Za-z0-9_-]+"
FOOTNOTE_ID = r"[A-Za-z0-9_-]+"
BLOCK_ID = r"[A-Za-z0-9-]+"
BLOCK_ID_LENGTH = 8
TEMPORAL_VALUE_PATTERN = r"\d{4}(?:-\d{2}(?:-\d{2})?)?"


class TopicFormatError(ValueError):
    """Raised when Topic Markdown cannot be mapped to stable memory units."""


@dataclass(frozen=True)
class EvidenceAnnotation:
    citation_id: str
    quote: str
    when: str | None
    source_refs: tuple[str, ...]
    source_links: tuple[str, ...]
    source_labels: tuple[str, ...] = ()
    # Which run of citations in the paragraph this one belongs to: a
    # paragraph can make several claims, each cited where it ends.
    claim: int = 1


@dataclass(frozen=True)
class MemoryUnit:
    memory_id: str
    content: str
    when: str | None
    source_refs: tuple[str, ...]
    source_links: tuple[str, ...]
    topic_path: str
    headings: tuple[str, ...]
    created_order: int
    source_labels: tuple[str, ...] = ()
    evidence: tuple[EvidenceAnnotation, ...] = ()
    relation_targets: tuple[str, ...] = ()
    # A paragraph with no ``^id`` of its own (or whose ID an earlier file
    # already holds) is read under an ID derived from its path and text.
    # That ID is never written back and is not a link target.
    derived_id: bool = False
    # The first line of the paragraph, 1-based; where it sits, not what
    # it says, so it takes no part in comparing units.
    line: int = field(default=0, compare=False)
    # Whether its file was written outside OpenProgram (``installed.json``
    # records it ``outside``): read and recalled, never rewritten.
    outside: bool = field(default=False, compare=False)

    @property
    def named(self) -> bool:
        """A block the Runtime can name and cite: its own ID and a source.

        The one admission rule for everything built from the tree: the
        indexes, recent events, the timeline and relations. Prose read
        under a derived ID, or citing nothing, is read but not admitted.
        """
        return not self.derived_id and bool(self.source_refs)

    @property
    def origin(self) -> str:
        """Where the paragraph came from, as a reader judges how far to
        trust it: ``cited`` (it cites the chat messages it rests on),
        ``outside`` (another tool wrote its file) or ``person`` (a person
        added it to one of memory's own files without a citation)."""
        if self.outside:
            return "outside"
        return "cited" if self.source_refs else "person"


def is_valid_temporal_value(value: str) -> bool:
    if not re.fullmatch(TEMPORAL_VALUE_PATTERN, value):
        return False
    parts = [int(part) for part in value.split("-")]
    try:
        date(
            parts[0],
            parts[1] if len(parts) > 1 else 1,
            parts[2] if len(parts) > 2 else 1,
        )
    except ValueError:
        return False
    return True
