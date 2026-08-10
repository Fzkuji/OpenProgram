"""Append-only source archive and stable source links."""

import hashlib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from openprogram._text import normalize_identity_header_part

from ..runtime.state import SourceRecord


_ANCHOR_LINE_RE = re.compile(r'\s*<a id="([^"]+)"></a>\s*')
_SOURCE_ID_LINE_RE = re.compile(r"\s*<!--\s*source-id:([^>]+?)\s*-->\s*")
_SPEAKER_ID_LINE_RE = re.compile(r"\s*<!--\s*speaker-id:[^>]*?\s*-->\s*")
_RECORD_LINES_LINE_RE = re.compile(
    r"\s*<!--\s*record-lines:(\d{1,9})\s*-->\s*"
)
_SOURCE_RECORD_RE = re.compile(r"^\[[^]]*\]\s+.+?: .*$")


def _encode_speaker_id(value: str) -> str:
    """Percent-encode an external ID so it cannot alter an HTML comment."""
    return quote(value, safe="").replace("-", "%2D")


def _record_lines(value: str) -> list[str]:
    """Split on literal LF so joining with LF reproduces ``value`` exactly."""
    return value.split("\n")


def _read_literal_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_literal_text(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(value)


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
        parts = ref.split("/", 2)
        if len(parts) != 3 or any(not part for part in parts):
            return None
        provider, thread_id, _message_id = parts
        path = Path("sources") / quote(provider, safe="-_.") / (
            quote(thread_id, safe="-_.") + ".md"
        )
        anchor = "source-" + hashlib.sha256(ref.encode()).hexdigest()[:16]
        return path, anchor

    @staticmethod
    def _source_link(
        topic_path: Path, ref: str, label: str | None = None
    ) -> str:
        legacy = re.fullmatch(r"D(\d+):(\d+)", ref)
        if legacy:
            conversation, turn = legacy.groups()
            target = Path("sources") / f"D{conversation}.md"
            anchor = f"d{conversation}-{turn}"
        else:
            location = SourceArchiveMixin._provider_source_location(ref)
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
            location = self._provider_source_location(record.source_id)
            if location is None:
                raise ValueError(
                    "provider source IDs require provider/thread/message"
                )
            grouped.setdefault(location[0], []).append(record)
        refs = []
        for relative, rows in grouped.items():
            path = target_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            text = _read_literal_text(path) if path.exists() else ""
            known = self._known_source_ids(text)
            additions = []
            for record in rows:
                refs.append(record.source_id)
                if record.source_id in known:
                    continue
                _target, anchor = self._provider_source_location(record.source_id)
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
                        f"{_encode_speaker_id(str(record.speaker_id or ''))} -->"
                    )
                record_text = (
                    f"[{record.timestamp or ''}] "
                    f"{record.speaker_label}: {record.content}"
                )
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
                    separator = (
                        "" if text.endswith("\n\n")
                        else "\n" if text.endswith("\n")
                        else "\n\n"
                    )
                    prefix = text + separator
                else:
                    prefix = f"# {rows[0].thread_id}\n\n"
                _write_literal_text(
                    path,
                    prefix + "\n".join(additions),
                )
        if root is None:
            self._refresh_stage()
        else:
            self._protect_staged_sources()
        return refs

    @staticmethod
    def _known_source_ids(text: str) -> set[str]:
        """Read runtime headers and skip declared multi-line record bodies."""
        lines = text.split("\n")
        known: set[str] = set()
        index = 0
        while index + 1 < len(lines):
            anchor = _ANCHOR_LINE_RE.fullmatch(lines[index])
            source = _SOURCE_ID_LINE_RE.fullmatch(lines[index + 1])
            if source is None or anchor is None:
                index += 1
                continue
            source_id = source.group(1).strip()
            location = SourceArchiveMixin._provider_source_location(source_id)
            if location is None or anchor.group(1) != location[1]:
                index += 1
                continue
            cursor = index + 2
            if (
                cursor < len(lines)
                and _SPEAKER_ID_LINE_RE.fullmatch(lines[cursor]) is not None
            ):
                cursor += 1
            count = (
                _RECORD_LINES_LINE_RE.fullmatch(lines[cursor])
                if cursor < len(lines)
                else None
            )
            if count is not None:
                record_count = int(count.group(1))
                record_index = cursor + 1
                record_end = record_index + record_count
                if (
                    record_count < 1
                    or record_end > len(lines)
                    or _SOURCE_RECORD_RE.match(lines[record_index]) is None
                ):
                    index += 1
                    continue
                known.add(source_id)
                index = record_end
                continue
            if cursor < len(lines) and _SOURCE_RECORD_RE.match(lines[cursor]):
                # Historical archives have no body boundary. Compatibility is
                # intentionally limited to their first physical record line.
                known.add(source_id)
                index = cursor + 1
                continue
            index += 1
        return known
