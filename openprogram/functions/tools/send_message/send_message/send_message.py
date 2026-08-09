"""send_message — the single branch-to-branch communication primitive.

Deliver a message to a branch → trigger that branch to run one turn →
its reply auto-returns to the sender. Three usages via ``to``:

  * ``"new"``            — start a fresh ROOT branch in the CURRENT session's
                           DAG, deliver the message, run it. (spawn)
  * ``"new:sid:msg_id"`` — fork a new branch off a node, deliver, run.
  * ``"sid:head"``       — deliver to an existing branch.

Async by default (``wait=False``): returns immediately with a delivery id;
the target runs in the background and its reply comes back to the sender
automatically (the task runner's followup). ``wait=True`` blocks for the
reply.

Design: docs/design/runtime/agent-collaboration.md. This module is the
entry point: the @function binding plus the main delivery flow.
Concern-specific pieces live alongside: ``prompt.py`` (LLM-facing
description), ``addressing.py`` (`to` parsing + branch-name lookup),
``delivery.py`` (synthesis blocks, receipt header, busy-target inbox,
reply clipping), ``depth.py`` (spawn-chain depth guard).
"""
from __future__ import annotations

from openprogram.functions._runtime import function
from openprogram.functions.tools.send_message.shared import _emit_branch_ui

from .addressing import _parse_to, _resolve_branch_by_name
from .delivery import (
    _clip_result,
    _gather_sources,
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
    """Current (session_id, turn_id, agent_id) for anchoring the spawn.

    Reads the dispatcher's ContextVars first (same as the ``task`` tool).
    ``_current_turn_id`` isn't always bound on every execution path (e.g.
    a followup turn, or some sub-call stacks), so when it's missing we
    fall back to the session's current head — that head IS a valid parent
    anchor for the new branch, which is what callers actually need. This
    is what fixes the "no active parent turn" the model sometimes hit."""
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
    to: str = "new",
    sources: list[str] | None = None,
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

    # Depth guard (§5.1): refuse spawning past MAX_SPAWN_DEPTH so A↔B /
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
    kind, tgt_sid, fork_msg = _parse_to(to)

    # Resolve target into (run_session, branch_from, is_new):
    #   new      → fresh root in current session
    #   fork     → fork off a node (inherit that chain)
    #   existing → deliver onto an existing branch = run one more turn off
    #              its head (the branch "continues" with the message)
    if kind == "existing":
        run_session = tgt_sid or sid
        branch_from = fork_msg  # the branch head to continue from
        is_new = False
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
                    "head after the colon (see list_branches for ready targets)."
                )
            else:
                return (
                    f"[send_message error] target {to!r} not found — it is "
                    "neither a session:head target nor a branch name (see "
                    "list_branches / list_sessions)."
                )
        # Self-target guard: messaging your own current turn is a direct loop.
        if run_session == sid and branch_from == aid:
            return (
                "[send_message refused] target is your own current turn — "
                "that's a direct loop. Pick a different branch or use to=new."
            )
    elif kind == "fork":
        run_session = tgt_sid or sid
        branch_from = fork_msg
        is_new = True
        if not branch_from:
            return (
                "[send_message error] to=\"new:SID:MSG_ID\" needs a "
                "fork node id after the second colon."
            )
    else:  # "new" — fresh branch in the current session repo (new root)
        run_session = sid
        branch_from = None
        is_new = True

    # Synthesis: prepend each source branch's content as a labelled block,
    # so the target model reads them and synthesizes (C5).
    delivery_body = _gather_sources(sources) + message
    # Deliveries to an EXISTING branch carry a sender-receipt header so
    # the receiver knows who sent it and that replying is optional. New
    # branches (spawn/fork) get the bare message — they are workers, not
    # correspondents.
    if kind == "existing":
        delivery_message = sender_header(sid, aid) + delivery_body
    else:
        delivery_message = delivery_body

    # Busy target → inbox (design §5.4). Only cross-session sends can
    # race another turn: a same-session send runs inside the sender's
    # own turn, which is the very token is_turn_running would see.
    if kind == "existing" and run_session != sid:
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
            "to": f"{run_session}:{branch_from}" if branch_from else run_session,
            "is_new": is_new,
            "sources": sources or [],
        },
    )
    # Also push a UI frame so the sender's session shows a "message sent"
    # line in its chat stream (front-end useWS handles `branch_message`).
    _to_label = f"{run_session}:{branch_from}" if branch_from else f"{run_session} (new branch)"
    _emit_branch_ui(sid, "sent", _to_label, message)

    # Async (default): submit to the task runner. It runs the new branch
    # on a worker thread, writes the attach pointer, and dispatches a
    # followup back to THIS session when done — that followup IS the
    # auto-return of the reply to the sender.
    if not wait:
        try:
            from openprogram.agent.sub_agent_run import run_agent_turn_async
            task_id = run_agent_turn_async(
                session_id=run_session,
                prompt=delivery_message,
                agent_id=chosen_agent,
                branch_from=branch_from,
                context_mode="inherit" if branch_from else "clean",
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
            # New branch (branch_from=None) → root's caller = spawning node,
            # so it's an explicit spawn, not seq-stitched into a sibling.
            spawn_caller=aid if branch_from is None else None,
            # A spawn/fork in the SENDER's own session must not steal
            # its head; a cross-session send continues the target's
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
    to: str = "new",
    sources: list[str] | None = None,
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Deliver a message to a branch, run it, get the reply back."""
    return _send_message_impl(
        message=message,
        to=to,
        sources=sources,
        agent_id=agent_id,
        wait=wait,
    )
