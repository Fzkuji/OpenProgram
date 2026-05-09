"""SQLite FTS5 index over wiki + short-term.

We rebuild the index incrementally on every write and on demand from
the sleep process. Two virtual tables (``wiki_fts``, ``short_fts``)
keep wiki pages and short-term entries indexed separately so callers
can scope searches.

Schema is intentionally minimal — BM25 ranking is good enough for the
sizes we expect (a few thousand entries). No embeddings, no vectors.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import store, wiki, short_term

_lock = threading.RLock()


# ── Connection ────────────────────────────────────────────────────────────────


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Open a connection with row factory and FK on. Thread-safe via lock."""
    path = store.index_db()
    with _lock:
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init() -> None:
    """Create FTS5 tables if missing. Idempotent."""
    with _conn() as c:
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5("
            "kind UNINDEXED, slug UNINDEXED, title, body, claims, aliases, "
            "tokenize='porter unicode61'"
            ")"
        )
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS short_fts USING fts5("
            "date UNINDEXED, ts UNINDEXED, kind UNINDEXED, tags, text, session UNINDEXED, "
            "tokenize='porter unicode61'"
            ")"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS index_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


# ── Indexing ─────────────────────────────────────────────────────────────────


def reindex_all() -> tuple[int, int]:
    """Rebuild both tables from on-disk content. Returns (wiki_n, short_n)."""
    init()
    with _conn() as c:
        c.execute("DELETE FROM wiki_fts")
        c.execute("DELETE FROM short_fts")
        wn = 0
        for page in wiki.all_pages():
            c.execute(
                "INSERT INTO wiki_fts (kind, slug, title, body, claims, aliases) VALUES (?,?,?,?,?,?)",
                (
                    page.type, page.id, page.title, page.body,
                    "\n".join(cl.text for cl in page.claims),
                    " ".join(page.aliases),
                ),
            )
            wn += 1
        sn = 0
        for date_iso, entry in short_term.all_entries():
            c.execute(
                "INSERT INTO short_fts (date, ts, kind, tags, text, session) VALUES (?,?,?,?,?,?)",
                (
                    date_iso, entry.timestamp, entry.type,
                    " ".join(entry.tags), entry.text, entry.session_id,
                ),
            )
            sn += 1
        c.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES ('last_reindex', datetime('now'))"
        )
    return wn, sn


def add_short_term(date_iso: str, entry) -> None:
    """Index a freshly-appended short-term entry without a full rebuild."""
    init()
    with _conn() as c:
        c.execute(
            "INSERT INTO short_fts (date, ts, kind, tags, text, session) VALUES (?,?,?,?,?,?)",
            (
                date_iso, entry.timestamp, entry.type,
                " ".join(entry.tags), entry.text, entry.session_id,
            ),
        )


def add_wiki_page(page) -> None:
    """Index (or re-index) a single wiki page."""
    init()
    with _conn() as c:
        c.execute(
            "DELETE FROM wiki_fts WHERE kind = ? AND slug = ?",
            (page.type, page.id),
        )
        c.execute(
            "INSERT INTO wiki_fts (kind, slug, title, body, claims, aliases) VALUES (?,?,?,?,?,?)",
            (
                page.type, page.id, page.title, page.body,
                "\n".join(c.text for c in page.claims),
                " ".join(page.aliases),
            ),
        )


def remove_wiki_page(kind: str, slug: str) -> None:
    init()
    with _conn() as c:
        c.execute("DELETE FROM wiki_fts WHERE kind = ? AND slug = ?", (kind, slug))


# ── Search ───────────────────────────────────────────────────────────────────


@dataclass
class WikiHit:
    kind: str
    slug: str
    title: str
    snippet: str
    score: float


@dataclass
class ShortHit:
    date: str
    ts: str
    kind: str
    tags: str
    text: str
    score: float


def _sanitize(query: str) -> str:
    """Make user input safe for FTS5 MATCH.

    FTS5 has its own query language; raw user input often blows it up
    on punctuation. We strip non-word chars and OR the surviving terms.
    """
    import re
    terms = [t for t in re.split(r"[^\w一-鿿]+", query) if t]
    if not terms:
        return ""
    return " OR ".join(terms)


def search_wiki(query: str, limit: int = 5) -> list[WikiHit]:
    init()
    q = _sanitize(query)
    if not q:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT kind, slug, title, snippet(wiki_fts, 3, '«', '»', '…', 16) AS snip, "
            "bm25(wiki_fts) AS score FROM wiki_fts WHERE wiki_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (q, limit),
        ).fetchall()
    return [WikiHit(r["kind"], r["slug"], r["title"], r["snip"], -r["score"]) for r in rows]


def search_short(query: str, limit: int = 10, days: int | None = 30) -> list[ShortHit]:
    init()
    q = _sanitize(query)
    if not q:
        return []
    sql = (
        "SELECT date, ts, kind, tags, text, bm25(short_fts) AS score "
        "FROM short_fts WHERE short_fts MATCH ?"
    )
    params: list = [q]
    if days:
        sql += " AND date >= date('now', ?)"
        params.append(f"-{days} days")
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [
        ShortHit(r["date"], r["ts"], r["kind"], r["tags"], r["text"], -r["score"])
        for r in rows
    ]


# ── Status ───────────────────────────────────────────────────────────────────


def stats() -> dict:
    """One-line counts for ``memory status``."""
    init()
    with _conn() as c:
        wn = c.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0]
        sn = c.execute("SELECT COUNT(*) FROM short_fts").fetchone()[0]
        last = c.execute(
            "SELECT value FROM index_meta WHERE key = 'last_reindex'"
        ).fetchone()
    return {
        "wiki_pages": wn,
        "short_entries": sn,
        "last_reindex": last[0] if last else None,
    }
