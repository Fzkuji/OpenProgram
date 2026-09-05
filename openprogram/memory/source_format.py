"""Versioned source-archive locations and strict v2 framing."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, unquote_to_bytes


V2_FORMAT_MARKER = "<!-- openprogram-source-archive:v2 -->"

_ANCHOR_RE = re.compile(r'<a id="([^"]+)"></a>')
_SOURCE_RE = re.compile(r"<!-- source-id:([^>]+) -->")
_SPEAKER_RE = re.compile(r"<!-- speaker-id:([^>]*) -->")
_SOURCE_META_RE = re.compile(r"<!-- source-meta:([A-Za-z0-9_-]+) -->")
_RECORD_LINES_RE = re.compile(r"<!-- record-lines:(\d{1,9}) -->")
_SOURCE_RECORD_RE = re.compile(r"^\[[^]]*\]\s+.+?: .*$")
_LEGACY_SCOPE_ORIGINS = {"legacy-unknown", "local-owner"}


@dataclass(frozen=True)
class V2Frame:
    source_id: str
    frame_start: int
    encoded_speaker_id: str | None
    metadata: dict[str, Any] | None
    metadata_index: int | None
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


def encode_speaker_id(value: str) -> str:
    """Canonical UTF-8 percent encoding for a v2 speaker marker."""
    return quote(value, safe="").replace("-", "%2D")


def is_canonical_speaker_id(value: str) -> bool:
    try:
        decoded = unquote_to_bytes(value).decode("utf-8")
    except UnicodeDecodeError:
        return False
    return encode_speaker_id(decoded) == value


def is_v2_source_path(relative: str | Path) -> bool:
    parts = Path(relative).parts
    if parts[:1] == ("sources",):
        parts = parts[1:]
    return len(parts) >= 3 and parts[-2] == "_v2"


def normalize_source_metadata(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    authority_tier = value.get("authority_tier")
    if authority_tier not in {None, "owner", "paired"}:
        return None
    trust_state = value.get("trust_state")
    if trust_state not in {"pending", "trusted"}:
        return None
    return {
        "version": 1,
        "trust_state": trust_state,
        "speaker_kind": str(value.get("speaker_kind") or "unknown"),
        "principal_id": str(value.get("principal_id") or "unknown"),
        "authority_tier": authority_tier,
    }


def _metadata_token(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _normalize_legacy_source_metadata(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    # The pre-tier authority batch wrote these fields under the same v1 marker.
    # Read that exact canonical shape without rewriting append-only archives.
    if not isinstance(value, Mapping) or set(value) != {
        "version", "trust_state", "speaker_kind", "principal_id",
        "origin_scope",
    } or value.get("version") != 1:
        return None
    scope = value.get("origin_scope")
    if not isinstance(scope, Mapping) or set(scope) != {
        "origin", "capabilities",
    }:
        return None
    origin = scope.get("origin")
    capabilities = scope.get("capabilities")
    if origin not in _LEGACY_SCOPE_ORIGINS or not isinstance(capabilities, list):
        return None
    if not all(
        isinstance(capability, str) and capability for capability in capabilities
    ) or capabilities != sorted(set(capabilities)):
        return None
    return normalize_source_metadata({
        "trust_state": value.get("trust_state"),
        "speaker_kind": value.get("speaker_kind"),
        "principal_id": value.get("principal_id"),
        "authority_tier": "owner" if origin == "local-owner" else None,
    })


def encode_source_metadata(value: Mapping[str, Any]) -> str:
    normalized = normalize_source_metadata(value)
    if normalized is None:
        raise ValueError("invalid source trust metadata")
    return f"<!-- source-meta:{_metadata_token(normalized)} -->"


def decode_source_metadata(token: str) -> dict[str, Any] | None:
    try:
        padding = "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(token + padding))
    except (ValueError, TypeError):
        return None
    normalized = normalize_source_metadata(payload)
    if normalized is not None and _metadata_token(normalized) == token:
        return normalized
    legacy = _normalize_legacy_source_metadata(payload)
    return legacy if legacy is not None and _metadata_token(payload) == token else None


def trusted_source_ids(memory_dir: Path | str) -> set[str]:
    """Every archived Source ID a claim may rest on, by workspace scan.

    Two archives answer the same way. A v2 file carries the trust verdict
    in each frame's metadata, and a frame written before that header
    existed is legacy evidence the owner already accepted, so it counts as
    trusted — the same reading the retrieval index applies. A pre-v2 file
    has no per-record framing at all, so every anchor in it is legacy and
    trusted for the same reason.

    Only IDs that are actually present come back. A caller asking whether
    a reference is trusted gets "no" for one that resolves to nothing,
    which is what keeps a dangling citation from reading as vouched-for.
    """
    root = Path(memory_dir) / "sources"
    if not root.is_dir():
        return set()
    trusted: set[str] = set()
    for path in sorted(root.rglob("*.md")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(Path(memory_dir)).as_posix()
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except OSError:
            continue
        if is_v2_source_path(relative):
            for frame in scan_source_archive(text, relative).frames:
                if (frame.metadata or {"trust_state": "trusted"}).get(
                    "trust_state"
                ) == "trusted":
                    trusted.add(frame.source_id)
            continue
        trusted.update(_SOURCE_RE.findall(text))
        trusted.update(
            f"D{conversation}:{turn}"
            for conversation, turn in re.findall(
                r'<a id="d(\d+)-(\d+)"></a>', text
            )
        )
    return trusted


def scan_source_archive(text: str, relative: str | Path) -> V2Scan:
    """Parse the valid v2 prefix without resynchronizing after an error."""
    # Archives written by OpenProgram use literal LF, but files copied from
    # or created by ordinary Windows text APIs can contain CRLF. Framing is
    # line-based, so accept the platform spelling without treating the CR as
    # part of every marker or record line.
    text = text.replace("\r\n", "\n")
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
            if not is_canonical_speaker_id(encoded_speaker_id):
                return V2Scan(tuple(frames), False)
            cursor += 1
            if cursor >= len(lines):
                return V2Scan(tuple(frames), False)

        metadata = None
        metadata_index = None
        metadata_match = _SOURCE_META_RE.fullmatch(lines[cursor])
        if metadata_match is not None:
            metadata_index = cursor
            metadata = decode_source_metadata(metadata_match.group(1))
            if metadata is None:
                return V2Scan(tuple(frames), False)
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
            frame_start=index,
            encoded_speaker_id=encoded_speaker_id,
            metadata=metadata,
            metadata_index=metadata_index,
            record_index=record_index,
            record_end=record_end,
        ))
        seen.add(source_id)
        index = record_end + 1

    return V2Scan(tuple(frames), True)
