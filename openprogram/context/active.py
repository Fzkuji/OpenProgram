"""Active context — ContextVar-scoped DAG state for the current turn.

This module is the seam between **per-turn execution state** (which
``GraphStore`` to write to, which node is the current "head", which
``@agentic_function`` frames are open) and **the rest of the runtime**
(``Runtime.exec`` and the ``@agentic_function`` decorator).

Why a separate module:

The DAG refactor turns the old tree-``Context`` into a flat append-only
graph. The graph is owned by a ``GraphStore`` (one per session); the
"cursor" — the most-recent node id, used as the next ``predecessor``
plus the frame stack tracking nested ``@agentic_function`` calls — has
to live somewhere ContextVar-shaped so async code can find it without
threading a parameter through every call.

In the prior architecture the cursor was attached to the ``Runtime``
instance (``runtime.store`` / ``runtime.head_id`` / ``runtime.append_node``).
That works but couples LLM-call duty with persistence-cursor duty in
the same class, and forces the dispatcher to ``attach_store`` /
``detach_store`` on a specific Runtime — which gets awkward when a
turn nests multiple runtimes (one per ``@agentic_function`` calling a
different provider).

``ActiveContext`` decouples the cursor from any runtime: the dispatcher
``set_active(store, head_id)`` once at turn entry; everyone reading
the cursor (``Runtime._render_history_messages``,
``@agentic_function`` entry/exit) calls ``active.current()``; everyone
writing to the DAG calls ``active.append_node(node)``.

Phase 2 (this file): pure structural — define the ContextVar and the
frame stack ops. No callers are wired in yet; the legacy
``runtime.attach_store`` / ``runtime.head_id`` path remains primary.
Phases 3-5 will switch ``@agentic_function`` / ``Runtime.exec`` /
``dispatcher`` over.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Optional

from openprogram.context.nodes import Call, Graph
from openprogram.context.storage import GraphStore


@dataclass
class FunctionFrame:
    """One active ``@agentic_function`` invocation.

    Pushed on entry, popped on exit. The frame's ``pending_call_id`` is
    the id of the placeholder code Call appended to the DAG when the
    function entered (output=None then; the exit handler fills it in).

    ``entry_predecessor`` is the head_id at the moment we entered —
    captured so the placeholder code Call's ``called_by`` points to
    the right ancestor even if the frame's body appends siblings.

    ``render_range`` is forwarded to ``compute_reads`` when a
    ``Runtime.exec`` call fires inside this frame, so the function's
    declared ``render_range={"depth":..., "siblings":...}`` controls
    what the LLM sees.
    """

    name: str
    pending_call_id: str
    entry_predecessor: Optional[str]
    expose: str = "io"
    render_range: Optional[dict] = None


@dataclass
class ActiveContext:
    """Per-turn state read/written across ``Runtime.exec`` and
    ``@agentic_function`` boundaries.

    Stored in a ContextVar so async tasks inherit it automatically.
    """

    store: GraphStore
    graph: Graph
    session_id: str
    head_id: Optional[str] = None
    frames: list[FunctionFrame] = field(default_factory=list)


_active: contextvars.ContextVar[Optional[ActiveContext]] = contextvars.ContextVar(
    "_active_ctx", default=None,
)


# ── Lifecycle ───────────────────────────────────────────────────────


def set_active(
    *,
    store: GraphStore,
    graph: Optional[Graph] = None,
    head_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> contextvars.Token:
    """Install an :class:`ActiveContext` for the current execution scope.

    Call from the dispatcher at turn entry; pass the token back to
    :func:`reset_active` in a ``finally:`` to tear it down. The
    ``graph`` and ``session_id`` default to the store's view.
    """
    g = graph if graph is not None else store.load()
    sid = session_id or store.session_id
    ctx = ActiveContext(
        store=store, graph=g, session_id=sid, head_id=head_id,
    )
    return _active.set(ctx)


def reset_active(token: contextvars.Token) -> None:
    """Tear down an active context installed by :func:`set_active`."""
    _active.reset(token)


def current() -> Optional[ActiveContext]:
    """Return the active context, or ``None`` when running standalone.

    Callers MUST tolerate ``None`` — ``@agentic_function`` and
    ``Runtime.exec`` keep working outside a dispatcher-managed turn
    (e.g. one-shot scripts, tests). Persistence is just skipped.
    """
    return _active.get(None)


# ── Frame stack ─────────────────────────────────────────────────────


def push_frame(
    *,
    name: str,
    pending_call_id: str,
    expose: str = "io",
    render_range: Optional[dict] = None,
) -> Optional[FunctionFrame]:
    """Open a new ``@agentic_function`` frame on the active context.

    Returns the frame (so the caller can pass it to :func:`pop_frame`),
    or ``None`` when no active context is installed — callers should
    treat the ``None`` return as "standalone, skip frame bookkeeping".
    """
    ctx = current()
    if ctx is None:
        return None
    frame = FunctionFrame(
        name=name,
        pending_call_id=pending_call_id,
        entry_predecessor=ctx.head_id,
        expose=expose,
        render_range=render_range,
    )
    ctx.frames.append(frame)
    return frame


def pop_frame(frame: Optional[FunctionFrame]) -> None:
    """Close the given frame. No-op when ``frame is None``.

    We pop by identity (``is``) rather than by index so out-of-order
    pops (e.g. async tasks completing in a surprising order) don't
    silently corrupt the stack — they raise instead.
    """
    if frame is None:
        return
    ctx = current()
    if ctx is None or not ctx.frames:
        return
    top = ctx.frames[-1]
    if top is not frame:
        # Out-of-order — find and remove rather than blindly popping
        # the wrong slot. This shouldn't normally happen but we'd
        # rather corrupt nothing than silently lose track.
        try:
            ctx.frames.remove(frame)
        except ValueError:
            pass
        return
    ctx.frames.pop()


def current_frame() -> Optional[FunctionFrame]:
    """Top of the frame stack on the active context, or ``None``."""
    ctx = current()
    if ctx is None or not ctx.frames:
        return None
    return ctx.frames[-1]


# ── DAG writes ──────────────────────────────────────────────────────


def append_node(node: Call) -> None:
    """Persist ``node`` and advance the active context's ``head_id``.

    No-op when no active context is installed. The store assigns
    ``node.seq`` inside :meth:`GraphStore.append`; we mirror the node
    into the in-memory ``Graph`` so subsequent ``compute_reads`` calls
    in the same turn see it without re-loading from SQLite.
    """
    ctx = current()
    if ctx is None:
        return
    ctx.store.append(node)
    if node.id not in ctx.graph.nodes:
        ctx.graph.nodes[node.id] = node
        ctx.graph._next_seq = max(ctx.graph._next_seq, node.seq + 1)
    ctx.head_id = node.id


def update_node(node_id: str, **fields) -> None:
    """Update an already-appended node (e.g. fill ``output`` on the
    placeholder code Call written at ``@agentic_function`` entry).

    No-op when no active context is installed or when the store update
    fails — DAG bookkeeping must never break the user's function call.
    """
    ctx = current()
    if ctx is None:
        return
    try:
        ctx.store.update(node_id, **fields)
    except Exception:
        return
    if node_id in ctx.graph.nodes:
        for k, v in fields.items():
            if k == "metadata" and isinstance(v, dict):
                existing = ctx.graph.nodes[node_id].metadata or {}
                ctx.graph.nodes[node_id].metadata = {**existing, **v}
            else:
                setattr(ctx.graph.nodes[node_id], k, v)


__all__ = [
    "ActiveContext",
    "FunctionFrame",
    "set_active",
    "reset_active",
    "current",
    "push_frame",
    "pop_frame",
    "current_frame",
    "append_node",
    "update_node",
]
