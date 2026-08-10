"""Validation and insertion of structured memory events."""

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from ..markdown import (
    BLOCK_ID_LENGTH,
    is_valid_temporal_value,
    parse_topic_tree,
    render_definition,
)
from .source_archive import SourceArchiveMixin


class EventWritingMixin:
    @staticmethod
    def _validate_event(event: dict[str, Any]) -> dict[str, Any]:
        when = str(event.get("when", "")).strip()
        content = str(event.get("content", "")).strip()
        refs = event.get("refs")
        topic_path = str(event.get("topic_path", "")).strip()
        headings = event.get("headings")
        if not when or not content or not isinstance(refs, list) or not refs:
            raise ValueError("each event requires when, content, and refs")
        if when != "undated" and not is_valid_temporal_value(when):
            raise ValueError(
                "when must use YYYY, YYYY-MM, YYYY-MM-DD, or undated"
            )
        expanded_refs = []
        for raw_ref in refs:
            ref = str(raw_ref).strip()
            single = re.fullmatch(r"D(\d+):(\d+)", ref)
            span = re.fullmatch(r"D(\d+):(\d+)-(?:D\1:)?(\d+)", ref)
            if single:
                expanded_refs.append(ref)
            elif span:
                conversation, first, last = map(int, span.groups())
                step = 1 if first <= last else -1
                expanded_refs.extend(
                    f"D{conversation}:{turn}"
                    for turn in range(first, last + step, step)
                )
            elif SourceArchiveMixin._provider_source_location(ref) is not None:
                expanded_refs.append(ref)
            else:
                raise ValueError(
                    "refs must be complete source references like D18:11"
                )
        relative = Path(topic_path)
        while relative.parts and relative.parts[0] == "topics":
            relative = Path(*relative.parts[1:])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".md"
        ):
            raise ValueError("topic_path must be a relative Markdown path")
        if not isinstance(headings, list) or not 1 <= len(headings) <= 6:
            raise ValueError("headings must contain one to six levels")
        clean_headings = [str(value).strip() for value in headings]
        if any(not value or "\n" in value for value in clean_headings):
            raise ValueError("headings must be non-empty single lines")
        refs = list(dict.fromkeys(expanded_refs))
        payload = json.dumps(
            [when, content, refs],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return {
            "event_id": "ev_" + digest,
            "memory_id": digest[:BLOCK_ID_LENGTH],
            "evidence_id": "e-" + digest[:10],
            "when": when,
            "content": content,
            "refs": refs,
            "topic_path": relative.as_posix(),
            "headings": clean_headings,
        }

    def _append_event(
        self, path: Path, event: dict[str, Any]
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        memory_id = event.get("memory_id") or event["event_id"].removeprefix(
            "ev_"
        )[:BLOCK_ID_LENGTH]
        evidence_id = event.get("evidence_id") or "e-" + event[
            "event_id"
        ].removeprefix("ev_")[:10]
        if re.search(rf"(?m)\s\^{re.escape(memory_id)}\s*$", text):
            return
        lines = text.rstrip().splitlines() if text.strip() else []
        wanted = event["headings"]
        wanted_keys = [
            unicodedata.normalize(
                "NFKC", " ".join(heading.split())
            ).casefold()
            for heading in wanted
        ]
        stack: list[str] = []
        best_depth = 0
        best_index = None
        best_level = 0
        for index, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if not match:
                continue
            level = len(match.group(1))
            stack = stack[: level - 1] + [match.group(2)]
            keys = [
                unicodedata.normalize(
                    "NFKC", " ".join(heading.split())
                ).casefold()
                for heading in stack
            ]
            if keys == wanted_keys[:len(keys)] and len(keys) > best_depth:
                best_depth = len(keys)
                best_index = index
                best_level = level
            if keys == wanted_keys:
                break
        insertion = len(lines)
        if best_index is not None:
            for later in range(best_index + 1, len(lines)):
                next_heading = re.match(r"^(#{1,6})\s+", lines[later])
                if (
                    next_heading
                    and len(next_heading.group(1)) <= best_level
                ):
                    insertion = later
                    break
        missing = wanted[best_depth:]
        if missing:
            headings = [
                f"{'#' * level} {heading}"
                for level, heading in enumerate(missing, best_depth + 1)
            ]
            if insertion and lines[insertion - 1].strip():
                headings.insert(0, "")
            lines[insertion:insertion] = headings
            insertion += len(headings)
        block = [
            "",
            f"{event['content']}[^{evidence_id}] ^{memory_id}",
            "",
            render_definition(
                evidence_id,
                None if event["when"] == "undated" else event["when"],
                (
                    self._source_link(
                        Path("topics") / event["topic_path"], ref
                    )
                    for ref in event["refs"]
                ),
            ),
        ]
        lines[insertion:insertion] = block
        path.write_text(
            "\n".join(lines).rstrip() + "\n", encoding="utf-8"
        )

    def save_memory(self, events: list[dict[str, Any]]) -> str:
        rows = []
        existing = parse_topic_tree(self.stage_dir / "topics")
        used = {unit.memory_id for unit in existing}
        identities = {
            (unit.when, unit.content, unit.source_refs): unit.memory_id
            for unit in existing
        }
        for event in events:
            row = self._validate_event(event)
            identity = (
                None if row["when"] == "undated" else row["when"],
                row["content"],
                tuple(row["refs"]),
            )
            if identity in identities:
                row["memory_id"] = identities[identity]
            elif row["memory_id"] in used:
                row["memory_id"] = self._stable_local_id(
                    f"event|{row['event_id']}", used
                )
                identities[identity] = row["memory_id"]
            else:
                used.add(row["memory_id"])
                identities[identity] = row["memory_id"]
            rows.append(row)
            self.pending[row["event_id"]] = row
            self._append_event(
                self.stage_dir / "topics" / row["topic_path"], row
            )
        self._synchronize()
        noun = "event" if len(rows) == 1 else "events"
        return f"saved {len(rows)} {noun}"
