"""The budgets that bound one collaboration chain.

A chain is everything that grows out of one user turn: spawns
(``agent``) and messages to existing agents (``send_message``).

  * **generation budget** (``agent.max_spawn_depth``, 1 by default) — how
    many generations of NEW agents the chain has created. Only ``agent``
    creating an agent adds to it. 1 = the main agent spawns helpers, a
    helper does the work itself (Codex ``DEFAULT_AGENT_MAX_DEPTH=1``,
    Hermes, OpenCode and Kimi Code all stop at one level).
  * **hop budget** (``agent.max_hops``, 4) — how many times the chain has
    passed a message on with ``send_message``; replies coming back do not
    count. Taken over from the always-on handoff's ``MAX_HOPS``.
  * **send rate** (``agent.max_sends_per_hour``, 20) — messages one
    conversation may send to other agents per hour, also from handoff.
  * **message budget** (``agent.max_messages``, off by default; the older
    form, still honoured when set) — how many messages the chain has
    passed, whatever the tool. The counter behind it is also the hop
    count: a spawn and a ``send_message`` delivery each add one.

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
with the tool it guards: ``agent.max_spawn_fanout`` (off by default) caps
the agents ONE turn may create, because a spawn hands its count to the
child and leaves the parent's own untouched, so nothing here counts
siblings. See ``programs/tools/agent/agent/agent.MAX_SPAWN_FANOUT``.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time

_log = logging.getLogger(__name__)

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
# spending their budget on acknowledgements instead of work. 0, the default, removes
# the limit. Worth knowing when tuning: openclaw also injects "turn N of
# M" into each prompt and gives the agents a token to end early, so its
# agents can stop themselves; ours only find out by being refused.
MAX_MESSAGES = 0


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
        from openprogram.state import config as user_config
        v = (user_config.read_config().get("agent") or {}).get(key)
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
    """Whether the ``agent`` tool belongs in this turn's tool list.

    Gone once the chain is out of generations (a helper at the default
    depth 1 has no ``agent`` tool, as in Codex, Hermes and OpenCode, where
    the deepest level never sees the spawn tool), and gone while the older
    message budget, when someone set it, is spent.

    A forked helper keeps the tool so its tool list stays byte-identical
    to its parent's and the provider's prompt cache is shared (Claude
    Code's fork does the same); the spawn is refused at run time instead.

    Deliberately binary — present or absent, never a per-count variant
    of the tool set. Tool definitions must be byte-identical across
    turns to hit the provider's prompt cache, and one set per counter
    value would shred it.
    """
    if not budget_left(current_chain_messages(), max_messages()):
        return False
    if forked_turn():
        return True
    return budget_left(current_chain_generations(), max_spawn_depth())


# --- generations -------------------------------------------------------

# 1, as Codex (DEFAULT_AGENT_MAX_DEPTH=1), Hermes, OpenCode and Kimi Code:
# the main agent delegates, a helper does the work. Claude Code's 3 suits
# a person watching synchronous nesting; unattended background helpers
# that can recurse are the expensive shape on the board.
MAX_SPAWN_DEPTH = 1


def max_spawn_depth() -> int:
    """``agent.max_spawn_depth`` from config; 0 = unlimited."""
    return config_limit("max_spawn_depth", MAX_SPAWN_DEPTH)


_forked: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_forked_turn", default=False,
)


def forked_turn() -> bool:
    """Whether this turn is a helper forked with the conversation
    (``start_from="fork"``)."""
    return _forked.get()


def set_forked_turn(value: bool):
    """Bind whether the current turn is a forked helper; returns the token."""
    return _forked.set(bool(value))


# --- hops and send rate ------------------------------------------------

# From the always-on handoff, which ran these limits before send_message
# took its job: four hops, twenty sends per sender per hour.
MAX_HOPS = 4
MAX_SENDS_PER_HOUR = 20
SEND_WINDOW_S = 3600.0

_send_lock = threading.Lock()
# Sender sessions whose window was read from disk; ``_sends`` then holds it.
_sends: dict[str, list[float]] = {}
_SENDS_FILE = "sends.json"


def max_hops() -> int:
    return config_limit("max_hops", MAX_HOPS)


def max_sends_per_hour() -> int:
    return config_limit("max_sends_per_hour", MAX_SENDS_PER_HOUR)


def hops_left() -> bool:
    """Whether this chain may pass one more message on."""
    return budget_left(current_chain_messages(), max_hops())


def _sends_path(sender_session_id: str):
    """The sender session's ``sends.json``, or None when it has no folder
    (the window is then kept in memory only)."""
    try:
        from openprogram.runtime.context.session_db import default_db
        sessions = default_db()
        if not sessions._session_dir(sender_session_id).exists():  # noqa: SLF001 — as notices
            return None
        return sessions.state_file(sender_session_id, _SENDS_FILE)
    except Exception:  # noqa: BLE001 - no store: memory only
        return None


def _read_window(sender_session_id: str) -> list[float]:
    """The sender's send times. The caller holds ``_send_lock``."""
    if sender_session_id in _sends:
        return _sends[sender_session_id]
    times: list[float] = []
    path = _sends_path(sender_session_id)
    if path is not None and path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            times = [float(t) for t in (blob.get("sent") or []) if isinstance(t, (int, float))]
        except Exception:  # noqa: BLE001 - an unreadable file starts an empty window
            times = []
    _sends[sender_session_id] = times
    return times


def _write_window(sender_session_id: str, times: list[float]) -> None:
    """Keep the window in memory and in the sender's session folder, so a
    Worker restart does not start a new hour. The caller holds ``_send_lock``."""
    _sends[sender_session_id] = times
    if len(_sends) > 2000:
        for key in list(_sends)[:500]:
            if key != sender_session_id:
                _sends.pop(key, None)
    path = _sends_path(sender_session_id)
    if path is None:
        return
    try:
        path.write_text(json.dumps({"version": 1, "sent": times}), encoding="utf-8")
    except OSError:
        _log.warning("send window of %s not saved", sender_session_id, exc_info=True)


def claim_send(sender_session_id: str, *, now: float | None = None) -> bool:
    """Count one send for ``sender_session_id``; False when the hour's
    allowance is spent (nothing is counted then). The window is saved in
    the sender's session folder (``sends.json``), so a Worker restart keeps
    the hour it is in."""
    limit = max_sends_per_hour()
    if not limit:
        return True
    at = time.time() if now is None else now
    with _send_lock:
        recent = [t for t in _read_window(sender_session_id) if at - t < SEND_WINDOW_S]
        if len(recent) >= limit:
            _write_window(sender_session_id, recent)
            return False
        recent.append(at)
        _write_window(sender_session_id, recent)
    return True


def release_send(sender_session_id: str) -> None:
    """Give back the last send counted for a message that was not sent."""
    with _send_lock:
        recent = list(_read_window(sender_session_id))
        if recent:
            recent.pop()
            _write_window(sender_session_id, recent)


# --- cycles ------------------------------------------------------------

# The conversations (session ids) and branches (``SID:HEAD``) a message
# passed through to reach this turn, oldest first: those waiting on it.
# From the always-on handoff's ``ancestors``: a message to one of them
# closes a loop, which it refused when it formed instead of letting the
# hop count end it.
_chain_ancestors: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "send_message_chain_ancestors", default=(),
)


def current_chain_ancestors() -> tuple[str, ...]:
    return _chain_ancestors.get()


def set_chain_ancestors(ancestors):
    """Bind the conversations waiting above this turn; returns the token."""
    return _chain_ancestors.set(tuple(str(a) for a in ancestors or () if a))


def closes_cycle(target_session_id: str, target_tip: str, ancestors=None) -> bool:
    """Whether a message to ``target_session_id:target_tip`` goes back to a
    conversation or branch already waiting on this chain (A→B→A)."""
    waiting = set(current_chain_ancestors() if ancestors is None else ancestors)
    return bool(waiting) and (
        target_session_id in waiting or f"{target_session_id}:{target_tip}" in waiting)


# --- helpers -----------------------------------------------------------

_helper: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_helper_turn", default=False,
)


def helper_turn() -> bool:
    """Whether this turn is a helper started with ``agent`` (not a message
    delivered to an existing conversation)."""
    return _helper.get()


def set_helper_turn(value: bool):
    """Bind whether the current turn is a helper; returns the token."""
    return _helper.set(bool(value))
