"""What other agents did.

Agents of a conversation (the main turn, the agents it starts) each keep
their own reading of a window, but the window is one. A result that carries
the application's state says when another agent acted on it since this
agent last looked; the locks keep two calls from interleaving.
"""
from __future__ import annotations

from contextlib import contextmanager
import itertools
import threading
from typing import Iterator


_registry = threading.Lock()
# application -> (sequence, agent) of the acting calls on it, newest last
_acted: dict[str, list[tuple[int, str]]] = {}
# (application, agent) -> the newest sequence that agent has seen the result of
_seen: dict[tuple[str, str], int] = {}
_sequence = itertools.count(1)
_KEPT_WRITES = 64
_MAX_SEEN = 4096


class _Turn:
    """One call on an application: whether it acted there and whether it saw the result."""

    def __init__(self, app: str, others: int) -> None:
        self.app = app
        self.others = others    # acting calls of other agents since this agent last saw the application
        self.acted = False      # input of this call may have reached the application
        self.looked = False     # this call returns the application's state

    def state(self, text: str) -> str:
        """A result that carries the application's state, led by what other agents did meanwhile."""
        self.looked = True
        if not self.others:
            return text
        return (f"note: another agent acted on {self.app} {self.others} time(s) since you last looked; what it "
                f"changed is in this result.\n{text}")


@contextmanager
def _turn(key: str, app: str, agent: str) -> Iterator[_Turn]:
    with _registry:
        seen = _seen.get((key, agent), 0)
        turn = _Turn(app, sum(1 for sequence, who in _acted.get(key, ()) if who != agent and sequence > seen))
    try:
        yield turn
    finally:
        with _registry:
            writes = _acted.setdefault(key, [])
            if turn.acted:
                writes.append((next(_sequence), agent))
                del writes[:-_KEPT_WRITES]
            if turn.looked:
                if len(_seen) >= _MAX_SEEN:
                    _seen.clear()
                _seen[(key, agent)] = writes[-1][0] if writes else 0


def forget(agent: str) -> None:
    """Drop what ``agent`` has seen, when it ends."""
    with _registry:
        for key in [key for key in _seen if key[1] == agent]:
            _seen.pop(key, None)
