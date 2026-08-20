"""Low-level parsing of headings, paragraphs, footnotes, and block links."""

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from ..source_format import (
    is_v2_source_path,
    provider_source_location,
    scan_source_archive,
    valid_v2_source_id,
)
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
SOURCE_HANDLE = re.compile(
    r"^(D\d+:\d+|[^/\s,]+/[^/\s,]+/[^/\s,]+)(?:\s*(?:,|·)\s*|\s+|$)"
)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_MARKDOWN_LABEL = re.compile(r"[\[\]()`*_~<>\\]")
_GENERIC_LABEL = re.compile(
    r"(?i)^(?:(?:owner|user|assistant|system|speaker|source|reference|ref|item|record)"
    r"(?:\s*[-:#]?\s*\d+)?|[a-z]\d+|(?:用户|助手|系统|说话人|来源|引用|记录|条目)\s*\d*)$"
)
_DATE_LABEL = re.compile(r"^\d{4}(?:[-/]\d{1,2}){0,2}$")
_UUID_LABEL = re.compile(
    r"(?i)^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
)
_RUNTIME_ID_LABEL = re.compile(
    r"(?i)^(?:D\d+:\d+|"
    r"(?:(?:e|source|evidence|event|memory|mem|message|msg|block|thread|turn|reply)"
    r"[-_])?[0-9a-f]{8,}(?:_?reply)?)$"
)
_ENGLISH_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


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


def normalize_source_label(value: str, source_ref: str | None = None) -> str:
    """Return a short plain display label; Source identity stays separate."""
    raw_label = "".join(
        character if character.isprintable() else " " for character in value
    )
    raw_label = " ".join(raw_label.split())
    id_candidate = re.sub(r"^[^\w]+|[^\w]+$", "", raw_label)
    if _RUNTIME_ID_LABEL.fullmatch(id_candidate):
        return "相关内容"
    label = raw_label
    label = " ".join(_MARKDOWN_LABEL.sub("", label).split())
    label = re.sub(r"^[^\w]+|[^\w]+$", "", label)
    if (
        not label
        or label == source_ref
        or "://" in label
        or "/" in label
        or _GENERIC_LABEL.fullmatch(label)
        or _DATE_LABEL.fullmatch(label)
        or _UUID_LABEL.fullmatch(label)
        or _RUNTIME_ID_LABEL.fullmatch(label)
    ):
        return "相关内容"
    if _CJK.search(label):
        visible = 0
        kept = []
        for character in label:
            if not character.isspace():
                visible += 1
                if visible > 8:
                    break
            kept.append(character)
        label = "".join(kept).strip()
    else:
        words = list(_ENGLISH_WORD.finditer(label))
        if len(words) > 6:
            label = label[:words[5].end()].strip()
    return label or "相关内容"


def is_plain_source_handle(value: str) -> bool:
    value = value.strip()
    return bool(re.fullmatch(r"D\d+:\d+", value)) or (
        "#" not in value and valid_v2_source_id(value)
    )


def _source_from_target(
    target: str,
    *,
    topic_path: Path | None,
    topics: Path | None,
    source_lookup: dict[Path, dict[str, str]] | None,
) -> str | None:
    if is_plain_source_handle(target):
        return target.strip()
    raw_path, separator, fragment = target.partition("#")
    if not separator or not raw_path or topic_path is None or topics is None:
        return None
    memory_dir = Path(topics).resolve().parent
    sources = (memory_dir / "sources").resolve()
    unresolved = Path(topic_path).resolve().parent / raw_path
    if unresolved.is_symlink():
        return None
    candidate = unresolved.resolve()
    if (
        not candidate.is_relative_to(sources)
        or not candidate.is_file()
    ):
        return None
    relative = candidate.relative_to(memory_dir)
    lookup = source_lookup if source_lookup is not None else {}
    if candidate not in lookup:
        text = candidate.read_text(encoding="utf-8")
        anchors = {}
        if is_v2_source_path(relative):
            for frame in scan_source_archive(text, relative).frames:
                location = provider_source_location(frame.source_id, v2=True)
                if location is not None:
                    anchors[location[1]] = frame.source_id
        else:
            for anchor, source_id in re.findall(
                r'<a id="([^"]+)"></a>\n'
                r'<!-- source-id:([^>\r\n]+) -->',
                text,
            ):
                location = provider_source_location(source_id)
                if location == (relative, anchor):
                    anchors[anchor] = source_id
        lookup[candidate] = anchors
    return lookup[candidate].get(fragment)


def source_reference(
    label: str,
    target: str,
    *,
    topic_path: Path | None = None,
    topics: Path | None = None,
    source_lookup: dict[Path, dict[str, str]] | None = None,
) -> str:
    resolved = _source_from_target(
        target,
        topic_path=topic_path,
        topics=topics,
        source_lookup=source_lookup,
    )
    if resolved is not None:
        return resolved
    handle = SOURCE_HANDLE.match(label.strip())
    if handle:
        return handle.group(1)
    anchor = re.search(r"#d(\d+)-(\d+)$", target, re.IGNORECASE)
    return f"D{anchor.group(1)}:{anchor.group(2)}" if anchor else label


def definitions(
    lines: list[str],
    *,
    topic_path: Path | None = None,
    topics: Path | None = None,
    source_lookup: dict[Path, dict[str, str]] | None = None,
) -> dict[
    str,
    tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]],
]:
    values = {}
    for line in lines:
        match = definition_match(line)
        if not match:
            continue
        citation_id = match.group("id")
        if citation_id in values:
            raise TopicFormatError(
                f"duplicate footnote definition: {citation_id}"
            )
        when = match.group("when")
        if when != "undated" and not is_valid_temporal_value(when):
            raise TopicFormatError(f"invalid evidence date: {when}")
        links = LINK.findall(match.group("sources"))
        if not links:
            raise TopicFormatError(
                f"memory source links required: {citation_id}"
            )
        values[citation_id] = (
            None if when == "undated" else when,
            tuple(
                source_reference(
                    label,
                    target,
                    topic_path=topic_path,
                    topics=topics,
                    source_lookup=source_lookup,
                )
                for label, target in links
            ),
            tuple(target for _label, target in links),
            tuple(label for label, _target in links),
        )
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
