"""Two budgets that bound one collaboration chain, one counter each.

A chain is everything that grows out of one user turn: spawns
(``agent``), messages (``send_message``), and task dispatches
(``agent(to=…)``).

  * **generation budget** (``agent.max_spawn_depth``, default 1) — how
    many generations of NEW agents the chain has created. Only ``agent``
    creating an agent adds to it. 1 = the main agent spawns workers, a
    worker does the work itself.
  * **message budget** (``agent.max_messages``, default 8) — how many
    messages the chain has passed, whatever the tool. A spawn, a
    ``send_message`` delivery, an ``agent(to=…)`` dispatch and a result
    flowing back each cost one, so A→B→A costs the same as A→B→C.

Reading a result spends a message and no generation. The reply turn is
the DISPATCHER's turn, so it runs at the dispatcher's generation count
(``Task.caller_chain_generations``, re-bound by
``TaskRunner._dispatch_followup``) and the agent that collected a batch
of results can create the next batch. Counting both on one counter is
what took that away: a coordinator that read one worker's result
inherited the worker's count of 1 and every further ``agent`` call in
that chain was refused, which is the shape of nearly every multi-agent
run — dispatch a batch, read the results, dispatch the next.

Either limit set to 0 means no limit at all. Both counters live in
ContextVars set by the task runner on the child turn (cross-thread) and
by the sync spawn path inline.

A third budget bounds the chain sideways instead of downward and lives
with the tool it guards: ``agent.max_spawn_fanout`` (default 8) caps
the agents ONE turn may create, because a spawn hands its count to the
child and leaves the parent's own untouched, so nothing here counts
siblings. See ``programs/tools/agent/agent/agent.MAX_SPAWN_FANOUT``.
"""
from __future__ import annotations

import contextvars

_chain_messages: contextvars.ContextVar[int] = contextvars.ContextVar(
    "send_message_chain_messages", default=0,
)

_chain_generations: contextvars.ContextVar[int] = contextvars.ContextVar(
    "send_message_chain_generations", default=0,
)

# Message budget: how many messages one chain may pass, whatever the
# tool. The reply hop re-binds the finished task's count rather than
# incrementing it (task.runner._dispatch_followup), so one A→B→A
# round trip costs 1 and 8 buys eight round trips.
#
# openclaw is the only reference implementation that counts the same
# thing and it is the anchor for this number: its agent-to-agent flow
# runs a ping-pong loop capped at 5 alternating replies by default, 20
# at most, 0 to disable (agents/tools/sessions-send-helpers.ts:15-16).
# 8 sits between its default and its ceiling, which is where we want to
# be: our counter is charged for more than conversation — spawns and
# agent(to=…) dispatches spend it too — so an equal-strength setting has
# to be at least openclaw's. The other seven have nothing to compare
# against; codex-cli V2 is the cautionary case, with sibling agents able
# to address each other directly and no counter anywhere.
#
# Raise it for long negotiations between two agents (openclaw's own
# ceiling of 20 is a defensible upper bound), lower it if chains are
# spending their budget on acknowledgements instead of work. 0 removes
# the limit. Worth knowing when tuning: openclaw also injects "turn N of
# M" into each prompt and gives the agents a token to end early, so its
# agents can stop themselves; ours only find out by being refused.
MAX_MESSAGES = 8


def current_chain_messages() -> int:
    return _chain_messages.get()


def set_chain_messages(count: int):
    """Bind the chain's message count for the current execution context
    (used by the task runner when starting a spawned child turn).
    Returns the token."""
    return _chain_messages.set(count)


def current_chain_generations() -> int:
    return _chain_generations.get()


def set_chain_generations(count: int):
    """Bind the chain's generation count for the current execution
    context. The task runner binds the child's count on a spawned turn
    and the dispatcher's count on the reply turn; the sync spawn path
    binds the child's inline. Returns the token."""
    return _chain_generations.set(count)


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


def budget_left(used: int, limit: int) -> bool:
    """Is there room left under ``limit`` for one more? 0 = no limit, so
    always yes."""
    return not limit or used < limit


def delegation_budget_left() -> bool:
    """Whether delegation tools still belong in the tool list: true
    while the MESSAGE budget has room.

    Every form of delegation hands a message over — a spawn, an
    ``agent(to=…)`` dispatch and a ``send_message`` each cost one — so a
    chain out of messages can do nothing with these three whatever its
    generation count reads. The reverse does not hold: a chain out of
    generations still dispatches work to agents that already exist, so
    the generation budget refuses spawns at runtime and never removes
    the tool.

    Deliberately binary — present or absent, never a per-count variant
    of the tool set. Tool definitions must be byte-identical across
    turns to hit the provider's prompt cache, and one set per counter
    value would shred it.
    """
    return budget_left(current_chain_messages(), max_messages())
