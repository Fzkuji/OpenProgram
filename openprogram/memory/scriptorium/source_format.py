"""Versioned source-archive locations and strict v2 framing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


V2_FORMAT_MARKER = "<!-- openprogram-source-archive:v2 -->"

_ANCHOR_RE = re.compile(r'<a id="([^"]+)"></a>')
_SOURCE_RE = re.compile(r"<!-- source-id:([^>]+) -->")
_SPEAKER_RE = re.compile(r"<!-- speaker-id:([^>]*) -->")
_RECORD_LINES_RE = re.compile(r"<!-- record-lines:(\d{1,9}) -->")
_SOURCE_RECORD_RE = re.compile(r"^\[[^]]*\]\s+.+?: .*$")


@dataclass(frozen=True)
class V2Frame:
    source_id: str
    encoded_speaker_id: str | None
    record_index: int
    record_end: int


@dataclass(frozen=True)
class V2Scan:
    frames: tuple[V2Frame, ...]
    complete: bool

    @property
    def known_source_ids(self) -> set[str]:
        return {frame.source_id for frame in self.frames}


def provider_source_location(
    ref: str, *, v2: bool = False
) -> tuple[Path, str] | None:
    """Return a workspace-relative source path and deterministic anchor."""
    parts = ref.split("/", 2)
    if len(parts) != 3 or any(not part for part in parts):
        return None
    provider, thread_id, _message_id = parts
    if provider in {".", ".."}:
        return None
    provider_path = quote(provider, safe="-_.")
    thread_path = quote(thread_id, safe="-_.") + ".md"
    path = Path("sources") / provider_path
    if v2:
        path /= "_v2"
    path /= thread_path
    anchor = "source-" + hashlib.sha256(ref.encode()).hexdigest()[:16]
    return path, anchor


def valid_v2_source_id(ref: str) -> bool:
    """Whether ``ref`` can be emitted literally in a strict v2 comment."""
    return provider_source_location(ref, v2=True) is not None and all(
        character.isprintable() and character not in "[]<>\r\n"
        for character in ref
    )


def is_v2_source_path(relative: str | Path) -> bool:
    parts = Path(relative).parts
    if parts[:1] == ("sources",):
        parts = parts[1:]
    return len(parts) >= 3 and parts[-2] == "_v2"


def scan_v2_archive(text: str, relative: str | Path) -> V2Scan:
    """Parse the valid v2 prefix without resynchronizing after an error."""
    lines = text.split("\n")
    if len(lines) < 3 or lines[0] != V2_FORMAT_MARKER or lines[1] != "":
        return V2Scan((), False)

    relative_path = Path(relative)
    if relative_path.parts[:1] != ("sources",):
        relative_path = Path("sources") / relative_path

    frames: list[V2Frame] = []
    seen: set[str] = set()
    index = 2
    while index < len(lines):
        if index == len(lines) - 1 and lines[index] == "":
            return V2Scan(tuple(frames), True)
        if index + 2 >= len(lines):
            return V2Scan(tuple(frames), False)

        anchor = _ANCHOR_RE.fullmatch(lines[index])
        source = _SOURCE_RE.fullmatch(lines[index + 1])
        if anchor is None or source is None:
            return V2Scan(tuple(frames), False)
        source_id = source.group(1)
        location = provider_source_location(source_id, v2=True)
        if (
            location is None
            or not valid_v2_source_id(source_id)
            or location[0] != relative_path
            or location[1] != anchor.group(1)
            or source_id in seen
        ):
            return V2Scan(tuple(frames), False)

        cursor = index + 2
        encoded_speaker_id = None
        speaker = _SPEAKER_RE.fullmatch(lines[cursor])
        if speaker is not None:
            encoded_speaker_id = speaker.group(1)
            cursor += 1
            if cursor >= len(lines):
                return V2Scan(tuple(frames), False)

        count = _RECORD_LINES_RE.fullmatch(lines[cursor])
        if count is None:
            return V2Scan(tuple(frames), False)
        record_count = int(count.group(1))
        record_index = cursor + 1
        record_end = record_index + record_count
        if (
            record_count < 1
            or record_end >= len(lines)
            or _SOURCE_RECORD_RE.fullmatch(lines[record_index]) is None
            or lines[record_end] != ""
        ):
            return V2Scan(tuple(frames), False)
        frames.append(V2Frame(
            source_id=source_id,
            encoded_speaker_id=encoded_speaker_id,
            record_index=record_index,
            record_end=record_end,
        ))
        seen.add(source_id)
        index = record_end + 1

    return V2Scan(tuple(frames), True)
