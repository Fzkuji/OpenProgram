"""Short-term store — daily append-only files."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .schema import ShortTermEntry, parse_short_term_file, render_short_term_entry, today_iso

_lock = threading.Lock()


def append(entry: ShortTermEntry) -> Path:
    """Append a single entry to today's short-term file. Thread-safe."""
    date = today_iso()
    path = store.short_term_for(date)
    with _lock:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not existing.strip():
            existing = f"# Short-term notes — {date}\n\n"
        elif not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + render_short_term_entry(entry), encoding="utf-8")
        try:
            from . import index as _idx
            _idx.add_short_term(date, entry)
        except Exception:
            pass
    return path


def append_text(
    text: str,
    *,
    type: str = "observation",
    tags: list[str] | None = None,
    session_id: str = "",
    confidence: float = 0.5,
) -> Path:
    """Convenience: build an entry from raw text and append."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return append(ShortTermEntry(
        timestamp=now,
        text=text,
        type=type,
        tags=tags or [],
        session_id=session_id,
        confidence=confidence,
    ))


def read_day(date_iso: str) -> list[ShortTermEntry]:
    """Return all entries for a given date, oldest first."""
    path = store.short_term_for(date_iso)
    if not path.exists():
        return []
    return parse_short_term_file(path.read_text(encoding="utf-8"))


def read_recent(days: int = 7) -> list[tuple[str, ShortTermEntry]]:
    """Return ``[(date_iso, entry), ...]`` for the last *days* of files.

    Sorted ascending by date+timestamp.
    """
    out: list[tuple[str, ShortTermEntry]] = []
    files = sorted(store.short_term_dir().glob("*.md"))
    files = files[-days:] if days else files
    for f in files:
        date = f.stem
        for e in parse_short_term_file(f.read_text(encoding="utf-8")):
            out.append((date, e))
    return out


def all_entries() -> list[tuple[str, ShortTermEntry]]:
    """Every short-term entry on disk, ascending."""
    return read_recent(days=0)
