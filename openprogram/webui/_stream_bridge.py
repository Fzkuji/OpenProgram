from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamBridge:
    """Tiny placeholder bridge kept for backwards-compatible imports.

    The current web UI routes stream directly from MessageStore / runtime
    callbacks, but server.py still imports this symbol. Keeping a lightweight
    implementation avoids import-time failures for tests and external callers.
    """

    events: list[dict[str, Any]] = field(default_factory=list)

    def push(self, event: dict[str, Any]) -> None:
        self.events.append(dict(event))

    def drain(self) -> list[dict[str, Any]]:
        drained = list(self.events)
        self.events.clear()
        return drained
