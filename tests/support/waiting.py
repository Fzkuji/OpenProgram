from __future__ import annotations

from collections.abc import Callable
from threading import Event
from time import monotonic
import sys


_DEFAULT_TIMEOUT = 5.0 if sys.platform == "win32" else 1.0


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
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
