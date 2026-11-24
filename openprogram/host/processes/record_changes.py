"""Telling windows the process records changed, and the Worker a supervisor ended.

``processes.db`` is written by each command's supervisor, a process of its
own: its start, output and exit. The Worker watches the database
(:func:`watch_records`) and signals :data:`TOPIC`, which also reaches the
windows as a ``changed`` frame; a window showing a conversation's programs
reads them again then, instead of on a timer. A quiet running command
writes nothing.

The file is not keyed by conversation: one event says only that some record
changed.

A supervisor that ends normally writes its command's end first. One that is
killed or crashes cannot, so the Worker follows every live record's
supervisor (:func:`follow_supervisors`): the operating system announces its
exit (``process_exits``) on :data:`SUPERVISORS_TOPIC`, and a record it left
live turns ``unknown``.
"""
from __future__ import annotations

from openprogram.host._platform import changes

TOPIC = "processes"
#: Signalled, keyed ``<record id>/<supervisor pid>``, when a live record's
#: supervisor ends. Inside the Worker only.
SUPERVISORS_TOPIC = "processes.supervisors"

changes.publish(TOPIC)


def watch_records(store=None) -> None:
    """In the Worker: a write to the process records signals :data:`TOPIC`.
    Registering it again (the exit-notice loop does too) changes nothing."""
    from .store import ProcessStore

    path = (store or ProcessStore()).path
    changes.watch(path.parent, TOPIC, names={path.name, path.name + "-wal"})


def follow_supervisors(store, ended=()) -> int:
    """Mark the records whose supervisor ``ended`` (keys signalled on
    :data:`SUPERVISORS_TOPIC`), then follow the supervisor of every live
    record. Following one already followed changes nothing, and one that
    already ended is announced at once. Returns how many records turned
    ``unknown``."""
    from openprogram.host._platform.process_exits import watch_process
    from .store import process_identity

    lost = 0
    for key in ended:
        process_id, _, pid = key.rpartition("/")
        if process_id and pid.isdigit() and store.supervisor_ended(process_id, int(pid)):
            lost += 1
    for process_id, pid, identity in store.live_supervisors():
        watch_process(pid, identity, SUPERVISORS_TOPIC, f"{process_id}/{pid}", identify=process_identity)
    return lost


__all__ = ["SUPERVISORS_TOPIC", "TOPIC", "follow_supervisors", "watch_records"]
