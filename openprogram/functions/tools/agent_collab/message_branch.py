"""message_branch — the single branch-to-branch communication primitive.

Deliver a message to a branch → trigger that branch to run one turn →
its reply auto-returns to the sender. Three usages via ``target``:

  * ``"new"``            — create a fresh branch (new session) from ROOT,
                           deliver the message, run it. (spawn / new chat)
  * ``"new:sid:msg_id"`` — fork a new branch off a node, deliver, run.
  * ``"sid:head"``       — deliver to an existing branch.

Async by default (``wait=False``): returns immediately with a delivery id;
the target runs in the background and its reply comes back to the sender
automatically (the task runner's followup). ``wait=True`` blocks for the
reply.

Design: docs/design/runtime/agent-collaboration.md. This file is C1 —
the core path for ``target="new"`` (spawn usage). Existing-branch /
cross-session / synthesis / robustness land in later steps.
"""
from __future__ import annotations

from openprogram.functions._runtime import function


_DESCRIPTION = (
    "Branch-to-branch communication: deliver a message to a branch, run "
    "one turn there, and (by default, async) have its reply come back to "
    "you automatically. ONE tool for spawning sub-agents, messaging other "
    "branches, and synthesizing across branches — chosen by `target`:\n"
    "\n"
    "  target=\"new\" (DEFAULT): create a fresh branch and run `message` "
    "in it — i.e. spawn a sub-agent / open a new line of work. The new "
    "branch sees ONLY `message` (a clean worker); pack what it needs into "
    "the message. Want several? call this several times — they run in "
    "parallel, each returning to you when done.\n"
    "  target=\"new:SID:MSG_ID\": fork a new branch off an existing node "
    "(it inherits the chain up to that node), then run `message`.\n"
    "  target=\"SID:HEAD\": deliver `message` to an existing branch and "
    "trigger it to respond.\n"
    "\n"
    "wait=False (DEFAULT): returns a delivery id immediately; you are NOT "
    "blocked — keep working. The target's reply is delivered back to you "
    "as a new message automatically when it finishes. wait=True: block "
    "and return the reply text directly.\n"
    "\n"
    "Use this to offload sub-tasks, run parallel explorations, or hand a "
    "message to another agent/branch."
)


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Current (session_id, turn_id, agent_id) from the dispatcher's
    ContextVars — same resolution the ``task`` tool uses."""
    try:
        from openprogram.webui._pause_stop import _current_session_id
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
        except Exception:
            agent_id = None
    return sid, aid, agent_id


def _gather_sources(sources: list[str] | None) -> str:
    """Pull each source branch's tip text and wrap it in a labelled block,
    so the target model reads them and synthesizes. Each source is
    ``"SID:HEAD"`` (or ``"SID"`` → that session's current head).

    Returns the assembled block string (empty if no usable sources).
    """
    if not sources:
        return ""
    from openprogram.agent.session_db import default_db
    from openprogram.agent.internals._merge import _peer_final_text
    store = default_db()
    blocks: list[str] = []
    for raw in sources:
        s = (raw or "").strip()
        if not s:
            continue
        ssid, _, shead = s.partition(":")
        ssid = ssid.strip()
        shead = shead.strip() or None
        if not ssid:
            continue
        try:
            text, _hid = _peer_final_text(store, ssid, shead)
        except Exception:
            text = ""
        text = (text or "").strip()
        if not text:
            text = "(this branch has no readable content)"
        blocks.append(f'<branch source="{s}">\n{text}\n</branch>')
    if not blocks:
        return ""
    return (
        "下面是几条分支的内容，请阅读后综合，再回应本条消息：\n\n"
        + "\n\n".join(blocks)
        + "\n\n---\n\n"
    )


def _parse_target(target: str) -> tuple[str, str | None, str | None]:
    """Parse the ``target`` arg into (kind, session_id, fork_msg_id).

    kind ∈ {"new", "fork", "existing"}:
      * "new"            → ("new", None, None)
      * "new:SID:MSG_ID" → ("fork", SID, MSG_ID)
      * "SID:HEAD"       → ("existing", SID, HEAD)
    """
    t = (target or "new").strip()
    if t == "new":
        return "new", None, None
    if t.startswith("new:"):
        rest = t[len("new:"):]
        sid, _, msg = rest.partition(":")
        return "fork", sid or None, (msg or None)
    sid, sep, head = t.partition(":")
    return "existing", sid or None, (head or None)


def _message_branch_impl(
    message: str,
    target: str = "new",
    sources: list[str] | None = None,
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Implementation body, pulled out of the @function binding so tests
    can drive it with their own ContextVars."""
    from openprogram.agent.event_bus import emit_safe

    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[message_branch error] no active parent turn — must be called "
            "from inside an assistant turn (the dispatcher sets the session "
            "+ turn ContextVars on entry)."
        )

    chosen_agent = (agent_id or "").strip() or parent_agent or "main"
    kind, tgt_sid, fork_msg = _parse_target(target)

    # Resolve target into (run_session, branch_from, is_new):
    #   new      → fresh root in current session
    #   fork     → fork off a node (inherit that chain)
    #   existing → deliver onto an existing branch = run one more turn off
    #              its head (the branch "continues" with the message)
    if kind == "existing":
        run_session = tgt_sid or sid
        branch_from = fork_msg  # the branch head to continue from
        is_new = False
        if not branch_from:
            return (
                "[message_branch error] target=\"SID:HEAD\" needs the branch "
                "head after the colon (see list_branches for ready targets)."
            )
        # Target session must exist — don't silently create.
        from openprogram.agent.session_db import default_db
        if default_db().get_session(run_session) is None:
            return (
                f"[message_branch error] target session {run_session!r} not "
                "found (see list_sessions)."
            )
    elif kind == "fork":
        run_session = tgt_sid or sid
        branch_from = fork_msg
        is_new = True
        if not branch_from:
            return (
                "[message_branch error] target=\"new:SID:MSG_ID\" needs a "
                "fork node id after the second colon."
            )
    else:  # "new" — fresh branch in the current session repo (new root)
        run_session = sid
        branch_from = None
        is_new = True

    # Synthesis: prepend each source branch's content as a labelled block,
    # so the target model reads them and synthesizes (C5).
    delivery_message = _gather_sources(sources) + message

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
                subject=message[:60],
                description=delivery_message,
                caller_msg_id=aid,
                caller_session_id=sid,  # reply returns to the sender
            )
        except Exception as e:  # noqa: BLE001
            return f"[message_branch error] {type(e).__name__}: {e}"
        return (
            f"[delivered, running async] delivery_id={task_id}\n"
            "The target branch is running; its reply will come back to you "
            "automatically when it finishes. You are not blocked — continue."
        )

    # Sync: run inline, write the attach pointer, return the reply text.
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
        )
    except Exception as e:  # noqa: BLE001
        return f"[message_branch error] {type(e).__name__}: {e}"

    try:
        write_attach_pointer_for_spawn(
            session_id=run_session,
            caller_msg_id=aid,
            result=result,
            label=None,
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

    if result.error and not result.final_text:
        return f"[message_branch error: head={result.head_id}] {result.error}"
    out = result.final_text or "(target branch returned no text)"
    if result.error:
        out = f"{out}\n\n[message_branch warning] {result.error}"
    return f"{out}\n\n[branch {run_session}:{result.head_id or '?'}]"


@function(
    name="message_branch",
    description=_DESCRIPTION,
    toolset=["core"],
)
def message_branch(
    message: str,
    target: str = "new",
    sources: list[str] | None = None,
    agent_id: str = "",
    wait: bool = False,
) -> str:
    """Deliver a message to a branch, run it, get the reply back."""
    return _message_branch_impl(
        message=message,
        target=target,
        sources=sources,
        agent_id=agent_id,
        wait=wait,
    )
