"""SQLite FTS5 index over wiki + journal — BM25 fallback path.

Two virtual tables:

* ``wiki_fts(path, title, type, body)`` — one row per content .md
  under the vault. ``path`` is relative to the vault root.
* ``journal_fts(date, ts, kind, tags, text, session)`` — one row per
  journal entry.

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

from . import journal, store, wiki
from .wiki.helpers import parse_frontmatter

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
            "CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5("
            "date UNINDEXED, ts UNINDEXED, kind UNINDEXED, tags, text, "
            "session UNINDEXED, tokenize='porter unicode61')"
        )
        # Wikilink graph — one row per [[link]] occurrence. Lets
        # ``backlinks(name)`` and ``outbound(path)`` answer in O(rows)
        # without a vault-wide rglob. Mirrors what Obsidian keeps in
        # its in-memory link cache.
        c.execute(
            "CREATE TABLE IF NOT EXISTS wiki_links ("
            "src_path TEXT NOT NULL, "
            "target_name TEXT NOT NULL, "
            "PRIMARY KEY (src_path, target_name))"
        )
        c.execute("CREATE INDEX IF NOT EXISTS wiki_links_target ON wiki_links(target_name)")
        c.execute("CREATE INDEX IF NOT EXISTS wiki_links_src ON wiki_links(src_path)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS index_meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )


def reindex_all() -> tuple[int, int]:
    init()
    root = store.wiki_dir()
    from .wiki.helpers import extract_wikilinks
    with _conn() as c:
        c.execute("DELETE FROM wiki_fts")
        c.execute("DELETE FROM journal_fts")
        c.execute("DELETE FROM wiki_links")
        wn = 0
        for path in wiki.iter_pages():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = parse_frontmatter(text)
            t = fm.get("type") or ""
            rel = str(path.relative_to(root))
            c.execute(
                "INSERT INTO wiki_fts (path, title, type, body) VALUES (?,?,?,?)",
                (rel, path.stem, str(t), body),
            )
            for target in extract_wikilinks(body):
                c.execute(
                    "INSERT OR IGNORE INTO wiki_links (src_path, target_name) "
                    "VALUES (?, ?)", (rel, target.lower()),
                )
            wn += 1
        sn = 0
        for date_iso, entry in journal.all_entries():
            c.execute(
                "INSERT INTO journal_fts (date, ts, kind, tags, text, session) "
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


def update_wiki_page(path) -> None:
    """Re-index one wiki page incrementally — drops stale FTS / link
    rows for ``path`` and inserts fresh ones from disk.

    Called by every code path that writes a page (ingest, enrich,
    rename, relink, prune_broken_links). Keeps the index coherent
    without a full reindex.

    No-op if ``path`` is a governance page or doesn't exist.
    """
    init()
    from pathlib import Path as _P
    p = _P(path)
    if p.name in store.GOVERNANCE_PAGES:
        return
    root = store.wiki_dir()
    try:
        rel = str(p.relative_to(root))
    except ValueError:
        return
    if not p.exists():
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return
    fm, body = parse_frontmatter(text)
    t = fm.get("type") or ""
    from .wiki.helpers import extract_wikilinks
    with _conn() as c:
        c.execute("DELETE FROM wiki_fts WHERE path = ?", (rel,))
        c.execute("DELETE FROM wiki_links WHERE src_path = ?", (rel,))
        c.execute(
            "INSERT INTO wiki_fts (path, title, type, body) VALUES (?,?,?,?)",
            (rel, p.stem, str(t), body),
        )
        for target in extract_wikilinks(body):
            c.execute(
                "INSERT OR IGNORE INTO wiki_links (src_path, target_name) "
                "VALUES (?, ?)", (rel, target.lower()),
            )


def remove_wiki_page(path) -> None:
    """Drop FTS + link rows for ``path``. Used after a delete or before
    a rename moves a file."""
    init()
    from pathlib import Path as _P
    p = _P(path)
    root = store.wiki_dir()
    try:
        rel = str(p.relative_to(root))
    except ValueError:
        return
    with _conn() as c:
        c.execute("DELETE FROM wiki_fts WHERE path = ?", (rel,))
        c.execute("DELETE FROM wiki_links WHERE src_path = ?", (rel,))


def inbound(name: str) -> list[str]:
    """Pages that have a ``[[name]]`` wikilink. Returns relative paths.

    O(rows-with-this-target). Replaces the O(full-vault-scan) version
    in :func:`wiki_ops.backlinks`.
    """
    init()
    name_l = name.lower().removesuffix(".md")
    with _conn() as c:
        rows = c.execute(
            "SELECT src_path FROM wiki_links WHERE target_name = ? ORDER BY src_path",
            (name_l,),
        ).fetchall()
    return [r["src_path"] for r in rows]


def outbound(src_path: str) -> list[str]:
    """Targets this page links to. ``src_path`` is relative to vault root."""
    init()
    with _conn() as c:
        rows = c.execute(
            "SELECT target_name FROM wiki_links WHERE src_path = ? ORDER BY target_name",
            (src_path,),
        ).fetchall()
    return [r["target_name"] for r in rows]


def add_journal(date_iso: str, entry) -> None:
    init()
    with _conn() as c:
        c.execute(
            "INSERT INTO journal_fts (date, ts, kind, tags, text, session) "
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
        "SELECT date, ts, kind, tags, text, bm25(journal_fts) AS score "
        "FROM journal_fts WHERE journal_fts MATCH ?"
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
        sn = c.execute("SELECT COUNT(*) FROM journal_fts").fetchone()[0]
        last = c.execute(
            "SELECT value FROM index_meta WHERE key = 'last_reindex'"
        ).fetchone()
    return {
        "wiki_pages": wn,
        "short_entries": sn,
        "last_reindex": last[0] if last else None,
    }
