"""Common helpers for the session DAG layout."""
from __future__ import annotations
from typing import Optional


def called_by_of(by_id: dict[str, dict], m: dict) -> Optional[str]:
    """The called_by edge, if it points to an in-graph node."""
    c = m.get("called_by") or m.get("caller")
    if c and c in by_id:
        return c
    return None


def conv_parent_of(by_id: dict[str, dict], m: dict) -> Optional[str]:
    """The conversation predecessor (via called_by), if in-graph."""
    p = m.get("called_by")
    if p and p in by_id and p != m.get("id"):
        return p
    return None


# backward compat alias — old name used by depth/topology/lane/filter
parent_id_of = conv_parent_of


def is_root(m: dict) -> bool:
    return m.get("display") == "root"


def ts(by_id: dict[str, dict], nid: str) -> float:
    return (by_id.get(nid) or {}).get("created_at") or 0.0
