"""Low-level parsing of headings, paragraphs, footnotes, and block links."""

import re
from collections.abc import Iterable, Iterator

from .. import chat_refs
from .models import (
    BLOCK_ID,
    FOOTNOTE_ID,
    MEMORY_ID,
    TEMPORAL_VALUE_PATTERN,
    TopicFormatError,
    is_valid_temporal_value,
)


CITATION_GROUP = re.compile(rf"(?:\[\^(?P<id>{FOOTNOTE_ID})\])+")
SINGLE_CITATION = re.compile(rf"\[\^(?P<id>{FOOTNOTE_ID})\]")
DEFINITION = re.compile(
    rf"^\[\^(?P<id>{FOOTNOTE_ID})\]:\s*"
    rf"Time:\s*(?P<tick>`)?(?P<when>{TEMPORAL_VALUE_PATTERN}|undated)(?(tick)`)"
    r"\s*;\s*Sources:\s*(?P<sources>.+?)\s*$"
)
LEGACY_DEFINITION = re.compile(
    rf"^\[\^(?P<id>{FOOTNOTE_ID})\]:\s*"
    rf"(?P<when>{TEMPORAL_VALUE_PATTERN}|undated)"
    r"\s*·\s*Sources:\s*(?P<sources>.+?)\s*$"
)
LINK = re.compile(r"\[([^]]+)\]\(([^)]+)\)")
# A merged paragraph carries every ID it absorbed, so the suffix is a run.
BLOCK_SUFFIX = re.compile(rf"(?:\s+\^{BLOCK_ID})*\s+\^(?P<id>{BLOCK_ID})\s*$")
BLOCK_SUFFIX_RUN = re.compile(rf"(?:\s+\^{BLOCK_ID})+\s*$")
ANY_BLOCK_SUFFIX = re.compile(r"\s+\^(?P<id>\S+)\s*$")
BLOCK_TARGET_ID = rf"(?:{MEMORY_ID}|{BLOCK_ID})"
BLOCK_LINK = re.compile(rf"\[[^]]+\]\([^)#]*#\^(?P<id>{BLOCK_TARGET_ID})\)")
def definition_match(line: str) -> re.Match[str] | None:
    """Match canonical definitions plus legacy definitions for migration."""
    return DEFINITION.match(line) or LEGACY_DEFINITION.match(line)


def render_definition(
    citation_id: str,
    when: str | None,
    sources: Iterable[str],
) -> str:
    return (
        f"[^{citation_id}]: Time: `{when or 'undated'}`; Sources: "
        + ", ".join(sources)
    )


def source_reference(_label: str, target: str) -> str:
    """The chat reference a footnote source link names."""
    resolved = chat_refs.from_target(target)
    if resolved is None:
        raise TopicFormatError(f"invalid source target: {target}")
    return resolved


Definition = tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]


def parse_definition(match: re.Match[str]) -> Definition:
    """(when, chat refs, link targets, labels) of one footnote definition."""
    citation_id = match.group("id")
    when = match.group("when")
    if when != "undated" and not is_valid_temporal_value(when):
        raise TopicFormatError(f"invalid evidence date: {when}")
    links = LINK.findall(match.group("sources"))
    if not links:
        raise TopicFormatError(
            f"memory source links required: {citation_id}"
        )
    if any(not label.strip() for label, _target in links):
        raise TopicFormatError(
            f"memory source labels required: {citation_id}"
        )
    return (
        None if when == "undated" else when,
        tuple(source_reference(label, target) for label, target in links),
        tuple(target for _label, target in links),
        tuple(label for label, _target in links),
    )


def definitions(lines: list[str], *, strict: bool = True) -> dict[str, Definition]:
    """Footnote definitions by ID.

    Leniently read, a definition that does not parse, or repeats an ID,
    is left out: a citation of it reads as plain text.
    """
    values = {}
    for line in lines:
        match = definition_match(line)
        if not match:
            continue
        citation_id = match.group("id")
        if citation_id in values:
            if strict:
                raise TopicFormatError(
                    f"duplicate footnote definition: {citation_id}"
                )
            continue
        try:
            values[citation_id] = parse_definition(match)
        except TopicFormatError:
            if strict:
                raise
    return values


def paragraph_spans(
    lines: list[str],
) -> Iterator[tuple[int, int, tuple[str, ...]]]:
    """Yield the exact line spans and heading path parsed as Topic prose."""
    headings: list[str] = []
    start: int | None = None
    paragraph_headings: tuple[str, ...] = ()
    in_fence = False

    def take(end: int) -> tuple[int, int, tuple[str, ...]] | None:
        nonlocal start
        if start is None:
            return None
        result = (start, end, paragraph_headings)
        start = None
        return result

    for index, line in enumerate(lines + [""]):
        if line.lstrip().startswith("```"):
            if result := take(index):
                yield result
            in_fence = not in_fence
            continue
        if in_fence or definition_match(line) or re.match(
            r"\s*(?:<!--|<a\s)", line
        ):
            if result := take(index):
                yield result
            continue
        if heading := re.match(r"^(#{1,6})\s+(.+?)\s*$", line):
            if result := take(index):
                yield result
            level = len(heading.group(1))
            headings = headings[: level - 1] + [heading.group(2)]
            continue
        if not line.strip():
            if result := take(index):
                yield result
            continue
        if start is None:
            start = index
            paragraph_headings = tuple(headings)


def paragraphs(lines: list[str]) -> Iterator[tuple[str, tuple[str, ...]]]:
    for start, end, headings in paragraph_spans(lines):
        value = "\n".join(lines[start:end]).strip()
        if value:
            yield value, headings
