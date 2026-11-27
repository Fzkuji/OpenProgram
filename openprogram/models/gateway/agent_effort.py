"""The reasoning effort picked in OpenProgram for an agent whose own config can't carry one.

After yetone/magpie ``internal/provider/agenteffort.go`` and the agent's pick
in ``internal/gateway/gateway.go`` (MIT License, Copyright (c) yetone). An
agent whose config has no effort for the gateway's models (Antigravity CLI:
its ``/effort`` is turned away for every custom model, and ``settings.json``
has no key for it; Cursor Private Inference, whose environment can't say
one; Antigravity, for a model its picker offers no levels of) has one picked in its row, a kept choice field ``effort`` that outlives
disconnecting (``kept_picks.py``), and the gateway asks the agent's
requests to a model that reasons for it, in place of the level the agent
sent. A level the request names itself (agy's ``--effort``, or ``/effort``
on a Google model it knows) is the user's pick of the moment and wins. None
picked, the agent's requests go as it asks.
"""
from __future__ import annotations

from typing import Any, Optional

#: The efforts an agent can be asked for, weakest first (magpie ``MemberEfforts``).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
#: The choice field that holds an agent's effort.
FIELD = "effort"


def agent_effort(agent: Optional[str]) -> str:
    """The effort ``agent``'s requests are asked for, ``""`` for none: its
    kept ``effort`` choice (``kept_picks.py``)."""
    if not agent:
        return ""
    from .kept_picks import kept
    level = kept(agent).get(FIELD, "").lower()
    return level if level in EFFORTS else ""


def picked_efforts(models: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The efforts to offer for an agent on ``models`` (magpie
    ``pickedEfforts``), as choices: the levels they take, low, medium and
    high for models that reason with no levels named; none when nothing reasons."""
    has: set[str] = set()
    reasons = False
    for row in models:
        reasons = reasons or bool(row.get("reasoning"))
        has.update(str(level).lower() for level in row.get("thinking_levels") or [])
    levels = [level for level in EFFORTS if level in has]
    if not levels and reasons:
        levels = ["low", "medium", "high"]
    return [(level, level) for level in levels]


def effort_options(agent: str) -> list[tuple[str, str]]:
    """The effort choices of an agent's row (magpie ``pickedEfforts``),
    offered only while it is on OpenProgram (Cursor Private Inference,
    Antigravity)."""
    from . import stash
    try:
        if stash.entry(agent) is None:
            return []
    except stash.StashUnreadable:
        return []
    from .conversion import enabled_model_refs
    return picked_efforts(enabled_model_refs())


def applied(agent: Optional[str], model: Any, requested: Optional[str], *, level_asked: bool = False) -> Optional[str]:
    """The reasoning level an agent's request goes with: the agent's pick, on
    a model that reasons, unless the request named a level of its own
    (``level_asked``); else ``requested`` as it was."""
    pick = agent_effort(agent)
    if not pick or level_asked or not getattr(model, "reasoning", False):
        return requested
    from .conversion import reasoning_level
    return reasoning_level(model, pick) or requested
