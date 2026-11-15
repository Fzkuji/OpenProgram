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
from dataclasses import asdict, dataclass, replace
from typing import Iterator, Optional


@dataclass(frozen=True)
class UsageContext:
    call_kind: Optional[str] = None  # None: no caller has said what this is
    call_label: Optional[str] = None
    session_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    agent_id: Optional[str] = None
    goal_id: Optional[str] = None
    goal_revision: Optional[int] = None
    goal_run_id: Optional[str] = None
    goal_session_id: Optional[str] = None
    execution_id: Optional[str] = None
    # Which prompt a conversation's call is made with, when it is not the
    # conversation's own (a Goal verification turn): requests are compared
    # for prompt-cache breaks only with requests of the same lane.
    prefix_lane: Optional[str] = None
    # The conversation branch the turn runs on: a request is compared with
    # the previous request of its own branch.
    branch_id: Optional[str] = None


_current: contextvars.ContextVar[UsageContext] = contextvars.ContextVar(
    "op_usage_ctx", default=UsageContext()
)

GOAL_FIELDS = frozenset({"goal_id", "goal_revision", "goal_run_id", "goal_session_id"})


@contextmanager
def bind_goal(identity, *, session_id: str):
    """Bind canonical Job attribution, clearing any caller's transient turn."""
    from openprogram.models.usage.goal_port import turn_goal
    identity = identity or {}
    token = _current.set(replace(_current.get(), session_id=session_id,
                                 **{key: identity.get(key) for key in GOAL_FIELDS}))
    turn_token = turn_goal.set(None)
    try:
        yield
    finally:
        turn_goal.reset(turn_token)
        _current.reset(token)


def current_usage_context() -> UsageContext:
    return _current.get()


def call_context() -> UsageContext:
    """The current context with the execution this call is made in.

    The execution bound to the running context (the turn's driver binds it)
    names the call, so a run's usage reads back by its execution tree. A
    call on a thread of its own, which starts from an empty context (the
    title the Worker writes beside a finished turn), names none.
    """
    from openprogram.state.execution_facts import current_execution_id

    base = _current.get()
    execution_id = current_execution_id() or base.execution_id
    return base if execution_id == base.execution_id else replace(base, execution_id=execution_id)


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
    return asdict(request_context())


def request_context(session_id: str | None = None) -> UsageContext:
    """Capture trusted turn identity, never infer it from the current session Goal."""
    from openprogram.models.usage.goal_port import turn_goal
    base = call_context()
    bound = turn_goal.get()
    effective = base.session_id or session_id
    updates = {"session_id": effective}
    if bound is not None:
        updates.update(goal_id=bound.get("goal_id"), goal_revision=bound.get("revision"),
                       goal_run_id=bound.get("run_id"),
                       goal_session_id=effective if bound else None)
    return replace(base, **updates)


def apply_snapshot(data: Optional[dict]) -> None:
    """Restore a context produced by ``snapshot()`` (e.g. at a spawned
    subprocess or worker-thread entry point). No-op on falsy input."""
    if not data:
        return
    _current.set(UsageContext(
        call_kind=data.get("call_kind"),
        call_label=data.get("call_label"),
        session_id=data.get("session_id"),
        parent_session_id=data.get("parent_session_id"),
        agent_id=data.get("agent_id"),
        goal_id=data.get("goal_id"),
        goal_revision=data.get("goal_revision"),
        goal_run_id=data.get("goal_run_id"),
        goal_session_id=data.get("goal_session_id"),
        execution_id=data.get("execution_id"),
        prefix_lane=data.get("prefix_lane"),
        branch_id=data.get("branch_id"),
    ))


__all__ = [
    "UsageContext", "current_usage_context", "usage_scope",
    "snapshot", "apply_snapshot",
]
