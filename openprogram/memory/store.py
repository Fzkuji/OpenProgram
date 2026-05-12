"""File-system layout for the memory subsystem.

All paths route through the active state directory so ``--profile`` and
``OPENPROGRAM_STATE_DIR`` overrides flow through automatically.

Wiki layout — Obsidian-style hierarchical vault (RAH pattern), with
nashsu-style `type:` frontmatter for semantic role enrichment:

    <state>/memory/
        journal/YYYY-MM-DD.md
        wiki/                          # Obsidian vault, git-tracked
            AGENTS.md                  # ingest-agent entrypoint (read-only)
            SCHEMA.md                  # protocol (read-only)
            purpose.md                 # scope rules (read-only)
            index.md                   # LLM-maintained catalog
            log.md                     # append-only timeline
            overview.md                # 2-4 paragraph TL;DR
            reflections.md             # sleep-REM appends here
            <Topic>/<Topic>.md         # folder form (has children)
            <Leaf>.md                  # bare leaf
            ...
        core.md
        index.sqlite                   # FTS over wiki + journal
        .state/
            recall-counts.json
            sleep-stage.json
            last-sleep.json
            sleep.lock
            review-queue.json          # ingest-flagged human-review items
            session-end.json           # processed-session bookkeeping
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


# Valid `type:` frontmatter values. Folder location is the primary
# taxonomy; type is secondary role-hint metadata.
WIKI_PAGE_TYPES = (
    "entity",
    "concept",
    "procedure",
    "user",
    "source",
    "query",
    "synthesis",
)

# Page-level filenames that are governance / bookkeeping and must
# never be treated as content pages.
GOVERNANCE_PAGES = (
    "AGENTS.md", "SCHEMA.md", "purpose.md",
    "index.md", "log.md", "overview.md", "reflections.md",
)


def root() -> Path:
    """Top-level memory directory. Created on first call."""
    from openprogram.paths import get_state_dir
    p = get_state_dir() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def journal_dir() -> Path:
    p = root() / "journal"
    # One-shot migration: legacy short-term/ → journal/. Renamed 2026-05
    # because "short-term" was misleading — these files accumulate
    # across years, they're a chronological journal not a transient
    # buffer.
    legacy = root() / "short-term"
    if legacy.exists() and not p.exists():
        try:
            legacy.rename(p)
        except OSError:
            pass
    p.mkdir(parents=True, exist_ok=True)
    return p


def journal_for(date_iso: str) -> Path:
    return journal_dir() / f"{date_iso}.md"


def wiki_dir() -> Path:
    p = root() / "wiki"
    p.mkdir(parents=True, exist_ok=True)
    return p


def wiki_index() -> Path:
    return wiki_dir() / "index.md"


def wiki_log() -> Path:
    return wiki_dir() / "log.md"


def wiki_overview() -> Path:
    return wiki_dir() / "overview.md"


def wiki_reflections() -> Path:
    return wiki_dir() / "reflections.md"


def core() -> Path:
    return root() / "core.md"


def index_db() -> Path:
    return root() / "index.sqlite"


def state_dir() -> Path:
    p = root() / ".state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def recall_counts_path() -> Path:
    return state_dir() / "recall-counts.json"


def last_sleep_path() -> Path:
    return state_dir() / "last-sleep.json"


def sleep_lock_path() -> Path:
    return state_dir() / "sleep.lock"


def review_queue_path() -> Path:
    return state_dir() / "review-queue.json"


def iter_wiki_pages() -> Iterable[Path]:
    """Yield every wiki content .md page (governance docs excluded)."""
    for p in sorted(wiki_dir().rglob("*.md")):
        if p.name in GOVERNANCE_PAGES:
            continue
        yield p


def iter_journal() -> Iterable[Path]:
    for child in sorted(journal_dir().glob("*.md")):
        yield child
