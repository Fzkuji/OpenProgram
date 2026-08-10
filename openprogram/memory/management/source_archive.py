"""Append-only source archive and stable source links."""

import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from openprogram._text import normalize_identity_header_part

from ..runtime.state import SourceRecord
from ..source_format import (
    V2_FORMAT_MARKER,
    encode_source_metadata,
    encode_speaker_id,
    provider_source_location,
    scan_source_archive,
    valid_v2_source_id,
)
from ..workspace_layout import resolve_within, runtime_dir


def _record_lines(value: str) -> list[str]:
    """Split on literal LF so joining with LF reproduces ``value`` exactly."""
    return value.split("\n")


def _read_literal_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_literal_text(path: Path, value: str, *, temporary_dir: Path) -> None:
    temporary_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="source-archive-", suffix=".tmp", dir=temporary_dir
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _source_path_key(relative: Path) -> str:
    return unicodedata.normalize("NFC", relative.as_posix()).casefold()


def _preflight_archive_paths(
    target_root: Path, relatives: list[Path]
) -> dict[Path, Path]:
    root = target_root.resolve()
    sources_root = (root / "sources").resolve()
    planned: dict[str, Path] = {}
    resolved: dict[Path, Path] = {}

    for relative in relatives:
        path = resolve_within(root, relative.as_posix())
        if path is None or not path.is_relative_to(sources_root):
            raise ValueError("source archive path escapes sources")
        resolved[relative] = path
        for length in range(1, len(relative.parts) + 1):
            prefix = Path(*relative.parts[:length])
            key = _source_path_key(prefix)
            previous = planned.setdefault(key, prefix)
            if previous != prefix:
                raise ValueError(
                    "source archive path collision: "
                    f"{previous.as_posix()} and {prefix.as_posix()}"
                )

    for relative in relatives:
        current = root
        prefix = Path()
        for part in relative.parts:
            if not current.is_dir():
                break
            expected = prefix / part
            exact = None
            key = _source_path_key(Path(part))
            for existing in current.iterdir():
                if _source_path_key(Path(existing.name)) != key:
                    continue
                actual = existing.relative_to(root)
                if actual != expected:
                    raise ValueError(
                        "source archive path collision: "
                        f"{expected.as_posix()} and {actual.as_posix()}"
                    )
                exact = existing
            if exact is None:
                break
            current = exact
            prefix = expected

    return resolved


class SourceArchiveMixin:
    def archive_sessions(self, sessions: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[tuple[str, str, str]]] = {}
        provider_records: list[SourceRecord] = []
        for session in sessions:
            date = str(session.get("observation_date", ""))
            for ordinal, ((speaker, content), raw_ref) in enumerate(
                zip(session.get("turns", []), session.get("refs", [])),
                start=1,
            ):
                ref = str(raw_ref)
                match = re.fullmatch(r"D(\d+):(\d+)", ref)
                if match:
                    grouped.setdefault(f"D{match.group(1)}", []).append(
                        (ref, date, f"{speaker}: {content}")
                    )
                    continue
                if self._provider_source_location(ref) is None:
                    raise ValueError(
                        "refs must be D18:11 or provider/thread/message IDs"
                    )
                provider, thread_id, message_id = ref.split("/", 2)
                provider_records.append(SourceRecord(
                    provider=provider,
                    thread_id=thread_id,
                    message_id=message_id,
                    ordinal=ordinal,
                    role=str(speaker),
                    content=str(content),
                    timestamp=date or None,
                ))
        source_dir = self.memory_dir / "sources"
        source_dir.mkdir(exist_ok=True)
        for conversation, rows in grouped.items():
            path = source_dir / f"{conversation}.md"
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            known = set(re.findall(r'<a id="(d\d+-\d+)"></a>', existing))
            additions = []
            for ref, date, content in rows:
                anchor = ref.lower().replace(":", "-")
                if anchor not in known:
                    additions.append(
                        f'<a id="{anchor}"></a>\n[{date}] {content} [{ref}]'
                    )
                    known.add(anchor)
            if additions:
                body = existing.rstrip()
                path.write_text(
                    (body + "\n\n" if body else f"# {conversation}\n\n")
                    + "\n\n".join(additions)
                    + "\n",
                    encoding="utf-8",
                )
        if provider_records:
            self.archive_source_records(provider_records)
        self._refresh_stage()

    @staticmethod
    def _provider_source_location(ref: str) -> tuple[Path, str] | None:
        return provider_source_location(ref)

    @staticmethod
    def _provider_v2_source_location(ref: str) -> tuple[Path, str] | None:
        return provider_source_location(ref, v2=True)

    def resolve_v2_source(self, ref: str):
        """The staged v2 archive frame a Topic reference names, or None.

        One resolver for every caller that has to know what a reference
        points at. Location alone answers "is there an anchor"; the frame
        also carries the trust metadata, and a reference cannot be checked
        for trust by a caller that only got a path back.
        """
        location = self._provider_v2_source_location(ref)
        if location is None:
            return None, None
        relative, _anchor = location
        path = self.stage_dir / relative
        if not path.is_file():
            return None, None
        scan = scan_source_archive(_read_literal_text(path), relative)
        frame = next(
            (item for item in scan.frames if item.source_id == ref), None
        )
        return (location, frame) if frame is not None else (None, None)

    def _valid_v2_source_location(
        self, ref: str
    ) -> tuple[Path, str] | None:
        return self.resolve_v2_source(ref)[0]

    def _source_link(
        self, topic_path: Path, ref: str, label: str | None = None
    ) -> str:
        legacy = re.fullmatch(r"D(\d+):(\d+)", ref)
        if legacy:
            conversation, turn = legacy.groups()
            target = Path("sources") / f"D{conversation}.md"
            anchor = f"d{conversation}-{turn}"
        else:
            location = self._valid_v2_source_location(ref)
            if location is None:
                location = self._provider_source_location(ref)
            if location is None:
                raise ValueError(f"invalid source reference: {ref}")
            target, anchor = location
        relative = os.path.relpath(target, topic_path.parent).replace(
            os.sep, "/"
        )
        return f"[{label or ref}]({relative}#{anchor})"

    def archive_source_records(
        self,
        records: list[SourceRecord],
        *,
        root: Path | None = None,
    ) -> list[str]:
        """Append records to the source tree.

        Writes into ``memory_dir`` and refreshes the stage by default. Pass
        ``root=self.stage_dir`` to archive inside an in-progress transaction
        so sources are installed atomically with the topics that cite them.
        """
        target_root = self.memory_dir if root is None else Path(root)
        grouped: dict[Path, list[SourceRecord]] = {}
        for record in sorted(
            records,
            key=lambda value: (
                value.provider,
                value.thread_id,
                value.ordinal,
            ),
        ):
            location = self._provider_v2_source_location(record.source_id)
            if location is None:
                raise ValueError(
                    "provider source IDs require provider/thread/message"
                )
            if not valid_v2_source_id(record.source_id):
                raise ValueError("source ID is not safe for a v2 header")
            timestamp = str(record.timestamp or "")
            if any(
                not character.isprintable() or character in "[]\r\n"
                for character in timestamp
            ):
                raise ValueError("source timestamp is not safe for a header")
            if not normalize_identity_header_part(str(record.speaker_label)):
                raise ValueError("source record role/speaker label is empty")
            grouped.setdefault(location[0], []).append(record)
        paths = _preflight_archive_paths(target_root, list(grouped))
        existing: dict[Path, tuple[str, set[str]]] = {}
        for relative in grouped:
            path = paths[relative]
            text = _read_literal_text(path) if path.exists() else ""
            if text:
                scan = scan_source_archive(text, relative)
                if not scan.complete:
                    raise ValueError(
                        f"invalid or truncated v2 archive: {relative}"
                    )
                known = scan.known_source_ids
            else:
                known = set()
            existing[relative] = (text, known)

        refs = []
        for relative, rows in grouped.items():
            path = paths[relative]
            path.parent.mkdir(parents=True, exist_ok=True)
            text, known = existing[relative]
            additions = []
            for record in rows:
                refs.append(record.source_id)
                if record.source_id in known:
                    continue
                _target, anchor = self._provider_v2_source_location(
                    record.source_id
                )
                header = [
                    f'<a id="{anchor}"></a>',
                    f"<!-- source-id:{record.source_id} -->",
                ]
                normalized_speaker_id = normalize_identity_header_part(
                    str(record.speaker_id or "")
                )
                normalized_speaker_display = normalize_identity_header_part(
                    str(record.speaker_display or "")
                )
                if normalized_speaker_id or normalized_speaker_display:
                    header.append(
                        "<!-- speaker-id:"
                        f"{encode_speaker_id(str(record.speaker_id or ''))} -->"
                    )
                header.append(encode_source_metadata({
                    "trust_state": record.trust_state,
                    "speaker_kind": record.speaker_kind,
                    "principal_id": record.principal_id,
                    "authority_tier": record.authority_tier,
                }))
                timestamp = str(record.timestamp or "")
                speaker_label = normalize_identity_header_part(
                    str(record.speaker_label)
                )
                if not speaker_label:
                    raise ValueError("source record role/speaker label is empty")
                record_text = f"[{timestamp}] {speaker_label}: {record.content}"
                record_lines = _record_lines(record_text)
                additions.extend([
                    *header,
                    f"<!-- record-lines:{len(record_lines)} -->",
                    *record_lines,
                    "",
                ])
                known.add(record.source_id)
            if additions:
                # Staged sources are read-only to the writer; archiving is the
                # Runtime's own append and restores the mode afterwards.
                if path.exists():
                    path.chmod(0o644)
                if text:
                    separator = "\n" if text.endswith("\n") else "\n\n"
                    prefix = text + separator
                else:
                    prefix = V2_FORMAT_MARKER + "\n\n"
                _write_literal_text(
                    path,
                    prefix + "\n".join(additions),
                    temporary_dir=runtime_dir(target_root),
                )
        if root is None:
            self._refresh_stage()
        else:
            self._protect_staged_sources()
        return refs
