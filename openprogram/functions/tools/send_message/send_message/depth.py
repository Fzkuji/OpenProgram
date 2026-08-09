"""Spawn-chain depth tracking for send_message (and task()).

Depth of the current spawn chain (A→B→C…). Each send_message that
spawns increments it for the child turn; when it reaches
MAX_SPAWN_DEPTH further spawns are refused — the guard against A↔B /
runaway recursion (design §5.1). Set by the task runner on the child
turn (cross-thread) and by the sync path inline. task() shares this
counter with its own tighter cap (MAX_TASK_DEPTH).
"""
from __future__ import annotations

import contextvars

_spawn_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "send_message_spawn_depth", default=0,
)
MAX_SPAWN_DEPTH = 8


def current_spawn_depth() -> int:
    return _spawn_depth.get()


def set_spawn_depth(depth: int):
    """Bind the spawn depth for the current execution context (used by the
    task runner when starting a spawned child turn). Returns the token."""
    return _spawn_depth.set(depth)
