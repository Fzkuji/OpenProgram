"""Shared helpers used across the layout stages — small enough to
live in one module so the others stay focused on their phase."""
from __future__ import annotations

from typing import Optional


def ts(by_id: dict[str, dict], nid: str) -> float:
    """Wall-clock timestamp from either created_at or timestamp.

    SessionDB writes ``timestamp``; some build paths normalize to
    ``created_at``. Honour whichever is present."""
    m = by_id.get(nid) or {}
    return float(m.get("created_at") or m.get("timestamp") or 0)


def caller_of(by_id: dict[str, dict], m: dict) -> Optional[str]:
    """The sub-call edge: ``caller`` if it points to an in-graph node."""
    c = m.get("caller")
    if c and c in by_id:
        return c
    return None


def conv_parent_of(by_id: dict[str, dict], m: dict) -> Optional[str]:
    """The conversation edge: ``parent_id`` only when ``caller`` is
    empty. Mutual exclusion enforced here so downstream stages don't
    have to guard.
    """
    if caller_of(by_id, m):
        return None
    p = m.get("parent_id")
    if p and p in by_id and p != m["id"]:
        return p
    return None
