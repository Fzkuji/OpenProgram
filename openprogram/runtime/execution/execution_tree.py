"""The executions one run is made of, rooted at the execution it started.

A run (a ``--print`` command, one turn of the Web UI) is admitted as one
execution. What it does can go on in others: the sub-agent Jobs it calls and
theirs in turn, and, when a Worker restart interrupts a chat turn, the turn
that continues it (admitted with the deterministic key
:func:`continuation_key`). The run's usage and transcript are those of this
whole tree, and of nothing else: not another branch of the same conversation
running at the same time, not the title or topic work the Worker starts
beside a finished turn.

Membership comes from the conversation's own scope
(:func:`~openprogram.runtime.execution.conversation_scope.conversation_execution_scope`),
which already places called Jobs under the execution that called them; this
module only roots it at an execution instead of the whole conversation.
"""
from __future__ import annotations

from typing import Any

#: The kind of the event a Worker restart writes on a chat turn it will
#: continue as a new turn (``execution.restart``).
RESTART_REQUESTED = "execution.restart.requested"
#: The kind of the event that answers one (``request_sequence``, ``outcome``).
RESTART_SETTLED = "execution.restart.settled"


def continuation_key(execution_id: str, sequence: int) -> str:
    """The admission key of the turn that continues ``execution_id`` after
    the restart intent written at event ``sequence``."""
    return f"restart:{execution_id}:{sequence}"


def continuation_ids(store: Any, execution: Any) -> list[str]:
    """The ids a continuation of ``execution`` would be admitted under, one
    per restart intent recorded on it, oldest first. Whether one was
    admitted is for the caller to check."""
    return [
        store.admission_execution_id(execution.session_id,
                                     continuation_key(execution.execution_id, event.sequence))
        for event in store.list_events(execution.execution_id)
        if event.kind == RESTART_REQUESTED
    ]


def execution_tree(execution_id: str, *, store: Any = None) -> frozenset[str]:
    """``execution_id`` and every execution of its run.

    The run's own descendants (children by ``parent_execution_id`` and the
    Jobs it called), and the continuation a Worker restart admitted for any
    of them, with that continuation's own descendants. An execution the
    store does not know yields just itself, so a caller's lookups still
    name the run.
    """
    from .conversation_scope import conversation_execution_scope

    if store is None:
        from . import default_store

        store = default_store()
    root = store.get_execution(execution_id)
    if root is None:
        return frozenset({execution_id})
    executions, parents = conversation_execution_scope(store, root.session_id)
    by_id = {execution.execution_id: execution for execution in executions}
    children: dict[str, set[str]] = {}
    for child, parent in parents.items():
        if parent is not None and parent != child:
            children.setdefault(parent, set()).add(child)
    members: set[str] = set()
    pending = [execution_id]
    while pending:
        current = pending.pop()
        if current in members:
            continue
        members.add(current)
        pending.extend(children.get(current, ()))
        record = by_id.get(current)
        if record is not None:
            pending.extend(key for key in continuation_ids(store, record) if key in by_id)
    return frozenset(members)


__all__ = ["RESTART_REQUESTED", "RESTART_SETTLED", "continuation_ids", "continuation_key", "execution_tree"]
