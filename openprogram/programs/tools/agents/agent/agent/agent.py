"""agent — start a new agent for a bounded task and return its result.

A helper is a branch of the caller's conversation DAG. Three start points:

  * ``start_from="fresh"`` (default) — a new root that sees only the
    prompt (``context_mode="clean"``). The light peers on the reference
    board (Maka, Hermes, OpenCode, Kimi Code, Antigravity) all start a
    helper from its brief; it is the cheap default.
  * ``start_from="fork"`` — forks off the caller's turn with the whole
    conversation, and keeps the caller's tool list so the provider's
    prompt cache is shared (Claude Code's fork). Only with the caller's
    own Agent; another Agent falls back to fresh with a note.
  * ``start_from="SID:MSG_ID"`` — forks off that exact node (any session).

``inherit`` and ``clean`` are the older names of fork and fresh.

``agent`` names a saved Agent (id or name) that other agents may use; the
tool's description lists them (``agent_registry/delegation.py``).
``agent_id`` is the older parameter name. There is no per-call model:
the Agent's own settings decide it (Codex v2 and ZCode removed the
per-call override).

Every result, waited or pushed later as a notice, is the result block of
``runtime/jobs/agent_result.py``. Talking to an agent that already exists
is ``send_message``'s job; ``to=`` here is refused with a pointer to it.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from openprogram.programs._host.runtime import ToolReturn, function
from openprogram.runtime.permissions import refusal
from openprogram.programs.tools.agents.send_message.send_message.depth import (
    delegation_budget_left as _delegation_budget_left,
)

from .prompt import DESCRIPTION
import logging

_log = logging.getLogger(__name__)


# Spawn budget: how many generations of NEW agents a chain may create.
# 1 by default (depth.MAX_SPAWN_DEPTH): the main agent delegates and a
# helper does the work itself; the helper's tool list has no ``agent``
# (depth.delegation_budget_left), so the limit costs no refused calls.
# The reasoning and the peers it follows are in depth.py.
from openprogram.programs.tools.agents.send_message.send_message.depth import (  # noqa: E402
    MAX_SPAWN_DEPTH,
    max_spawn_depth,
)


# Fan-out budget: how many NEW agents ONE parent turn may create. The
# spawn budget above bounds the chain downward and the message budget
# bounds it sideways between existing agents, but neither counts
# siblings: a spawn hands the child one more generation and leaves the
# parent's own count untouched, so before this guard a single turn could
# call ``agent`` until the user stops the turn, i.e. unbounded
# nested agent runs from one runaway turn unless a caller cap is set.
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
# it runs a pool of 8 against a per-parent cap of 5, we run 8 too
# (resource_governance.limits.DEFAULT_JOB_WORKERS) and background children
# queue on it, so a per-parent cap below the pool width would refuse fan-out
# that the pool is sized to absorb. 8 was two pool widths of 4 — a turn can
# fill the pool and keep one wave queued behind it, and the ninth spawn
# is refused with the existing agents' addresses so the model reuses
# them instead. It is also the most permissive of the three references,
# which is the right side to err on: this guard exists to stop a
# runaway, not to shape normal parallelism.
#
# Off by default (0); the job pool bounds how many run at once, and
# OPENPROGRAM_JOB_WORKERS raises that.
MAX_SPAWN_FANOUT = 0

# Children already created by each parent turn, keyed (session, turn).
# Module state rather than a ContextVar because every tool body runs in
# its own ``copy_context()`` (programs/_host/runtime.py), so a ContextVar
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
    from openprogram.runtime.jobs.resource_governance import AdmissionRejected
    if isinstance(exc, AdmissionRejected):
        return
    release_fanout_slot(session_id, turn_id)


def _spawn_parent_id() -> str | None:
    """The DAG node a clean spawn should hang off.

    Prefer the executing Agent method's code node (``_call_id``): a
    composer-launched function has no assistant turn, and an LLM-issued
    function should own its inner ``agent()`` calls. Fall back to the
    dispatcher turn id, including the ``|node:<id>`` suffix process_runner
    threads for a pre-created function node.
    """
    try:
        from openprogram.runtime.agent.call_state import current_call_id
        cid = current_call_id()
        if cid:
            return cid
    except Exception:
        # Call state is optional context; fall back to the dispatcher turn id.
        _log.debug("current call id unavailable", exc_info=True)
    try:
        from openprogram.state.store import _current_turn_id
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
        from openprogram.runtime.execution.run_control.binding import (
            _current_session_id,
        )
        sid = _current_session_id.get(None)
    except Exception:
        sid = None
    aid = _spawn_parent_id()
    agent_id = None
    if sid:
        try:
            from openprogram.runtime.context.session_db import default_db
            sess = default_db().get_session(sid) or {}
            agent_id = sess.get("agent_id")
        except Exception:
            agent_id = None
    return sid, aid, agent_id


AGENT_NAME_MAX = 48


def agent_name(description: str | None) -> str:
    """A sub-agent's name: its ``description`` as written, on one line and
    at most ``AGENT_NAME_MAX`` characters, cut at a word boundary when one
    falls in the second half. Spaces stay as typed, two in a row included,
    as Claude Code keeps an Agent call's description; only a line break or
    tab, with the indentation around it, becomes one space. send_message
    reaches it by this exact text; see addressing.name_key for the looser
    match that folds spaces."""
    import re
    # Every line boundary str.splitlines knows (U+2028, NEL and the rest),
    # and tabs, so the name is one line whatever the description held.
    breaks = "\t\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
    text = re.sub(f" *[{breaks}][ {breaks}]*", " ", description or "").strip()
    if len(text) <= AGENT_NAME_MAX:
        return text
    cut = text[:AGENT_NAME_MAX + 1]
    space = cut.rfind(" ")
    if space >= AGENT_NAME_MAX // 2:
        return cut[:space].rstrip(" ,;:-")
    return text[:AGENT_NAME_MAX].rstrip()


def _isolated_worktree(session_id: str, label: str, job_id: str):
    """Create the worktree an ``isolation="worktree"`` sub-agent runs in.

    Made from the repository of the folder this turn works in, so a
    sub-agent of a branch that itself runs in a worktree nests inside it
    and merges back into it. That folder is the turn's worktree, else the
    conversation's project folder, else the configured default folder —
    never the server process's working directory, which is wherever the
    worker happened to start (a checkout of OpenProgram itself, under a
    test run). Returns ``(worktree, error_text)``."""
    import subprocess
    from openprogram.runtime.conversation.session_settings.workdir import project_workdir_for
    from openprogram.state.paths import configured_workdir
    from openprogram.state.worktree.context import current_worktree_path
    from openprogram.state.worktree.manager import WorktreeError, get_manager
    from openprogram.state.worktree.store import current_branch_for
    base = current_worktree_path()
    if not base:
        folder = project_workdir_for(session_id)
        base = str(folder) if folder is not None else configured_workdir()
    top = None
    if base:
        try:
            proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=base,
                                  capture_output=True, text=True, timeout=10)
            top = proc.stdout.strip() if proc.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            top = None
    if not top:
        return None, (f"[agent error] isolation='worktree' needs a git repository, and "
                      f"{base or 'this turn'} is not in one. Spawn without isolation, "
                      "or work in a repository.")
    try:
        wt = get_manager().create_worktree(
            top, label=label or "agent", parent_session=session_id,
            parent_job=job_id, parent_branch=current_branch_for(session_id),
            ephemeral=True,
        )
    except WorktreeError as exc:
        return None, f"[agent error] {exc}"
    return wt, None


def _worktree_outcome(worktree) -> str:
    """What the caller learns about a finished isolated run's worktree."""
    from openprogram.state.worktree.manager import get_manager
    manager = get_manager()
    try:
        if manager.discard_if_unchanged(worktree.id) is not None:
            return f"[worktree {worktree.id}] the agent changed nothing; its worktree was removed."
        current = manager.get_worktree(worktree.id) or worktree
        summary = manager.summarize(current)
    except Exception as exc:  # noqa: BLE001
        return f"[worktree {worktree.id}] at {worktree.worktree_path}: {exc}"
    count = len(summary["files"])
    pending = " (uncommitted changes)" if summary["dirty"] else ""
    return (f"[worktree {worktree.id}] {count} file(s) changed{pending} on branch "
            f"{worktree.branch_name} at {worktree.worktree_path}. Nothing reached "
            f"{worktree.source_repo} yet: commit there and call worktree_merge("
            f"worktree_id={worktree.id!r}, strategy='auto'), or worktree_discard it; "
            "the person can also merge or discard it from the input box.")


_START_ALIASES = {"inherit": "fork", "clean": "fresh"}


def _resolve_agent(ref: str, parent_agent: str | None) -> tuple[str, object, str | None]:
    """``(agent_id, spec_or_None, error)`` for the Agent a call asks for.

    Empty runs the caller's own Agent. A name or id must be a saved Agent
    other agents may use (``delegation``), except the caller's own."""
    own = parent_agent or "main"
    ref = (ref or "").strip()
    try:
        from openprogram.runtime.agent_registry import delegation, manager
    except Exception:  # pragma: no cover - registry is part of the package
        return (ref or own), None, None
    if not ref:
        try:
            return own, manager.get(own), None
        except Exception:
            return own, None, None
    try:
        # Saved Agents and the project's Markdown agent files.
        specs = delegation.delegatable()
    except Exception:
        specs = []
    spec = delegation.find(ref, specs)
    if spec is None:
        if not specs:
            # No registry on this instance (a bare test or a fresh state
            # dir): the id is taken as given, as before.
            return ref, None, None
        names = ", ".join(name for _, name, _ in delegation.roster(specs)) or "none"
        return ref, None, (f"[agent error] no saved Agent is named {ref!r}. Agents you can "
                           f"start: {names}. Leave agent empty to run your own Agent.")
    if spec.id != own and not delegation.delegation_enabled(spec):
        return spec.id, spec, (f"[agent error] Agent {spec.name or spec.id!r} is not offered "
                               "to other agents (its \"other agents can use it\" setting is "
                               "off). Pick one from the list in this tool's description.")
    return spec.id, spec, None


def _agent_impl(
    prompt: str,
    description: str = "",
    agent_id: str = "",
    start_from: str = "fresh",
    run_in_background: bool = False,
    to: str = "",
    archive_when_done: bool = False,
    isolation: str = "",
    agent: str = "",
) -> str:
    """Implementation body. Pulled out of the @function-wrapped binding
    so unit tests can drive it directly with their own ContextVars
    instead of going through the AgentTool execute path.
    """
    if (to or "").strip():
        # agent(to=…) moved into send_message, which also reaches Agents by
        # name; old transcripts keep their stored results.
        return ("[agent error] agent() only starts NEW agents. To give work to an "
                f"agent that already exists, call send_message(to={to.strip()!r}, "
                "message=…); its reply comes back to you automatically.")
    isolation = (isolation or "").strip().lower()
    if isolation and isolation not in ("worktree", "none"):
        return (f"[agent error] unknown isolation {isolation!r} — use 'worktree' "
                "(the agent works in its own git worktree), 'none' (it shares this "
                "folder) or leave it empty for the default.")
    sid, aid, parent_agent = _resolve_parent()
    if not sid or not aid:
        return (
            "[agent error] no active parent turn — agent() must be called "
            "from inside an assistant turn (the dispatcher sets the "
            "session + turn ContextVars on entry)."
        )
    from openprogram.runtime.permissions.authority import authority_from_message
    caller_authority = authority_from_message(sid, aid)
    chosen_agent, spec, agent_error = _resolve_agent(agent or agent_id, parent_agent)
    if agent_error:
        return agent_error
    # A specialist runs on its own model (Explorer on the utility tier); the
    # helper's conversation is the caller's, whose model would win otherwise.
    try:
        from openprogram.runtime.agent_registry.delegation import delegate_model
        helper_model = delegate_model(spec, caller_agent=parent_agent or "main")  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - the caller's model serves
        _log.debug("delegate model not resolved for %s", chosen_agent, exc_info=True)
        helper_model = None

    # The agent's name as people and the model read it. Identifiers made
    # from it (a worktree's ref and folder) are slugged where they are made.
    label = agent_name(description)

    # Generation guard — counts agents CREATED by this chain, and only
    # that: reading a worker's result travels on the message counter, so
    # an agent that collects a batch of replies can create the next
    # batch. A fresh helper at the limit has no agent tool at all; a
    # forked one keeps it for the shared cache and is refused here.
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
        raise refusal.agent_budget("agent", "generations", spawn_limit,
                                   "do the work yourself with your own tools instead of delegating again")

    # The older message budget, when someone set it.
    message_limit = max_messages()
    if message_limit and messages >= message_limit:
        raise refusal.agent_budget("agent", "messages", message_limit,
                                   "finish the work here instead of spawning another agent")

    # Spend per delegation tree (delegation_watch): no new helper from a
    # turn whose helpers have used the allowance.
    try:
        from openprogram.runtime.jobs import delegation_watch
        spent = delegation_watch.spawn_refusal(sid, aid)
    except Exception:
        _log.debug("delegation spend not checked", exc_info=True)
        spent = None
    if spent is not None:
        raise refusal.agent_budget("agent", "dollars", spent[1],
                                   "finish the work here with what the helpers already returned")

    # Fan-out guard — the counters above bound the chain downward and
    # along, this bounds siblings within one turn (see
    # MAX_SPAWN_FANOUT). Claimed after the checks so a refused spawn
    # never spends a slot.
    used = claim_fanout_slot(sid, aid)
    if used is not None:
        raise refusal.agent_budget("agent", "agents", max_spawn_fanout(),
                                   "give the rest of the work to one of them with send_message "
                                   "(see list_agents), or do it here")

    # Resolve the start point: fresh (default), fork, or a node address.
    mode = (start_from or "").strip() or "fresh"
    mode = _START_ALIASES.get(mode.lower(), mode)
    note = ""
    run_session = sid
    branch_from: str | None = None
    if mode.lower() in ("fresh", "fork"):
        mode = mode.lower()
        if mode == "fork" and chosen_agent != (parent_agent or "main"):
            # A fork shares the caller's cached prompt only when the bytes
            # match, and another Agent's prompt and tools differ.
            mode = "fresh"
            note = ("[agent note] start_from='fork' needs your own Agent; this agent "
                    "started fresh and saw only your prompt.")
        branch_from = aid if mode == "fork" else None
        context_mode = "inherit" if mode == "fork" else "clean"
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
        from openprogram.runtime.context.session_db import default_db
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
        context_mode = "inherit"  # fork = inherit the chain up to the node
    else:
        release_fanout_slot(sid, aid)
        return (
            f"[agent error] unknown start_from {start_from!r} — use 'fresh' "
            "(default, only your prompt), 'fork' (a copy of this "
            "conversation) or 'SID:MSG_ID' (fork off that exact node)."
        )

    # Isolation: the call's own value, else the Agent's, else the default
    # rule (a background helper that edits files gets its own worktree).
    from openprogram.runtime.agent_registry.delegation import isolation_for
    wanted, by_default = isolation_for(
        spec, requested=isolation, background=bool(run_in_background),  # type: ignore[arg-type]
    )
    worktree = None
    isolated_job_id = None
    if wanted == "worktree" and run_session != sid:
        if not by_default:
            release_fanout_slot(sid, aid)
            return ("[agent error] isolation='worktree' runs a sub-agent of this "
                    "conversation; it cannot fork into another session.")
        wanted = ""
    if wanted == "worktree":
        from openprogram.runtime.jobs.types import mint_job_id as _mint_job_id
        isolated_job_id = _mint_job_id()
        worktree, worktree_error = _isolated_worktree(sid, label, isolated_job_id)
        if worktree_error:
            if not by_default:
                release_fanout_slot(sid, aid)
                return worktree_error
            # Not a git repository: the default rule shares the folder.
            isolated_job_id = None

    def _drop_worktree() -> None:
        if worktree is None:
            return
        try:
            from openprogram.state.worktree.manager import get_manager as _wt_manager
            # The spawn failed, so no helper writes there.
            _wt_manager().discard_worktree(worktree.id, force=True, owner_done=True)
        except Exception:
            _log.debug("isolated worktree not removed after a failed spawn", exc_info=True)

    if run_in_background:
        # Background path: submit and return the execution id. The runner is
        # responsible for state transitions and attach card updates.
        try:
            from openprogram.runtime.conversation.peer_agents.sub_agent_run import (
                run_agent_turn_async,
                write_attach_placeholder_for_spawn,
            )
            from openprogram.runtime.jobs.types import mint_job_id
            from openprogram.programs._host.runtime import current_tool_call_id
            import uuid as _uuid

            job_id = isolated_job_id or mint_job_id()
            attach_id = _uuid.uuid4().hex[:12]
            tool_call_id = current_tool_call_id()

            # Admission owns the first external side effect. Persist the
            # placeholder, including its durable job id, before the runner is
            # allowed to dispatch the job; the transcript publishes the card.
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
                    tool_call_id=tool_call_id,
                )
                if written_id != attach_id:
                    raise RuntimeError("failed to persist agent attach placeholder")

            job_id = run_agent_turn_async(
                session_id=run_session,
                prompt=prompt,
                agent_id=chosen_agent,
                branch_from=branch_from,
                label=label or None,
                subject=description or prompt[:60],
                description=description or prompt,
                context_mode=context_mode,
                # Anchor the spawned branch to THIS turn (a fresh start gets
                # its root's caller from this via the runner) and carry the
                # chain counts so the guards above trip in the child too.
                caller_msg_id=aid,
                # A fork into another session must return its reply to
                # the caller's session, not the fork target's.
                caller_session_id=sid if run_session != sid else None,
                chain_messages=messages + 1,
                # The child IS the new generation; the reply turn back
                # here is not, so it gets this turn's count.
                chain_generations=generations + 1,
                caller_chain_generations=generations,
                archive_when_done=archive_when_done,
                attach_pointer_id=attach_id,
                job_id=job_id,
                spawn_caller=aid,
                on_accepted=_on_accepted,
                authority=caller_authority,
                worktree_id=worktree.id if worktree is not None else None,
                # On the Job too, so a card rebuilt after a crash pairs
                # with this call (jobs/handoff.ensure_attach_placeholder).
                tool_call_id=tool_call_id,
                model_override=helper_model,
            )
        except Exception as e:  # noqa: BLE001
            _drop_worktree()
            _release_after_spawn_failure(sid, aid, e)
            return f"[agent error] {type(e).__name__}: {e}"
        where = (
            f"\nIt works in worktree {worktree.id} at {worktree.worktree_path} "
            f"(branch {worktree.branch_name}); review its diff and run the tests "
            "before merging it with worktree_merge."
            if worktree is not None else ""
        )
        return (
            f"[agent started in background] execution_id={job_id}\n"
            "Its result comes back to you automatically as a notice; end your "
            "turn or keep working. read_conversation with this id shows its "
            f"progress.{where}" + (f"\n{note}" if note else "")
        )

    # Write the running card BEFORE the run: a synchronous spawn blocks
    # this tool call for as long as the sub-agent runs, so without the
    # placeholder the caller's turn shows nothing until it finishes. The
    # id is minted here and reused for the end write, so the card is
    # updated in place.
    import uuid as _uuid
    from openprogram.programs._host.runtime import current_tool_call_id
    _card_id = _uuid.uuid4().hex[:12]
    _tool_call_id = current_tool_call_id()
    accepted: dict[str, object] = {}
    try:
        from openprogram.runtime.conversation.peer_agents.sub_agent_run import (
            AgentTurnResult,
            run_agent_turn,
            write_attach_placeholder_for_spawn,
            write_attach_pointer_for_spawn,
        )

        def _on_accepted(job) -> None:
            accepted["job_id"] = job.id
            # The running card carries its job from its first write. If the
            # write fails the spawn still runs and the end write adds the row.
            write_attach_placeholder_for_spawn(
                session_id=sid,
                caller_msg_id=aid,
                label=label or None,
                prompt=prompt,
                chosen_agent=chosen_agent,
                node_id=_card_id,
                job_id=job.id,
                target_session_id=run_session,
                tool_call_id=_tool_call_id,
                delivery="tool_result",
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
                # fresh = new branch → its root's caller = the spawning
                # node, so the DAG attaches the branch to this turn instead of
                # forking it from ROOT (dag/overview.md §2.3).
                spawn_caller=(
                    aid if branch_from is None or run_session != sid else None
                ),
                caller_msg_id=aid,
                caller_session_id=sid if run_session != sid else None,
                # The child turn runs as its own execution, which the
                # context variables above do not reach: its Job carries
                # the counts, as a background helper's does.
                chain_messages=messages + 1,
                chain_generations=generations + 1,
                caller_chain_generations=generations,
                advance_head=False,  # same-session spawn never steals head
                authority=caller_authority,
                on_accepted=_on_accepted,
                attach_pointer_id=_card_id,
                job_id=isolated_job_id,
                worktree_id=worktree.id if worktree is not None else None,
                tool_call_id=_tool_call_id,
                model_override=helper_model,
            )
        finally:
            for _token in _chain_tokens:
                _token.var.reset(_token)
    except Exception as e:  # noqa: BLE001
        # The card is already on screen in "running" — close it out, or
        # it spins forever.
        write_attach_pointer_for_spawn(
            session_id=sid,
            caller_msg_id=aid,
            result=AgentTurnResult(failed=True, error=f"{type(e).__name__}: {e}"),
            label=label or None,
            prompt=prompt,
            chosen_agent=chosen_agent,
            node_id=_card_id,
            target_session_id=run_session,
            tool_call_id=_tool_call_id,
        )
        _drop_worktree()
        _release_after_spawn_failure(sid, aid, e)
        return f"[agent error] {type(e).__name__}: {e}"

    # Write an attach pointer node so the DAG paints a `function=attach`
    # square_outline on the caller's lane referencing the sub-branch tip.
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
            tool_call_id=_tool_call_id,
        )
    except Exception:
        # The result is returned regardless; only the attach pointer is missing.
        _log.warning("spawn attach pointer not written session=%s", sid, exc_info=True)

    # Spawn-branch meta, after the result is in hand: archive on
    # request. Best-effort — the result below flows back regardless.
    if result.head_id and archive_when_done:
        try:
            import time as _time
            from openprogram.runtime.context.session_db import default_db
            default_db().set_branch_meta(
                run_session, result.head_id,
                archived=True, archived_at=_time.time(),
            )
        except Exception:
            _log.debug("spawn branch archive stamp failed", exc_info=True)

    return _sync_result(
        result, run_session=run_session, job_id=str(accepted.get("job_id") or isolated_job_id or ""),
        label=label, chosen_agent=chosen_agent, worktree=worktree, note=note,
    )


def _sync_result(result, *, run_session: str, job_id: str, label: str,
                 chosen_agent: str, worktree, note: str = "") -> str:
    """The waited-for result in the one result shape (agent_result.render)."""
    from openprogram.runtime.jobs.agent_result import render
    from openprogram.runtime.jobs.types import Job, JobStatus
    job = None
    if job_id:
        try:
            from openprogram.runtime.jobs.store import load_job
            job = load_job(run_session, job_id)
        except Exception:
            job = None
    if job is None:
        job = Job(id=job_id or "?", parent_session_id=run_session, prompt="",
                  agent_id=chosen_agent, label=label or None)
    failed = bool(result.error and not result.final_text) or (result.failed and not result.final_text)
    if failed and job.status not in (JobStatus.CANCELLED, JobStatus.ERRORED):
        job.status = JobStatus.ERRORED
    elif not failed and job.status != JobStatus.COMPLETED:
        job.status = JobStatus.COMPLETED
    job.head_id = result.head_id or job.head_id
    reply = result.final_text or ""
    body = None
    if job.status == JobStatus.CANCELLED and not result.final_text:
        # Stopped, by the person or a limit: no result is coming, and what
        # it wrote stays on its branch (the same line a stopped background
        # helper's notice carries).
        body = f"(no reply) Its work so far: read_conversation(\"{job.id}\")."
    elif failed:
        reply = f"[agent error: head={job.head_id or '?'}] {result.error or 'the agent ended without a reply'}"
    elif result.error:
        reply = f"{reply}\n\n[agent warning] {result.error}"
    lines = [note] if note else []
    if worktree is not None:
        lines.append(_worktree_outcome(worktree))
    out = render(job, reply=reply or "(spawned agent returned no text)",
                 body=body, note="\n".join(lines))
    tail = f"[spawned agent branch={run_session}:{job.head_id or '?'}]"
    return f"{out}\n{tail}"


_PARAMETERS = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "The task. With start_from='fresh' this is ALL the agent sees: write a self-contained brief."},
        "description": {"type": "string", "description": "Short name (a few words), shown as written; send_message(to=...) reaches the agent by it."},
        "agent": {"type": "string", "description": "The saved Agent to run, by name or id, from the list in this tool's description. Empty runs your own Agent."},
        "start_from": {"type": "string", "description": "'fresh' (default), 'fork', or 'SID:MSG_ID'."},
        "run_in_background": {"type": "boolean", "description": "True returns at once; the result is pushed to you as a notice."},
        "isolation": {"type": "string", "description": "'worktree', 'none', or empty for the default rule."},
        "archive_when_done": {"type": "boolean", "description": "True archives the agent once its result is back."},
    },
    "required": ["prompt"],
}


def _describe() -> dict:
    """The description with the roster of Agents other agents may start."""
    from .prompt import roster_text
    try:
        from openprogram.runtime.agent_registry.delegation import roster
        return {"description": DESCRIPTION + roster_text(roster())}
    except Exception:
        _log.debug("agent roster not rendered", exc_info=True)
        return {"description": DESCRIPTION}


@function(
    name="agent",
    description=DESCRIPTION,
    parameters=_PARAMETERS,
    toolset=["core"],
    describe=_describe,
    # Present while the chain may still create agents; a helper at the
    # depth limit has no agent tool (depth.delegation_budget_left).
    can_use=_delegation_budget_left,
)
def agent(
    prompt: str,
    description: str = "",
    agent: str = "",
    start_from: str = "fresh",
    run_in_background: bool = False,
    isolation: str = "",
    archive_when_done: bool = False,
    agent_id: str = "",
    to: str = "",
) -> str | ToolReturn:
    """Start a new agent for a bounded task.

    ``run_in_background=False`` (default) blocks until it finishes and
    returns its result block; ``run_in_background=True`` returns an
    execution id at once and the result comes back as a notice.
    ``agent_id`` is the older name of ``agent`` and is still accepted;
    ``to`` is refused with a pointer to send_message.
    """
    out = _agent_impl(
        prompt=prompt, description=description,
        agent=agent, agent_id=agent_id, start_from=start_from,
        run_in_background=run_in_background,
        to=to,
        archive_when_done=archive_when_done,
        isolation=isolation,
    )
    # A spawn that never started is a failed call, not a reply.
    if isinstance(out, str) and out.startswith("[agent error]"):
        return ToolReturn(text=out, is_error=True)
    return out
