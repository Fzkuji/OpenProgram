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
from ..workspace_layout import RUNTIME_DIR_MODE, resolve_within, runtime_dir


# Archived speech is the rawest thing memory holds — verbatim quotes from
# every conversation. A world-readable 0644 handed it to every other
# account on the machine, so the archive is owner-only like the rest of
# the profile.
SOURCE_FILE_MODE = 0o600
# Directory names inside sources/ are themselves identity: provider, then
# thread. Owner-only for the same reason the files are.
DIRECTORY_MODE = 0o700


def _record_lines(value: str) -> list[str]:
    """Split on literal LF so joining with LF reproduces ``value`` exactly."""
    return value.split("\n")


def _read_literal_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_literal_text(path: Path, value: str, *, temporary_dir: Path) -> None:
    # Staging happens inside the runtime directory, so this is one of the
    # places that can bring it into being; created bare it would inherit
    # the umask's 0755 and expose the trust audit and cursor beside it.
    temporary_dir.mkdir(parents=True, exist_ok=True)
    try:
        if not temporary_dir.is_symlink():
            temporary_dir.chmod(RUNTIME_DIR_MODE)
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(
        prefix="source-archive-", suffix=".tmp", dir=temporary_dir
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, SOURCE_FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _make_owner_only_dirs(directory: Path, root: Path) -> None:
    """Create a source subtree owner-only, not at whatever the umask says.

    ``mkdir(parents=True)`` gives each new level 0755, so the directory
    names alone — provider, then thread — would tell any other account on
    the machine which services the owner talks on and to whom. The mode
    is applied to each level this call creates, up to but not including
    the workspace root, which the workspace itself owns.
    """
    directory.mkdir(parents=True, exist_ok=True)
    current = directory
    while True:
        try:
            if current.is_symlink() or current.resolve() == Path(root).resolve():
                break
            if (current.stat().st_mode & 0o777) != DIRECTORY_MODE:
                current.chmod(DIRECTORY_MODE)
        except OSError:
            break
        if current.parent == current:
            break
        current = current.parent


def _source_path_key(relative: Path) -> str:
    return unicodedata.normalize("NFC", relative.as_posix()).casefold()


def _preflight_archive_paths(
    target_root: Path, relatives: list[Path]
) -> dict[str, Path]:
    root = target_root.resolve()
    sources_root = (root / "sources").resolve()
    planned: dict[str, str] = {}
    resolved: dict[str, Path] = {}

    for relative in relatives:
        path = resolve_within(root, relative.as_posix())
        if path is None or not path.is_relative_to(sources_root):
            raise ValueError("source archive path escapes sources")
        relative_text = relative.as_posix()
        resolved[relative_text] = path
        for length in range(1, len(relative.parts) + 1):
            prefix = Path(*relative.parts[:length])
            key = _source_path_key(prefix)
            prefix_text = prefix.as_posix()
            previous = planned.setdefault(key, prefix_text)
            if previous != prefix_text:
                raise ValueError(
                    "source archive path collision: "
                    f"{previous} and {prefix_text}"
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
                if actual.as_posix() != expected.as_posix():
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
        return f"[{ref if label is None else label}]({relative}#{anchor})"

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
        grouped: dict[str, list[SourceRecord]] = {}
        relatives: dict[str, Path] = {}
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
            relative = location[0]
            relative_key = relative.as_posix()
            relatives.setdefault(relative_key, relative)
            grouped.setdefault(relative_key, []).append(record)
        paths = _preflight_archive_paths(target_root, list(relatives.values()))
        existing: dict[str, tuple[str, set[str]]] = {}
        for relative_key, relative in relatives.items():
            path = paths[relative_key]
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
            existing[relative_key] = (text, known)

        refs = []
        for relative_key, rows in grouped.items():
            relative = relatives[relative_key]
            path = paths[relative_key]
            _make_owner_only_dirs(path.parent, target_root)
            text, known = existing[relative_key]
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
                    path.chmod(SOURCE_FILE_MODE)
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
