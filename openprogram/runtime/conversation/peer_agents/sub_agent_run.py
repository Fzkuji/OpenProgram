"""Run an agent turn that can be inherited (sibling branch) or clean
(new root) — both inside the same session.

All agents are peers. There is no "sub-agent type". A turn is just
``(predecessor, prompt, agent_id)``:

  * ``predecessor = <existing_node_id>`` — the new turn forks off that
    node. The agent inherits the conversation chain that leads to
    ``predecessor`` as context. This is the normal "fork from here" /
    Claude-Code Job feel.
  * ``predecessor = None`` — the new turn starts a fresh root. The
    agent sees only the prompt; its turn series becomes an
    independent DAG tree inside the same session.

Either way the new turn lands in the parent session as
a branch (or a new root). No separate ``sub_xxx`` session id
is minted — the previous design of "detached spawn = independent
session" has been removed; multi-root DAGs in a single session cover
the same use case without a separate ``session_id`` namespace.

Merge is the symmetric op (see ``_merge.process_merge_turn``): pick
N branch heads in one session, write a multi-parent commit node
referencing all of them.
"""
from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from typing import Any, Optional

_log = logging.getLogger(__name__)


@dataclass
class AgentTurnResult:
    """Outcome of one agent turn (whether inherit or clean)."""

    head_id: Optional[str] = None      # new assistant message id (the branch tip)
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None


def validate_self_update_turn_request(request: Any) -> None:
    """Validate the frozen unattended verifier contract before dispatch."""
    source = getattr(request, "source", None)
    if source in {"self_update_continue", "self_update_replan"}:
        from openprogram.runtime import maintenance
        maintenance.require_execution(source, request=request)
        return
    if source not in {
        "self_update_verify",
        "self_update_diagnose",
        "self_update_repair",
    }:
        return
    model_override = getattr(request, "model_override", None)
    profile_snapshot = getattr(request, "profile_snapshot", None)
    tools_override = getattr(request, "tools_override", None)
    spawn_caller = getattr(request, "spawn_caller", None)
    response_format = getattr(request, "response_format", None)
    branch_from = getattr(request, "branch_from", None)
    advance_head = getattr(request, "advance_head", True)
    if (
        profile_snapshot is None
        or not isinstance(model_override, str)
        or "/" not in model_override
        or not all(model_override.split("/", 1))
        or tools_override is None
        or not spawn_caller
        or response_format is None
        or branch_from is not None
        or advance_head
    ):
        raise ValueError(
            "verifier requires frozen profile/model/tools/schema "
            "and a clean non-head branch"
        )
    from dataclasses import asdict
    from openprogram.state.authority import normalize_authority
    from openprogram.models.providers.structured_output import normalize_response_format

    normalized_format = normalize_response_format(response_format)
    request.response_format = normalized_format
    from openprogram.runtime import maintenance
    maintenance.require_execution(
        source,
        session_id=request.session_id,
        spawn_caller=spawn_caller,
        prompt=request.user_text,
        agent_id=request.agent_id,
        profile_snapshot=profile_snapshot,
        model_override=model_override,
        tools_override=tools_override,
        response_format=asdict(normalized_format),
        authority=normalize_authority(request),
    )


def _execute_agent_turn(
    session_id: str,
    prompt: str,
    agent_id: str,
    *,
    branch_from: Optional[str] = None,
    label: Optional[str] = None,
    spawn_caller: Optional[str] = None,
    spawned_from_session: Optional[str] = None,
    advance_head: bool = True,
    tools_override: Optional[list[str]] = None,
    render_range: Optional[dict[str, int]] = None,
    model_override: Optional[str] = None,
    thinking_effort: Optional[str] = None,
    authority: Optional[dict[str, Any]] = None,
    creates_agent: bool = True,
    source: str = "agent_spawn",
    profile_snapshot: Optional[dict[str, Any]] = None,
    response_format: Optional[dict[str, Any]] = None,
) -> AgentTurnResult:
    """Run one agent turn inside ``session_id``.

    ``predecessor`` controls the context:
      * ``None`` → new root (clean start, agent sees only ``prompt``).
      * ``<node_id>`` → fork off that node (agent inherits the chain
        ending at ``node_id`` as context).

    Returns ``AgentTurnResult`` with the new branch tip's assistant
    message id and final text. Caller decides what to do with it
    (write an attach indicator, surface in chat, kick off a merge).
    """
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter, STOPPED_BEFORE_START

    if source in {"self_update_verify", "self_update_diagnose", "self_update_repair"} and (
        profile_snapshot is None or not isinstance(model_override, str)
        or "/" not in model_override or not all(model_override.split("/", 1))
        or tools_override is None or not spawn_caller
        or response_format is None or branch_from is not None or advance_head
    ):
        return AgentTurnResult(
            failed=True,
            error="verifier requires frozen profile/model/tools/schema and a clean non-head branch",
        )
    from openprogram.models.providers.structured_output import normalize_response_format

    output_format = (
        normalize_response_format(response_format)
        if response_format is not None else None
    )
    if source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
        from dataclasses import asdict
        from openprogram.state.authority import normalize_authority
        from openprogram.runtime import maintenance

        try:
            maintenance.require_execution(
                source,
                session_id=session_id,
                spawn_caller=spawn_caller,
                prompt=prompt,
                agent_id=agent_id,
                profile_snapshot=profile_snapshot,
                model_override=model_override,
                tools_override=tools_override,
                response_format=asdict(output_format),
                authority=normalize_authority(authority or {}),
            )
        except Exception as exc:
            return AgentTurnResult(
                failed=True,
                error=(
                    str(exc) if isinstance(exc, ValueError)
                    else type(exc).__name__
                ),
            )
    store = default_db()
    if store._open(session_id) is None:
        return AgentTurnResult(
            failed=True,
            error=f"session {session_id!r} not found",
        )

    # Canonical admission freezes branch/parent model and tool intent. Do not
    # consult the page-selected session model before that boundary.

    # Clean start: pass ``history_override=[]`` so the dispatcher's
    # context assembly doesn't pull in any conversation history.
    # Inherit: history is whatever leads to ``predecessor``, which the
    # dispatcher already resolves from ``predecessor``.
    # A sub-agent works under its parent's mode and rules, so Ask means Ask
    # all the way down. A Job admitted for it also takes its own Agent's
    # narrowing and, in the foreground, asks through its caller's card
    # (jobs/input.py); this direct path asks nobody, so what would ask is
    # refused instead of waiting.
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.state.authority import runtime_authority
    from openprogram.runtime.permissions.approvals.lifecycle import current_permission_request
    from openprogram.runtime.permissions.modes import DEFAULT_PERMISSION
    _authority = runtime_authority(authority or {}, source)
    _parent = get_turn_request()
    if _parent is not None:
        _parent = current_permission_request(_parent)
    req = TurnRequest(
        session_id=session_id,
        user_text=prompt,
        agent_id=agent_id,
        source=source,
        branch_from=branch_from,
        history_override=[] if branch_from is None else None,
        permission_mode=_parent.permission_mode if _parent is not None else DEFAULT_PERMISSION,
        permission_rules=_parent.permission_rules if _parent is not None else None,
        # New-branch (branch_from=None) root points its caller at the
        # spawning node, so the branch is an explicit spawn (see
        # dag/overview.md §2.3). No-op for inherit forks.
        spawn_caller=spawn_caller,
        spawned_from_session=spawned_from_session,
        # Same-session sub-agent turns pass False: the spawned branch
        # must not steal the session head mid-run (HEAD single-writer,
        # context/compaction.md §5) — the transcript follows the head,
        # and a stolen head switched the user's window to the agent's
        # conversation until the outer reply moved it back.
        advance_head=advance_head,
        tools_override=tools_override,
        render_range=render_range,
        model_override=model_override,
        thinking_effort=thinking_effort,
        profile_snapshot=profile_snapshot,
        response_format=output_format,
        **_authority,
    )
    try:
        adapter = CanonicalAgentAdapter()
        admission = adapter.admit(
            req,
            trusted_actor=_authority,
            user_message_id=req.user_msg_id,
            config_snapshot_ref=f"agent-spawn:{session_id}",
        )
        activated = asyncio.run(adapter.activate(admission))
    except Exception as e:  # noqa: BLE001
        return AgentTurnResult(
            failed=True,
            error=f"{type(e).__name__}: {e}",
        )
    if activated is None:
        # A stop reached the spawned turn before it ran; it ended cancelled.
        return AgentTurnResult(failed=True, error=STOPPED_BEFORE_START)
    _active, turn = activated
    if turn is None:
        return AgentTurnResult(
            failed=True,
            error="Agent runner failed before returning a turn result",
        )

    # dispatcher already stamped ``agent_id`` on the user + assistant
    # rows via ``req.agent_id``. If a label was provided, attach it as
    # a named branch so the right-rail "Branches" panel and the DAG
    # use the human label instead of the bare commit hash.
    if getattr(turn, "assistant_msg_id", None) and label:
        try:
            store.set_branch_name(session_id, turn.assistant_msg_id, label)
        except Exception:  # noqa: BLE001
            # Cosmetic: the branch shows its commit hash instead of the label.
            _log.warning("branch label not set in session %s", session_id, exc_info=True)

    return AgentTurnResult(
        head_id=getattr(turn, "assistant_msg_id", None),
        final_text=getattr(turn, "final_text", "") or "",
        failed=bool(getattr(turn, "failed", False)),
        error=getattr(turn, "error", None),
    )


def run_agent_turn(
    session_id: str,
    prompt: str,
    agent_id: str,
    *,
    branch_from: Optional[str] = None,
    label: Optional[str] = None,
    spawn_caller: Optional[str] = None,
    advance_head: bool = True,
    tools_override: Optional[list[str]] = None,
    render_range: Optional[dict[str, int]] = None,
    authority: Optional[dict[str, Any]] = None,
    creates_agent: bool = True,
    parent_job_id: Optional[str] = None,
    caller_msg_id: Optional[str] = None,
    caller_session_id: Optional[str] = None,
    chain_messages: int = 0,
    chain_generations: int = 0,
    caller_chain_generations: int = 0,
    archive_when_done: bool = False,
    on_accepted=None,
    attach_pointer_id: Optional[str] = None,
    model_override: Optional[str] = None,
    thinking_effort: Optional[str] = None,
    source: str = "agent_spawn",
    profile_snapshot: Optional[dict[str, Any]] = None,
    response_format: Optional[dict[str, Any]] = None,
    job_id: Optional[str] = None,
    worktree_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
) -> AgentTurnResult:
    """Durably admit one agent turn and wait for its Job result.

    ``worktree_id``: the run's own worktree (``agent(isolation=
    "worktree")``); its tools work there. ``job_id`` lets the caller mint
    the id first so the worktree record can name its job."""
    from openprogram.runtime.context.session_db import default_db
    if default_db().get_session(session_id) is None:
        return AgentTurnResult(
            failed=True,
            error=f"session {session_id!r} not found",
        )
    from openprogram.runtime.jobs import get_runner
    from openprogram.runtime.jobs.types import JobStatus, is_terminal, mint_job_id
    from openprogram.host.worker.lock import WorkerLock

    runner = get_runner()
    borrow_current_claim = runner.can_borrow_current_claim(session_id)
    job_id = job_id or mint_job_id()
    direct_lock = WorkerLock()
    owns_worker = False
    claim_scope = None
    try:
        if not borrow_current_claim:
            claim_scope = runner.claim_only(job_id)
            claim_scope.__enter__()
            owns_worker = direct_lock.try_acquire()
            if not owns_worker:
                claim_scope.__exit__(None, None, None)
                claim_scope = None
        job_id = runner.spawn_job(
            job_id=job_id,
            session_id=session_id,
            prompt=prompt,
            agent_id=agent_id,
            subject=prompt[:60],
            description=prompt,
            context_mode="inherit" if branch_from is not None else "clean",
            parent_msg_id=branch_from,
            parent_job_id=parent_job_id,
            label=label,
            wait=True,
            caller_msg_id=caller_msg_id or branch_from,
            caller_session_id=caller_session_id,
            chain_messages=chain_messages,
            chain_generations=chain_generations,
            caller_chain_generations=caller_chain_generations,
            archive_when_done=archive_when_done,
            spawn_caller=spawn_caller,
            advance_head=advance_head,
            tools_override=tools_override,
            render_range=render_range,
            model_override=model_override,
            thinking_effort=thinking_effort,
            source=source,
            profile_snapshot=profile_snapshot,
            response_format=response_format,
            authority=authority,
            creates_agent=creates_agent,
            on_accepted=on_accepted,
            attach_pointer_id=attach_pointer_id,
            borrow_current_claim=borrow_current_claim,
            worktree_id=worktree_id,
            tool_call_id=tool_call_id,
        )
        if borrow_current_claim or owns_worker:
            job = runner.await_job(job_id)
            while _asking_the_person(runner, job):
                # Its question is on the caller's card: wait for the answer
                # and the run it resumes (lifecycle.spawn_question_route).
                job = _await_answer(runner, job_id)
        else:
            def take_over_if_worker_exited() -> None:
                nonlocal owns_worker, claim_scope
                if owns_worker:
                    return
                candidate_scope = runner.claim_only(job_id)
                candidate_scope.__enter__()
                try:
                    if not direct_lock.try_acquire():
                        return
                    owns_worker = True
                    claim_scope = candidate_scope
                    candidate_scope = None
                finally:
                    if candidate_scope is not None:
                        candidate_scope.__exit__(None, None, None)

            job = runner.await_job_durable(
                job_id, on_poll=take_over_if_worker_exited,
            )
            while _asking_the_person(runner, job):
                job = _await_answer(runner, job_id, on_poll=take_over_if_worker_exited)
            if not owns_worker:
                runner.retire_external_waiter(job_id)
    finally:
        try:
            if owns_worker:
                direct_lock.release()
        finally:
            if claim_scope is not None:
                claim_scope.__exit__(None, None, None)
    if job is None:
        return AgentTurnResult(failed=True, error=f"job {job_id!r} not found")
    stopped = None if is_terminal(job.status) else runner.stopped_execution(job_id)
    if stopped is not None:
        why = ("an operation's outcome is unknown and needs checking"
               if stopped.status.value == "reconciliation_required" else "it was paused")
        return AgentTurnResult(
            head_id=job.head_id, final_text="", failed=True,
            error=(f"the agent stopped before finishing: {why} "
                   f"({stopped.reason_code or stopped.status.value}). "
                   "Check the page or app before repeating its work."),
        )
    return AgentTurnResult(
        head_id=job.head_id,
        final_text=job.result_text or "",
        failed=job.status != JobStatus.COMPLETED,
        error=job.error,
    )


#: How often a caller waiting on a sub-agent's question looks again.
ANSWER_POLL_S = 0.2


def _asking_the_person(runner, job) -> bool:
    """Whether a waited-on sub-agent stopped at a question its caller's
    conversation shows (an approval card), rather than for good."""
    from openprogram.runtime.jobs.types import is_terminal
    if job is None or is_terminal(job.status):
        return False
    stopped = runner.stopped_execution(job.id)
    if (stopped is None or stopped.status.value != "paused"
            or stopped.reason_code != "wait_open"):
        return False
    try:
        stored = runner._execution_store.get_job_agent_input(job.id) or {}  # noqa: SLF001
    except Exception:  # noqa: BLE001 - unreadable input: report the pause as before
        return False
    return bool((stored.get("turn_request") or {}).get("asks_through"))


def _await_answer(runner, job_id: str, *, on_poll=None):
    """Wait until the person answers and the sub-agent runs on, then wait for it again.

    The answer resumes the paused execution through the runner's own queue
    (``JobRunner._queue_wait_resume``); a stop of the caller cancels the
    sub-agent with it, which ends this wait too.
    """
    import time as _time
    while True:
        job = runner.get_job(job_id)
        if not _asking_the_person(runner, job):
            break
        if on_poll is not None:
            on_poll()
        _time.sleep(ANSWER_POLL_S)
    if job is None:
        return None
    return runner.await_job_durable(job_id, on_poll=on_poll)


def _attach_node(store, session_id: str, node_id: Optional[str]):
    """The attach row a spawn already wrote, or None."""
    if not node_id:
        return None
    pair = store._open(session_id)  # noqa: SLF001
    return pair[1].nodes_by_id.get(node_id) if pair is not None else None


def write_attach_pointer_for_spawn(
    *,
    session_id: str,
    caller_msg_id: str,
    result: AgentTurnResult,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
    node_id: Optional[str] = None,
    target_session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    delivery: Optional[str] = "tool_result",
) -> Optional[str]:
    """Settle the attach row of a synchronous spawn.

    The row normally exists already, written when the job was accepted, so
    this patches it. A placeholder that could not be written is replaced
    here by the full row. A result with no head can only close an existing
    placeholder, because a new row would have nothing to open.
    """
    import json as _json
    import time as _time
    import uuid as _uuid

    if not result:
        return None
    try:
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store import SessionNodeWriter
        store = default_db()
        placeholder = _attach_node(store, session_id, node_id)
        if placeholder is None and not result.head_id:
            return None
        target_session = target_session_id or session_id
        source_commit_id = None
        failed = bool(result.failed or result.error or not result.head_id)
        terminal_status = "errored" if failed else "completed"
        if result.head_id:
            try:
                from openprogram.state.store.session.context_commit.store import load_commit_for_head
                _src = load_commit_for_head(store, target_session, result.head_id)
                if _src is not None:
                    source_commit_id = _src.id
            except Exception:
                # No commit id: the attach card falls back to the final text.
                _log.debug("source commit lookup failed for session %s", target_session, exc_info=True)

        content = (result.final_text or result.error or "(no output)").strip()
        attach: dict[str, Any] = {
            "session_id": target_session,
            "head_id": result.head_id,
            "label": label or "",
            "prompt": prompt[:500],
            "source_commit_id": source_commit_id,
            "status": terminal_status,
        }
        if delivery:
            # The agent tool already returned the reply as its result.
            attach["delivery"] = delivery
        if tool_call_id:
            attach["tool_call_id"] = tool_call_id
        attach_node_id = node_id or _uuid.uuid4().hex[:12]
        if placeholder is not None:
            # The job runner settles this card too, when the job ends, and
            # its write can land just before this one: merge into the card
            # as it is now, not the copy read above, so its usage and
            # execution fields stay.
            def settle(current) -> dict:
                merged = dict((current.metadata or {}).get("attach") or {})
                if not delivery:
                    merged.pop("delivery", None)
                # A field this caller could not fill (no commit found, no
                # label) does not blank the one the runner wrote.
                mine = {key: value for key, value in attach.items()
                        if value not in (None, "") or not merged.get(key)}
                return {"output": content,
                        "metadata": {"attach": {**merged, **mine}, "is_error": failed}}

            SessionNodeWriter(store, session_id).merge(attach_node_id, settle)
        else:
            # Attach is a branch-referencing function_call that lives ON
            # the main sequence (per docs/design/runtime/dag-node-model.md),
            # so it hangs off the caller via ``predecessor``.
            store.append_message(session_id, {
                "id": attach_node_id,
                "role": "assistant",
                "display": "runtime",
                "function": "attach",
                "content": content,
                "predecessor": caller_msg_id,
                "timestamp": _time.time(),
                "is_error": failed,
                "agent_id": chosen_agent,
                "extra": _json.dumps({"attach": attach}, default=str),
            }, advance_head=False)
        store.commit_turn(session_id, f"agent tool spawn: {label or chosen_agent}")
        # Hide the spawned sub-branch from the Branches panel — its
        # content is now reachable from main via the attach pointer.
        # Same retirement the async runner does on completion (see
        # job/runner.py::_update_attach_card).
        if terminal_status == "completed" and delivery:
            try:
                store.mark_merged(target_session, [result.head_id])
            except Exception:
                # Cosmetic: the sub-branch still shows in the Branches panel.
                _log.warning("sub-agent head not marked merged in session %s", target_session, exc_info=True)
        # The attach pointer changed what the next request carries, so the
        # context ring re-estimates — the same refresh the async runner does
        # on its own attach write. Open transcripts get the card from the
        # transcript publisher.
        try:
            from openprogram.runtime import interface_link
            interface_link.refresh_context_stats(session_id)
        except Exception:
            # Context ring refreshes on the next turn.
            _log.debug("context stats refresh failed for session %s", session_id, exc_info=True)
        return attach_node_id
    except Exception:
        _log.warning("spawn attach row not written in session %s", session_id, exc_info=True)
        return None


def write_attach_placeholder_for_spawn(
    *,
    session_id: str,
    caller_msg_id: str,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
    node_id: Optional[str] = None,
    job_id: Optional[str] = None,
    target_session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    delivery: Optional[str] = None,
) -> Optional[str]:
    """Write a ``status=running`` attach row when a spawn is accepted,
    anchored at the calling node. The job carries the row's id, so every
    path that ends the job patches it. ``delivery="tool_result"`` keeps the
    row out of the next request: the reply arrives as the tool's result.
    """
    import json as _json
    import time as _time
    import uuid as _uuid

    try:
        from openprogram.runtime.context.session_db import default_db
        store = default_db()
        target_session = target_session_id or session_id
        attach_node_id = node_id or _uuid.uuid4().hex[:12]
        attach: dict[str, Any] = {
            "session_id": target_session,
            "head_id": None,
            "label": label or "",
            "prompt": prompt[:500],
            "source_commit_id": None,
            "status": "running",
            "job_id": job_id,
        }
        if tool_call_id:
            attach["tool_call_id"] = tool_call_id
        if delivery:
            attach["delivery"] = delivery
        elif job_id:
            # The card's prompt text stays as written now; the helper's
            # result reaches the caller as the notice turn (``render.
            # _attach_report``, ``jobs/notices.py``). A card without this
            # mark (written before) still carries its report.
            attach["result_in"] = "notice"
        store.append_message(session_id, {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": "(running)",
            "predecessor": caller_msg_id,
            "timestamp": _time.time(),
            "is_error": False,
            "agent_id": chosen_agent,
            "extra": _json.dumps({"attach": attach}, default=str),
        }, advance_head=False)
        store.commit_turn(
            session_id, f"agent tool spawn (async): {label or chosen_agent}",
        )
        return attach_node_id
    except Exception:
        _log.warning("spawn placeholder not written in session %s", session_id, exc_info=True)
        return None


def run_agent_turn_async(
    session_id: str,
    prompt: str,
    agent_id: str,
    *,
    branch_from: Optional[str] = None,
    label: Optional[str] = None,
    subject: str = "",
    description: str = "",
    context_mode: str = "inherit",
    parent_job_id: Optional[str] = None,
    attach_pointer_id: Optional[str] = None,
    target_branch_head_id: Optional[str] = None,
    caller_msg_id: Optional[str] = None,
    caller_session_id: Optional[str] = None,
    chain_messages: int = 0,
    chain_generations: int = 0,
    caller_chain_generations: int = 0,
    archive_when_done: bool = False,
    job_id: Optional[str] = None,
    authority: Optional[dict[str, Any]] = None,
    creates_agent: bool = True,
    spawn_caller: Optional[str] = None,
    advance_head: bool = False,
    tools_override: Optional[list[str]] = None,
    deferred_inbox: Optional[dict[str, Any]] = None,
    on_accepted=None,
    defer_dispatch: bool = False,
    resume_deferred: bool = False,
    message_origin: Optional[str] = None,
    worktree_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    chain_ancestors: Optional[list[str]] = None,
    model_override: Optional[str] = None,
) -> str:
    """Submit an agent turn to the job runner, return ``job_id``.

    ``job_id``: reuse a pre-created pending Job (tracked dispatch
    queued in the inbox) instead of minting a new id — the dispatcher
    already holds this id, so drain must run the SAME job.

    Non-blocking counterpart of :func:`run_agent_turn`. The runner
    walks the job through the state machine on a worker thread and
    eventually invokes ``run_agent_turn`` under the hood. Callers
    that need the result block on ``runner.await_job(job_id)``;
    callers that want fire-and-forget (the ``--async`` slash flag,
    plan-mode spawns) ignore the return value.
    """
    from openprogram.runtime.jobs import get_runner
    runner = get_runner()
    return runner.spawn_job(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        subject=subject or (description or prompt[:60]),
        description=description or prompt,
        context_mode=context_mode if branch_from is not None or context_mode == "clean" else context_mode,
        parent_msg_id=branch_from,
        parent_job_id=parent_job_id,
        label=label,
        attach_pointer_id=attach_pointer_id,
        target_branch_head_id=target_branch_head_id,
        wait=False,
        caller_msg_id=caller_msg_id,
        caller_session_id=caller_session_id,
        chain_messages=chain_messages,
        chain_generations=chain_generations,
        caller_chain_generations=caller_chain_generations,
        archive_when_done=archive_when_done,
        spawn_caller=spawn_caller if spawn_caller is not None else caller_msg_id,
        advance_head=advance_head,
        tools_override=tools_override,
        deferred_inbox=deferred_inbox,
        job_id=job_id,
        authority=authority,
        creates_agent=creates_agent,
        on_accepted=on_accepted,
        defer_dispatch=defer_dispatch,
        resume_deferred=resume_deferred,
        message_origin=message_origin,
        worktree_id=worktree_id,
        tool_call_id=tool_call_id,
        chain_ancestors=chain_ancestors,
        model_override=model_override,
    )
