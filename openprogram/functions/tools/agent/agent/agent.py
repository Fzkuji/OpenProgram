"""agent — spawn another agent in the same session and return its reply.

Same-session multi-agent model: a turn is just
``(predecessor, prompt, agent_id)``. The new turn lands as a branch
in the parent session's DAG. Three context modes:

  * ``context="clean"`` (default) — the spawned agent starts at a new
    root (``caller=null``), inside the same session repo. It sees
    only the prompt; the result becomes a peer DAG tree alongside
    the original conversation.
  * ``context="inherit"`` — the spawned agent forks off the caller's
    turn, inheriting the conversation that led up to it. Same DAG
    semantics as a "fork from here" click.
  * ``context="SID:MSG_ID"`` — the spawned agent forks off that exact
    node (any session), inheriting the chain up to it. This is how a
    new branch is forked from an arbitrary point in the DAG.

Returns the spawned agent's final text. The branch tip
(``session_id:head_id``) is recoverable from the chat history via
the attach indicator the caller writes afterwards (see
``run_agent_turn``).

The parent context — which session is active and which turn id is
running — is supplied via two ContextVars set by the dispatcher /
webui:

  * ``openprogram.agent.run_control._current_session_id`` —
    bound at ``execute_in_context`` entry.
  * ``openprogram.store._current_turn_id`` — set by
    ``dispatcher.process_user_turn`` to the assistant message id of
    the turn currently running.

If either is missing the tool returns an error string so the calling
LLM sees a clear message it can act on.
"""
from __future__ import annotations

from openprogram.functions._runtime import function

from .prompt import DESCRIPTION


# agent() delegation cap. ONE level: the main agent may spawn workers;
# a spawned agent does the work itself — it never re-delegates. Even a
# single "coordinator" hop turned out to be an agent avoiding its job
# in practice (observed live: a weather query bounced through a whole
# delegation chain, every hop re-wording the same prompt). Deliberately
# much tighter than send_message's MAX_SPAWN_DEPTH=8, which budgets
# multi-round branch-to-branch conversation, not delegation.
MAX_AGENT_DEPTH = 1


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Pull (session_id, assistant_msg_id, default_agent_id) from the
    ambient ContextVars + the parent's session row. Returns
    (None, ...) if either ContextVar is unset — the tool can't run
    without a parent turn to hang off."""
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
        except Exception:
            agent_id = None
    return sid, aid, agent_id


def _agent_impl(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    context: str = "clean",
    wait: bool = True,
) -> str:
    """Implementation body. Pulled out of the @function-wrapped binding
    so unit tests can drive it directly with their own ContextVars
    instead of going through the AgentTool execute path.
    """
    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[agent error] no active parent turn — agent() must be called "
            "from inside an assistant turn (the dispatcher sets the "
            "session + turn ContextVars on entry)."
        )
    chosen_agent = (agent_id or "").strip() or parent_agent or "main"

    label = (description or "").strip()
    # Sanitize label for branch name: git ref chars only.
    if label:
        label = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in label
        )[:24]

    # Depth guard — shares send_message's counter so agent() and
    # send_message deliveries count toward the same chain, but with a
    # much tighter cap: only the main agent may spawn; a spawned agent
    # works with its own tools, it never re-delegates (observed live: a
    # 5-generation weather-query delegation chain, every hop just
    # re-wording the same prompt). send_message keeps its own looser
    # MAX_SPAWN_DEPTH for branch-to-branch dialogue.
    from openprogram.functions.tools.send_message.send_message.depth import (
        current_spawn_depth,
        set_spawn_depth,
        _spawn_depth,
    )
    depth = current_spawn_depth()
    if depth >= MAX_AGENT_DEPTH:
        return (
            f"[agent refused] spawn depth {depth} reached the agent() max "
            f"({MAX_AGENT_DEPTH}). Do the work yourself with your own "
            "tools instead of delegating again."
        )

    # Resolve the context mode. Besides the two named modes, a node
    # address "SID:MSG_ID" forks the new branch off that exact node —
    # the spawned agent inherits the chain up to it.
    mode = (context or "").strip() or "clean"
    run_session = sid
    branch_from: str | None = None
    if mode.lower() in ("inherit", "clean"):
        mode = mode.lower()
        branch_from = aid if mode == "inherit" else None
    elif ":" in mode:
        fork_sid, _, fork_msg = mode.partition(":")
        fork_sid = fork_sid.strip()
        fork_msg = fork_msg.strip()
        if not fork_sid or not fork_msg:
            return (
                f"[agent error] context {context!r} — a node address needs "
                "both parts: 'SID:MSG_ID'."
            )
        from openprogram.agent.session_db import default_db
        if default_db().get_session(fork_sid) is None:
            return (
                f"[agent error] context {context!r} — session "
                f"{fork_sid!r} not found (see list_agents)."
            )
        run_session = fork_sid
        branch_from = fork_msg
        mode = "inherit"  # fork = inherit the chain up to the node
    else:
        return (
            f"[agent error] unknown context {context!r} — use 'clean' "
            "(default, new root, no parent history), 'inherit' (fork off "
            "this turn, full chain visible), or 'SID:MSG_ID' (fork off "
            "that exact node)."
        )

    if not wait:
        # Async path: submit and return the task_id. Caller can
        # invoke task_output / task_stop / get_task. The runner is
        # responsible for state transitions + attach card update.
        try:
            from openprogram.agent.sub_agent_run import run_agent_turn_async
            from openprogram.agent.sub_agent_run import (
                write_attach_placeholder_for_spawn,
            )
            # Drop a "running" placeholder attach card first, anchored on
            # the calling turn — the card shows up where it was invoked;
            # the runner fills in the result in place at terminal state.
            # Without this card, a wait=False result could only drift
            # back via task_followup with nowhere to anchor.
            attach_id = write_attach_placeholder_for_spawn(
                session_id=sid,
                caller_msg_id=aid,
                label=label or None,
                prompt=prompt,
                chosen_agent=chosen_agent,
            )
            task_id = run_agent_turn_async(
                session_id=run_session,
                prompt=prompt,
                agent_id=chosen_agent,
                branch_from=branch_from,
                label=label or None,
                subject=description or prompt[:60],
                description=description or prompt,
                context_mode=mode,
                # Anchor the spawned branch to THIS turn (clean mode gets
                # its root's caller from this via the runner) and carry the
                # chain depth so the guard above trips in the child too.
                # Without caller_msg_id the async branch forked from ROOT.
                caller_msg_id=aid,
                # A fork into another session must return its reply to
                # the caller's session, not the fork target's.
                caller_session_id=sid if run_session != sid else None,
                spawn_depth=depth + 1,
                attach_pointer_id=attach_id,
            )
            # Live counterpart of the placeholder row above, so the card
            # appears without a reload. Terminal state still arrives via
            # the runner's session_reload — see the sync/async note in
            # emit_spawn_event's callers.
            if attach_id:
                from openprogram.agent.sub_agent_run import emit_spawn_event
                from openprogram.functions._runtime import current_tool_call_id
                emit_spawn_event(
                    session_id=sid,
                    status="running",
                    label=label or None,
                    prompt=prompt,
                    chosen_agent=chosen_agent,
                    card_id=attach_id,
                    tool_call_id=current_tool_call_id(),
                    task_id=task_id,
                )
        except Exception as e:  # noqa: BLE001
            return f"[agent error] {type(e).__name__}: {e}"
        return (
            f"[agent spawned async] task_id={task_id}\n"
            f"Call task_output(task_id={task_id!r}) to retrieve result, "
            f"or task_stop(task_id={task_id!r}) to stop it."
        )

    # Announce the spawn BEFORE running it: a synchronous spawn blocks
    # this tool call for as long as the sub-agent runs, so without a
    # "running" event the caller's turn shows nothing until it finishes.
    # The id is minted here and reused for the attach node below, so the
    # live card and the reloaded row are one and the same.
    import uuid as _uuid
    from openprogram.functions._runtime import current_tool_call_id
    _card_id = _uuid.uuid4().hex[:12]
    _tool_call_id = current_tool_call_id()
    try:
        from openprogram.agent.sub_agent_run import (
            emit_spawn_event,
            run_agent_turn,
            write_attach_pointer_for_spawn,
        )
        emit_spawn_event(
            session_id=sid,
            status="running",
            label=label or None,
            prompt=prompt,
            chosen_agent=chosen_agent,
            card_id=_card_id,
            tool_call_id=_tool_call_id,
        )
        # Bind depth+1 for the child turn (same-context synchronous run),
        # mirroring what the async runner does with task.spawn_depth.
        _depth_token = set_spawn_depth(depth + 1)
        try:
            result = run_agent_turn(
                session_id=run_session,
                prompt=prompt,
                agent_id=chosen_agent,
                branch_from=branch_from,
                label=label or None,
                # clean mode = new branch → its root's caller = the spawning
                # node, so the DAG attaches the branch to this turn instead of
                # forking it from ROOT (dag/overview.md §2.3). The async path
                # (runner.py) already does this; without it here the sync
                # path's sub-branch rendered as an unrelated root-level fork.
                spawn_caller=aid if branch_from is None else None,
                advance_head=False,  # same-session spawn never steals head
            )
        finally:
            _spawn_depth.reset(_depth_token)
    except Exception as e:  # noqa: BLE001
        # The card is already on screen in "running" — close it out, or
        # it spins forever.
        try:
            emit_spawn_event(
                session_id=sid, status="errored", label=label or None,
                prompt=prompt, chosen_agent=chosen_agent, card_id=_card_id,
                tool_call_id=_tool_call_id,
                content=f"{type(e).__name__}: {e}",
            )
        except Exception:
            pass
        return f"[agent error] {type(e).__name__}: {e}"

    # Write an attach pointer node so the DAG paints a `function=attach`
    # square_outline on the caller's lane referencing the sub-branch tip.
    # Without this the sub-branch is orphaned in the graph view (no
    # reference edge connects it back to main). Matches what /spawn and
    # the async path do.
    try:
        write_attach_pointer_for_spawn(
            session_id=sid,
            caller_msg_id=aid,
            result=result,
            label=label or None,
            prompt=prompt,
            chosen_agent=chosen_agent,
            node_id=_card_id,
        )
    except Exception:
        pass

    try:
        emit_spawn_event(
            session_id=sid,
            status="errored" if (result.failed or result.error) else "completed",
            label=label or None,
            prompt=prompt,
            chosen_agent=chosen_agent,
            card_id=_card_id,
            tool_call_id=_tool_call_id,
            head_id=result.head_id,
            content=(result.final_text or result.error or "").strip(),
        )
    except Exception:
        pass

    if result.error and not result.final_text:
        return f"[agent error: head={result.head_id}] {result.error}"

    out = result.final_text or "(spawned agent returned no text)"
    if result.error:
        out = f"{out}\n\n[agent warning] {result.error}"

    tail = f"branch={run_session}:{result.head_id or '?'}"
    return f"{out}\n\n[spawned agent {tail}]"


@function(
    name="agent",
    description=DESCRIPTION,
    toolset=["core"],
    # A spawned agent never even sees this tool (the dispatcher filters
    # by req.source) — the delegated agent does the work itself, no
    # re-subcontracting. With the tool absent from the listing the model
    # won't reach for it; the depth guard in _agent_impl is a backstop
    # (e.g. when tools_override explicitly puts the tool back).
    unsafe_in=["agent_spawn"],
)
def agent(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    context: str = "clean",
    wait: bool = True,
) -> str:
    """Spawn another agent in the same session.

    With ``wait=True`` (default) blocks until the spawned agent
    finishes and returns its final reply. With ``wait=False`` returns
    immediately with a task_id; call :func:`task_output` to retrieve
    the result, or :func:`task_stop` to stop it.

    Args:
        prompt: instruction for the spawned agent. In
            ``context="clean"`` this is ALL it sees, so include any
            context it needs.
        description: short label (1-3 words) used as the branch name.
        agent_id: agent profile to run under. Defaults to this
            session's agent.
        context: ``"clean"`` (default) ⇒ the spawned agent starts at
            a new root with only the prompt visible. ``"inherit"`` ⇒
            forks off this turn and sees the full chain that led here.
            ``"SID:MSG_ID"`` ⇒ forks off that exact node.
        wait: True (default) blocks for the final reply. False
            returns ``task_id`` immediately for parallel execution.
    """
    return _agent_impl(
        prompt=prompt, description=description,
        agent_id=agent_id, context=context, wait=wait,
    )
