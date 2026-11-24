"""What tells a loop that a busy conversation may have gone quiet.

A notice or a queued message for a conversation that is mid-turn waits
until it is not. Nothing is looked at on a timer for that: these sources
say when it may have happened, and the waiter checks then.

* ``TURN_ENDED_TOPIC``: a turn's cancel token was retired, or the server
  gave a run slot back.
* ``run_tables`` commits to ``executions`` or ``job_finalizations``: an
  execution or a Job reached another state (a background one ended).
* ``runs``: a Job was written (its status in ``jobs.json``).
"""
from __future__ import annotations

from typing import Callable

from openprogram.runtime.execution.run_control.tokens import TURN_ENDED_TOPIC

#: Run-table commits that can end a conversation's busy state.
IDLE_TABLES = frozenset({"executions", "job_finalizations"})


def subscribe_idle(callback: Callable[[], None]) -> Callable[[], None]:
    """Call ``callback()`` on the signalling thread whenever a conversation
    may have become idle; quick, and it must not raise. Returns the
    unsubscribe."""
    from openprogram.host._platform import changes
    from openprogram.runtime.jobs.store import RUNS_TOPIC

    def on_tables(tables: tuple[str, ...]) -> None:
        if not IDLE_TABLES.isdisjoint(tables):
            callback()

    unsubscribes = [
        changes.subscribe(TURN_ENDED_TOPIC, lambda _keys: callback()),
        changes.subscribe(RUNS_TOPIC, lambda _keys: callback()),
        changes.subscribe("run_tables", on_tables),
    ]

    def unsubscribe() -> None:
        for undo in unsubscribes:
            undo()

    return unsubscribe


__all__ = ["IDLE_TABLES", "TURN_ENDED_TOPIC", "subscribe_idle"]
