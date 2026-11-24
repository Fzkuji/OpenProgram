"""Telling windows that a durable write's receipt changed.

A window whose answer to a write was lost (the socket dropped, the reply
went missing) keeps the write's idempotency key and asks for its receipt
(``project_file_operation_status``, ``turn_operation_status``). It asks
once, then again only when told: here, when a project-file operation's
journal row becomes final (:mod:`.file_operations`) or a turn's revert or
reapply finishes (``interfaces/server/ws_actions/turn_files/history.py``),
:data:`TOPIC` is signalled with that key and reaches the windows as a
``changed`` frame. A window that was disconnected asks once after it
reconnects, since frames sent meanwhile are lost.
"""
from __future__ import annotations

from openprogram.host._platform import changes

TOPIC = "operation.receipts"

changes.publish(TOPIC)


def changed(key: object) -> None:
    """The receipt of the write with idempotency ``key`` changed."""
    if isinstance(key, str) and key:
        changes.signal(TOPIC, key)


__all__ = ["TOPIC", "changed"]
