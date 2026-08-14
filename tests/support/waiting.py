from __future__ import annotations

from collections.abc import Callable
from threading import Event
from time import monotonic


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 1.0,
    interval: float = 0.01,
) -> bool:
    """Poll a state without fixed sleeps, bounded by a monotonic deadline."""
    deadline = monotonic() + timeout
    wake = Event()
    while True:
        if predicate():
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        wake.wait(min(interval, remaining))
