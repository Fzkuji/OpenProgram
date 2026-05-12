"""Short-term store — daily append-only files."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .schema import JournalEntry, parse_journal_file, render_journal_entry, today_iso

_lock = threading.Lock()


def append(entry: JournalEntry) -> Path:
    """Append a single entry to today's journal file. Thread-safe."""
    date = today_iso()
    path = store.journal_for(date)
    with _lock:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if not existing.strip():
            existing = f"# Short-term notes — {date}\n\n"
        elif not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + render_journal_entry(entry), encoding="utf-8")
        try:
            from . import index as _idx
            _idx.add_journal(date, entry)
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
    return append(JournalEntry(
        timestamp=now,
        text=text,
        type=type,
        tags=tags or [],
        session_id=session_id,
        confidence=confidence,
    ))


def read_day(date_iso: str) -> list[JournalEntry]:
    """Return all entries for a given date, oldest first."""
    path = store.journal_for(date_iso)
    if not path.exists():
        return []
    return parse_journal_file(path.read_text(encoding="utf-8"))


def read_recent(days: int = 7) -> list[tuple[str, JournalEntry]]:
    """Return ``[(date_iso, entry), ...]`` for the last *days* of files.

    Sorted ascending by date+timestamp.
    """
    out: list[tuple[str, JournalEntry]] = []
    files = sorted(store.journal_dir().glob("*.md"))
    files = files[-days:] if days else files
    for f in files:
        date = f.stem
        for e in parse_journal_file(f.read_text(encoding="utf-8")):
            out.append((date, e))
    return out


def all_entries() -> list[tuple[str, JournalEntry]]:
    """Every journal entry on disk, ascending."""
    return read_recent(days=0)
