"""Background work a finished turn starts beside itself.

After a turn, its conversation is titled, its branch named and its messages
sorted into topics. Each is a model request on a thread of its own, so the
turn does not wait for it. In a long-lived process (the worker) that is all
it needs. A process that exits as soon as its turn ends
(``openprogram --print``) would leave those threads mid-request, stopped turn
or not: the interpreter shuts down under them, a request then fails to start
("cannot schedule new futures after interpreter shutdown"), and a thread
writing its error to stderr at that point aborts the process.

Such a process holds side work for as long as it runs (:func:`held`): none
starts, so the run makes no model request beyond its turn's own, and its
conversation keeps the title cut from the first message. The turn's own
hand-off to memory is held too: once enough of the conversation has
gathered it is a model run inside the turn, after the reply, which the
command did not ask for and could leave mid-write; the idle watcher of the
next worker folds the conversation in instead. Each place that starts such
work asks :func:`allowed` first.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_lock = threading.Lock()
_holds = 0


def allowed() -> bool:
    """Whether side work may start in this process now."""
    with _lock:
        return _holds == 0


@contextmanager
def held():
    """Within the block, no side work starts in this process."""
    global _holds
    with _lock:
        _holds += 1
    try:
        yield
    finally:
        with _lock:
            _holds -= 1


__all__ = ["allowed", "held"]
