"""
Event bus: the framework-wide event layer.

Three APIs live here:

* **Typed events** (``docs/reference/design/proactive/event-layer.md``):
  a frozen :class:`Event` (core trio ``type``/``payload``/``ts`` plus ``id``,
  ``origin`` and an open ``metadata`` pocket), emitted to the process-wide
  singleton from :func:`get_event_bus`. Sources call ``emit(make_event(...))``;
  consumers call ``subscribe(handler, types={...})``. Sources and consumers
  never know each other — only the bus.

* **Gate dispatch** (``emit_gate`` / ``subscribe_gate``): the synchronous
  veto path for the registry's gate-kind types (``registry.EVENTS``). Gate
  subscribers run in the emitter's thread, in registration order; any
  returned reason denies the action. Gates must be fast — no LLM calls, no
  slow IO.

* **Legacy channel pub/sub** (``emit("channel", data)`` / ``on()``): the
  original API, kept verbatim because ``AgentSession`` still targets it.
  New code uses typed events.

The process-wide bus (``get_event_bus``) appends every completed typed
dispatch as one JSON line via ``event_log.log_event`` — per-session
``events.jsonl`` files with 5 MB rotation.  A gate event observed through
``emit`` defers that write until ``emit_gate`` can put its verdict on the
same line as a ``gate`` field.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from openprogram.events.event_log import log_event as _log_event
from openprogram.events.registry import EVENTS

_log = logging.getLogger(__name__)

# Event

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


# 透传信封：外部源（job runner / channels / worktree / functions watcher /
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


# Registry warning — one per unregistered type; legacy emit sites migrate
# gradually into registry.EVENTS.

_warned_unregistered: set[str] = set()


def _warn_unregistered(event_type: str) -> None:
    if event_type not in EVENTS and event_type not in _warned_unregistered:
        _warned_unregistered.add(event_type)
        _log.warning("event type %r is not in the events registry",
                     event_type)


# Gate dispatch result

@dataclass(frozen=True)
class GateOutcome:
    """Merged verdict of one ``emit_gate`` round. ``allowed`` is False as
    soon as any gate returned a reason; ``reasons`` aggregates them in
    subscriber-registration order."""
    allowed: bool
    reasons: list[str]


# Bus

class EventBus:
    """Process-wide fan-out. Typed subscribers get :class:`Event` objects,
    optionally filtered by type; gate subscribers run synchronously via
    ``emit_gate``; legacy channel handlers keep working."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}          # legacy channels
        # [(handler, frozenset(types) | None)]
        self._subscribers: list[tuple[Callable, frozenset[str] | None]] = []
        self._gates: dict[str, list[Callable]] = {}             # type -> gates
        self._lock = threading.Lock()
        self._gate_active = threading.local()   # re-entrancy guard
        # Only the process-wide singleton logs to disk; isolated buses
        # (create_event_bus) stay silent unless a test flips this.
        self.log_events = False

    # typed API（事件层）

    def emit(self, target: Event | str, data: Any = None) -> None:
        """Emit a typed :class:`Event`, or (legacy) ``emit(channel, data)``.

        Fire-and-forget; a raising handler never breaks the emitter — sources
        must not pay for a bad consumer.
        """
        if isinstance(target, Event):
            _warn_unregistered(target.type)
            spec = EVENTS.get(target.type)
            # A gate event may pass through typed observers before its
            # synchronous verdict.  Defer its only log row until emit_gate
            # can include that verdict instead of writing the same ID twice.
            if self.log_events and (spec is None or spec.kind != "gate"):
                _log_event(target)
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

    # gate dispatch（同步否决）

    def subscribe_gate(
        self, type: str, fn: Callable[[Event], "str | None"],
    ) -> Callable[[], None]:
        """Register a gate for one event type. Gates run in registration
        order inside ``emit_gate``; return ``None`` to allow, a reason
        string to deny. Returns an unregister function."""
        with self._lock:
            self._gates.setdefault(type, []).append(fn)

        def unregister() -> None:
            with self._lock:
                try:
                    self._gates.get(type, []).remove(fn)
                except ValueError:
                    pass

        return unregister

    def emit_gate(
        self, event: Event, timeout_s: float | None = None,
    ) -> GateOutcome:
        """Ask every gate for this event's type, synchronously, in the
        caller's thread. Any deny makes ``allowed`` False; reasons
        aggregate. A raising gate is fail-open (stderr, like tool_gate's
        original rule). ``timeout_s`` is a soft overall budget: once
        exceeded, the remaining gates are skipped fail-open with a
        warning. Re-entrant ``emit_gate`` on the same type in the same
        thread allows immediately with a warning — a gate must not gate
        itself into a loop. The verdict is recorded on the event's log
        line (``gate`` field), not emitted as a second event."""
        _warn_unregistered(event.type)
        active: set[str] = getattr(self._gate_active, "types", None) or set()
        self._gate_active.types = active
        if event.type in active:
            _log.warning("re-entrant emit_gate(%s) allowed without asking "
                         "gates", event.type)
            return GateOutcome(allowed=True, reasons=[])
        active.add(event.type)
        started = time.monotonic()
        try:
            with self._lock:
                gates = list(self._gates.get(event.type, []))
            reasons: list[str] = []
            for gate in gates:
                if (timeout_s is not None
                        and time.monotonic() - started > timeout_s):
                    _log.warning("emit_gate(%s) budget %ss exhausted; "
                                 "remaining gates skipped (fail-open)",
                                 event.type, timeout_s)
                    break
                try:
                    verdict = gate(event)
                except Exception as exc:
                    # fail-open：一个 gate 的 bug 不能砖掉整个动作
                    print(f"Gate error ({event.type}, fail-open): {exc}",
                          file=sys.stderr)
                    continue
                if verdict:
                    reasons.append(str(verdict))
            outcome = GateOutcome(allowed=not reasons, reasons=reasons)
            if self.log_events:
                _log_event(event, gate={
                    "allowed": outcome.allowed,
                    "reasons": outcome.reasons,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "subscribers": len(gates),
                })
            return outcome
        finally:
            active.discard(event.type)

    # shared dispatch

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

    # legacy channel API

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
        """Remove all handlers (legacy channels, typed subscribers, gates)."""
        self._handlers.clear()
        with self._lock:
            self._subscribers.clear()
            self._gates.clear()


def create_event_bus() -> EventBus:
    """Create a new EventBus instance (isolated; tests, embedded use)."""
    return EventBus()


# process-wide singleton（照 AuthStore / JobRunner 的双检锁先例）

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
                bus.log_events = True   # 事件日志常开（仅进程单例落盘）
                _event_bus = bus
    return _event_bus
