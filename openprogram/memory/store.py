"""File-system layout for the memory subsystem.

All paths route through the active state directory so ``--profile`` and
``OPENPROGRAM_STATE_DIR`` overrides flow through automatically.

Layout::

    <state>/memory/
        short-term/YYYY-MM-DD.md
        wiki/
            index.md
            log.md
            reflections.md
            user/profile.md
            entities/<id>.md
            concepts/<id>.md
            procedures/<id>.md
        core.md
        index.sqlite
        .state/
            recall-counts.json
            last-sleep.json
            sleep.lock
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

WIKI_KINDS = ("user", "entities", "concepts", "procedures")


def root() -> Path:
    """Top-level memory directory. Created on first call."""
    from openprogram.paths import get_state_dir
    p = get_state_dir() / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def short_term_dir() -> Path:
    p = root() / "short-term"
    p.mkdir(parents=True, exist_ok=True)
    return p


def short_term_for(date_iso: str) -> Path:
    """Path of the daily short-term file for an ISO date (``YYYY-MM-DD``)."""
    return short_term_dir() / f"{date_iso}.md"


def wiki_dir() -> Path:
    p = root() / "wiki"
    p.mkdir(parents=True, exist_ok=True)
    for kind in WIKI_KINDS:
        (p / kind).mkdir(parents=True, exist_ok=True)
    return p


def wiki_index() -> Path:
    return wiki_dir() / "index.md"


def wiki_log() -> Path:
    return wiki_dir() / "log.md"


def wiki_reflections() -> Path:
    return wiki_dir() / "reflections.md"


def wiki_page(kind: str, slug: str) -> Path:
    """Path of a wiki page. Creates the kind dir on demand."""
    if kind not in WIKI_KINDS:
        raise ValueError(f"unknown wiki kind {kind!r}, expected one of {WIKI_KINDS}")
    d = wiki_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{slug}.md"


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


def iter_wiki_pages() -> Iterable[tuple[str, str, Path]]:
    """Yield (kind, slug, path) for every existing wiki page."""
    for kind in WIKI_KINDS:
        d = wiki_dir() / kind
        if not d.exists():
            continue
        for child in sorted(d.glob("*.md")):
            yield kind, child.stem, child


def iter_short_term() -> Iterable[Path]:
    """Yield short-term files in ascending date order."""
    d = short_term_dir()
    for child in sorted(d.glob("*.md")):
        yield child
