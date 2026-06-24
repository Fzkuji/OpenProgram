"""
Event bus: the framework-wide event layer.

Two APIs live here:

* **Typed events** (the event layer, ``docs/design/proactive/event-layer.md``):
  a frozen :class:`Event` (core trio ``type``/``payload``/``ts`` plus ``id``,
  ``origin`` and an open ``metadata`` pocket), emitted to the process-wide
  singleton from :func:`get_event_bus`. Sources call ``emit(make_event(...))``;
  consumers call ``subscribe(handler, types={...})``. Sources and consumers
  never know each other — only the bus.

* **Legacy channel pub/sub** (``emit("channel", data)`` / ``on()``): the
  original API, kept verbatim because ``AgentSession`` still targets it.
  New code uses typed events.

Set ``OPENPROGRAM_EVENT_LOG=1`` (or ``=/path/to/file``) to append every typed
event as one JSON line — the step-1 acceptance check is reading that log after
a real chat turn.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# ─── Event ───────────────────────────────────────────────────────────────────

#: ``origin`` values: who caused the event.
ORIGINS = ("user", "agent", "tool", "system", "proactive")


@dataclass(frozen=True)
class Event:
    """One "something just happened" record. Frozen: append-only semantics,
    safe to share across threads."""

    id: str
    ts: float
    type: str            # e.g. "tool.before", "credential.cooldown"
    origin: str          # one of ORIGINS
    payload: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)  # open pocket: session/turn/…


def _context_metadata() -> dict:
    """session/turn from the store ContextVars, when inside an agent turn.

    Mirrors ``checkpoint.helpers.checkpoint_before_edit``: graceful empty
    dict outside a dispatcher-driven turn (unit tests, B-class sources), so
    A-class events get their correlation for free and B-class events simply
    don't carry a turn — exactly the design's metadata-pocket semantics.
    """
    meta: dict = {}
    try:
        from openprogram.store import _store, _current_turn_id

        shim = _store.get()
        if shim is not None and getattr(shim, "session_id", None):
            meta["session"] = shim.session_id
        turn_id = _current_turn_id.get()
        if turn_id:
            meta["turn"] = turn_id
    except Exception:
        pass
    return meta


def make_event(
    type: str,
    origin: str,
    payload: dict | None = None,
    metadata: dict | None = None,
) -> Event:
    """Build an Event, auto-filling id/ts and the ContextVar correlation
    (session/turn). Explicit ``metadata`` keys win over the auto ones."""
    meta = _context_metadata()
    if metadata:
        meta.update(metadata)
    return Event(
        id=uuid.uuid4().hex,
        ts=time.time(),
        type=type,
        origin=origin,
        payload=payload or {},
        metadata=meta,
    )


def emit_safe(
    type: str,
    origin: str,
    payload: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Tap helper for sources: build + emit on the process bus, swallowing
    every failure — the event layer must never break the emitting code path."""
    try:
        get_event_bus().emit(make_event(type, origin, payload, metadata))
    except Exception:
        pass


# 透传信封：外部源（task runner / channels / worktree / functions watcher /
# sub_agent）原本直接 import webui 的 _broadcast 把现成 WS 帧推给前端——这是
# "外部源直连中枢"的耦合。改成 emit 一个 `ws.frame` 事件、payload 里放原始帧；
# webui 订阅它原样广播。前端零改动（收到的帧一字不差），但外部源不再认识 webui。
# 设计：docs/design/proactive/framework-evolution.md 步 4。
WS_FRAME_EVENT = "ws.frame"


def emit_ws_frame(frame: dict) -> None:
    """外部源用：把一个现成的 WS 帧（{"type":..., "data":...}）经总线送往前端。
    全失败吞掉——事件层绝不影响调用方。"""
    try:
        get_event_bus().emit(make_event(WS_FRAME_EVENT, "system", {"frame": frame}))
    except Exception:
        pass


# ─── Bus ─────────────────────────────────────────────────────────────────────

class EventBus:
    """Process-wide fan-out. Typed subscribers get :class:`Event` objects,
    optionally filtered by type; legacy channel handlers keep working."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}          # legacy channels
        # [(handler, frozenset(types) | None)]
        self._subscribers: list[tuple[Callable, frozenset[str] | None]] = []
        self._lock = threading.Lock()

    # ── typed API（事件层） ──

    def emit(self, target: Event | str, data: Any = None) -> None:
        """Emit a typed :class:`Event`, or (legacy) ``emit(channel, data)``.

        Fire-and-forget; a raising handler never breaks the emitter — sources
        must not pay for a bad consumer.
        """
        if isinstance(target, Event):
            with self._lock:
                subs = list(self._subscribers)
            for handler, types in subs:
                if types is not None and target.type not in types:
                    continue
                self._call(handler, target, label=target.type)
            return
        # legacy channel path
        for handler in list(self._handlers.get(target, [])):
            self._call(handler, data, label=target)

    def subscribe(
        self,
        handler: Callable[[Event], Any],
        *,
        types: set[str] | frozenset[str] | None = None,
    ) -> Callable[[], None]:
        """Subscribe to typed events. ``types=None`` receives everything;
        otherwise only those event types. Returns an unsubscribe function."""
        entry = (handler, frozenset(types) if types is not None else None)
        with self._lock:
            self._subscribers.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(entry)
                except ValueError:
                    pass

        return unsubscribe

    # ── shared dispatch ──

    def _call(self, handler: Callable, arg: Any, label: str) -> None:
        if asyncio.iscoroutinefunction(handler):
            try:
                asyncio.ensure_future(self._safe_call(label, handler, arg))
            except RuntimeError:
                # No running loop on this thread (worker daemon threads).
                # Async subscribers need a loop; skip rather than crash the
                # emitting source.
                print(
                    f"Event handler skipped (no event loop) ({label})",
                    file=sys.stderr,
                )
        else:
            try:
                handler(arg)
            except Exception as exc:
                print(f"Event handler error ({label}): {exc}", file=sys.stderr)

    async def _safe_call(self, label: str, handler: Callable, arg: Any) -> None:
        try:
            await handler(arg)
        except Exception as exc:
            print(f"Event handler error ({label}): {exc}", file=sys.stderr)

    # ── legacy channel API ──

    def on(self, channel: str, handler: Callable) -> Callable[[], None]:
        """Subscribe to a legacy channel. Returns an unsubscribe function."""
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

        def unsubscribe() -> None:
            if channel in self._handlers:
                try:
                    self._handlers[channel].remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def clear(self) -> None:
        """Remove all handlers (legacy channels and typed subscribers)."""
        self._handlers.clear()
        with self._lock:
            self._subscribers.clear()


def create_event_bus() -> EventBus:
    """Create a new EventBus instance (isolated; tests, embedded use)."""
    return EventBus()


# ─── process-wide singleton（照 AuthStore / TaskRunner 的双检锁先例） ──────────

_event_bus: EventBus | None = None
_event_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """The process-wide bus. All sources emit here; all consumers subscribe
    here. Same instance from every thread in the worker process."""
    global _event_bus
    if _event_bus is None:
        with _event_bus_lock:
            if _event_bus is None:
                bus = EventBus()
                _attach_event_log(bus)
                _event_bus = bus
    return _event_bus


# ─── debug log subscriber（步 1 验收通道） ────────────────────────────────────

def _attach_event_log(bus: EventBus) -> None:
    """If OPENPROGRAM_EVENT_LOG is set, append every typed event as one JSON
    line. ``1``/``true`` → /tmp/openprogram-events.jsonl, else the value is
    the path. Never raises."""
    raw = os.environ.get("OPENPROGRAM_EVENT_LOG", "").strip()
    if not raw:
        return
    path = "/tmp/openprogram-events.jsonl" if raw.lower() in ("1", "true") else raw
    write_lock = threading.Lock()

    def _log(ev: Event) -> None:
        line = json.dumps(
            {
                "id": ev.id,
                "ts": ev.ts,
                "type": ev.type,
                "origin": ev.origin,
                "payload": ev.payload,
                "metadata": ev.metadata,
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            with write_lock, open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    bus.subscribe(_log)
