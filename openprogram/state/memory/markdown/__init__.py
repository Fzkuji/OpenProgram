"""Authoritative topic-Markdown API for the memory workspace."""

from .models import (
    BLOCK_ID,
    BLOCK_ID_LENGTH,
    FOOTNOTE_ID,
    MEMORY_ID,
    TEMPORAL_VALUE_PATTERN,
    EvidenceAnnotation,
    MemoryUnit,
    TopicFormatError,
    is_valid_temporal_value,
)
from .parser import (
    TopicTree,
    parse_topic_tree,
    read_checked_tree,
    read_topic_tree,
    topic_prose,
    topic_tree_problems,
)
from .syntax import definition_match, paragraph_spans, render_definition
from .writer import append_memory_unit

__all__ = [
    "BLOCK_ID",
    "BLOCK_ID_LENGTH",
    "FOOTNOTE_ID",
    "MEMORY_ID",
    "TEMPORAL_VALUE_PATTERN",
    "EvidenceAnnotation",
    "MemoryUnit",
    "TopicFormatError",
    "TopicTree",
    "append_memory_unit",
    "definition_match",
    "is_valid_temporal_value",
    "parse_topic_tree",
    "read_checked_tree",
    "read_topic_tree",
    "topic_tree_problems",
    "paragraph_spans",
    "render_definition",
    "topic_prose",
]
