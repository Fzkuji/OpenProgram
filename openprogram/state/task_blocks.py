"""Which task and step each block of a reply belongs to.

A reply's blocks are read in order. A ``task_start`` or ``task_step`` call,
or any call whose ``task_progress`` moved the task
(``programs/tools/planning/todo/progress.py``), is a boundary: the call
says what the reply belongs to from there on (its ``task_effect``), and
every block after it, and the call itself, carries that as ``task`` and
``step``. Blocks before the first call belong to what
the reply started with (``initial``): nothing, except in a Goal turn that
continues the open task.

Ownership is by position alone. A block is never moved to another step
because of when it ran, so the stored blocks give the same answer whether
the reply is streaming, finished or read back later.

    {"type": "text", "text": "I'll look first."}
    {"type": "tool", "tool": "task_start", "task": "T3", "step": "14",
     "task_effect": {"task": "T3", "step": "14"}}
    {"type": "tool", "tool": "grep", "task": "T3", "step": "14"}
    {"type": "tool", "tool": "task_step", "task_effect": {"task": None}}
    {"type": "text", "text": "Done."}

A task without steps stamps ``task`` alone. A steering message is the
user's and is never stamped. An effect comes from the tool's result
(``details["task"]``); a call with no effect (it only added or dropped
steps, or it never ran) leaves ownership as it was. A task call's effect
comes when it returns; a ``task_progress`` effect is applied before its
call runs and comes with the call.
"""
from __future__ import annotations

from typing import Any, Optional

#: The tools whose calls move a reply from one task or step to another.
TASK_TOOLS = frozenset({"task_start", "task_step"})

_STAMPED = ("text", "thinking", "tool")

Owner = tuple[Optional[str], Optional[str]]


def _is_call(block: Any) -> bool:
    """A tool block: any of them can carry an effect."""
    return isinstance(block, dict) and block.get("type") == "tool"


def clean_effect(effect: Any) -> Optional[dict[str, Any]]:
    """An effect as it is stored: ``{"task": id, "step"?: id}`` or
    ``{"task": None}``; None for anything else."""
    if not isinstance(effect, dict) or "task" not in effect:
        return None
    task = effect.get("task")
    if not task:
        return {"task": None}
    step = effect.get("step")
    return {"task": str(task), **({"step": str(step)} if step else {})}


def _owner(effect: Optional[dict[str, Any]]) -> Owner:
    effect = clean_effect(effect)
    if effect is None or not effect.get("task"):
        return None, None
    return effect["task"], effect.get("step")


def owner_dict(owner: Owner) -> dict[str, str]:
    """``{"task", "step"}`` with only the keys that are set."""
    task, step = owner
    if not task:
        return {}
    return {"task": task, **({"step": step} if step else {})}


def effects_of(blocks: list[dict]) -> dict[str, dict[str, Any]]:
    """``{tool_call_id: effect}`` of the calls in ``blocks`` that had one."""
    found: dict[str, dict[str, Any]] = {}
    for block in blocks:
        if not _is_call(block) or not block.get("tool_call_id"):
            continue
        effect = clean_effect(block.get("task_effect"))
        if effect is not None:
            found[block["tool_call_id"]] = effect
    return found


def stamp(blocks: list[dict], effects: dict[str, dict[str, Any]],
          initial: Optional[dict[str, Any]] = None) -> Optional[int]:
    """Write ``task``, ``step`` and ``task_effect`` on ``blocks`` in place.

    ``effects`` names the calls that moved the reply, by call id; a
    stamp or effect a block should no longer have is removed. Returns the
    index of the first block that changed, or None.
    """
    owner = _owner(initial)
    first: Optional[int] = None
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        wanted: dict[str, Any] = {}
        if _is_call(block):
            effect = effects.get(block.get("tool_call_id") or "")
            if effect is not None:
                owner = _owner(effect)
                wanted["task_effect"] = clean_effect(effect)
        if block.get("type") in _STAMPED:
            wanted.update(owner_dict(owner))
        changed = False
        for key in ("task", "step", "task_effect"):
            if key in wanted:
                if block.get(key) != wanted[key] or key not in block:
                    block[key] = wanted[key]
                    changed = True
            elif key in block:
                del block[key]
                changed = True
        if changed and first is None:
            first = index
    return first


def owner_after(blocks: list[dict], initial: Optional[dict[str, Any]] = None) -> dict[str, str]:
    """What the next block of the reply belongs to, as ``{"task", "step"}``
    (empty when it belongs to no task)."""
    owner = _owner(initial)
    for block in blocks:
        if _is_call(block):
            effect = clean_effect(block.get("task_effect"))
            if effect is not None:
                owner = _owner(effect)
    return owner_dict(owner)
