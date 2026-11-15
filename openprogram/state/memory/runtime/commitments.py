"""Read-only parser for the one-time Commitments-to-Scheduler migration."""

from __future__ import annotations

from datetime import date
import json
import re
from pathlib import Path
from typing import Any


FILENAME = "commitments.jsonl"
_ID_RE = re.compile(r"^com_[0-9a-f]{16}$")


def _valid_due(value: Any) -> bool:
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_row(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and bool(_ID_RE.fullmatch(value["id"]))
        and isinstance(value.get("text"), str)
        and bool(value["text"].strip())
        and value.get("status") in {"open", "done", "dismissed"}
        and _valid_due(value.get("due"))
    )


def load_commitments(
    memory_dir: str | Path,
    *,
    strict: bool = False,
) -> list[dict[str, Any]]:
    """Load valid legacy rows without exposing mutation APIs."""
    path = Path(memory_dir) / FILENAME
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
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
    if strict and invalid:
        raise ValueError(f"invalid legacy commitment records: {invalid}")
    return rows


__all__ = ["FILENAME", "load_commitments"]
