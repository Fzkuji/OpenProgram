from __future__ import annotations

from collections.abc import Callable
from threading import Event
from time import monotonic


# The default only guards against a hang: polling returns as soon as the
# predicate holds, so a generous ceiling costs nothing on a healthy run and
# keeps a loaded machine from failing it. A caller asserting that something
# does NOT happen passes its own short timeout.
_DEFAULT_TIMEOUT = 30.0


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
