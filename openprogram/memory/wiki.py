"""Wiki-layer read / write / log.

Wiki pages are managed by the sleep process. The agent reads via
``wiki_get`` / ``memory_recall`` but does not write directly. ``apply()``
exists for explicit edits (via ``wiki_apply`` tool or CLI), and every
write goes through ``log()`` to keep an append-only audit trail.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import store
from .schema import (
    WikiPage,
    parse_wiki_page,
    render_wiki_page,
    slugify,
    today_iso,
)

_lock = threading.Lock()


# ── Read ─────────────────────────────────────────────────────────────────────


def get(kind: str, slug: str) -> WikiPage | None:
    """Load a wiki page; returns None if missing."""
    path = store.wiki_page(kind, slug)
    if not path.exists():
        return None
    return parse_wiki_page(path.read_text(encoding="utf-8"), kind=kind, slug=slug)


def find(slug_or_alias: str) -> WikiPage | None:
    """Resolve by slug or alias across all kinds. First match wins."""
    target = slug_or_alias.strip().lower()
    for kind, slug, path in store.iter_wiki_pages():
        if slug.lower() == target:
            return parse_wiki_page(path.read_text(encoding="utf-8"), kind=kind, slug=slug)
    for kind, slug, path in store.iter_wiki_pages():
        page = parse_wiki_page(path.read_text(encoding="utf-8"), kind=kind, slug=slug)
        if any(a.lower() == target for a in page.aliases):
            return page
    return None


def all_pages() -> Iterator[WikiPage]:
    """Iterate every page on disk."""
    for kind, slug, path in store.iter_wiki_pages():
        yield parse_wiki_page(path.read_text(encoding="utf-8"), kind=kind, slug=slug)


# ── Write ────────────────────────────────────────────────────────────────────


def write(page: WikiPage, *, source: str = "sleep", reason: str = "") -> Path:
    """Persist a wiki page and append a log entry. Thread-safe."""
    path = store.wiki_page(page.type, page.id)
    with _lock:
        path.write_text(render_wiki_page(page), encoding="utf-8")
        log(action="write", page=f"{page.type}/{page.id}", source=source, reason=reason)
        try:
            from . import index as _idx
            _idx.add_wiki_page(page)
        except Exception:
            pass
    return path


def remove(kind: str, slug: str, *, source: str = "sleep", reason: str = "") -> bool:
    """Delete a page from disk and append a log entry. Returns True if removed."""
    path = store.wiki_page(kind, slug)
    with _lock:
        if not path.exists():
            return False
        path.unlink()
        log(action="remove", page=f"{kind}/{slug}", source=source, reason=reason)
        try:
            from . import index as _idx
            _idx.remove_wiki_page(kind, slug)
        except Exception:
            pass
    return True


# ── Log ──────────────────────────────────────────────────────────────────────


def log(*, action: str, page: str, source: str = "", reason: str = "") -> None:
    """Append one structured line to ``wiki/log.md``.

    Format: ``- 2026-05-09T12:34:56Z action:write page:entities/openprogram source:sleep reason:""``
    Designed to grep cleanly: ``grep "page:entities/" log.md``.
    """
    log_path = store.wiki_log()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    safe_reason = reason.replace("\n", " ").replace("\"", "'")
    line = f'- {ts} action:{action} page:{page} source:{source} reason:"{safe_reason}"\n'
    if not log_path.exists():
        log_path.write_text(f"# Wiki log\n\n", encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


# ── Index page ───────────────────────────────────────────────────────────────


def regenerate_index() -> Path:
    """Re-render ``wiki/index.md`` from on-disk pages.

    Format: a navigable table grouped by kind, with title + last_updated.
    """
    pages_by_kind: dict[str, list[WikiPage]] = {}
    for p in all_pages():
        pages_by_kind.setdefault(p.type, []).append(p)
    out = ["# Wiki index", "", f"Last regenerated: {today_iso()}", ""]
    for kind in store.WIKI_KINDS:
        items = pages_by_kind.get(kind) or []
        if not items:
            continue
        out.append(f"## {kind}")
        out.append("")
        for p in sorted(items, key=lambda x: x.id):
            updated = p.last_updated.split("T")[0] if p.last_updated else "?"
            out.append(f"- [{p.title}]({kind}/{p.id}.md) — `{p.id}` (updated {updated})")
        out.append("")
    path = store.wiki_index()
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ── Helpers ──────────────────────────────────────────────────────────────────


__all__ = [
    "get", "find", "all_pages",
    "write", "remove",
    "log", "regenerate_index",
    "slugify",
]
