"""agent — spawn another agent in the same session and return its reply.

Same-session multi-agent model: a turn is just
``(predecessor, prompt, agent_id)``. The new turn lands as a branch
in the parent session's DAG. Three start points:

  * ``start_from="clean"`` (default) — the spawned agent starts at a new
    root (``caller=null``), inside the same session repo. It sees
    only the prompt; the result becomes a peer DAG tree alongside
    the original conversation.
  * ``start_from="inherit"`` — the spawned agent forks off the caller's
    turn, inheriting the conversation that led up to it. Same DAG
    semantics as a "fork from here" click.
  * ``start_from="SID:MSG_ID"`` — the spawned agent forks off that exact
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

import threading
from collections import OrderedDict

from openprogram.programs._runtime import function
from openprogram.programs.tools.agents.send_message.send_message.depth import (
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
#
# Calibrated against the eight reference implementations (see
# docs/reference/design/runtime/agent-collab-comparison.html §05). Five
# of them cap generations at 1: openclaw, codex-cli V1, hermes-agent,
# opencode (by injecting a deny rule instead of counting) and us.
# Claude Code's 3 is the outlier and it does not transfer:
#
#   * its leaked source tree has no depth counter at all. `Agent` is
#     removed from every subagent's tool pool unless USER_TYPE=ant
#     (src/constants/tools.ts:36-46), so an external user's effective
#     depth there is 1. The depth-3 constant in the 2.1.226 binary
#     replaces that env gate; it loosens a hard 1, it does not extend
#     an already-working 3.
#   * background subagents are capped at 1 regardless of the counter:
#     the async tool allowlist (tools.ts:55-71) omits `Agent`, so an
#     agent spawned to run unattended can never spawn again. Depth 3
#     only ever applies to synchronous nesting, where the parent's tool
#     call blocks for the whole child run and a human is watching.
#     Our unattended path is `run_in_background=True`, so 1 is the
#     value Claude Code enforces on the path that matches ours.
#   * their per-level tool pool is rebuilt and `Agent` disappears at the
#     last level, which they pay for in prompt-cache misses. Ours is
#     deliberately binary (depth.delegation_budget_left), so a raised
#     limit would leave `agent` visible at every level and each extra
#     generation would cost a refused call instead of a smaller tool
#     list.
#
# Raise it when a spawned worker genuinely needs its own workers — a
# decomposition whose sub-parts are themselves multi-agent. 2 is the
# next sensible value; the refusal text tells the worker to do the work
# itself, so a chain that hits the limit still finishes.
MAX_SPAWN_DEPTH = 1


def max_spawn_depth() -> int:
    """``agent.max_spawn_depth`` from config; 0 = unlimited."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        config_limit,
    )
    return config_limit("max_spawn_depth", MAX_SPAWN_DEPTH)


# Fan-out budget: how many NEW agents ONE parent turn may create. The
# spawn budget above bounds the chain downward and the message budget
# bounds it sideways between existing agents, but neither counts
# siblings: a spawn hands the child one more generation and leaves the
# parent's own count untouched, so before this guard a single turn could
# call ``agent`` until the turn's 50-iteration cap
# (agent_loop.MAX_INNER_ITERATIONS) stopped it, i.e. up to 50 full agent
# runs from one runaway turn.
#
# 8 comes from the two reference implementations that guard the same
# thing, read for what they actually count rather than for their number:
#
#   * openclaw caps live children per parent session at 5, range 1-20
#     (config/agent-limits.ts:5, enforced agents/subagent-spawn.ts:793).
#     That is the only true fan-out cap among the eight and it is a
#     spawn-time refusal, exactly like this one.
#   * hermes caps the task list of one ``delegate_task`` call at 3
#     (tools/delegate_tool.py:132) and separately truncates extra
#     ``delegate_task`` calls within one turn (run_agent.py:2344) —
#     that second guard is this one, aimed at the same runaway.
#   * pi-mono's 8 (examples/extensions/subagent/index.ts:27) caps one
#     call's task array and nothing else, so a parent there can still
#     spawn without bound over a conversation.
#
# hermes' 3 and pi-mono's 8 are batch-argument validation and do not
# transfer: ``agent`` creates one child per call, so a per-call cap
# would always read 1. openclaw's unit transfers, its number does not:
# it runs a pool of 8 against a per-parent cap of 5, we run 4
# (task.runner._DEFAULT_MAX_WORKERS) and background children queue on
# it, so a per-parent cap below the pool width would refuse fan-out
# that the pool is sized to absorb. 8 is two pool widths — a turn can
# fill the pool and keep one wave queued behind it, and the ninth spawn
# is refused with the existing agents' addresses so the model reuses
# them instead. It is also the most permissive of the three references,
# which is the right side to err on: this guard exists to stop a
# runaway, not to shape normal parallelism.
#
# Raise it for genuinely wide parallel work (one agent per file over a
# large set) and raise OPENPROGRAM_JOB_WORKERS with it, or the extra
# children only queue longer. 0 removes the limit.
MAX_SPAWN_FANOUT = 8

# Children already created by each parent turn, keyed (session, turn).
# Module state rather than a ContextVar because every tool body runs in
# its own ``copy_context()`` (functions/_runtime.py), so a ContextVar
# written by one ``agent`` call is invisible to the next one in the same
# turn. Bounded: a turn's key is dropped once _FANOUT_TURNS newer turns
# have run, long after the turn itself ended.
_FANOUT_TURNS = 256
_fanout_lock = threading.Lock()
_fanout_used: OrderedDict[tuple[str, str], int] = OrderedDict()


def max_spawn_fanout() -> int:
    """``agent.max_spawn_fanout`` from config; 0 = unlimited."""
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        config_limit,
    )
    return config_limit("max_spawn_fanout", MAX_SPAWN_FANOUT)


def claim_fanout_slot(session_id: str, turn_id: str) -> int | None:
    """Reserve one child slot for this parent turn.

    Returns ``None`` when the spawn may proceed, or the number already
    created when the turn is at its limit. Counted per turn rather than
    per live child: a turn that spawns, collects and spawns again is the
    same runaway shape as one that spawns in a burst, and the count is
    what the refusal text has to quote.
    """
    limit = max_spawn_fanout()
    if not limit:
        return None
    key = (session_id, turn_id)
    with _fanout_lock:
        used = _fanout_used.get(key, 0)
        if used >= limit:
            return used
        _fanout_used[key] = used + 1
        _fanout_used.move_to_end(key)
        while len(_fanout_used) > _FANOUT_TURNS:
            _fanout_used.popitem(last=False)
    return None


def release_fanout_slot(session_id: str, turn_id: str) -> None:
    """Return a slot reserved by :func:`claim_fanout_slot`."""
    limit = max_spawn_fanout()
    if not limit:
        return
    key = (session_id, turn_id)
    with _fanout_lock:
        used = _fanout_used.get(key, 0)
        if used <= 1:
            _fanout_used.pop(key, None)
        else:
            _fanout_used[key] = used - 1


def _release_after_spawn_failure(
    session_id: str, turn_id: str, exc: BaseException,
) -> None:
    """Release only real launch failures, not quota admission refusals."""
    from openprogram.agent.resource_governance import AdmissionRejected
    if isinstance(exc, AdmissionRejected):
        return
    release_fanout_slot(session_id, turn_id)


def _spawn_parent_id() -> str | None:
    """The DAG node a clean spawn should hang off.

    Prefer the executing @agentic_function's code node (``_call_id``): a
    composer-launched function has no assistant turn, and an LLM-issued
    function should own its inner ``agent()`` calls. Fall back to the
    dispatcher turn id, including the ``|node:<id>`` suffix process_runner
    threads for a pre-created function node.
    """
    try:
        from openprogram.agentic_programming.function import current_call_id
        cid = current_call_id()
        if cid:
            return cid
    except Exception:
        pass
    try:
        from openprogram.store import _current_turn_id
        raw = _current_turn_id.get() or ""
    except Exception:
        raw = ""
    if "|node:" in raw:
        raw = raw.rsplit("|node:", 1)[-1]
    if not raw or raw == "ROOT":
        return None
    return raw


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
    aid = _spawn_parent_id()
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
    start_from: str,
    description: str,
) -> str:
    """``agent(to=…)`` — dispatch a tracked task to an EXISTING agent.

    No branch is created: the task runs as the target branch's next
    turn (send_message's addressing + delivery path), but unlike a
    message it is a formal task — a Task entity is created, the
    dispatcher gets an execution id, and the result flows back like a spawn's.
    Busy target → the task queues in the target's inbox and runs when
    its current turn ends. Always asynchronous.
    """
    # to= dispatches onto an existing branch, which keeps its own
    # history — a start_from/fork-point choice contradicts that.
    if (start_from or "").strip().lower() not in ("", "clean"):
        return (
            "[agent error] to= and start_from are mutually exclusive — "
            "to= dispatches the task onto an EXISTING branch, which "
            "keeps its own history. Drop start_from, or drop to= and "
            "spawn a new agent."
        )
    # Same parent resolution as send_message (falls back to the session
    # head when no turn id is bound — e.g. a followup turn).
    from openprogram.programs.tools.agents.send_message.send_message.send_message import (
        _resolve_parent as _resolve_sender,
    )
    sid, aid, parent_agent = _resolve_sender()
    if not sid or not aid:
        return (
            "[agent error] no active parent turn — agent(to=…) must be "
            "called from inside an assistant turn."
        )
    from openprogram.agent.authority import authority_from_message, normalize_authority
    caller_authority = authority_from_message(sid, aid)
    chosen_agent = (agent_id or "").strip() or parent_agent or "main"

    # Budget guard: a dispatch to an existing agent spends the message
    # budget (branch-to-branch traffic), not the generation budget — it
    # creates no agent, so the generation count travels through
    # unchanged.
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        current_chain_generations,
        current_chain_messages,
        max_messages,
    )
    messages = current_chain_messages()
    generations = current_chain_generations()
    limit = max_messages()
    if limit and messages >= limit:
        return (
            f"[agent refused] this chain has passed {messages} messages, "
            f"the maximum ({limit}). Finish the work here instead of "
            "dispatching further."
        )

    # Addressing is send_message's, verbatim: SID:HEAD snaps to the
    # branch's current tip; a name resolves exact-first then unique
    # prefix; ambiguity lists candidates.
    from openprogram.programs.tools.agents.send_message.send_message.addressing import (
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

    from openprogram.programs.tools.agents.send_message.send_message.delivery import (
        job_header,
    )
    delivery_message = job_header(sid, aid) + prompt

    # Busy target → pre-create the Task (so the dispatcher holds a real
    # execution id while the work waits) and queue the dispatch in the
    # target's inbox; drain runs it, reusing the id. Same cross-session-
    # only reasoning as send_message: a same-session dispatch runs
    # inside the dispatcher's own turn, whose token is the one the busy
    # check would see.
    if run_session != sid:
        from openprogram.agent.run_control import is_turn_running
        if is_turn_running(run_session):
            from openprogram.agent import inbox
            from openprogram.agent.job.runner import _current_job_id
            from openprogram.agent.job import get_runner
            from openprogram.agent.job.types import Job, JobStatus, mint_job_id
            job = Job(
                id=mint_job_id(),
                parent_session_id=run_session,
                prompt=delivery_message,
                agent_id=chosen_agent,
                subject=description or prompt[:60],
                description=delivery_message,
                context_mode="inherit",
                parent_msg_id=target_tip,
                parent_job_id=_current_job_id.get(),
                label=label or None,
                wait=False,
                caller_msg_id=aid,
                caller_session_id=sid,
                creates_agent=False,
                relation="linked",
                origin_turn_id=aid,
                chain_messages=messages + 1,
                chain_generations=generations,
                caller_chain_generations=generations,
                status=JobStatus.PENDING,
                **normalize_authority(caller_authority),
            )
            runner = get_runner()
            try:
                runner.admit_job_entity(
                    job, creates_agent=False, caller_turn_id=aid,
                    dispatch_ready=False,
                )
            except Exception as e:  # noqa: BLE001
                return f"[agent error] {type(e).__name__}: {e}"
            try:
                q = inbox.enqueue(
                    run_session,
                    message=prompt,
                    sender_session_id=sid,
                    sender_msg_id=aid,
                    sender_agent_id=parent_agent,
                    agent_id=chosen_agent,
                    chain_messages=messages,
                    chain_generations=generations,
                    target_head_id=target_tip,
                    job_id=job.id,
                    authority=caller_authority,
                )
            except Exception as e:  # noqa: BLE001
                try:
                    # The Job was admitted canonically before touching the
                    # session inbox. If delivery fails, cancel that exact
                    # execution through RuntimeControlService so the command
                    # log, Job projection, owner signal, and resource
                    # admission converge on one outcome.
                    runner.cancel_execution(
                        job.id, reason=f"inbox enqueue failed: {e}",
                    )
                except Exception as cancel_error:  # noqa: BLE001
                    return (
                        f"[agent error] {type(e).__name__}: {e}; "
                        "canonical cleanup failed: "
                        f"{type(cancel_error).__name__}: {cancel_error}"
                    )
                return f"[agent error] {type(e).__name__}: {e}"
            if q == "duplicate":
                try:
                    # Duplicate admission is also an exact canonical
                    # cancellation. Do not terminalize only the projection;
                    # otherwise the accepted execution and its resource
                    # admission remain authoritative and can be picked up.
                    runner.cancel_execution(
                        job.id, reason="duplicate dispatch",
                    )
                except Exception as cancel_error:  # noqa: BLE001
                    return (
                        "[agent error] duplicate dispatch cleanup failed: "
                        f"{type(cancel_error).__name__}: {cancel_error}"
                    )
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
                f"[job dispatched, queued] execution_id={job.id} "
                f"target={run_session}:{target_tip} — the target is busy "
                "running a turn; your task runs when it ends and the "
        "result comes back automatically; use job_output for the reply and "
        "the canonical execution resource and control surfaces to inspect "
        "or manage it."
            )

    from openprogram.events import emit_safe
    from openprogram.programs.tools.agents.send_message.shared import _emit_branch_ui
    emit_safe(
        "branch.message_sent",
        "agent",
        {"from": f"{sid}:{aid}", "to": f"{run_session}:{target_tip}"},
    )
    _emit_branch_ui(sid, "sent", f"{run_session}:{target_tip}", prompt)

    try:
        from openprogram.agent.sub_agent_run import run_agent_turn_async
        job_id = run_agent_turn_async(
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
            chain_messages=messages + 1,
            chain_generations=generations,
            caller_chain_generations=generations,
            authority=caller_authority,
            creates_agent=False,
        )
    except Exception as e:  # noqa: BLE001
        return f"[agent error] {type(e).__name__}: {e}"
    return (
        f"[job dispatched] execution_id={job_id} "
        f"target={run_session}:{target_tip}\n"
        "The target branch is running your task as its next turn; the "
        "result comes back to you automatically. Use job_output for the reply "
        "and the canonical execution resource and control surfaces to inspect "
        "or manage it."
    )


def _agent_impl(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    start_from: str = "clean",
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
        # a to= dispatch targets an existing agent, whose lifecycle this
        # call does not own.
        if archive_when_done:
            return (
                "[agent error] archive_when_done applies to the branch "
                "this call spawns — to= dispatches to an EXISTING agent "
                "instead. Drop archive_when_done, or archive the target "
                "later with archive_agent."
            )
        # Dispatch to an EXISTING agent — always async, returns a
        # execution id immediately; run_in_background is meaningless here
        # and ignored.
        return _dispatch_to_existing(
            prompt=prompt,
            to=to.strip(),
            agent_id=agent_id,
            start_from=start_from,
            description=description,
        )
    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[agent error] no active parent turn — agent() must be called "
            "from inside an assistant turn (the dispatcher sets the "
            "session + turn ContextVars on entry)."
        )
    from openprogram.agent.authority import authority_from_message
    caller_authority = authority_from_message(sid, aid)
    chosen_agent = (agent_id or "").strip() or parent_agent or "main"

    label = (description or "").strip()
    # Sanitize label for branch name: git ref chars only.
    if label:
        label = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in label
        )[:24]

    # Generation guard — counts agents CREATED by this chain, and only
    # that: reading a worker's result travels on the message counter, so
    # an agent that collects a batch of replies can create the next
    # batch. The cap is much tighter than the message budget on purpose:
    # only the main agent may spawn; a spawned agent works with its own
    # tools, it never re-delegates (observed live: a 5-generation
    # weather-query delegation chain, every hop just re-wording the same
    # prompt).
    from openprogram.programs.tools.agents.send_message.send_message.depth import (
        current_chain_generations,
        current_chain_messages,
        max_messages,
        set_chain_generations,
        set_chain_messages,
    )
    generations = current_chain_generations()
    messages = current_chain_messages()
    spawn_limit = max_spawn_depth()
    if spawn_limit and generations >= spawn_limit:
        return (
            f"[agent refused] this chain has already created "
            f"{generations} generations of agents, the maximum "
            f"({spawn_limit}). Do the work yourself with your own tools "
            "instead of delegating again."
        )

    # A spawn also hands the child a message, so it spends the message
    # budget too — otherwise spawning would be a way around the cap that
    # send_message and agent(to=…) both respect.
    message_limit = max_messages()
    if message_limit and messages >= message_limit:
        return (
            f"[agent refused] this chain has passed {messages} messages, "
            f"the maximum ({message_limit}). Finish the work here "
            "instead of spawning another agent."
        )

    # Fan-out guard — the counters above bound the chain downward and
    # along, this bounds siblings within one turn (see
    # MAX_SPAWN_FANOUT). Claimed after the two checks so a refused
    # spawn never spends a slot.
    used = claim_fanout_slot(sid, aid)
    if used is not None:
        return (
            f"[agent refused] this turn has already created {used} "
            f"agents, the maximum ({max_spawn_fanout()}). Give the rest "
            "of the work to one of them with agent(to=…) (see "
            "list_agents), or do it here."
        )

    # Resolve the start point. Besides the two named modes, a node
    # address "SID:MSG_ID" forks the new branch off that exact node —
    # the spawned agent inherits the chain up to it.
    mode = (start_from or "").strip() or "clean"
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
            release_fanout_slot(sid, aid)
            return (
                f"[agent error] start_from {start_from!r} — a node address needs "
                "both parts: 'SID:MSG_ID'."
            )
        from openprogram.agent.session_db import default_db
        store = default_db()
        if store.get_session(fork_sid) is None:
            release_fanout_slot(sid, aid)
            return (
                f"[agent error] start_from {start_from!r} — session "
                f"{fork_sid!r} not found (see list_agents)."
            )
        if not store.message_exists(fork_sid, fork_msg):
            release_fanout_slot(sid, aid)
            return (
                f"[agent error] start_from {start_from!r} — message "
                f"{fork_msg!r} not found in session {fork_sid!r}."
            )
        run_session = fork_sid
        branch_from = fork_msg
        mode = "inherit"  # fork = inherit the chain up to the node
    else:
        release_fanout_slot(sid, aid)
        return (
            f"[agent error] unknown start_from {start_from!r} — use 'clean' "
            "(default, new root, no parent history), 'inherit' (fork off "
            "this turn, full chain visible), or 'SID:MSG_ID' (fork off "
            "that exact node)."
        )

    if run_in_background:
        # Background path: submit and return the execution id. The runner is
        # responsible for state transitions and attach card updates.
        try:
            from openprogram.agent.sub_agent_run import (
                emit_spawn_event,
                run_agent_turn_async,
                write_attach_placeholder_for_spawn,
            )
            from openprogram.agent.job.types import mint_job_id
            from openprogram.programs._runtime import current_tool_call_id
            import uuid as _uuid

            job_id = mint_job_id()
            attach_id = _uuid.uuid4().hex[:12]
            tool_call_id = current_tool_call_id()

            # Admission owns the first external side effect. Persist the
            # placeholder, including its durable job id, and publish the live
            # running card before the runner is allowed to dispatch the job.
            def _on_accepted(job) -> None:
                written_id = write_attach_placeholder_for_spawn(
                    session_id=sid,
                    caller_msg_id=aid,
                    label=label or None,
                    prompt=prompt,
                    chosen_agent=chosen_agent,
                    node_id=attach_id,
                    job_id=job.id,
                    target_session_id=run_session,
                )
                if written_id != attach_id:
                    raise RuntimeError("failed to persist agent attach placeholder")
                try:
                    emit_spawn_event(
                        session_id=sid,
                        status="running",
                        label=label or None,
                        prompt=prompt,
                        chosen_agent=chosen_agent,
                        card_id=attach_id,
                        target_session_id=run_session,
                        tool_call_id=tool_call_id,
                        job_id=job.id,
                    )
                except Exception:
                    pass

            job_id = run_agent_turn_async(
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
                # chain counts so the guards above trip in the child too.
                # Without caller_msg_id the async branch forked from ROOT.
                caller_msg_id=aid,
                # A fork into another session must return its reply to
                # the caller's session, not the fork target's.
                caller_session_id=sid if run_session != sid else None,
                chain_messages=messages + 1,
                # The child IS the new generation; the reply turn back
                # here is not, so it gets this turn's count.
                chain_generations=generations + 1,
                caller_chain_generations=generations,
                # This call CREATES the branch — let the runner archive
                # it at terminal state if the spawn asked for that.
                archive_when_done=archive_when_done,
                attach_pointer_id=attach_id,
                job_id=job_id,
                spawn_caller=aid,
                on_accepted=_on_accepted,
                authority=caller_authority,
            )
        except Exception as e:  # noqa: BLE001
            _release_after_spawn_failure(sid, aid, e)
            return f"[agent error] {type(e).__name__}: {e}"
        return (
            f"[agent spawned async] execution_id={job_id}\n"
            f"Use job_output({job_id!r}) for the final reply, and the "
            f"canonical execution resource and control surfaces for execution "
            f"{job_id!r}."
        )

    # Announce the spawn BEFORE running it: a synchronous spawn blocks
    # this tool call for as long as the sub-agent runs, so without a
    # "running" event the caller's turn shows nothing until it finishes.
    # The id is minted here and reused for the attach node below, so the
    # live card and the reloaded row are one and the same.
    import uuid as _uuid
    from openprogram.programs._runtime import current_tool_call_id
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
            target_session_id=run_session,
            tool_call_id=_tool_call_id,
        )
        # Bind both counts + 1 for the child turn (same-context
        # synchronous run), mirroring what the async runner does from
        # the Task's chain_messages / chain_generations.
        _chain_tokens = [
            set_chain_messages(messages + 1),
            set_chain_generations(generations + 1),
        ]
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
                spawn_caller=(
                    aid if branch_from is None or run_session != sid else None
                ),
                caller_msg_id=aid,
                caller_session_id=sid if run_session != sid else None,
                advance_head=False,  # same-session spawn never steals head
                authority=caller_authority,
            )
        finally:
            for _token in _chain_tokens:
                _token.var.reset(_token)
    except Exception as e:  # noqa: BLE001
        # The card is already on screen in "running" — close it out, or
        # it spins forever.
        try:
            emit_spawn_event(
                session_id=sid, status="errored", label=label or None,
                prompt=prompt, chosen_agent=chosen_agent, card_id=_card_id,
                target_session_id=run_session,
                tool_call_id=_tool_call_id,
                content=f"{type(e).__name__}: {e}",
            )
        except Exception:
            pass
        _release_after_spawn_failure(sid, aid, e)
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
            target_session_id=run_session,
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
            target_session_id=run_session,
            tool_call_id=_tool_call_id,
            head_id=result.head_id,
            content=(result.final_text or result.error or "").strip(),
        )
    except Exception:
        pass

    # Spawn-branch meta, after the result is in hand: archive on
    # request. Best-effort — the result below flows back regardless.
    if result.head_id and archive_when_done:
        try:
            import time as _time
            from openprogram.agent.session_db import default_db
            default_db().set_branch_meta(
                run_session, result.head_id,
                archived=True, archived_at=_time.time(),
            )
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "spawn branch archive stamp failed", exc_info=True,
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
    # Exposed while the chain still has message budget; gone once that
    # is spent, because every form of delegation hands a message over
    # and a tool sitting in the listing invites the model to reach for
    # it. The generation budget refuses at runtime instead — a spawned
    # agent keeps `agent` for to= dispatch with no generations left.
    can_use=_delegation_budget_left,
)
def agent(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    start_from: str = "clean",
    run_in_background: bool = False,
    to: str = "",
    archive_when_done: bool = False,
) -> str:
    """Spawn a new agent, or dispatch a tracked task to an existing one.

    Without ``to``: spawns a new agent. ``run_in_background=False``
    (default) blocks until it finishes and returns its final reply;
    ``run_in_background=True`` returns immediately with an execution id;
    use the canonical execution resource and control surfaces.

    With ``to``: no agent is created — the task is dispatched to the
    named EXISTING branch and runs as its next turn. Always
    asynchronous: returns an execution id immediately (``run_in_background``
    is ignored); the result comes back automatically.

    Args:
        prompt: full instruction. In ``start_from="clean"`` this is ALL
            the spawned agent sees, so include any context it needs.
        description: short label (1-3 words) used as the branch name.
        agent_id: agent profile to run under. Defaults to this
            session's agent.
        start_from: ``"clean"`` (default) ⇒ the spawned agent starts at
            a new root with only the prompt visible. ``"inherit"`` ⇒
            forks off this turn and sees the full chain that led here.
            ``"SID:MSG_ID"`` ⇒ forks off that exact node. Mutually
            exclusive with ``to``.
        run_in_background: False (default) blocks for the final
            reply. True returns an execution id immediately for parallel
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
        agent_id=agent_id, start_from=start_from,
        run_in_background=run_in_background,
        to=to,
        archive_when_done=archive_when_done,
    )
