"""Common helpers for the session DAG layout.

Two distinct parent edges on a node — keep them separate:
  * predecessor — conversation-chain parent (聊天顺序上的前驱)
  * caller      — sub-call parent (谁调用了我)
"""
from __future__ import annotations
from typing import Optional


def predecessor_of(by_id: dict[str, dict], m: dict) -> Optional[str]:
    """The conversation predecessor edge, if it points to an in-graph node."""
    p = m.get("predecessor")
    if p and p in by_id and p != m.get("id"):
        return p
    return None


def caller_of(by_id: dict[str, dict], m: dict) -> Optional[str]:
    """The sub-call caller edge, if it points to an in-graph node."""
    c = m.get("caller")
    if c and c in by_id and c != m.get("id"):
        return c
    return None


def is_root(m: dict) -> bool:
    return m.get("display") == "root"


def is_top_program_run(m: dict) -> bool:
    """A user-invoked Program entry, not an internal execution node."""
    caller = m.get("caller") or ""
    return bool(
        not is_root(m)
        and m.get("role") in ("tool", "code")
        and caller in ("", "ROOT")
        and (m.get("function") or m.get("name"))
    )


def retry_source(m: dict) -> Optional[str]:
    value = m.get("retry_of")
    return value if isinstance(value, str) and value else None


def ts(by_id: dict[str, dict], nid: str) -> float:
    return (by_id.get(nid) or {}).get("created_at") or 0.0
