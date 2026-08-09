"""Append-only source archive and stable source links."""

import hashlib
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.state import SourceRecord


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
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            known = set(re.findall(r"<!-- source-id:([^>]+) -->", text))
            additions = []
            for record in rows:
                refs.append(record.source_id)
                if record.source_id in known:
                    continue
                _target, anchor = self._provider_source_location(record.source_id)
                additions.extend([
                    f'<a id="{anchor}"></a>',
                    f"<!-- source-id:{record.source_id} -->",
                    f"[{record.timestamp or ''}] {record.role}: {record.content}",
                    "",
                ])
                known.add(record.source_id)
            if additions:
                body = text.rstrip()
                # Staged sources are read-only to the writer; archiving is the
                # Runtime's own append and restores the mode afterwards.
                if path.exists():
                    path.chmod(0o644)
                path.write_text(
                    (body + "\n\n" if body else f"# {rows[0].thread_id}\n\n")
                    + "\n".join(additions).rstrip()
                    + "\n",
                    encoding="utf-8",
                )
        if root is None:
            self._refresh_stage()
        else:
            self._protect_staged_sources()
        return refs
