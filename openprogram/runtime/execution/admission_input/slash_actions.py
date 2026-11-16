"""Which slash action a chat turn's request carries out.

A slash action (``/spawn``, ``/merge``) is carried out instead of a model
round. Recovery reads it from the admitted request, so the reader lives at
the execution layer; the turn engine and ``TurnRequest`` use the same one.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

#: The slash actions a chat turn can carry out instead of a model round.
SLASH_ACTIONS = frozenset({"spawn", "merge"})


def slash_action_of(request: Any) -> Optional[str]:
    """The slash action a turn request (a ``TurnRequest`` or its stored
    mapping) carries out, or ``None`` for a model turn. A request admitted
    before ``slash_action`` existed named it in ``interaction``."""
    if request is None:
        return None
    if isinstance(request, Mapping):
        values = (request.get("slash_action"), request.get("interaction"))
    else:
        values = (getattr(request, "slash_action", None), getattr(request, "interaction", None))
    for value in values:
        if value in SLASH_ACTIONS:
            return value
    return None


__all__ = ["SLASH_ACTIONS", "slash_action_of"]
