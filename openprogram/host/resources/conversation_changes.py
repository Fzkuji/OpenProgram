"""Telling a conversation's windows that its resources list changed.

The list (``GET /api/session/{id}/resources``) is made of the machines and
desktops a call operates (:mod:`.session`), the browser's pages, the
applications bound to the conversation and the state of the runs that own
them. Whatever changes one of these signals :data:`TOPIC` with the
conversations that list it, and a window showing one of them reads the list
again; nothing reads it on a timer.

A run of a sub-agent or of a called Job belongs to another session, and its
pages and machines are listed by the conversation that started it: a change
is signalled for the run's own session and for every session above it.

Another process (an agentic subprocess, the CLI) writes the same
``session-resources.db``. It cannot reach this process's listeners, so after
each write it replaces :data:`MARKER` beside the database, and the Worker
watches that one file. The database itself is not watched: SQLite rewrites
it and its log for the Worker's own reads and checkpoints too.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

from openprogram.host._platform import changes

TOPIC = "conversation.resources"
#: Replaced by a process other than the Worker after it changed the list.
MARKER = "session-resources.changed"

_log = logging.getLogger(__name__)

changes.publish(TOPIC)


def changed(*sessions: str | None, stored: bool = True) -> None:
    """The resources of ``sessions`` changed in this process. ``stored``
    is false for a change only the Worker sees (a run's lifecycle, which
    the Worker follows for every process), so no mark is left."""
    keys = sorted({session for session in sessions if session})
    if keys:
        changes.signal(TOPIC, *keys)
    if stored:
        _tell_the_worker()


def changed_everywhere() -> None:
    """Something changed whose conversations are not known here."""
    changes.signal(TOPIC)
    _tell_the_worker()


def changed_for_execution(execution_id: str | None, *also: str | None) -> None:
    """A change owned by a run: tell its session and the sessions above it
    (the runtime hands the chain down), and ``also``."""
    sessions = list(also)
    if execution_id:
        try:
            from openprogram.host import runtime_facts
            sessions.extend(runtime_facts.sessions_above_execution(execution_id))
        except Exception:  # noqa: BLE001 - the named sessions are still told
            _log.debug("sessions above the run unreadable", exc_info=True)
    changed(*sessions)


def _tell_the_worker() -> None:
    """In a process that is not the Worker, leave the mark the Worker watches."""
    if changes.file_events_available():
        return
    try:
        from openprogram.state.paths import get_state_dir
        from openprogram.state.store.session.files.atomic_file import atomic_write_text

        atomic_write_text(get_state_dir() / MARKER, f"{time.time()}\n")
    except Exception:  # noqa: BLE001 - the window reads again when it is next shown
        _log.debug("resources change mark not written", exc_info=True)


def watch_other_processes() -> None:
    """In the Worker: a mark another process leaves counts as a change of
    every conversation's resources."""
    from openprogram.state.paths import get_state_dir

    changes.watch(get_state_dir(), TOPIC, names={MARKER})


def browser_page_sessions(db, resource_ids: Iterable[str]) -> list[str]:
    """The conversations that list these pages, read in the writer's own
    connection."""
    ids = [str(item) for item in resource_ids if item]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    found: set[str] = set()
    for row in db.execute(
        "SELECT session_id, conversation_session_id FROM browser_resource_associations "
        f"WHERE resource_id IN ({marks})", ids,
    ):
        found.update(value for value in (row[0], row[1]) if value)
    return sorted(found)


__all__ = [
    "MARKER", "TOPIC", "browser_page_sessions", "changed", "changed_everywhere",
    "changed_for_execution", "watch_other_processes",
]
