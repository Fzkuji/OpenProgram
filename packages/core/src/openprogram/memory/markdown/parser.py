"""Convert Topic Markdown files into stable memory units."""

import re
from pathlib import Path

from .models import EvidenceAnnotation, MEMORY_ID, MemoryUnit, TopicFormatError
from .syntax import (
    ANY_BLOCK_SUFFIX,
    BLOCK_LINK,
    BLOCK_SUFFIX,
    CITATION_GROUP,
    SINGLE_CITATION,
    definitions,
    paragraphs,
)


def _block_unit(
    paragraph: str,
    headings: tuple[str, ...],
    path: Path,
    topics: Path,
    known_definitions: dict,
    created_order: int,
    *,
    strict: bool,
) -> tuple[list[MemoryUnit], set[str]] | None:
    block = BLOCK_SUFFIX.search(paragraph)
    any_block = ANY_BLOCK_SUFFIX.search(paragraph)
    if any_block and not block:
        raise TopicFormatError(f"invalid block_id: {any_block.group('id')}")
    if not block:
        return None
    # A merge keeps every absorbed ID on one paragraph. The last is this
    # paragraph's identity; the rest are aliases the caller re-emits so
    # links pointing at them still resolve.
    absorbed = re.findall(r"\^([A-Za-z0-9-]+)", paragraph[block.start():])
    memory_id = absorbed[-1]
    body = paragraph[:block.start()].rstrip()
    evidence = []
    used: set[str] = set()
    cursor = 0
    for group in CITATION_GROUP.finditer(body):
        quote = body[cursor:group.start()].strip()
        ids = [match.group("id") for match in SINGLE_CITATION.finditer(group.group(0))]
        if not quote:
            raise TopicFormatError(f"missing memory content before {ids[0]}")
        for citation_id in ids:
            if citation_id not in known_definitions:
                raise TopicFormatError(f"undefined footnote: {citation_id}")
            when, refs, links = known_definitions[citation_id]
            evidence.append(EvidenceAnnotation(
                citation_id=citation_id,
                quote=quote,
                when=when,
                source_refs=refs,
                source_links=links,
            ))
            used.add(citation_id)
        cursor = group.end()
    if strict and not evidence:
        raise TopicFormatError(f"memory source footnote required: {memory_id}")
    if strict and body[cursor:].strip():
        raise TopicFormatError(f"content after final evidence: {memory_id}")
    refs: list[str] = []
    links: list[str] = []
    for annotation in evidence:
        for ref, link in zip(annotation.source_refs, annotation.source_links):
            if ref not in refs:
                refs.append(ref)
                links.append(link)
    shared = dict(
        content=SINGLE_CITATION.sub("", body).strip(),
        when=next((item.when for item in evidence if item.when is not None), None),
        source_refs=tuple(refs),
        source_links=tuple(links),
        topic_path=path.relative_to(topics).as_posix(),
        headings=headings,
        created_order=created_order,
        evidence=tuple(evidence),
        relation_targets=tuple(
            match.group("id") for match in BLOCK_LINK.finditer(body)
        ),
    )
    return [
        MemoryUnit(memory_id=value, **shared) for value in absorbed
    ], used


def _legacy_units(
    paragraph: str,
    headings: tuple[str, ...],
    path: Path,
    topics: Path,
    known_definitions: dict,
    created_order: int,
) -> tuple[list[MemoryUnit], set[str]]:
    units = []
    used = set()
    cursor = 0
    for group in CITATION_GROUP.finditer(paragraph):
        content = paragraph[cursor:group.start()].strip()
        ids = [match.group("id") for match in SINGLE_CITATION.finditer(group.group(0))]
        if any(not re.fullmatch(MEMORY_ID, memory_id) for memory_id in ids):
            continue
        if not content:
            raise TopicFormatError(f"missing memory content before {ids[0]}")
        for memory_id in ids:
            if memory_id not in known_definitions:
                raise TopicFormatError(f"undefined footnote: {memory_id}")
            when, refs, links = known_definitions[memory_id]
            units.append(MemoryUnit(
                memory_id=memory_id,
                content=content,
                when=when,
                source_refs=refs,
                source_links=links,
                topic_path=path.relative_to(topics).as_posix(),
                headings=headings,
                created_order=created_order + len(units),
            ))
            used.add(memory_id)
        cursor = group.end()
    return units, used


def parse_topic_tree(topics: Path, *, strict: bool = True) -> list[MemoryUnit]:
    """Return memory units in their current Markdown occurrence order."""
    topics = Path(topics)
    units: list[MemoryUnit] = []
    seen: set[str] = set()
    for path in sorted(topics.rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        known_definitions = definitions(lines)
        used_definitions: set[str] = set()
        for paragraph, headings in paragraphs(lines):
            parsed = _block_unit(
                paragraph,
                headings,
                path,
                topics,
                known_definitions,
                len(units),
                strict=strict,
            )
            if parsed:
                block_units, used = parsed
                for unit in block_units:
                    if unit.memory_id in seen:
                        raise TopicFormatError(
                            f"duplicate memory_id: {unit.memory_id}"
                        )
                    units.append(unit)
                    seen.add(unit.memory_id)
                used_definitions.update(used)
                continue
            legacy, used = _legacy_units(
                paragraph,
                headings,
                path,
                topics,
                known_definitions,
                len(units),
            )
            if strict and not legacy:
                raise TopicFormatError(
                    f"memory block ID required: {path.relative_to(topics)}"
                )
            for unit in legacy:
                if unit.memory_id in seen:
                    raise TopicFormatError(f"duplicate memory_id: {unit.memory_id}")
                units.append(unit)
                seen.add(unit.memory_id)
            used_definitions.update(used)
        unused = set(known_definitions) - used_definitions
        if strict and unused:
            raise TopicFormatError(
                f"unused footnote definition: {sorted(unused)[0]}"
            )
    return units


def topic_prose(topics: Path) -> str:
    """Return normalized non-structural Topic prose, including unbound text."""
    values = []
    for path in sorted(Path(topics).rglob("*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for paragraph, _headings in paragraphs(lines):
            value = SINGLE_CITATION.sub("", paragraph)
            value = BLOCK_SUFFIX.sub("", value)
            values.append(" ".join(value.split()))
    return "\n".join(values)
