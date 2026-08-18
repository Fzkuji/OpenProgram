"""Persisted recall-count signals.

Sleep's deep phase uses ``recall_count`` and ``distinct_queries`` to
decide which journal candidates deserve promotion. Those signals
have to come from somewhere: every time the recall path surfaces an
entry, we bump the count for that entry and remember which queries
surfaced it.

Schema (``<state>/memory/.state/recall-counts.json``)::

    {
        "<normalized_text>": {
            "count": 7,
            "queries": ["how do i deploy", "git pull"],
            "last_seen": "2026-05-09T12:34:56Z"
        },
        ...
    }

Cap each entry's ``queries`` list to keep the file bounded. We just
need *distinct* queries, not the full history.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import store

_lock = threading.Lock()
_QUERY_CAP = 20


def _normalize(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[\W_]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _load() -> dict:
    p = store.recall_counts_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    store.recall_counts_path().write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def record_hit(*, texts: Iterable[str], query: str) -> None:
    """Increment counts for one or more recalled texts under a single query.

    Called from the recall path with all hits from a single query.
    Same text appearing in two hits within the same query still counts
    once for ``count`` (not ``distinct_queries``) — caller should pass
    deduped texts.
    """
    if not query or not query.strip():
        return
    q = _normalize(query)[:80]
    seen: set[str] = set()
    with _lock:
        data = _load()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for raw in texts:
            key = _normalize(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            entry = data.get(key) or {"count": 0, "queries": [], "last_seen": ""}
            entry["count"] = int(entry.get("count", 0)) + 1
            queries = list(entry.get("queries") or [])
            if q and q not in queries:
                queries.append(q)
            entry["queries"] = queries[-_QUERY_CAP:]
            entry["last_seen"] = now
            data[key] = entry
        _save(data)


def get(text: str) -> dict:
    key = _normalize(text)
    return (_load().get(key) or {}).copy()
