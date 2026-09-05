"""Local event-level BM25 retrieval over the memory workspace.

The index is deliberately lexical: it uses no embedding model or vector store.
Topic and source files are indexed; timeline files are excluded because they
duplicate topic events.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from rank_bm25 import BM25Plus

from openprogram._text import normalize_identity_header_part

from ..markdown.syntax import (
    definition_match,
    source_reference,
)
from ..workspace_layout import runtime_dir
from ..source_format import (
    is_v2_source_path,
    provider_source_location,
    scan_source_archive,
)

# The cache sits beside the runtime directory and takes its name, so a
# workspace built before the rename keeps every file it already has.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
# Scripts written without spaces, where a whole run would otherwise become one
# token: CJK ideographs (plus extensions A/B and compatibility), hiragana,
# katakana and hangul syllables.
_UNSEGMENTED_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]"
    r"|[\U00020000-\U0002a6df]"
)
_REF_RE = re.compile(r"D\d+:\d+(?:-(?:D\d+:)?\d+)?")
_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_TEMPORAL_VALUE_RE = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_EVENT_RE = re.compile(r"<!--\s*memory-event:(ev_[0-9a-f]+)\s*-->")
_SOURCE_ID_LINE_RE = re.compile(r"\s*<!--\s*source-id:([^>]+?)\s*-->\s*")
_SPEAKER_ID_LINE_RE = re.compile(r"\s*<!--\s*speaker-id:([^>]*?)\s*-->\s*")
_RECORD_LINES_LINE_RE = re.compile(
    r"\s*<!--\s*record-lines:(\d{1,9})\s*-->\s*"
)
_ANCHOR_LINE_RE = re.compile(r'\s*<a id="([^"]+)"></a>\s*')
_SOURCE_RECORD_RE = re.compile(r"^\[([^]]*)\]\s+(.+?): (.*)$")
_LEGACY_SPEAKER_RE = re.compile(r"^\[([^]\r\n]+)\]\s*(.*)$")
_COMMENT_RE = re.compile(r"<!--.*?-->")
_MARKDOWN_LINK_RE = re.compile(r"\[([^]]+)\]\(([^)]+)\)")
_BLOCK_SUFFIX_RE = re.compile(r"\s+\^([A-Za-z0-9-]+)\s*$")
_EVIDENCE_RE = re.compile(r"\[\^([A-Za-z0-9_-]+)\]")
_EVIDENCE_GROUP_RE = re.compile(r"(?:\[\^[A-Za-z0-9_-]+\])+")


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    path: str
    line: int
    headings: list[str]
    date: str
    dates: list[str]
    content: str
    refs: list[str]
    speaker_id: str = ""
    speaker_display: str = ""
    speaker_label: str = ""
    speaker_trusted: bool = False
    trust_state: str = "unknown"
    speaker_kind: str = "unknown"
    principal_id: str = "unknown"
    authority_tier: str | None = None
    # Audience visibility, expressed with the same vocabulary as
    # ``AuthorityTier``. A record a paired speaker may read is
    # ``"paired"``; anything narrower is ``"owner"``. Kept apart from
    # ``trust_state`` on purpose: trust decides whether a claim may be
    # believed, visibility decides who may see it, and one is not the
    # other. ``None`` means the event carries no visibility of its own
    # and inherits the workspace default.
    visibility: str | None = None


def tokenize(text: str) -> list[str]:
    """Tokenize lexical text without language-model dependencies.

    Space-delimited scripts split on word boundaries. Scripts written without
    spaces are additionally indexed per character and as adjacent pairs: a run
    like "成本是怎么回事" is one word-boundary token, so a query for "成本"
    would never match it, making such memory findable only by repeating the
    whole run verbatim. Emitting the run, its characters and its bigrams keeps
    exact-run matches ranked highest while making substrings retrievable.
    """
    tokens = []
    for token in _WORD_RE.findall(text):
        folded = token.casefold()
        tokens.append(folded)
        characters = _UNSEGMENTED_RE.findall(folded)
        if len(characters) < 2 or len(characters) != len(folded):
            continue
        tokens.extend(characters)
        tokens.extend(
            first + second
            for first, second in zip(characters, characters[1:])
        )
    return tokens


def _clean_markdown(text: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _COMMENT_RE.sub(" ", text)
    return " ".join(text.split())


def _refs(text: str) -> list[str]:
    return list(dict.fromkeys(_REF_RE.findall(text)))


def _date(text: str) -> str:
    match = _DATE_RE.search(text)
    return match.group(0) if match else ""


def _stable_id(path: str, line: int, content: str, refs: list[str]) -> str:
    payload = json.dumps([path, line, content, refs], ensure_ascii=False)
    return "lex_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def temporal_bounds(value: str) -> tuple[date, date]:
    """Expand a year, month, or day to a half-open calendar interval."""
    value = str(value).strip()
    if not _TEMPORAL_VALUE_RE.fullmatch(value):
        raise ValueError(f"invalid temporal value: {value}")
    parts = value.split("-")
    year = int(parts[0])
    try:
        if len(parts) == 1:
            return date(year, 1, 1), date(year + 1, 1, 1)
        month = int(parts[1])
        start = date(year, month, int(parts[2]) if len(parts) == 3 else 1)
        if len(parts) == 3:
            return start, start + timedelta(days=1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return start, end
    except ValueError as error:
        raise ValueError(f"invalid temporal value: {value}") from error


def _normalize_path_prefix(path_prefix: str) -> str:
    """Resolve a search prefix against the indexed roots.

    A prefix may name a root ("topics", "sources/"), a path under a root
    ("topics/tooling"), or a path relative to topics ("tooling"). Compare the
    bare root name, not the slash-stripped string: stripping "topics/" down to
    "topics" and then testing for a "topics/" prefix fails, which silently
    rewrote the most common query into "topics/topics" and matched nothing.
    """
    normalized = path_prefix.strip().strip("/")
    if not normalized:
        return ""
    head = normalized.split("/", 1)[0]
    if head in ("topics", "sources"):
        return normalized
    return f"topics/{normalized}"


def event_matches_path_prefix(event_path: str, normalized: str) -> bool:
    """Match physical v2 paths and their legacy logical source path."""
    if event_path.startswith(normalized):
        return True
    if event_path.startswith("sources/") and is_v2_source_path(event_path):
        parts = list(Path(event_path).parts)
        del parts[-2]
        logical = Path(*parts).as_posix()
        return logical.startswith(normalized)
    return False


def resolve_topic_trust(events: list[MemoryEvent]) -> list[MemoryEvent]:
    """Give every Topic event the trust of the Sources it cites.

    A Topic block is a claim written *from* evidence, so it can be no more
    trusted than that evidence. Parsing a Topic file cannot know this — the
    trust lives in the Source archive — so the verdict is computed here,
    once the whole workspace has been read, and stamped onto the event.

    All-or-nothing, deliberately. A paragraph citing one trusted and one
    pending Source is partly built on speech nobody has vouched for, and
    there is no way to tell from the prose which half came from which. So
    a single pending citation makes the whole block pending, and it stays
    that way until every Source it rests on is promoted. Promoting them is
    what brings the block back, with no extra bookkeeping to undo.

    A reference naming no known Source leaves the block pending too:
    unresolvable evidence is not evidence, and failing open here is what
    would let a dangling ref launder an unvouched claim into recall.
    """
    trust_by_source = {
        event.event_id: event.trust_state
        for event in events
        if event.path.startswith("sources/")
    }
    resolved = []
    for event in events:
        if event.path.startswith("sources/") or not event.refs:
            resolved.append(event)
            continue
        states = {
            trust_by_source.get(str(ref), "unknown") for ref in event.refs
        }
        trust = "trusted" if states == {"trusted"} else "pending"
        resolved.append(
            event if event.trust_state == trust
            else replace(event, trust_state=trust)
        )
    return resolved


def prefer_v2_source_events(events: list[MemoryEvent]) -> list[MemoryEvent]:
    """Drop legacy copies only when a valid v2 event has the same ID."""
    v2_ids = {
        event.event_id
        for event in events
        if event.path.startswith("sources/") and is_v2_source_path(event.path)
    }
    return [
        event
        for event in events
        if not (
            event.path.startswith("sources/")
            and not is_v2_source_path(event.path)
            and event.event_id in v2_ids
        )
    ]


def _query_time_window(
    date_from: str | None, date_to: str | None
) -> tuple[date | None, date | None] | None:
    if not date_from and not date_to:
        return None
    try:
        start = temporal_bounds(date_from)[0] if date_from else None
    except ValueError as error:
        raise ValueError(f"invalid date_from: {date_from}") from error
    try:
        end = temporal_bounds(date_to)[1] if date_to else None
    except ValueError as error:
        raise ValueError(f"invalid date_to: {date_to}") from error
    if start is not None and end is not None and start >= end:
        raise ValueError("date_from must not be after date_to")
    return start, end


def _event_overlaps_window(
    event: MemoryEvent, window: tuple[date | None, date | None] | None
) -> bool:
    if window is None:
        return True
    query_start, query_end = window
    for value in event.dates:
        try:
            event_start, event_end = temporal_bounds(value)
        except ValueError:
            continue
        if (query_end is None or event_start < query_end) and (
            query_start is None or query_start < event_end
        ):
            return True
    return False


def event_matches_time_window(
    event: MemoryEvent,
    date_from: str | None = None,
    date_to: str | None = None,
) -> bool:
    """Return whether any dated evidence overlaps the optional query window."""
    return _event_overlaps_window(event, _query_time_window(date_from, date_to))


def parse_topic_file(
    path: Path,
    topics_root: Path,
    *,
    source_lookup: dict[Path, dict[str, str]] | None = None,
) -> list[MemoryEvent]:
    """Parse legacy line records and current paragraph-block Topic files."""
    relative = path.relative_to(topics_root).as_posix()
    lines = path.read_text(encoding="utf-8").splitlines()
    definitions = {}
    source_lookup = source_lookup if source_lookup is not None else {}
    for line in lines:
        match = definition_match(line)
        if match:
            labels = [
                source_reference(
                    label,
                    target,
                    topic_path=path,
                    topics=topics_root,
                    source_lookup=source_lookup,
                )
                for label, target in _MARKDOWN_LINK_RE.findall(
                    match.group("sources")
                )
            ]
            definitions[match.group("id")] = (
                "" if match.group("when") == "undated" else match.group("when"),
                labels or _refs(match.group("sources")),
            )
    headings: list[str] = []
    events: list[MemoryEvent] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            headings = headings[: level - 1] + [heading.group(2).strip()]
            index += 1
            continue

        marker = _EVENT_RE.search(line)
        if marker:
            event_id = marker.group(1)
            block = [line]
            content_line = ""
            content_number = index + 1
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                if _HEADING_RE.match(candidate) or _EVENT_RE.search(candidate):
                    break
                block.append(candidate)
                is_footnote_content = re.search(r"\[\^mem_[A-Za-z0-9_-]+\]", candidate)
                if (
                    not content_line
                    and not candidate.lstrip().startswith(("<!--", "[^mem_"))
                    and (_DATE_RE.search(candidate) or is_footnote_content)
                ):
                    content_line = candidate
                    content_number = cursor + 1
                cursor += 1
            joined = "\n".join(block)
            refs = _refs(joined)
            content = _clean_markdown(
                re.sub(r"\[\^mem_[A-Za-z0-9_-]+\]", "", content_line)
            )
            date = _date(joined)
            if content and date and not content.startswith(f"[{date}]"):
                content = f"[{date}] {content}"
            if content and refs:
                events.append(MemoryEvent(
                    event_id=event_id,
                    path=f"topics/{relative}",
                    line=content_number,
                    headings=list(headings),
                    date=date,
                    dates=[date] if date else [],
                    content=content,
                    refs=refs,
                ))
            index = cursor
            continue

        if definition_match(line):
            index += 1
            continue

        if line.strip():
            cursor = index
            paragraph = []
            while cursor < len(lines):
                candidate = lines[cursor]
                if not candidate.strip() or _HEADING_RE.match(candidate):
                    break
                if definition_match(candidate) or _EVENT_RE.search(candidate):
                    break
                paragraph.append(candidate)
                cursor += 1
            joined = "\n".join(paragraph)
            block = _BLOCK_SUFFIX_RE.search(joined)
            if block:
                body = joined[:block.start()]
                groups = list(_EVIDENCE_GROUP_RE.finditer(body))
                claim_start = 0
                for group_number, group in enumerate(groups, start=1):
                    citations = _EVIDENCE_RE.findall(group.group(0))
                    dates = list(dict.fromkeys(
                        definitions[citation][0]
                        for citation in citations
                        if citation in definitions and definitions[citation][0]
                    ))
                    refs = list(dict.fromkeys(
                        ref
                        for citation in citations
                        for ref in definitions.get(citation, ("", []))[1]
                    ))
                    content = _clean_markdown(body[claim_start:group.start()])
                    claim_start = group.end()
                    if not content or not refs:
                        continue
                    if dates and not content.startswith("["):
                        content = f"[{'; '.join(dates)}] {content}"
                    events.append(MemoryEvent(
                        event_id=f"{block.group(1)}:{group_number}",
                        path=f"topics/{relative}",
                        line=index + 1,
                        headings=list(headings),
                        date=dates[0] if dates else "",
                        dates=dates,
                        content=content,
                        refs=refs,
                    ))
                index = cursor
                continue

        refs = _refs(line)
        content = _clean_markdown(line)
        if refs and content and not line.lstrip().startswith("<!--"):
            event_date = _date(line)
            events.append(MemoryEvent(
                event_id=_stable_id(relative, index + 1, content, refs),
                path=f"topics/{relative}",
                line=index + 1,
                headings=list(headings),
                date=event_date,
                dates=[event_date] if event_date else [],
                content=content,
                refs=refs,
            ))
        index += 1

    return events


def _runtime_source_id(
    lines: list[str], index: int, relative: str
) -> str:
    """Return an adjacent runtime anchor/source header pair, or empty."""
    if index + 1 >= len(lines):
        return ""
    anchor = _ANCHOR_LINE_RE.fullmatch(lines[index])
    source = _SOURCE_ID_LINE_RE.fullmatch(lines[index + 1])
    if anchor is None or source is None:
        return ""
    source_id = source.group(1).strip()
    location = provider_source_location(source_id)
    if location is None:
        return ""
    expected_path = location[0].relative_to("sources").as_posix()
    expected_anchor = location[1]
    if relative != expected_path or anchor.group(1) != expected_anchor:
        return ""
    return source_id


def _structured_speaker(
    label: str, encoded_id: str
) -> tuple[str, str, str]:
    """Decode identity only from a runtime speaker marker and safe label."""
    speaker_id = unquote(encoded_id, errors="replace")
    normalized_id = normalize_identity_header_part(speaker_id)
    raw_label = label.strip()
    suffix = f" ({normalized_id})" if normalized_id else ""
    if suffix and raw_label.endswith(suffix):
        display_value = raw_label[:-len(suffix)]
    elif raw_label != normalized_id:
        display_value = raw_label
    else:
        display_value = ""
    display = normalize_identity_header_part(display_value)
    if display and normalized_id and display != normalized_id:
        safe_label = f"{display} ({normalized_id})"
    else:
        safe_label = display or normalized_id
    return speaker_id, display, safe_label


def _legacy_speaker(label: str, body: str) -> tuple[str, str, str]:
    """Read the historical body prefix only for a ``user`` record."""
    safe_label = normalize_identity_header_part(label.strip())
    if safe_label.casefold() != "user":
        return "", "", ""
    legacy = _LEGACY_SPEAKER_RE.match(body)
    if legacy is None:
        return "", "", ""
    legacy_label = normalize_identity_header_part(legacy.group(1).strip())
    both = re.fullmatch(r"(.+) \(([^()]*)\)", legacy_label)
    if both is not None:
        return both.group(2), both.group(1), legacy_label
    return "", legacy_label, legacy_label


def _parse_v2_source_file(
    path: Path, sources_root: Path, relative: str, text: str
) -> list[MemoryEvent]:
    lines = text.split("\n")
    scan = scan_source_archive(text, relative)
    events = []
    for frame in scan.frames:
        record = _SOURCE_RECORD_RE.match(lines[frame.record_index])
        if record is None:  # The shared scanner already enforces this.
            break
        if frame.encoded_speaker_id is not None:
            speaker_id, speaker_display, speaker_label = _structured_speaker(
                record.group(2), frame.encoded_speaker_id
            )
        else:
            speaker_id, speaker_display, speaker_label = "", "", ""
        record_text = "\n".join(
            lines[frame.record_index:frame.record_end]
        )
        content = _clean_markdown(record_text)
        event_date = _date(record_text)
        # A frame with no recorded metadata predates the authority header.
        # It is legacy evidence the local owner already accepted, so it
        # stays trusted; naming that here keeps the one place where an
        # absent header becomes a trust verdict visible.
        metadata = frame.metadata or {
            "trust_state": "trusted",
            "speaker_kind": "unknown",
            "principal_id": "unknown",
            "authority_tier": None,
        }
        speaker_trusted = bool(
            frame.encoded_speaker_id is not None
            and metadata["trust_state"] == "trusted"
        )
        events.append(MemoryEvent(
            event_id=frame.source_id,
            path=f"sources/{relative}",
            line=frame.record_index + 1,
            headings=[],
            date=event_date,
            dates=[event_date] if event_date else [],
            content=content,
            refs=[frame.source_id],
            speaker_id=speaker_id,
            speaker_display=speaker_display,
            speaker_label=speaker_label,
            speaker_trusted=speaker_trusted,
            trust_state=str(metadata["trust_state"]),
            speaker_kind=str(metadata["speaker_kind"]),
            principal_id=str(metadata["principal_id"]),
            authority_tier=metadata["authority_tier"],
        ))
    return events


def parse_source_file(path: Path, sources_root: Path) -> list[MemoryEvent]:
    """Parse a strict v2 archive or a compatibility-only legacy file."""
    relative = path.relative_to(sources_root).as_posix()
    with path.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    text = text.replace("\r\n", "\n")
    if is_v2_source_path(relative):
        return _parse_v2_source_file(path, sources_root, relative, text)
    lines = text.split("\n")
    headings: list[str] = []
    events: list[MemoryEvent] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            headings = headings[: level - 1] + [heading.group(2).strip()]
            index += 1
            continue

        source_id = _runtime_source_id(lines, index, relative)
        if source_id:
            record_index = index + 2
            if record_index < len(lines):
                speaker = _SPEAKER_ID_LINE_RE.fullmatch(lines[record_index])
                if speaker is not None:
                    record_index += 1
            record_count = 1
            framed = False
            if record_index < len(lines):
                count = _RECORD_LINES_LINE_RE.fullmatch(lines[record_index])
                if count is not None:
                    framed = True
                    record_count = int(count.group(1))
                    record_index += 1
            if record_index < len(lines):
                record_line = lines[record_index]
                record = _SOURCE_RECORD_RE.match(record_line)
                record_end = record_index + record_count
                if (
                    record is not None
                    and record_count > 0
                    and record_end <= len(lines)
                ):
                    if framed:
                        speaker_id, speaker_display, speaker_label = "", "", ""
                    else:
                        speaker_id, speaker_display, speaker_label = (
                            _legacy_speaker(record.group(2), record.group(3))
                        )
                    record_text = "\n".join(lines[record_index:record_end])
                    content = _clean_markdown(record_text)
                    event_date = _date(record_text)
                    events.append(MemoryEvent(
                        event_id=source_id,
                        path=f"sources/{relative}",
                        line=record_index + 1,
                        headings=list(headings),
                        date=event_date,
                        dates=[event_date] if event_date else [],
                        content=content,
                        refs=[source_id],
                        speaker_id=speaker_id,
                        speaker_display=speaker_display,
                        speaker_label=speaker_label,
                        # A pre-v2 archive has no per-record trust header
                        # at all, so every record in it is evidence the
                        # owner accepted before the header existed.
                        trust_state="trusted",
                    ))
                    index = record_end
                    continue

        if not line.strip() or line.lstrip().startswith(("<a ", "<!--")):
            index += 1
            continue
        refs = _refs(line)
        content = _clean_markdown(line)
        if refs and content:
            event_date = _date(line)
            events.append(MemoryEvent(
                event_id=_stable_id(
                    f"sources/{relative}", index + 1, content, refs
                ),
                path=f"sources/{relative}",
                line=index + 1,
                headings=list(headings),
                date=event_date,
                dates=[event_date] if event_date else [],
                content=content,
                refs=refs,
                trust_state="trusted",
            ))
        index += 1
    return events


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _indexable_files(
    memory_dir: Path,
    files: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Path]:
    root = memory_dir.resolve()
    if files is None:
        candidates = (
            path
            for directory in (root / "topics", root / "sources")
            if directory.exists()
            for path in directory.rglob("*.md")
        )
    else:
        candidates = iter(files)

    result = {}
    for candidate in candidates:
        path = Path(candidate)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as error:
            raise ValueError("index path escapes memory workspace") from error
        if (
            len(relative.parts) >= 2
            and relative.parts[0] in {"topics", "sources"}
            and path.suffix == ".md"
            and path.is_file()
            and not path.is_symlink()
        ):
            result[relative.as_posix()] = path
    return result


class MemoryBM25Index:
    """Incrementally parsed local Topic and Source BM25 index."""

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        persist: bool = True,
        files: list[Path] | tuple[Path, ...] | None = None,
    ):
        self.memory_dir = Path(memory_dir).resolve()
        self.topics_dir = self.memory_dir / "topics"
        self.sources_dir = self.memory_dir / "sources"
        self._runtime_name = runtime_dir(self.memory_dir).name
        self.cache_path = self.memory_dir / f"{self._runtime_name}-bm25.json"
        self._visible_files = None if files is None else tuple(files)
        self.persist = persist and files is None
        self._lock = threading.RLock()
        self._files: dict[str, dict[str, Any]] = {}
        self.events: list[MemoryEvent] = []
        if self.persist:
            self._load_cache()
        self.refresh()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if payload.get("version") == 10 and isinstance(payload.get("files"), dict):
                self._files = payload["files"]
        except (OSError, ValueError, TypeError):
            self._files = {}

    def _write_cache(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 10, "files": self._files}
        fd, temporary = tempfile.mkstemp(prefix=f"{self._runtime_name}-bm25-", dir=self.memory_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temporary, self.cache_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def refresh(self) -> None:
        with self._lock:
            self._refresh()

    def _refresh(self) -> None:
        current = _indexable_files(self.memory_dir, self._visible_files)

        changed = set(self._files) != set(current)
        refreshed: dict[str, dict[str, Any]] = {}
        source_lookup: dict[Path, dict[str, str]] = {}
        for relative, path in sorted(current.items()):
            digest = _file_hash(path)
            cached = self._files.get(relative)
            if cached and cached.get("sha256") == digest:
                refreshed[relative] = cached
                continue
            changed = True
            is_source = relative.startswith("sources/")
            events = (
                parse_source_file(path, self.sources_dir)
                if is_source
                else parse_topic_file(
                    path,
                    self.topics_dir,
                    source_lookup=source_lookup,
                )
            )
            refreshed[relative] = {
                "sha256": digest,
                "events": [asdict(event) for event in events],
            }

        self._files = refreshed
        # Trust is resolved after the whole workspace is parsed, not inside
        # the per-file cache: a Topic's verdict depends on Source files it
        # does not contain, so a cached per-file answer would go stale the
        # moment a Source was promoted.
        self.events = resolve_topic_trust(prefer_v2_source_events([
            MemoryEvent(**row)
            for relative in sorted(self._files)
            for row in self._files[relative].get("events", [])
        ]))
        if changed and self.persist:
            self._write_cache()

    @staticmethod
    def _search_text(event: MemoryEvent) -> str:
        path = event.path.removeprefix("topics/").replace("/", " ").replace("_", " ")
        headings = " ".join(event.headings)
        return f"{event.content} {path} {headings} {' '.join(event.dates)}"

    @staticmethod
    def _rule_adjustment(event: MemoryEvent, query: str, tokens: list[str]) -> tuple[float, list[str]]:
        adjustment = 0.0
        reasons: list[str] = []
        content_tokens = set(tokenize(event.content))
        path_tokens = set(tokenize(event.path + " " + " ".join(event.headings)))
        significant = {token for token in tokens if len(token) >= 3}

        entity_hits = significant & content_tokens
        if entity_hits:
            bonus = min(0.18, 0.03 * len(entity_hits))
            adjustment += bonus
            reasons.append("exact_terms=" + ",".join(sorted(entity_hits)[:5]))

        path_hits = significant & path_tokens
        if path_hits:
            bonus = min(0.15, 0.05 * len(path_hits))
            adjustment += bonus
            reasons.append("path_heading=" + ",".join(sorted(path_hits)[:4]))

        query_dates = set(_DATE_RE.findall(query))
        if query_dates and event.date in query_dates:
            adjustment += 0.12
            reasons.append("exact_date")

        if event.refs:
            adjustment += 0.02
            reasons.append("source_grounded")
        return adjustment, reasons

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        path_prefix: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        speaker: str | None = None,
        rerank: bool = True,
        refresh_index: bool = True,
    ) -> list[dict[str, Any]]:
        if refresh_index:
            self.refresh()
        query_tokens = tokenize(query)
        if not query_tokens or not self.events:
            return []

        time_window = _query_time_window(date_from, date_to)
        speaker_keys: set[str] = set()
        if speaker:
            speaker_value = str(speaker).strip()
            speaker_keys = {
                speaker_value.casefold(),
                normalize_identity_header_part(speaker_value).casefold(),
            }
        candidates = []
        for event in self.events:
            if path_prefix:
                normalized = _normalize_path_prefix(path_prefix)
                if not event_matches_path_prefix(event.path, normalized):
                    continue
            if not _event_overlaps_window(event, time_window):
                continue
            if speaker_keys:
                identities = (
                    event.speaker_id,
                    event.speaker_display,
                    event.speaker_label,
                )
                identity_keys = {
                    value.casefold() for value in identities if value
                }
                if (
                    not event.path.startswith("sources/")
                    or speaker_keys.isdisjoint(identity_keys)
                ):
                    continue
            candidates.append(event)
        if not candidates:
            return []

        corpus = [tokenize(self._search_text(event)) for event in candidates]
        scores = BM25Plus(corpus).get_scores(query_tokens)
        max_positive = max((float(score) for score in scores if score > 0), default=0.0)
        results = []
        for event, raw_score in zip(candidates, scores):
            raw = float(raw_score)
            lexical = raw / max_positive if max_positive else 0.0
            adjustment, reasons = self._rule_adjustment(event, query, query_tokens) if rerank else (0.0, [])
            final = lexical + adjustment
            if lexical <= 0 and adjustment <= 0.02:
                continue
            results.append({
                **asdict(event),
                "bm25_score": round(raw, 6),
                "lexical_score": round(lexical, 6),
                "rule_adjustment": round(adjustment, 6),
                "final_score": round(final, 6),
                "rule_features": reasons,
            })

        results.sort(key=lambda row: (-row["final_score"], row["path"], row["line"], row["event_id"]))
        return results[: max(1, min(int(top_k), 50))]


def render_search_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No BM25 matches."
    blocks = []
    for rank, row in enumerate(results, start=1):
        features = ", ".join(row["rule_features"]) or "none"
        blocks.append(
            f"{rank}. {row['path']}:{row['line']} "
            f"[date={','.join(row['dates']) or 'unknown'}; score={row['final_score']:.4f}; rules={features}]\n"
            f"   {row['content']}\n"
            f"   refs: {', '.join(row['refs'])}"
        )
    return "\n".join(blocks)
