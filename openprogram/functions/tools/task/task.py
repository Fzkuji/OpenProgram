"""task — spawn another agent in the same session and return its reply.

Same-session multi-agent model: a turn is just
``(predecessor, prompt, agent_id)``. The new turn lands as a branch
in the parent session's DAG. Two context modes:

  * ``context="inherit"`` (default) — the spawned agent forks off
    the caller's turn, inheriting the conversation that led up to
    it. Same DAG semantics as a "fork from here" click.
  * ``context="clean"`` — the spawned agent starts at a new root
    (``caller=null``), inside the same session repo. It sees
    only the prompt; the result becomes a peer DAG tree alongside
    the original conversation.

Returns the spawned agent's final text. The branch tip
(``session_id:head_id``) is recoverable from the chat history via
the attach indicator the caller writes afterwards (see
``run_agent_turn``).

The parent context — which session is active and which turn id is
running — is supplied via two ContextVars set by the dispatcher /
webui:

  * ``openprogram.webui._pause_stop._current_session_id`` —
    bound at ``execute_in_context`` entry.
  * ``openprogram.store._current_turn_id`` — set by
    ``dispatcher.process_user_turn`` to the assistant message id of
    the turn currently running.

If either is missing the tool returns an error string so the calling
LLM sees a clear message it can act on.
"""
from __future__ import annotations

from openprogram.functions._runtime import function


_DESCRIPTION = (
    "Spawn another agent in the same session, run one turn against "
    "it with the given prompt, and return either the final reply "
    "(wait=True, default) or a task_id you can await later "
    "(wait=False). Two context modes — YOU decide per call:\n"
    "\n"
    "  clean (DEFAULT): the spawned agent starts at a new root with "
    "ONLY the prompt visible. No conversation history, no prior tool "
    "results — a clean worker. Use this when the task is self-"
    "contained: pack everything the sub-agent needs into the prompt "
    "yourself. Lower token cost, sub-agent stays focused, your "
    "history stays clean. THIS IS THE RIGHT DEFAULT for most "
    "offloaded sub-tasks.\n"
    "\n"
    "  inherit: the spawned agent forks off this turn and sees the "
    "full conversation chain that led to it (subject to context-"
    "engine compression rules). Same shape as a user clicking 'fork "
    "from here'. Use this only when the sub-task genuinely needs the "
    "running dialogue as context — e.g. a multi-step decision that "
    "depends on earlier turns you can't easily condense into the "
    "prompt. Costs more tokens; brings noise from prior tool calls.\n"
    "\n"
    "Rule of thumb: if you can write a self-contained instruction, "
    "use clean. If you'd genuinely want the sub-agent to read all "
    "the chat history above, use inherit.\n"
    "\n"
    "If YOU are a spawned agent, this tool is NOT available to you: "
    "do the work yourself with your own tools. Re-delegation is "
    "refused outright (depth cap 1 — only the main agent spawns).\n"
    "\n"
    "In both modes the reply lands as a branch in the current "
    "session's DAG. The user can switch to it from the branches "
    "panel, or you can merge it back later with merge_branches.\n"
    "\n"
    "Args:\n"
    "  prompt: full instruction for the spawned agent. In 'clean' "
    "mode this is ALL it sees — include any context it needs.\n"
    "  description: short label (1-3 words) used as the branch name.\n"
    "  agent_id: which agent profile to run as. Defaults to this "
    "session's agent.\n"
    "  context: 'clean' (default) or 'inherit'.\n"
    "  wait: True (default) blocks until the spawned agent finishes "
    "and returns its final text — same as today. False returns "
    "immediately with a task_id string; call await_task(task_id) to "
    "retrieve the result. Use wait=False to run multiple agents in "
    "parallel."
)


# task() delegation cap. ONE level: the main agent may spawn workers;
# a spawned agent does the work itself — it never re-delegates. Even a
# single "coordinator" hop turned out to be an agent avoiding its job
# in practice (observed live: a weather query bounced through a whole
# delegation chain, every hop re-wording the same prompt). Deliberately
# much tighter than message_branch's MAX_SPAWN_DEPTH=8, which budgets
# multi-round branch-to-branch conversation, not delegation.
MAX_TASK_DEPTH = 1


def _resolve_parent() -> tuple[str | None, str | None, str | None]:
    """Pull (session_id, assistant_msg_id, default_agent_id) from the
    ambient ContextVars + the parent's session row. Returns
    (None, ...) if either ContextVar is unset — the tool can't run
    without a parent turn to hang off."""
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


def _task_impl(
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
            "[task error] no active parent turn — task() must be called "
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

    # Depth guard — shares message_branch's counter so task() and
    # message_branch spawns count toward the same chain, but with a much
    # tighter cap: only the main agent may task(); a spawned agent works
    # with its own tools, it never re-delegates (observed live: a
    # 5-generation weather-query delegation chain, every hop just
    # re-wording the same prompt). message_branch keeps its own looser
    # MAX_SPAWN_DEPTH for branch-to-branch dialogue.
    from openprogram.functions.tools.agent_collab.message_branch import (
        current_spawn_depth,
        set_spawn_depth,
        _spawn_depth,
    )
    depth = current_spawn_depth()
    if depth >= MAX_TASK_DEPTH:
        return (
            f"[task refused] spawn depth {depth} reached the task() max "
            f"({MAX_TASK_DEPTH}). Do the work yourself with your own "
            "tools instead of delegating again."
        )

    mode = (context or "").strip().lower() or "clean"
    if mode not in ("inherit", "clean"):
        return (
            f"[task error] unknown context {context!r} — use 'clean' "
            "(default, new root, no parent history) or 'inherit' "
            "(spawned agent forks off this turn and sees the full chain)."
        )

    if not wait:
        # Async path: submit and return the task_id. Caller can
        # invoke await_task / cancel_task / get_task. The runner is
        # responsible for state transitions + attach card update.
        try:
            from openprogram.agent.sub_agent_run import run_agent_turn_async
            from openprogram.agent.sub_agent_run import (
                write_attach_placeholder_for_spawn,
            )
            # 先落一张 running 占位 attach 卡，锚在发起调用的这轮——卡片
            # 在哪被调用就显示在哪；runner 终态时原地补结果。没有这张卡，
            # wait=False 的结果只能靠 task_followup 漂回来、无处锚定。
            attach_id = write_attach_placeholder_for_spawn(
                session_id=sid,
                caller_msg_id=aid,
                label=label or None,
                prompt=prompt,
                chosen_agent=chosen_agent,
            )
            task_id = run_agent_turn_async(
                session_id=sid,
                prompt=prompt,
                agent_id=chosen_agent,
                branch_from=aid if mode == "inherit" else None,
                label=label or None,
                subject=description or prompt[:60],
                description=description or prompt,
                context_mode=mode,
                # Anchor the spawned branch to THIS turn (clean mode gets
                # its root's caller from this via the runner) and carry the
                # chain depth so the guard above trips in the child too.
                # Without caller_msg_id the async branch forked from ROOT.
                caller_msg_id=aid,
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
            return f"[task error] {type(e).__name__}: {e}"
        return (
            f"[task spawned async] task_id={task_id}\n"
            f"Call await_task(task_id={task_id!r}) to retrieve result, "
            f"or cancel_task(task_id={task_id!r}) to stop it."
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
                session_id=sid,
                prompt=prompt,
                agent_id=chosen_agent,
                branch_from=aid if mode == "inherit" else None,
                label=label or None,
                # clean mode = new branch → its root's caller = the spawning
                # node, so the DAG attaches the branch to this turn instead of
                # forking it from ROOT (session-dag.md §2.3). The async path
                # (runner.py) already does this; without it here the sync
                # path's sub-branch rendered as an unrelated root-level fork.
                spawn_caller=aid if mode != "inherit" else None,
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
        return f"[task error] {type(e).__name__}: {e}"

    # Write an attach pointer node so the DAG paints a `function=attach`
    # square_outline on the caller's lane referencing the sub-branch tip.
    # Without this the sub-branch is orphaned in the graph view (no
    # reference edge connects it back to main). Matches what /spawn and
    # the async task path do.
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
        return f"[task error: head={result.head_id}] {result.error}"

    out = result.final_text or "(spawned agent returned no text)"
    if result.error:
        out = f"{out}\n\n[task warning] {result.error}"

    tail = f"branch={sid}:{result.head_id or '?'}"
    return f"{out}\n\n[spawned agent {tail}]"


@function(
    name="task",
    description=_DESCRIPTION,
    toolset=["core"],
    # 被 spawn 的 agent 根本看不到这个工具（dispatcher 按 req.source
    # 过滤）——派活的 agent 自己干活，不再转包。工具不在清单里，模型
    # 就不会想去用；_task_impl 里的深度守卫只是兜底（比如工具被
    # tools_override 显式塞回来的路径）。
    unsafe_in=["agent_spawn"],
)
def task(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    context: str = "clean",
    wait: bool = True,
) -> str:
    """Spawn another agent in the same session.

    With ``wait=True`` (default) blocks until the spawned agent
    finishes and returns its final reply. With ``wait=False`` returns
    immediately with a task_id; call :func:`await_task` to retrieve
    the result, or :func:`cancel_task` to stop it.

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
        wait: True (default) blocks for the final reply. False
            returns ``task_id`` immediately for parallel execution.
    """
    return _task_impl(
        prompt=prompt, description=description,
        agent_id=agent_id, context=context, wait=wait,
    )


@function(
    name="await_task",
    description=(
        "Block until an async task spawned with task(wait=False) "
        "reaches a terminal state (completed/cancelled/errored). "
        "Returns the task's final reply text plus its terminal "
        "status. Pair with task(wait=False) for parallel agent "
        "execution.\n"
        "\n"
        "Args:\n"
        "  task_id: id returned by task(wait=False).\n"
        "  timeout: max seconds to block. None = wait forever. "
        "On timeout the call returns with the task still running."
    ),
    toolset=["core"],
    unsafe_in=["agent_spawn"],  # 同 task：被 spawn 的 agent 不派活也不等活
)
def await_task(task_id: str, timeout: float = 0) -> str:
    """Wait for an async task and return its final reply."""
    if not task_id or not isinstance(task_id, str):
        return "[await_task error] task_id required"
    from openprogram.agent.task import get_runner
    runner = get_runner()
    eff_timeout = None if (timeout is None or timeout <= 0) else float(timeout)
    t = runner.await_task(task_id.strip(), timeout=eff_timeout)
    if t is None:
        return f"[await_task error] unknown task_id={task_id!r}"
    status = t.status.value
    if status == "completed":
        out = t.result_text or "(spawned agent returned no text)"
        return f"{out}\n\n[task {task_id} status={status}]"
    if status == "cancelled":
        return f"[task {task_id} cancelled] {t.error or ''}".rstrip()
    if status == "errored":
        return f"[task {task_id} errored] {t.error or 'unknown error'}"
    # still running / queued
    return (
        f"[task {task_id} still {status}] "
        f"timed out after {timeout}s; call await_task again to keep waiting."
    )


@function(
    name="cancel_task",
    description=(
        "Cancel an in-flight async task. Idempotent — calling on an "
        "already-terminal task is a no-op. The runner sets the "
        "session's cancel event, which propagates into the LLM "
        "stream + tool pre-invocation hook so the spawned agent "
        "stops at its next cooperative checkpoint. A 30s watchdog "
        "force-flips the entity if the worker won't drop.\n"
        "\n"
        "Args:\n"
        "  task_id: id of the task to cancel.\n"
        "  reason: optional human-readable reason recorded on the "
        "task entity."
    ),
    toolset=["core"],
    unsafe_in=["agent_spawn"],  # 同 task
)
def cancel_task(task_id: str, reason: str = "") -> str:
    """Signal cancel for an async task."""
    if not task_id or not isinstance(task_id, str):
        return "[cancel_task error] task_id required"
    from openprogram.agent.task import get_runner
    runner = get_runner()
    t = runner.cancel_task(task_id.strip(), reason=reason or None)
    if t is None:
        return f"[cancel_task error] unknown task_id={task_id!r}"
    return f"[cancel_task] task_id={task_id} status={t.status.value}"
