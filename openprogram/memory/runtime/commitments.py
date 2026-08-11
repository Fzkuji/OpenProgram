"""Commitments derived from trusted Source records."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


FILENAME = "commitments.jsonl"
STATUSES = frozenset({"open", "done", "dismissed"})
MAX_COMMITMENT_BATCH_ITEMS = 64
MAX_COMMITMENT_TEXT_CHARS = 2_048
MAX_COMMITMENT_QUOTE_CHARS = 8_192
MAX_COMMITMENT_SOURCE_CHARS = 512
MAX_COMMITMENT_SPEAKER_CHARS = 512
MAX_NOTIFICATION_STEPS = 64
PUBLIC_FIELDS = (
    "id",
    "text",
    "due",
    "speaker_id",
    "source",
    "status",
    "status_source",
    "status_changed_at",
    "notification_steps",
)
_ID_RE = re.compile(r"^com_[0-9a-f]{16}$")
_NOTIFICATION_RE = re.compile(r"^(?:due|overdue:[1-9][0-9]*)$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _valid_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    if not isinstance(row.get("id"), str) or not _ID_RE.fullmatch(row["id"]):
        return False
    field_limits = {
        "text": MAX_COMMITMENT_TEXT_CHARS,
        "speaker_id": MAX_COMMITMENT_SPEAKER_CHARS,
        "source": MAX_COMMITMENT_SOURCE_CHARS,
        "source_quote": MAX_COMMITMENT_QUOTE_CHARS,
    }
    for field, max_chars in field_limits.items():
        value = row.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\n" in value
            or len(value) > max_chars
        ):
            return False
    try:
        _due(row.get("due"))
    except ValueError:
        return False
    status = row.get("status")
    if status not in STATUSES:
        return False
    steps = row.get("notification_steps")
    if (
        not isinstance(steps, list)
        or len(steps) > MAX_NOTIFICATION_STEPS
        or len(steps) != len(set(steps))
        or any(
            not isinstance(step, str) or not _NOTIFICATION_RE.fullmatch(step)
            for step in steps
        )
    ):
        return False
    status_source = row.get("status_source")
    status_quote = row.get("status_source_quote")
    status_changed_at = row.get("status_changed_at")
    if status == "open":
        return (
            status_source is None and status_quote is None and status_changed_at is None
        )
    if (
        not isinstance(status_changed_at, str)
        or len(status_changed_at) > 64
        or not _valid_timestamp(status_changed_at)
    ):
        return False
    if status_source == "owner/manual":
        return status_quote is None
    return (
        isinstance(status_source, str)
        and bool(status_source.strip())
        and len(status_source) <= MAX_COMMITMENT_SOURCE_CHARS
        and isinstance(status_quote, str)
        and bool(status_quote.strip())
        and len(status_quote) <= MAX_COMMITMENT_QUOTE_CHARS
        and "\n" not in status_quote
    )


def _load_validated(memory_dir: str | Path) -> tuple[list[dict[str, Any]], int]:
    path = Path(memory_dir) / FILENAME
    if not path.is_file():
        return [], 0
    rows = []
    invalid = 0
    seen_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if not _valid_row(row) or row["id"] in seen_ids:
            invalid += 1
            continue
        rows.append(row)
        seen_ids.add(row["id"])
    return rows, invalid


def load_commitments(
    memory_dir: str | Path,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Load valid rows, isolating malformed records from reminder processing."""
    rows, invalid = _load_validated(memory_dir)
    if strict and invalid:
        raise ValueError(f"invalid commitment records: {invalid}")
    return rows


def commitment_status(memory_dir: str | Path) -> dict[str, Any]:
    """Owner/model-safe commitment counts and records."""
    rows, invalid = _load_validated(memory_dir)
    records = [{field: row.get(field) for field in PUBLIC_FIELDS} for row in rows]
    counts = {
        "total": len(rows),
        **{
            status: sum(row.get("status") == status for row in rows)
            for status in ("open", "done", "dismissed")
        },
    }
    if invalid:
        counts["invalid"] = invalid
    return {
        "counts": counts,
        "records": records,
    }


def _write(memory_dir: str | Path, rows: list[dict[str, Any]]) -> None:
    from openprogram.store.session.git_session import atomic_write_text

    path = Path(memory_dir) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
    )


def _write_valid_updates(memory_dir: str | Path, rows: list[dict[str, Any]]) -> None:
    """Update valid rows in place while retaining every malformed raw line."""
    from openprogram.store.session.git_session import atomic_write_text

    path = Path(memory_dir) / FILENAME
    updates = {str(row["id"]): row for row in rows}
    rendered: list[str] = []
    seen_ids: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines(keepends=True):
        ending = "\n" if raw.endswith("\n") else ""
        line = raw[:-1] if ending else raw
        try:
            stored = json.loads(line)
        except (TypeError, ValueError):
            rendered.append(raw)
            continue
        if not _valid_row(stored) or stored["id"] in seen_ids:
            rendered.append(raw)
            continue
        seen_ids.add(stored["id"])
        replacement = updates.get(stored["id"])
        rendered.append(json.dumps(replacement or stored, ensure_ascii=False) + ending)
    atomic_write_text(path, "".join(rendered))


def _source(memory_dir: Path, source_id: str):
    from ..retrieval.bm25 import parse_source_file
    from ..source_format import provider_source_location

    location = provider_source_location(source_id, v2=True)
    if location is None:
        raise ValueError(f"invalid commitment source: {source_id}")
    path = memory_dir / location[0]
    if not path.is_file():
        raise ValueError(f"commitment source not found: {source_id}")
    event = next(
        (
            row
            for row in parse_source_file(path, memory_dir / "sources")
            if row.event_id == source_id
        ),
        None,
    )
    if event is None or event.trust_state != "trusted":
        raise ValueError(f"commitment source must be trusted: {source_id}")
    if (
        not event.speaker_id
        or "\n" in event.speaker_id
        or len(event.speaker_id) > MAX_COMMITMENT_SPEAKER_CHARS
    ):
        raise ValueError(f"commitment source has invalid speaker identity: {source_id}")
    return event


def _due(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("commitment due must be YYYY-MM-DD or null")
    normalized = value.strip()
    if not _DATE_RE.fullmatch(normalized):
        raise ValueError("commitment due must be YYYY-MM-DD or null")
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("commitment due must be YYYY-MM-DD or null") from exc
    return normalized


def _limited_line(value: Any, *, field: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"commitment {field} must be a string")
    normalized = value.strip()
    if not normalized or "\n" in normalized:
        raise ValueError(f"commitment {field} must be one non-empty line")
    if len(normalized) > max_chars:
        raise ValueError(f"commitment {field} exceeds {max_chars} characters")
    return normalized


def _source_ref(value: Any) -> str:
    return _limited_line(
        value,
        field="source",
        max_chars=MAX_COMMITMENT_SOURCE_CHARS,
    )


def _quote(event: Any, value: Any) -> str:
    quote = _limited_line(
        value,
        field="source_quote",
        max_chars=MAX_COMMITMENT_QUOTE_CHARS,
    )
    if quote not in event.content:
        raise ValueError(
            "commitment source_quote must be one exact substring of Source"
        )
    return quote


def validate_writer_batch_size(
    commitments: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> None:
    if not isinstance(commitments, list) or not isinstance(transitions, list):
        raise ValueError("commitments and transitions must be arrays")
    total = len(commitments) + len(transitions)
    if total > MAX_COMMITMENT_BATCH_ITEMS:
        raise ValueError(
            f"at most {MAX_COMMITMENT_BATCH_ITEMS} commitments and transitions "
            "per writer batch"
        )


def upsert_commitments(
    memory_dir: str | Path,
    items: list[dict[str, Any]],
    *,
    source_memory_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError("commitments must be an array")
    if len(items) > MAX_COMMITMENT_BATCH_ITEMS:
        raise ValueError(
            f"at most {MAX_COMMITMENT_BATCH_ITEMS} commitments per writer batch"
        )
    root = Path(memory_dir)
    source_root = Path(source_memory_dir) if source_memory_dir is not None else root
    existing = load_commitments(root, strict=True)
    by_id = {str(row["id"]): row for row in existing}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each commitment must be an object")
        text = _limited_line(
            item.get("text"),
            field="text",
            max_chars=MAX_COMMITMENT_TEXT_CHARS,
        )
        source_id = _source_ref(item.get("source"))
        event = _source(source_root, source_id)
        source_quote = _quote(event, item.get("source_quote"))
        due = _due(item.get("due"))
        payload = json.dumps(
            [source_id, source_quote],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        commitment_id = (
            "com_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        )
        if commitment_id in by_id:
            continue
        row = {
            "id": commitment_id,
            "text": text,
            "due": due,
            "speaker_id": event.speaker_id,
            "source": source_id,
            "source_quote": source_quote,
            "status": "open",
            "status_source": None,
            "status_source_quote": None,
            "status_changed_at": None,
            "notification_steps": [],
        }
        existing.append(row)
        by_id[commitment_id] = row
    _write(root, existing)
    return existing


def transition_commitments(
    memory_dir: str | Path,
    transitions: list[dict[str, Any]],
    *,
    source_memory_dir: str | Path | None = None,
    allowed_source_refs: frozenset[str] | set[str] | None = None,
    manual_source: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(transitions, list):
        raise ValueError("commitment transitions must be an array")
    if len(transitions) > MAX_COMMITMENT_BATCH_ITEMS:
        raise ValueError(
            f"at most {MAX_COMMITMENT_BATCH_ITEMS} transitions per writer batch"
        )
    root = Path(memory_dir)
    source_root = Path(source_memory_dir) if source_memory_dir is not None else root
    if manual_source not in {None, "owner/manual"}:
        raise ValueError("manual commitment source must be owner/manual")
    rows = load_commitments(root, strict=True)
    by_id = {str(row["id"]): row for row in rows}
    for transition in transitions:
        if not isinstance(transition, dict):
            raise ValueError("each commitment transition must be an object")
        commitment_id = _limited_line(
            transition.get("id"),
            field="id",
            max_chars=20,
        )
        if not _ID_RE.fullmatch(commitment_id):
            raise ValueError("commitment id is invalid")
        status = _limited_line(
            transition.get("status"),
            field="status",
            max_chars=9,
        )
        if status not in {"done", "dismissed"}:
            raise ValueError("commitment status must be done or dismissed")
        status_source: str
        status_quote: str | None
        if manual_source is not None:
            status_source = manual_source
            status_quote = None
        else:
            status_source = _source_ref(transition.get("source"))
            if (
                allowed_source_refs is not None
                and status_source not in allowed_source_refs
            ):
                raise ValueError(
                    "commitment transition source is outside the selected writer batch"
                )
            event = _source(source_root, status_source)
            status_quote = _quote(event, transition.get("source_quote"))
        row = by_id.get(commitment_id)
        if row is None or row.get("status") != "open":
            raise ValueError(f"open commitment not found: {commitment_id}")
        row["status"] = status
        row["status_source"] = status_source
        row["status_source_quote"] = status_quote
        row["status_changed_at"] = datetime.now(timezone.utc).isoformat()
    _write(root, rows)
    return rows


__all__ = [
    "FILENAME",
    "MAX_COMMITMENT_BATCH_ITEMS",
    "MAX_COMMITMENT_QUOTE_CHARS",
    "MAX_COMMITMENT_SPEAKER_CHARS",
    "MAX_COMMITMENT_SOURCE_CHARS",
    "MAX_COMMITMENT_TEXT_CHARS",
    "MAX_NOTIFICATION_STEPS",
    "commitment_status",
    "load_commitments",
    "transition_commitments",
    "upsert_commitments",
    "validate_writer_batch_size",
]
