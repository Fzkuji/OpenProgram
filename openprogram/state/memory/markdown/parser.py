"""Convert Topic Markdown files into memory units.

Two readings of the same files. The strict one is the structure a write
must leave in the files it writes: every paragraph a block with an
``^id``, each ID held once, every footnote it cites defined. Whether a
paragraph cites a source is not the file's concern but its author's, and
is checked where a write commits (``commit_edits``). The lenient one is
how memory is read: any Topic file that decodes is memory, a paragraph
without an ``^id`` is read under a derived one, a footnote that does not
parse reads as plain text, and whatever goes wrong in one file stays in
that file. A write checks strictly only the files it wrote
(``read_checked_tree``); every other file is read leniently.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .models import EvidenceAnnotation, MEMORY_ID, MemoryUnit, TopicFormatError
from .syntax import (
    ANY_BLOCK_SUFFIX,
    BLOCK_LINK,
    BLOCK_SUFFIX,
    CITATION_GROUP,
    SINGLE_CITATION,
    definitions,
    paragraph_spans,
    paragraphs,
)

# The derived ID of a paragraph that has none: never valid ``^id`` syntax,
# so it cannot be written into a file or linked to.
DERIVED_PREFIX = "~"
NOT_TEXT = "not text"


def _evidence(
    body: str,
    known_definitions: dict,
    *,
    strict: bool,
) -> tuple[list[EvidenceAnnotation], set[str], int]:
    """The cited claims in ``body``, the footnotes they use, and where the
    last citation ends."""
    evidence = []
    used: set[str] = set()
    cursor = 0
    claim = 0
    for group in CITATION_GROUP.finditer(body):
        quote = body[cursor:group.start()].strip()
        ids = [match.group("id") for match in SINGLE_CITATION.finditer(group.group(0))]
        if strict and not quote:
            raise TopicFormatError(f"missing memory content before {ids[0]}")
        cursor = group.end()
        if not quote:
            continue
        claim += 1
        for citation_id in ids:
            if citation_id not in known_definitions:
                if strict:
                    raise TopicFormatError(f"undefined footnote: {citation_id}")
                continue
            when, refs, links, labels = known_definitions[citation_id]
            evidence.append(EvidenceAnnotation(
                citation_id=citation_id,
                quote=quote,
                when=when,
                source_refs=refs,
                source_links=links,
                source_labels=labels,
                claim=claim,
            ))
            used.add(citation_id)
    return evidence, used, cursor


def _unit_fields(
    body: str,
    evidence: list[EvidenceAnnotation],
    headings: tuple[str, ...],
    relative: str,
    created_order: int,
    line: int,
) -> dict:
    refs: list[str] = []
    links: list[str] = []
    labels: list[str] = []
    for annotation in evidence:
        for ref, link, label in zip(
            annotation.source_refs,
            annotation.source_links,
            annotation.source_labels,
        ):
            if ref not in refs:
                refs.append(ref)
                links.append(link)
                labels.append(label)
    return dict(
        content=SINGLE_CITATION.sub("", body).strip(),
        when=next((item.when for item in evidence if item.when is not None), None),
        source_refs=tuple(refs),
        source_links=tuple(links),
        source_labels=tuple(labels),
        topic_path=relative,
        headings=headings,
        created_order=created_order,
        evidence=tuple(evidence),
        relation_targets=tuple(
            match.group("id") for match in BLOCK_LINK.finditer(body)
        ),
        line=line,
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
    line: int = 0,
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
    evidence, used, cursor = _evidence(body, known_definitions, strict=True)
    # A paragraph may cite nothing (a person's note); one that cites ends
    # with its last citation.
    if strict and evidence and body[cursor:].strip():
        raise TopicFormatError(f"content after final evidence: {memory_id}")
    shared = _unit_fields(
        body, evidence, headings, path.relative_to(topics).as_posix(),
        created_order, line,
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
            when, refs, links, labels = known_definitions[memory_id]
            units.append(MemoryUnit(
                memory_id=memory_id,
                content=content,
                when=when,
                source_refs=refs,
                source_links=links,
                source_labels=labels,
                topic_path=path.relative_to(topics).as_posix(),
                headings=headings,
                created_order=created_order + len(units),
            ))
            used.add(memory_id)
        cursor = group.end()
    return units, used


def parse_topic_tree(topics: Path, *, strict: bool = True) -> list[MemoryUnit]:
    """Return memory units in their current Markdown occurrence order.

    ``strict`` checks the format a write leaves behind and raises on the
    first departure. Unstrict, the tree is read the way memory is read:
    see ``read_topic_tree``.
    """
    if not strict:
        return read_topic_tree(topics).units
    topics = Path(topics)
    units: list[MemoryUnit] = []
    seen: set[str] = set()
    for path in sorted(topics.rglob("*.md")):
        try:
            _parse_topic_file(path, topics, units, seen, strict=strict)
        except TopicFormatError as exc:
            # Name the file: a writer told only "undefined footnote: e1" has
            # every file it touched to search.
            relative = path.relative_to(topics).as_posix()
            if relative in str(exc):
                raise
            raise TopicFormatError(f"{exc} (in topics/{relative})") from exc
    return units


def topic_tree_problems(
    topics: Path,
) -> tuple[list[MemoryUnit], dict[str, str]]:
    """Parse every Topic file that parses; name the ones that do not.

    A file that fails contributes no units, so the rest of the tree can
    still serve as the baseline of the edit that repairs it.
    """
    topics = Path(topics)
    units: list[MemoryUnit] = []
    seen: set[str] = set()
    problems: dict[str, str] = {}
    for path in sorted(topics.rglob("*.md")):
        mark, seen_before = len(units), set(seen)
        try:
            _parse_topic_file(path, topics, units, seen, strict=True)
        except TopicFormatError as exc:
            del units[mark:]
            seen.intersection_update(seen_before)
            problems[path.relative_to(topics).as_posix()] = str(exc)
    return units, problems


def _parse_topic_file(
    path: Path,
    topics: Path,
    units: list[MemoryUnit],
    seen: set[str],
    *,
    strict: bool,
) -> None:
    lines = without_front_matter(path.read_text(encoding="utf-8").splitlines())
    known_definitions = definitions(lines)
    used_definitions: set[str] = set()
    for start, end, headings in paragraph_spans(lines):
        paragraph = "\n".join(lines[start:end]).strip()
        if not paragraph:
            continue
        parsed = _block_unit(
            paragraph,
            headings,
            path,
            topics,
            known_definitions,
            len(units),
            strict=strict,
            line=start + 1,
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


@dataclass(frozen=True)
class TopicTree:
    """Every unit memory holds, and what kept a file or ID out."""

    units: list[MemoryUnit]
    # Topic path (relative to ``topics/``) -> what went wrong there.
    problems: dict[str, str]


def topic_files(topics: Path) -> list[Path]:
    """The Topic files under ``topics``, ``.md`` in any case, in path order."""
    topics = Path(topics)
    if not topics.is_dir():
        return []
    return sorted(
        path for path in topics.rglob("*")
        if path.suffix.lower() == ".md" and path.is_file() and not path.is_symlink()
    )


def decode_topic(data: bytes) -> str | None:
    """A Topic file's text: a byte-order mark dropped, line ends made
    newlines. None when the bytes are not UTF-8 text."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def without_front_matter(lines: list[str]) -> list[str]:
    """YAML front matter is metadata, not prose: blank it out, keeping
    line numbers."""
    if not lines or lines[0].strip() != "---":
        return lines
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return [""] * (index + 1) + lines[index + 1:]
    return lines


def path_key(relative: str) -> str:
    """The part of a derived ID that names its file: six hex digits of the
    path, so an ID whose paragraph has since changed still leads to it."""
    return hashlib.sha256(relative.encode()).hexdigest()[:6]


def _derived_id(relative: str, paragraph: str) -> str:
    digest = hashlib.sha256(f"{relative}\0{paragraph}".encode()).hexdigest()
    return f"{DERIVED_PREFIX}{path_key(relative)}-{digest[:12]}"


def derived_path_key(memory_id: str) -> str | None:
    """The file key inside a derived ID, or None for any other ID."""
    match = re.fullmatch(
        re.escape(DERIVED_PREFIX) + r"([0-9a-f]{6})-[0-9a-f]{12}", memory_id or "",
    )
    return match.group(1) if match else None


def file_for_derived_id(topics: Path, memory_id: str) -> Path | None:
    """The Topic file a derived ID was read from, found by its path key."""
    key = derived_path_key(memory_id)
    if key is None:
        return None
    topics = Path(topics)
    for path in topic_files(topics):
        if path_key(path.relative_to(topics).as_posix()) == key:
            return path
    return None


def read_topic_file(
    path: Path, topics: Path, *, data: bytes | None = None,
    outside: bool = False,
) -> tuple[list[MemoryUnit], str | None]:
    """One Topic file, read leniently; never raises.

    Returns its units and, when the file yields nothing because it could
    not be read, why. ``created_order`` counts within the file; IDs are
    the file's own until ``read_topic_tree`` settles them across files.
    """
    relative = Path(path).relative_to(topics).as_posix()
    try:
        raw = Path(path).read_bytes() if data is None else data
    except OSError as exc:
        return [], f"unreadable: {exc.strerror or exc}"
    text = decode_topic(raw)
    if text is None:
        return [], NOT_TEXT
    lines = without_front_matter(text.split("\n"))
    try:
        known = definitions(lines, strict=False)
    except Exception as exc:  # noqa: BLE001 - one file never fails the rest
        return [], str(exc)
    units: list[MemoryUnit] = []
    derived: set[str] = set()
    for start, end, headings in paragraph_spans(lines):
        paragraph = "\n".join(lines[start:end]).strip()
        if not paragraph:
            continue
        try:
            block = BLOCK_SUFFIX.search(paragraph)
            body = paragraph[:block.start()].rstrip() if block else paragraph
            evidence, _used, _cursor = _evidence(body, known, strict=False)
            shared = _unit_fields(
                body, evidence, headings, relative, len(units), start + 1,
            )
        except Exception:  # noqa: BLE001 - the paragraph reads as text
            evidence = []
            block = None
            body = paragraph
            shared = _unit_fields(
                body, [], headings, relative, len(units), start + 1,
            )
        shared["outside"] = outside
        if block:
            absorbed = re.findall(
                r"\^([A-Za-z0-9-]+)", paragraph[block.start():],
            )
            units.extend(
                MemoryUnit(memory_id=value, **shared) for value in absorbed
            )
            continue
        try:
            # An older file cites each claim under its own ``mem_`` ID.
            legacy, _used = _legacy_units(
                paragraph, headings, Path(path), Path(topics), known, len(units),
            )
        except Exception:  # noqa: BLE001 - then it reads as prose
            legacy = []
        if legacy:
            units.extend(
                replace(unit, line=start + 1, outside=outside) for unit in legacy
            )
            continue
        memory_id = _derived_id(relative, paragraph)
        while memory_id in derived:
            memory_id = _derived_id(relative, f"{paragraph}\0{len(derived)}")
        derived.add(memory_id)
        units.append(MemoryUnit(memory_id=memory_id, derived_id=True, **shared))
    return units, None


def settle_ids(
    files: list[tuple[str, list[MemoryUnit]]],
) -> TopicTree:
    """Join per-file units in path order, each ID held once.

    A file whose ``^id`` an earlier file (by path) already holds keeps its
    paragraph under a derived ID instead, and the clash is named.
    """
    units: list[MemoryUnit] = []
    problems: dict[str, str] = {}
    seen: set[str] = set()
    for relative, file_units in sorted(files, key=lambda item: item[0]):
        offset = len(units)
        for unit in file_units:
            memory_id, derived = unit.memory_id, unit.derived_id
            if memory_id in seen:
                problems.setdefault(relative, f"duplicate memory_id: {memory_id}")
                memory_id = _derived_id(relative, f"{memory_id}\0{unit.content}")
                derived = True
            seen.add(memory_id)
            units.append(replace(
                unit, memory_id=memory_id, derived_id=derived,
                created_order=offset + unit.created_order,
            ))
    return TopicTree(units, problems)


def read_topic_tree(topics: Path, *, outside=frozenset()) -> TopicTree:
    """Every Topic file read leniently, each in isolation; never raises.

    This is how memory is read. Which units an index admits is the
    index's choice, not the reader's. ``outside`` names the files (paths
    under ``topics/``) written outside OpenProgram; their units say so.
    """
    topics = Path(topics)
    files: list[tuple[str, list[MemoryUnit]]] = []
    problems: dict[str, str] = {}
    for path in topic_files(topics):
        relative = path.relative_to(topics).as_posix()
        units, problem = read_topic_file(
            path, topics, outside=relative in outside,
        )
        if problem:
            problems[relative] = problem
        files.append((relative, units))
    tree = settle_ids(files)
    return TopicTree(tree.units, {**problems, **tree.problems})


def read_checked_tree(
    topics: Path, *, checked=frozenset(), outside=frozenset(),
) -> list[MemoryUnit]:
    """The tree read leniently, with the files in ``checked`` held to the
    strict structure. Raises ``TopicFormatError`` naming the first checked
    file that departs from it, or that holds an ``^id`` another file holds.

    A write checks the files it wrote: a file nobody touched, or one
    written outside OpenProgram, stays readable whatever it holds, and
    never fails a write it had no part in.
    """
    topics = Path(topics)
    tree = read_topic_tree(topics, outside=outside)
    if not checked:
        return tree.units
    holder = {
        unit.memory_id: unit.topic_path
        for unit in tree.units if not unit.derived_id
    }
    prefix = "duplicate memory_id: "
    for later, problem in sorted(tree.problems.items()):
        if not problem.startswith(prefix):
            continue
        memory_id = problem[len(prefix):]
        earlier = holder.get(memory_id)
        # A checked file may not take an ID another of memory's own files
        # holds. A file written outside that copies one of ours is read
        # under derived IDs and blames no write.
        if later in checked:
            raise TopicFormatError(f"{problem} (in topics/{later})")
        if earlier in checked and later not in outside:
            raise TopicFormatError(f"{problem} (in topics/{earlier})")
    for relative in sorted(checked):
        path = topics / relative
        if not path.is_file():
            continue
        try:
            _parse_topic_file(path, topics, [], set(), strict=True)
        except TopicFormatError as exc:
            if relative in str(exc):
                raise
            raise TopicFormatError(f"{exc} (in topics/{relative})") from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise TopicFormatError(f"{exc} (in topics/{relative})") from exc
    return tree.units


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
