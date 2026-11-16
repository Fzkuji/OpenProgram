"""Whether an admitted turn carries its conversation's own effects.

The flag is frozen on the turn's request at admission, so the execution
control plane (which reads the stored request) and the call core (which
reads the live ``TurnRequest``) answer the same way. It lives here, below
the call core, because both layers read it; ``openprogram.runtime.agent.
turn_request`` re-exports it.
"""
from __future__ import annotations

from typing import Any


def owns_conversation_effects(request: Any) -> bool:
    """Whether a turn carries the conversation's own effects.

    ``request`` is a request object or its stored dict. The flag frozen at
    admission decides; never the conversation's current selection, so a
    turn keeps its standing across a pause, a resume and a Worker restart.
    """
    def read(key: str):
        if isinstance(request, dict):
            return request.get(key)
        return getattr(request, key, None)

    frozen = read("owns_conversation_effects")
    if frozen is not None:
        return bool(frozen)
    # A request admitted before the flag was frozen.
    return not (read("side_branch") or read("background"))
