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
from openprogram.functions.tools.send_message.send_message.depth import (
    delegation_budget_left as _delegation_budget_left,
)

from .prompt import DESCRIPTION


# Spawn budget: how many generations of NEW agents a chain may create.
# ONE level by default — the main agent spawns workers; a worker does
# the work itself. Even a single "coordinator" hop turned out to be an
# agent avoiding its job in practice (observed live: a weather query
# bounced through a whole delegation chain, every hop re-wording the
# same prompt). Deliberately tighter than the message budget
# (MAX_MESSAGES=8), which pays for multi-round conversation, not for
# creating agents. Module-level fallback for ``agent.max_spawn_depth``.
MAX_SPAWN_DEPTH = 1


def max_spawn_depth() -> int:
    """``agent.max_spawn_depth`` from config; 0 = unlimited."""
    from openprogram.functions.tools.send_message.send_message.depth import (
        config_limit,
    )
    return config_limit("max_spawn_depth", MAX_SPAWN_DEPTH)


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


def _dispatch_to_existing(
    prompt: str,
    to: str,
    agent_id: str,
    context: str,
    description: str,
) -> str:
    """``agent(to=…)`` — dispatch a tracked task to an EXISTING agent.

    No branch is created: the task runs as the target branch's next
    turn (send_message's addressing + delivery path), but unlike a
    message it is a formal task — a Task entity is created, the
    dispatcher gets a task_id (``task_output`` waits, ``task_stop``
    withdraws/cancels), and the result flows back like a spawn's.
    Busy target → the task queues in the target's inbox and runs when
    its current turn ends. Always asynchronous.
    """
    # to= dispatches onto an existing branch, which keeps its own
    # history — a context/fork-point choice contradicts that.
    if (context or "").strip().lower() not in ("", "clean"):
        return (
            "[agent error] to= and context are mutually exclusive — "
            "to= dispatches the task onto an EXISTING branch, which "
            "keeps its own history. Drop context, or drop to= and "
            "spawn a new agent."
        )
    # Same parent resolution as send_message (falls back to the session
    # head when no turn id is bound — e.g. a followup turn).
    from openprogram.functions.tools.send_message.send_message.send_message import (
        _resolve_parent as _resolve_sender,
    )
    sid, aid, parent_agent = _resolve_sender()
    if not sid or not aid:
        return (
            "[agent error] no active parent turn — agent(to=…) must be "
            "called from inside an assistant turn."
        )
    chosen_agent = (agent_id or "").strip() or parent_agent or "main"

    # Budget guard: a dispatch to an existing agent spends the message
    # budget (branch-to-branch traffic), not the spawn budget — it
    # creates no agent.
    from openprogram.functions.tools.send_message.send_message.depth import (
        current_chain_messages,
        max_messages,
    )
    depth = current_chain_messages()
    limit = max_messages()
    if limit and depth >= limit:
        return (
            f"[agent refused] this chain has passed {depth} messages, "
            f"the maximum ({limit}). Finish the work here instead of "
            "dispatching further."
        )

    # Addressing is send_message's, verbatim: SID:HEAD snaps to the
    # branch's current tip; a name resolves exact-first then unique
    # prefix; ambiguity lists candidates.
    from openprogram.functions.tools.send_message.send_message.addressing import (
        resolve_existing_target,
    )
    status, payload = resolve_existing_target(to, sid)
    if status != "ok":
        return f"[agent error] {payload}"
    run_session, target_tip = payload  # type: ignore[misc]

    # Self-dispatch guard: a task cannot be dispatched to its own
    # dispatcher — just do the work.
    if run_session == sid and target_tip == aid:
        return (
            "[agent refused] to= points at your own current branch — a "
            "task cannot be dispatched to its dispatcher. Continue the "
            "work here directly."
        )

    label = (description or "").strip()
    if label:
        label = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in label
        )[:24]

    from openprogram.functions.tools.send_message.send_message.delivery import (
        task_header,
    )
    delivery_message = task_header(sid, aid) + prompt

    # Busy target → pre-create the Task (so the dispatcher holds a real
    # task_id while the work waits) and queue the dispatch in the
    # target's inbox; drain runs it, reusing the id. Same cross-session-
    # only reasoning as send_message: a same-session dispatch runs
    # inside the dispatcher's own turn, whose token is the one the busy
    # check would see.
    if run_session != sid:
        from openprogram.agent.run_control import is_turn_running
        if is_turn_running(run_session):
            from openprogram.agent import inbox
            from openprogram.agent.task.runner import _current_task_id
            from openprogram.agent.task.store import save_task, update_task_status
            from openprogram.agent.task.types import Task, TaskStatus, mint_task_id
            task = Task(
                id=mint_task_id(),
                parent_session_id=run_session,
                prompt=delivery_message,
                agent_id=chosen_agent,
                subject=description or prompt[:60],
                description=delivery_message,
                context_mode="inherit",
                parent_msg_id=target_tip,
                parent_task_id=_current_task_id.get(),
                label=label or None,
                wait=False,
                caller_msg_id=aid,
                caller_session_id=sid,
                chain_messages=depth + 1,
                status=TaskStatus.PENDING,
            )
            save_task(run_session, task)
            try:
                q = inbox.enqueue(
                    run_session,
                    message=prompt,
                    sender_session_id=sid,
                    sender_msg_id=aid,
                    sender_agent_id=parent_agent,
                    agent_id=chosen_agent,
                    chain_messages=depth,
                    target_head_id=target_tip,
                    task_id=task.id,
                )
            except Exception as e:  # noqa: BLE001
                try:
                    update_task_status(
                        run_session, task.id, TaskStatus.ERRORED,
                        error=f"enqueue failed: {e}",
                    )
                except Exception:
                    pass
                return f"[agent error] {type(e).__name__}: {e}"
            if q == "duplicate":
                try:
                    update_task_status(
                        run_session, task.id, TaskStatus.CANCELLED,
                        error="duplicate dispatch",
                    )
                except Exception:
                    pass
                return (
                    "[agent] duplicate dispatch ignored — an identical "
                    "task from you is already queued for this target "
                    "(sent within the last 60s)."
                )
            # Race window: the target may have finished between the busy
            # check and the enqueue — drain now.
            if not is_turn_running(run_session):
                try:
                    inbox.drain(run_session)
                except Exception:
                    pass
            return (
                f"[task dispatched, queued] task_id={task.id} "
                f"target={run_session}:{target_tip} — the target is busy "
                "running a turn; your task runs when it ends and the "
                "result comes back automatically. task_output(task_id) "
                "waits for it; task_stop(task_id) withdraws it."
            )

    from openprogram.events import emit_safe
    from openprogram.functions.tools.send_message.shared import _emit_branch_ui
    emit_safe(
        "branch.message_sent",
        "agent",
        {"from": f"{sid}:{aid}", "to": f"{run_session}:{target_tip}"},
    )
    _emit_branch_ui(sid, "sent", f"{run_session}:{target_tip}", prompt)

    try:
        from openprogram.agent.sub_agent_run import run_agent_turn_async
        task_id = run_agent_turn_async(
            session_id=run_session,
            prompt=delivery_message,
            agent_id=chosen_agent,
            branch_from=target_tip,
            context_mode="inherit",
            label=label or None,
            subject=description or prompt[:60],
            description=delivery_message,
            caller_msg_id=aid,
            caller_session_id=sid,
            chain_messages=depth + 1,
        )
    except Exception as e:  # noqa: BLE001
        return f"[agent error] {type(e).__name__}: {e}"
    return (
        f"[task dispatched] task_id={task_id} "
        f"target={run_session}:{target_tip}\n"
        "The target branch is running your task as its next turn; the "
        "result comes back to you automatically. task_output(task_id) "
        "waits for it; task_stop(task_id) cancels it."
    )


def _agent_impl(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    context: str = "clean",
    run_in_background: bool = False,
    to: str = "",
    archive_when_done: bool = False,
) -> str:
    """Implementation body. Pulled out of the @function-wrapped binding
    so unit tests can drive it directly with their own ContextVars
    instead of going through the AgentTool execute path.
    """
    if (to or "").strip():
        # archive_when_done characterizes a branch THIS call creates;
        # a to= dispatch targets an existing agent this call did not
        # create — only its creator may archive it (archive_agent).
        if archive_when_done:
            return (
                "[agent error] archive_when_done applies to the branch "
                "this call spawns — to= dispatches to an EXISTING agent "
                "instead. Drop archive_when_done, or archive the target "
                "later with archive_agent (creator only)."
            )
        # Dispatch to an EXISTING agent — always async, returns a
        # task_id immediately; run_in_background is meaningless here
        # and ignored.
        return _dispatch_to_existing(
            prompt=prompt,
            to=to.strip(),
            agent_id=agent_id,
            context=context,
            description=description,
        )
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

    # Spawn-budget guard — shares the chain counter with send_message so
    # spawns and messages spend the same chain, but with a much tighter
    # cap: only the main agent may spawn; a spawned agent works with its
    # own tools, it never re-delegates (observed live: a 5-generation
    # weather-query delegation chain, every hop just re-wording the same
    # prompt). The message budget stays looser for branch-to-branch
    # dialogue.
    from openprogram.functions.tools.send_message.send_message.depth import (
        current_chain_messages,
        set_chain_messages,
        _chain_messages,
    )
    depth = current_chain_messages()
    spawn_limit = max_spawn_depth()
    if spawn_limit and depth >= spawn_limit:
        return (
            f"[agent refused] spawn depth {depth} reached the max "
            f"({spawn_limit}). Do the work yourself with your own tools "
            "instead of delegating again."
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

    if run_in_background:
        # Background path: submit and return the task_id. Caller can
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
            # Without this card, a background result could only drift
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
                chain_messages=depth + 1,
                # This call CREATES the branch — record the creator so
                # archive_agent can gate on it, and let the runner
                # archive the branch at terminal state if asked.
                spawner_session_id=sid,
                archive_when_done=archive_when_done,
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
        # mirroring what the async runner does with task.chain_messages.
        _depth_token = set_chain_messages(depth + 1)
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
            _chain_messages.reset(_depth_token)
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

    # Spawn-branch meta, after the result is in hand: stamp the creator
    # (archive_agent gates on it) and archive on request. Best-effort —
    # the result below flows back regardless.
    if result.head_id:
        try:
            import time as _time
            from openprogram.agent.session_db import default_db
            _fields: dict = {"spawner_session_id": sid}
            if archive_when_done:
                _fields["archived"] = True
                _fields["archived_at"] = _time.time()
            default_db().set_branch_meta(run_session, result.head_id, **_fields)
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "spawn branch meta stamp failed", exc_info=True,
            )

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
    # Exposed while the chain still has spawn OR message budget; gone
    # once both are spent, because a tool sitting in the listing invites
    # the model to reach for it. In between the runtime guards in
    # _agent_impl refuse the calls that overrun — e.g. a spawned agent
    # keeps `agent` for to= dispatch while its spawn budget reads 0.
    can_use=_delegation_budget_left,
)
def agent(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    context: str = "clean",
    run_in_background: bool = False,
    to: str = "",
    archive_when_done: bool = False,
) -> str:
    """Spawn a new agent, or dispatch a tracked task to an existing one.

    Without ``to``: spawns a new agent. ``run_in_background=False``
    (default) blocks until it finishes and returns its final reply;
    ``run_in_background=True`` returns immediately with a task_id;
    call :func:`task_output` to retrieve the result, or
    :func:`task_stop` to stop it.

    With ``to``: no agent is created — the task is dispatched to the
    named EXISTING branch and runs as its next turn. Always
    asynchronous: returns a task_id immediately (``run_in_background``
    is ignored); the result comes back automatically.

    Args:
        prompt: full instruction. In ``context="clean"`` this is ALL
            the spawned agent sees, so include any context it needs.
        description: short label (1-3 words) used as the branch name.
        agent_id: agent profile to run under. Defaults to this
            session's agent.
        context: ``"clean"`` (default) ⇒ the spawned agent starts at
            a new root with only the prompt visible. ``"inherit"`` ⇒
            forks off this turn and sees the full chain that led here.
            ``"SID:MSG_ID"`` ⇒ forks off that exact node. Mutually
            exclusive with ``to``.
        run_in_background: False (default) blocks for the final
            reply. True returns ``task_id`` immediately for parallel
            execution; completion notifies the caller automatically.
            Ignored when ``to`` is set (always async).
        to: dispatch target — an existing branch, addressed as
            ``"SID:HEAD"`` or by branch name (see list_agents). A busy
            target queues the task and runs it when its turn ends.
        archive_when_done: True ⇒ archive the spawned branch once its
            task reaches terminal state (after the result flowed
            back): it disappears from list_agents and refuses further
            send_message / agent(to=) deliveries; its history stays
            readable and forkable. Default False keeps the agent
            addressable for follow-up questions. Spawn-only —
            incompatible with ``to``.
    """
    return _agent_impl(
        prompt=prompt, description=description,
        agent_id=agent_id, context=context,
        run_in_background=run_in_background,
        to=to,
        archive_when_done=archive_when_done,
    )
