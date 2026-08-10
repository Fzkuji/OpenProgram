"""Data types and validation shared by Topic Markdown modules."""

import re
from dataclasses import dataclass
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
    evidence: tuple[EvidenceAnnotation, ...] = ()
    relation_targets: tuple[str, ...] = ()


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
