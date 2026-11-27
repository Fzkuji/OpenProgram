"""send_message — the one way to reach an agent that already exists.

Deliver a message to an EXISTING agent → it runs one turn → its reply
comes back to the sender as a notice. ``to`` names the target:

  * ``"sid:head"``       — a branch. The node names the BRANCH, not a fork
                           point: delivery always lands on the branch's
                           current tip.
  * ``"<branch name>"``  — a named branch, or a helper by its description.
  * a conversation's id or exact title, or a saved Agent's name or id —
                           that conversation (the always-on handoff's
                           addressing, which this tool took over together
                           with ``agent(to=…)``).

Every message is a tracked Job (``runtime.conversation.peer_agents
.dispatch``) with an ``execution_id``; a busy target queues it and is never
interrupted. The chain limits are ``jobs.chain_budgets``': 4 hops
and 20 sends per conversation per hour, no approval between agents. How a
message is addressed, delivered and steered belongs to the conversation
runtime, which also drains the inbox at turn end; this tool is its
surface for the model.

Creating agents is the ``agent`` tool's job.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

import contextlib

import logging

from openprogram.programs._host.runtime import function

from openprogram.runtime.conversation.peer_agents.addressing import (
    AddressRefused,
    _parse_to,
    resolve_conversation,
    resolve_existing_target,
)
from openprogram.runtime.conversation.peer_agents.delivery import target_label
from openprogram.runtime.jobs.chain_budgets import (
    claim_send,
    closes_cycle,
    current_chain_ancestors,
    current_chain_generations,
    current_chain_messages,
    hops_left,
    max_hops,
    max_messages,
    max_sends_per_hour,
    release_send,
)
from .prompt import DESCRIPTION

_log = logging.getLogger(__name__)


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Current (session_id, turn_id, agent_id) for anchoring the send.

    Reads the dispatcher's ContextVars first (same as the ``agent``
    tool). ``_current_turn_id`` isn't always bound on every execution
    path (e.g. a followup turn, or some sub-call stacks), so when it's
    missing we fall back to the session's current head — that head IS a
    valid parent anchor, which is what callers actually need. This is
    what fixes the "no active parent turn" the model sometimes hit."""
    try:
        from openprogram.runtime.execution.run_control.binding import (
            _current_session_id,
        )
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    try:
        from openprogram.state.store import _current_turn_id
        aid = _current_turn_id.get()
    except Exception:
        aid = None
    agent_id = None
    if sid:
        try:
            from openprogram.runtime.context.session_db import default_db
            sess = default_db().get_session(sid) or {}
            agent_id = sess.get("agent_id")
            if not aid:
                # Fall back to the session head as the parent anchor.
                aid = sess.get("head_id")
        except Exception:
            agent_id = None
    return sid, aid, agent_id


def _resolve_target(to: str, sid: str) -> tuple[str, object]:
    """``to`` as a branch (``SID:HEAD``, a branch or helper name), else as a
    conversation (its id or exact title), else as a saved Agent's name or
    id, which reaches that Agent's one conversation."""
    status, payload = resolve_existing_target(to, sid)
    if status == "ok":
        return status, payload
    try:
        target = resolve_conversation(sid, to)
    except AddressRefused as exc:
        return "error", (f"{payload} As a conversation or Agent: {exc} To start a "
                         "saved Agent that has no conversation, use agent(agent=…).")
    except Exception:  # noqa: BLE001 — an unreadable store leaves the branch answer
        _log.debug("conversation addressing failed for %r", to, exc_info=True)
        return "error", payload
    head = target.get("head_id")
    if not head:
        # A title match comes from a listing row, which may not carry the head.
        try:
            from openprogram.runtime.context.session_db import default_db
            head = (default_db().get_session(target.get("id")) or {}).get("head_id")
        except Exception:  # noqa: BLE001
            head = None
    if not head:
        # A conversation nobody has written in yet: the message is its
        # first turn (the always-on hand-off reached these too).
        return "ok", (str(target["id"]), "")
    return resolve_existing_target(f"{target['id']}:{head}", sid)


def _send_message_impl(
    message: str,
    to: str = "",
    agent_id: str = "",
    notify_when_idle: bool = False,
) -> str:
    """Implementation body, pulled out of the @function binding so tests
    can drive it with their own ContextVars."""
    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[send_message error] no active parent turn — must be called "
            "from inside an assistant turn (the dispatcher sets the session "
            "+ turn ContextVars on entry)."
        )
    from openprogram.runtime.permissions import refusal
    from openprogram.runtime.permissions.authority import authority_from_message
    caller_authority = authority_from_message(sid, aid)

    # Chain budget: the older message budget when someone set it, and the
    # hop limit (4) taken over from handoff. A message creates no agent, so
    # the generation count travels through untouched.
    messages = current_chain_messages()
    generations = current_chain_generations()
    limit = max_messages()
    if limit and messages >= limit:
        raise refusal.agent_budget("send_message", "messages", limit,
                                   "finish the work here instead of delegating further")
    if not hops_left():
        raise refusal.agent_budget("send_message", "hops", max_hops(),
                                   "finish the work here and answer with what you have")

    chosen_agent = (agent_id or "").strip() or parent_agent or "main"
    kind, _, _ = _parse_to(to)
    if kind == "spawn_syntax":
        return (
            f"[send_message error] to={to!r} is not a valid target — "
            "send_message only talks to EXISTING agents. To create a "
            "new agent, use the `agent` tool."
        )
    if not (to or "").strip():
        return (
            "[send_message error] `to` is required — an agent's name, a "
            "conversation's title or id, or a SID:HEAD address (see list_agents)."
        )

    # Resolution normalizes stale heads onto the branch tip BEFORE the
    # self-target guard below, so an old head of the sender's own chain
    # still trips the loop check.
    status, payload = _resolve_target(to.strip(), sid)
    if status != "ok":
        return f"[send_message error] {payload}"
    run_session, branch_from = payload  # type: ignore[misc]
    if run_session == sid and branch_from == aid:
        return (
            "[send_message refused] target is your own current turn — "
            "that's a direct loop. Pick a different agent, or use the "
            "`agent` tool to start a new one."
        )
    if run_session != sid and closes_cycle(run_session, branch_from):
        # A→B→A: the conversation is already waiting on this chain, and
        # its answer reaches it on its own (the hand-off's cycle rule).
        return (
            f"[send_message refused] {to!r} is already waiting on this chain of "
            "messages; your final reply goes back to it on its own. Answer "
            "there instead of sending it a new message."
        )
    if run_session != sid and not (agent_id or "").strip():
        # Another conversation answers as its own Agent, as a handoff did.
        with contextlib.suppress(Exception):
            from openprogram.runtime.context.session_db import default_db
            chosen_agent = (default_db().get_session(run_session) or {}).get("agent_id") or chosen_agent

    # A watch, not a message: one notice when the target goes idle.
    if notify_when_idle and not (message or "").strip():
        return _watch(sid, aid, parent_agent or chosen_agent, run_session, branch_from,
                      messages, generations, caller_authority)

    if not claim_send(sid):
        raise refusal.agent_budget("send_message", "sends this hour", max_sends_per_hour(),
                                   "wait, or finish the work here")
    from openprogram.runtime.conversation.peer_agents.dispatch import deliver
    # Who waits on the message from here on: this chain's waiters and,
    # across conversations, this one.
    ancestors = [*current_chain_ancestors()]
    if run_session != sid and sid not in ancestors:
        ancestors.append(sid)
    out = deliver(
        message=message, sid=sid, aid=aid, parent_agent=parent_agent,
        chosen_agent=chosen_agent, run_session=run_session, target_tip=branch_from,
        caller_authority=caller_authority, messages=messages, generations=generations,
        ancestors=ancestors,
    )
    if out.startswith("[send_message error]") or out.startswith("[send_message] duplicate"):
        release_send(sid)
    return out


def _watch(sid, aid, agent_id, run_session, branch_from, messages, generations, authority) -> str:
    from openprogram.runtime.conversation.peer_agents import inbox
    from openprogram.runtime.jobs import notices
    address = f"{run_session}:{branch_from}"
    if not inbox.target_busy(run_session, branch_from, sid):
        reply = notices.tip_reply(run_session, branch_from) or "(no text)"
        # The sender has this reply now; a notice for the job that wrote it
        # would only repeat it.
        notices.consume_reply(sid, run_session, branch_from)
        return f"[idle] {address} is not running anything. Its latest reply:\n{reply}"
    if not notices.watch(
        sender_session_id=sid, target_session_id=run_session, target_head_id=branch_from,
        label=target_label(run_session, branch_from, sid), agent_id=agent_id, chain_messages=messages,
        chain_generations=generations, authority=authority,
    ):
        return "[send_message error] this session cannot hold a watch"
    return (
        f"[watching] {address} is busy. You will get one notice with its "
        "latest reply when it goes idle. You are not blocked — continue."
    )


@function(
    name="send_message",
    description=DESCRIPTION,
    toolset=["core"],
)
def send_message(
    to: str,
    message: str = "",
    notify_when_idle: bool = False,
) -> str:
    """Send a message to an existing agent; its reply comes back to the
    sender automatically when the target finishes.

    Args:
        to: Who to reach: a saved Agent's name (reaches its conversation),
            a conversation's title or id, a helper's name, or a SID:HEAD
            address from list_agents.
        message: What to tell the target. Leave it empty together with
            ``notify_when_idle`` to only watch the target.
        notify_when_idle: With an empty message, send nothing and get
            one notice carrying the target's latest reply when it next
            goes idle (it answers at once if the target is idle now).
    """
    return _send_message_impl(
        message=message,
        to=to,
        notify_when_idle=notify_when_idle,
    )
