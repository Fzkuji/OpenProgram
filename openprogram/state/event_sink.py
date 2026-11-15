"""Where models and state publish events.

The event bus (``openprogram.runtime.events``) is not a leaf: it logs each
event into the session's directory and reads the per-turn store, so it
sits above the store. Models and state still announce things (a memory
write started, a worktree changed, a provider's model list moved). They
publish through this sink, which the runtime points at the bus when it
loads; before that, an event has no subscriber and is dropped.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__name__)

_emit: Callable[..., Any] | None = None
_emit_ws_frame: Callable[[dict], Any] | None = None


def register_event_sink(
    *,
    emit: Callable[..., Any],
    emit_ws_frame: Callable[[dict], Any],
) -> None:
    """Send events published here to the runtime's bus."""
    global _emit, _emit_ws_frame
    _emit = emit
    _emit_ws_frame = emit_ws_frame


def emit_safe(
    event_type: str,
    source: str,
    payload: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Publish one bus event; never raises."""
    if _emit is None:
        return
    try:
        _emit(event_type, source, payload, metadata)
    except Exception:
        # Publishing is best-effort: a failing sink must not fail the caller.
        _log.debug("event sink raised for %s", event_type, exc_info=True)


def emit_ws_frame(frame: dict) -> None:
    """Push one frame to open clients; never raises."""
    if _emit_ws_frame is None:
        return
    try:
        _emit_ws_frame(frame)
    except Exception:
        # Publishing is best-effort: a failing sink must not fail the caller.
        _log.debug("frame sink raised for %s", frame.get("type"), exc_info=True)


__all__ = ["emit_safe", "emit_ws_frame", "register_event_sink"]
