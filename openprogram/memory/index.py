"""SQLite FTS5 index over wiki + short-term — BM25 fallback path.

Two virtual tables:

* ``wiki_fts(path, title, type, body)`` — one row per content .md
  under the vault. ``path`` is relative to the vault root.
* ``short_fts(date, ts, kind, tags, text, session)`` — one row per
  short-term entry.

Used by :func:`recall_for_prompt` as a keyword fallback when the
LLM doesn't know which wiki page to read. The primary read path is
folder-tree navigation; FTS is only invoked by ``memory_recall``.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from . import short_term, store, wiki
from .wiki_helpers import parse_frontmatter

_lock = threading.RLock()


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    path = store.index_db()
    with _lock:
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init() -> None:
    with _conn() as c:
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5("
            "path UNINDEXED, title, type UNINDEXED, body, "
            "tokenize='porter unicode61')"
        )
        c.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS short_fts USING fts5("
            "date UNINDEXED, ts UNINDEXED, kind UNINDEXED, tags, text, "
            "session UNINDEXED, tokenize='porter unicode61')"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS index_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


def reindex_all() -> tuple[int, int]:
    init()
    root = store.wiki_dir()
    with _conn() as c:
        c.execute("DELETE FROM wiki_fts")
        c.execute("DELETE FROM short_fts")
        wn = 0
        for path in wiki.iter_pages():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = parse_frontmatter(text)
            t = fm.get("type") or ""
            c.execute(
                "INSERT INTO wiki_fts (path, title, type, body) VALUES (?,?,?,?)",
                (str(path.relative_to(root)), path.stem, str(t), body),
            )
            wn += 1
        sn = 0
        for date_iso, entry in short_term.all_entries():
            c.execute(
                "INSERT INTO short_fts (date, ts, kind, tags, text, session) "
                "VALUES (?,?,?,?,?,?)",
                (date_iso, entry.timestamp, entry.type,
                 " ".join(entry.tags), entry.text, entry.session_id),
            )
            sn += 1
        c.execute(
            "INSERT OR REPLACE INTO index_meta (key, value) VALUES "
            "('last_reindex', datetime('now'))"
        )
    return wn, sn


def add_short_term(date_iso: str, entry) -> None:
    init()
    with _conn() as c:
        c.execute(
            "INSERT INTO short_fts (date, ts, kind, tags, text, session) "
            "VALUES (?,?,?,?,?,?)",
            (date_iso, entry.timestamp, entry.type,
             " ".join(entry.tags), entry.text, entry.session_id),
        )


@dataclass
class WikiHit:
    path: str
    title: str
    type: str
    snippet: str
    score: float

    @property
    def kind(self) -> str:
        return self.type
    @property
    def slug(self) -> str:
        return self.title


@dataclass
class ShortHit:
    date: str
    ts: str
    kind: str
    tags: str
    text: str
    score: float


def _sanitize(query: str) -> str:
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
            "SELECT path, title, type, snippet(wiki_fts, 3, '«', '»', '…', 16) AS snip, "
            "bm25(wiki_fts) AS score FROM wiki_fts WHERE wiki_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (q, limit),
        ).fetchall()
    return [WikiHit(r["path"], r["title"], r["type"] or "", r["snip"], -r["score"]) for r in rows]


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


def stats() -> dict:
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
