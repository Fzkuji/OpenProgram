"""Two budgets that bound one collaboration chain.

A chain is everything that grows out of one user turn: spawns
(``agent``), messages (``send_message``), and task dispatches
(``agent(to=…)``). Every hop carries a counter forward, so A→B→A
back-and-forth costs the same as A→B→C.

  * **spawn budget** (``agent.max_spawn_depth``, default 1) — how many
    generations of NEW agents may be created. 1 = the main agent
    spawns workers, a worker does the work itself.
  * **message budget** (``agent.max_messages``, default 8) — how many
    messages the whole chain may pass, whatever the tool.

Either set to 0 means no limit at all. The counter lives in a
ContextVar set by the task runner on the child turn (cross-thread) and
by the sync spawn path inline.
"""
from __future__ import annotations

import contextvars

_chain_messages: contextvars.ContextVar[int] = contextvars.ContextVar(
    "send_message_chain_messages", default=0,
)
MAX_MESSAGES = 8


def current_chain_messages() -> int:
    return _chain_messages.get()


def set_chain_messages(count: int):
    """Bind the chain's message count for the current execution context
    (used by the task runner when starting a spawned child turn).
    Returns the token."""
    return _chain_messages.set(count)


def config_limit(key: str, default: int) -> int:
    """``agent.<key>`` from config.json, falling back to ``default``.
    0 means "no limit" and is honoured as written."""
    try:
        from openprogram import setup as _setup
        v = (_setup._read_config().get("agent") or {}).get(key)
        return max(0, int(v)) if v not in (None, "") else default
    except Exception:
        return default


def max_messages() -> int:
    return config_limit("max_messages", MAX_MESSAGES)


def budget_left(limit: int) -> bool:
    """Is there room left under ``limit`` for one more hop? 0 = no
    limit, so always yes."""
    return not limit or current_chain_messages() < limit


def delegation_budget_left() -> bool:
    """Whether ``agent`` / ``task_output`` / ``task_stop`` still belong
    in the tool list: true while EITHER budget has room, because
    ``agent`` both spawns (spawn budget) and dispatches with ``to=``
    (message budget).

    Deliberately binary — present or absent, never a per-depth variant
    of the tool set. Tool definitions must be byte-identical across
    turns to hit the provider's prompt cache, and one set per depth
    value would shred it. Between "some budget left" and "none left"
    the runtime guards refuse the calls that overrun.
    """
    from openprogram.functions.tools.agent.agent.agent import max_spawn_depth
    return budget_left(max_spawn_depth()) or budget_left(max_messages())
