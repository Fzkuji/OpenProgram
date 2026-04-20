"""
Async event bus with channel-based pub/sub.

Originally ported from pi_coding_agent.core.event_bus (which mirrors
core/event-bus.ts). Self-contained — no dependencies.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Callable


class EventBus:
    """Async event bus with channel-based pub/sub."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = {}

    def emit(self, channel: str, data: Any) -> None:
        """Emit an event on a channel (fire-and-forget async handlers)."""
        for handler in list(self._handlers.get(channel, [])):
            if asyncio.iscoroutinefunction(handler):
                asyncio.ensure_future(self._safe_call(channel, handler, data))
            else:
                try:
                    handler(data)
                except Exception as exc:
                    print(f"Event handler error ({channel}): {exc}", file=sys.stderr)

    async def _safe_call(self, channel: str, handler: Callable, data: Any) -> None:
        try:
            await handler(data)
        except Exception as exc:
            print(f"Event handler error ({channel}): {exc}", file=sys.stderr)

    def on(self, channel: str, handler: Callable) -> Callable[[], None]:
        """Subscribe to a channel. Returns an unsubscribe function."""
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
        """Remove all handlers."""
        self._handlers.clear()


def create_event_bus() -> EventBus:
    """Create a new EventBus instance."""
    return EventBus()
