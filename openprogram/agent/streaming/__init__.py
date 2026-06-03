"""Streaming / resumable in-progress state — central manager.

See ``docs/design/runtime/streaming-resume.md`` for the full design.

Any backend code path that's going to produce a long-running message
(LLM streaming reply, tool call, agentic function execution, task
spawn, merge) goes through this module instead of writing
``display=runtime`` placeholders ad-hoc. Centralizing it means:

  * One source of truth for the ``status`` lifecycle (pending →
    running → done/error/aborted).
  * One throttled persistence layer (250ms default debounce, hard
    flush on terminal transitions).
  * One WS broadcast channel (``msg_update`` events keyed by
    (session_id, msg_id)) so the frontend's ``subscribe_msg`` handler
    can re-attach after a refresh.
  * One worker-startup sweep that aborts stale ``running`` msgs from
    a previous crash.

Usage::

    from openprogram.agent.streaming import open_stream

    with open_stream(session_id, msg_id, kind="agentic_function",
                     function="gui_agent") as s:
        s.update(content="step 1...", tree=tree_snapshot)
        ...
        s.update(content="step 2...", tree=tree_snapshot)
        # On context exit the manager marks ``done`` and final-saves.
        # On exception it marks ``error`` with the traceback.

Manual control is available via ``StreamingMsg.update`` /
``StreamingMsg.finalize`` for paths that don't fit the with-block
pattern.

Public API:

  * :class:`StreamingMsg`           — per-message handle
  * :class:`StreamingRegistry`      — global registry of active streams
  * :func:`open_stream`              — context-manager helper
  * :func:`sweep_stale_running`      — worker startup hook
"""
from __future__ import annotations

from .registry import (
    StreamingMsg,
    StreamingRegistry,
    StreamStatus,
    get_registry,
    open_stream,
    sweep_stale_running,
)

__all__ = [
    "StreamingMsg",
    "StreamingRegistry",
    "StreamStatus",
    "get_registry",
    "open_stream",
    "sweep_stale_running",
]
