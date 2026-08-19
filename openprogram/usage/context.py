"""Call-source context for usage metering — a contextvar carrying "who
is making the LLM call right now".

The bottom-level ``stream_simple`` can't tell whether it's serving a chat
turn, a compaction summary, or a memory ingest. Rather than thread a
``call_kind`` parameter through every layer, call sites wrap their work
in ``usage_scope(...)`` and the recorder at the stream.py chokepoint reads
the current context. asyncio Tasks copy the context automatically, so the
scope propagates down through ``await`` without any plumbing.

Cross-process (fork) copies the contextvar value too; for spawn or worker
threads use ``snapshot()`` / ``apply_snapshot()`` to carry it explicitly.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator, Optional

from .event import CALL_KIND_UNKNOWN


@dataclass(frozen=True)
class UsageContext:
    call_kind: str = CALL_KIND_UNKNOWN
    call_label: Optional[str] = None
    session_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    agent_id: Optional[str] = None


_current: contextvars.ContextVar[UsageContext] = contextvars.ContextVar(
    "op_usage_ctx", default=UsageContext()
)


def current_usage_context() -> UsageContext:
    return _current.get()


@contextmanager
def usage_scope(
    *,
    call_kind: Optional[str] = None,
    call_label: Optional[str] = None,
    session_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Iterator[UsageContext]:
    """Set the call-source context for the duration of the block.

    Nests: fields left as ``None`` inherit from the enclosing scope, so an
    inner ``usage_scope(call_kind="summarize")`` keeps the outer
    ``session_id`` / ``agent_id`` — that's how a compaction call made inside
    a chat turn still attributes to the right session.
    """
    base = _current.get()
    merged = replace(
        base,
        call_kind=call_kind if call_kind is not None else base.call_kind,
        call_label=call_label if call_label is not None else base.call_label,
        session_id=session_id if session_id is not None else base.session_id,
        parent_session_id=parent_session_id if parent_session_id is not None
        else base.parent_session_id,
        agent_id=agent_id if agent_id is not None else base.agent_id,
    )
    token = _current.set(merged)
    try:
        yield merged
    finally:
        _current.reset(token)


def snapshot() -> dict:
    """Serializable copy of the current context, for crossing a process or
    thread boundary that doesn't inherit contextvars."""
    c = _current.get()
    return {
        "call_kind": c.call_kind,
        "call_label": c.call_label,
        "session_id": c.session_id,
        "parent_session_id": c.parent_session_id,
        "agent_id": c.agent_id,
    }


def apply_snapshot(data: Optional[dict]) -> None:
    """Restore a context produced by ``snapshot()`` (e.g. at a spawned
    subprocess or worker-thread entry point). No-op on falsy input."""
    if not data:
        return
    _current.set(UsageContext(
        call_kind=data.get("call_kind") or CALL_KIND_UNKNOWN,
        call_label=data.get("call_label"),
        session_id=data.get("session_id"),
        parent_session_id=data.get("parent_session_id"),
        agent_id=data.get("agent_id"),
    ))


__all__ = [
    "UsageContext", "current_usage_context", "usage_scope",
    "snapshot", "apply_snapshot",
]
