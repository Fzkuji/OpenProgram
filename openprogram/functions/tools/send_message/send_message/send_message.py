"""send_message — the single branch-to-branch communication primitive.

Deliver a message to an EXISTING branch → trigger that branch to run
one turn → its reply auto-returns to the sender. ``to`` names the
branch:

  * ``"sid:head"``       — deliver to an existing branch. The node names
                           the BRANCH, not a fork point: delivery always
                           lands on the branch's current tip, so a stale
                           head (the branch ran more turns since) is
                           still a valid address.
  * ``"<branch name>"``  — address a named branch directly.

Creating agents is the ``agent`` tool's job (spawn = ``agent(...)``,
fork off a node = ``agent(context="SID:MSG_ID")``); send_message only
talks to branches that already exist.

Async by default (``wait=False``): returns immediately with a delivery id;
the target runs in the background and its reply comes back to the sender
automatically (the task runner's followup). ``wait=True`` blocks for the
reply.

Design: docs/reference/design/runtime/agent-collaboration.md. This
module is the entry point: the @function binding plus the main delivery
flow. Concern-specific pieces live alongside: ``prompt.py`` (LLM-facing
description), ``addressing.py`` (`to` parsing + branch-name lookup),
``delivery.py`` (receipt header, busy-target inbox, reply clipping),
``depth.py`` (spawn-chain depth guard).
"""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.functions.tools.send_message.shared import _emit_branch_ui

from .addressing import (
    _normalize_existing_target,
    _parse_to,
    _resolve_branch_by_name,
)
from .delivery import (
    _clip_result,
    enqueue_for_busy_target,
    sender_header,
)
from .depth import (
    MAX_SPAWN_DEPTH,
    _spawn_depth,
    current_spawn_depth,
    set_spawn_depth,
)
from .prompt import DESCRIPTION


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Current (session_id, turn_id, agent_id) for anchoring the send.

    Reads the dispatcher's ContextVars first (same as the ``agent``
    tool). ``_current_turn_id`` isn't always bound on every execution
    path (e.g. a followup turn, or some sub-call stacks), so when it's
    missing we fall back to the session's current head — that head IS a
    valid parent anchor, which is what callers actually need. This is
    what fixes the "no active parent turn" the model sometimes hit."""
    try:
        from openprogram.agent.run_control import _current_session_id
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    try:
        from openprogram.store import _current_turn_id
        aid = _current_turn_id.get()
    except Exception:
        aid = None
    agent_id = None
    if sid:
        try:
            from openprogram.agent.session_db import default_db
            sess = default_db().get_session(sid) or {}
            agent_id = sess.get("agent_id")
            if not aid:
                # Fall back to the session head as the parent anchor.
                aid = sess.get("head_id")
        except Exception:
            agent_id = None
    return sid, aid, agent_id


def _send_message_impl(
    message: str,
    to: str = "",
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Implementation body, pulled out of the @function binding so tests
    can drive it with their own ContextVars."""
    from openprogram.events import emit_safe

    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[send_message error] no active parent turn — must be called "
            "from inside an assistant turn (the dispatcher sets the session "
            "+ turn ContextVars on entry)."
        )

    # Depth guard (§5.1): refuse deliveries past MAX_SPAWN_DEPTH so A↔B /
    # runaway recursion can't blow up. The reply-followup inherits the
    # same depth, so back-and-forth also counts toward it.
    depth = current_spawn_depth()
    if depth >= MAX_SPAWN_DEPTH:
        return (
            f"[send_message refused] spawn depth {depth} reached the max "
            f"({MAX_SPAWN_DEPTH}). This chain is too deep — finish the work "
            "here instead of delegating further."
        )

    chosen_agent = (agent_id or "").strip() or parent_agent or "main"
    kind, tgt_sid, head = _parse_to(to)
    if kind == "spawn_syntax":
        return (
            f"[send_message error] to={to!r} is not a valid target — "
            "send_message only talks to EXISTING branches. To create a "
            "new agent, use the `agent` tool (fork off a node with "
            "agent(context=\"SID:MSG_ID\"))."
        )
    if not (to or "").strip():
        return (
            "[send_message error] `to` is required — pass a SID:HEAD "
            "address or a branch name (see list_agents)."
        )

    # Resolve the target: deliver onto an existing branch = run one more
    # turn off its head (the branch "continues" with the message).
    run_session = tgt_sid or sid
    branch_from = head  # the branch head to continue from
    from openprogram.agent.session_db import default_db
    db = default_db()
    # Not valid SID:HEAD syntax (missing head, or the SID part is not
    # a session)? Treat the whole `to` as a branch NAME: exact match
    # first, unique prefix next (see _resolve_branch_by_name).
    if not branch_from or db.get_session(run_session) is None:
        status, resolved = _resolve_branch_by_name(to)
        if status == "ok":
            run_session, branch_from = resolved  # type: ignore[misc]
        elif status == "ambiguous":
            lines = "\n".join(
                f"  «{n}» → {s}:{h}" for n, s, h in resolved  # type: ignore[union-attr]
            )
            return (
                f"[send_message error] branch name {to!r} matches "
                f"several branches — use the exact SID:HEAD target:\n"
                f"{lines}"
            )
        elif not branch_from and db.get_session(run_session) is not None:
            return (
                "[send_message error] to=\"SID:HEAD\" needs the branch "
                "head after the colon (see list_agents for ready targets)."
            )
        else:
            return (
                f"[send_message error] target {to!r} not found — it is "
                "neither a session:head target nor a branch name (see "
                "list_agents)."
            )
    # SID:HEAD names a BRANCH, not a fork point: snap the given node
    # onto that branch's CURRENT tip so the delivery continues the
    # conversation instead of forking off a stale head. Runs before
    # the self-target guard so an old head of the sender's own chain
    # normalizes to its tip and the guard still catches the loop.
    status, norm = _normalize_existing_target(run_session, branch_from)
    if status == "ok":
        branch_from = norm
    elif status == "ambiguous":
        lines = "\n".join(
            f"  «{n or '(unnamed)'}» → {run_session}:{h}" for n, h in norm  # type: ignore[union-attr]
        )
        return (
            f"[send_message error] node {branch_from!r} is a shared "
            "ancestor of several branches — address the branch you "
            "mean by its current tip (see list_agents):\n"
            f"{lines}"
        )
    else:
        return (
            f"[send_message error] target {to!r} not found — the node "
            f"is on no branch of session {run_session} (see "
            "list_agents for ready targets)."
        )
    # Self-target guard: messaging your own current turn is a direct loop.
    if run_session == sid and branch_from == aid:
        return (
            "[send_message refused] target is your own current turn — "
            "that's a direct loop. Pick a different branch, or use the "
            "`agent` tool to spawn a new one."
        )

    # Every delivery carries a sender-receipt header so the receiver
    # knows who sent it and that replying is optional.
    delivery_body = message
    delivery_message = sender_header(sid, aid) + delivery_body

    # Busy target → inbox (design §5.4). Only cross-session sends can
    # race another turn: a same-session send runs inside the sender's
    # own turn, which is the very token is_turn_running would see.
    if run_session != sid:
        queued = enqueue_for_busy_target(
            run_session,
            branch_from,
            delivery_body,
            sender_session_id=sid,
            sender_msg_id=aid,
            sender_agent_id=parent_agent,
            agent_id=chosen_agent,
            spawn_depth=depth,
        )
        if queued is not None:
            return queued

    emit_safe(
        "branch.message_sent",
        "agent",
        {
            "from": f"{sid}:{aid}",
            "to": f"{run_session}:{branch_from}",
        },
    )
    # Also push a UI frame so the sender's session shows a "message sent"
    # line in its chat stream (front-end useWS handles `branch_message`).
    _emit_branch_ui(sid, "sent", f"{run_session}:{branch_from}", message)

    # Async (default): submit to the task runner. It runs the target
    # branch on a worker thread, writes the attach pointer, and dispatches
    # a followup back to THIS session when done — that followup IS the
    # auto-return of the reply to the sender.
    if not wait:
        try:
            from openprogram.agent.sub_agent_run import run_agent_turn_async
            task_id = run_agent_turn_async(
                session_id=run_session,
                prompt=delivery_message,
                agent_id=chosen_agent,
                branch_from=branch_from,
                context_mode="inherit",
                label=message[:60],  # Stage-1 placeholder name (Stage-2 LLM refines later)
                subject=message[:60],
                description=delivery_message,
                caller_msg_id=aid,
                caller_session_id=sid,  # reply returns to the sender
                spawn_depth=depth + 1,  # child inherits depth+1 (loop guard)
            )
        except Exception as e:  # noqa: BLE001
            return f"[send_message error] {type(e).__name__}: {e}"
        return (
            f"[delivered, running async] delivery_id={task_id}\n"
            "The target branch is running; its reply will come back to you "
            "automatically when it finishes. You are not blocked — continue."
        )

    # Sync: run inline, write the attach pointer, return the reply text.
    # Bind depth+1 for the inline child turn (same execution context).
    _tok = set_spawn_depth(depth + 1)
    try:
        from openprogram.agent.sub_agent_run import (
            run_agent_turn,
            write_attach_pointer_for_spawn,
        )
        result = run_agent_turn(
            session_id=run_session,
            prompt=delivery_message,
            agent_id=chosen_agent,
            branch_from=branch_from,
            label=message[:60],  # Stage-1 placeholder name
            # A delivery in the SENDER's own session must not steal its
            # head; a cross-session send continues the target's
            # conversation and advances theirs as usual.
            advance_head=(run_session != sid),
        )
    except Exception as e:  # noqa: BLE001
        return f"[send_message error] {type(e).__name__}: {e}"
    finally:
        _spawn_depth.reset(_tok)

    try:
        write_attach_pointer_for_spawn(
            session_id=run_session,
            caller_msg_id=aid,
            result=result,
            label=message[:60],  # match the branch's Stage-1 placeholder name
            prompt=message,
            chosen_agent=chosen_agent,
        )
    except Exception:
        pass

    emit_safe(
        "branch.message_replied",
        "agent",
        {
            "from": run_session,
            "to": f"{sid}:{aid}",
            "is_error": bool(result.failed or result.error),
        },
    )
    _emit_branch_ui(sid, "replied", run_session, result.final_text or "")

    if result.error and not result.final_text:
        return f"[send_message error: head={result.head_id}] {result.error}"
    out = _clip_result(result.final_text or "(target branch returned no text)")
    if result.error:
        out = f"{out}\n\n[send_message warning] {result.error}"
    return f"{out}\n\n[branch {run_session}:{result.head_id or '?'}]"


@function(
    name="send_message",
    description=DESCRIPTION,
    toolset=["core"],
)
def send_message(
    message: str,
    to: str,
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Deliver a message to an existing branch, run it, get the reply back."""
    return _send_message_impl(
        message=message,
        to=to,
        agent_id=agent_id,
        wait=wait,
    )
