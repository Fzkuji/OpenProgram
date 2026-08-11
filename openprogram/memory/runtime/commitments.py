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
    for field in ("text", "speaker_id", "source", "source_quote"):
        value = row.get(field)
        if not isinstance(value, str) or not value.strip() or "\n" in value:
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
    if not _valid_timestamp(status_changed_at):
        return False
    if status_source == "owner/manual":
        return status_quote is None
    return (
        isinstance(status_source, str)
        and bool(status_source.strip())
        and isinstance(status_quote, str)
        and bool(status_quote.strip())
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
    if not event.speaker_id:
        raise ValueError(f"commitment source has no speaker identity: {source_id}")
    return event


def _due(value: Any) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip()
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("commitment due must be YYYY-MM-DD or null") from exc
    return normalized


def _quote(event: Any, value: Any) -> str:
    quote = str(value or "").strip()
    if not quote or "\n" in quote or quote not in event.content:
        raise ValueError(
            "commitment source_quote must be one exact substring of Source"
        )
    return quote


def upsert_commitments(
    memory_dir: str | Path,
    items: list[dict[str, Any]],
    *,
    source_memory_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    root = Path(memory_dir)
    source_root = Path(source_memory_dir) if source_memory_dir is not None else root
    existing = load_commitments(root, strict=True)
    by_id = {str(row["id"]): row for row in existing}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each commitment must be an object")
        text = str(item.get("text") or "").strip()
        source_id = str(item.get("source") or "").strip()
        if not text or "\n" in text:
            raise ValueError("commitment text must be one non-empty line")
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
    root = Path(memory_dir)
    source_root = Path(source_memory_dir) if source_memory_dir is not None else root
    if manual_source not in {None, "owner/manual"}:
        raise ValueError("manual commitment source must be owner/manual")
    rows = load_commitments(root, strict=True)
    by_id = {str(row["id"]): row for row in rows}
    for transition in transitions:
        commitment_id = str(transition.get("id") or "").strip()
        status = str(transition.get("status") or "").strip()
        if status not in {"done", "dismissed"}:
            raise ValueError("commitment status must be done or dismissed")
        status_source: str
        status_quote: str | None
        if manual_source is not None:
            status_source = manual_source
            status_quote = None
        else:
            status_source = str(transition.get("source") or "").strip()
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
    "commitment_status",
    "load_commitments",
    "transition_commitments",
    "upsert_commitments",
]
