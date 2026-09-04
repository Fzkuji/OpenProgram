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
    independent DAG tree inside the same session repo.

Either way the new turn lands in the parent session's git repo as
a branch (or a new root commit). No separate ``sub_xxx`` session id
is minted — the previous design of "detached spawn = independent
session" has been removed; multi-root DAGs in a single repo cover
the same use case without a separate ``session_id`` namespace.

Merge is the symmetric op (see ``_merge.process_merge_turn``): pick
N branch heads in one session, write a multi-parent commit node
referencing all of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AgentTurnResult:
    """Outcome of one agent turn (whether inherit or clean)."""

    head_id: Optional[str] = None      # new assistant message id (the branch tip)
    final_text: str = ""
    failed: bool = False
    error: Optional[str] = None


def _execute_agent_turn(
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
    from openprogram.agent.session_db import default_db
    from openprogram.agent.dispatcher import TurnRequest, process_user_turn

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
    from openprogram.providers.structured_output import normalize_response_format
    output_format = normalize_response_format(response_format) if response_format is not None else None
    if source in {"self_update_verify", "self_update_diagnose", "self_update_repair"}:
        from dataclasses import asdict
        from openprogram.agent.authority import normalize_authority
        if source == "self_update_verify":
            from openprogram.self_update.recovery import require_verifier_execution as require_execution
        elif source == "self_update_diagnose":
            from openprogram.self_update.diagnosis import require_execution
        else:
            from openprogram.self_update.source_repair import require_execution
        try:
            require_execution(
                session_id=session_id, spawn_caller=spawn_caller,
                prompt=prompt, agent_id=agent_id, profile_snapshot=profile_snapshot,
                model_override=model_override, tools_override=tools_override,
                response_format=asdict(output_format), authority=normalize_authority(authority or {}),
            )
        except Exception as exc:
            return AgentTurnResult(
                failed=True, error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
            )
    store = default_db()
    if store._open(session_id) is None:
        return AgentTurnResult(
            failed=True,
            error=f"session {session_id!r} not found",
        )

    # A same-session spawn runs on the session's picked model, exactly
    # like a chat turn: the web model picker stores
    # provider_override/model_override in the session meta
    # (webui/_execute/chat.py composes the same string). Without this,
    # spawned turns fell back to the agent profile's default provider —
    # a goal decision could fail on a dead default while every chat
    # turn of the session ran fine on the picked model.
    if model_override is None:
        try:
            meta = (store.get_session(session_id) or {}).get("extra_meta") or {}
            if isinstance(meta, str):
                import json as _json
                meta = _json.loads(meta)
            _prov = meta.get("provider_override")
            _model = meta.get("model_override")
            if _prov and _model:
                model_override = f"{_prov}/{_model}"
            elif _model:
                model_override = _model
        except Exception:
            pass

    # Clean start: pass ``history_override=[]`` so the dispatcher's
    # context assembly doesn't pull in any conversation history.
    # Inherit: history is whatever leads to ``predecessor``, which the
    # dispatcher already resolves from ``predecessor``.
    # Spawned sub-agents run with ``permission_mode="bypass"``: there's
    # no UI subscribed to approval_request events on the spawned lane
    # (the chat view only listens to its own turn), so the default
    # ``"ask"`` would hang on every bash/list/read until the 300s
    # timeout and return ``[denied]`` for every tool call. Spawning a
    # sub-agent is itself an explicit user act, so the user has
    # already consented to tool use within that turn.
    from openprogram.agent.authority import runtime_authority
    req = TurnRequest(
        session_id=session_id,
        user_text=prompt,
        agent_id=agent_id,
        source=source,
        branch_from=branch_from,
        history_override=[] if branch_from is None else None,
        permission_mode="bypass",
        # New-branch (branch_from=None) root points its caller at the
        # spawning node, so the branch is an explicit spawn (see
        # dag/overview.md §2.3). No-op for inherit forks.
        spawn_caller=spawn_caller,
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
        **runtime_authority(authority or {}, source),
    )
    try:
        turn = process_user_turn(req)
    except Exception as e:  # noqa: BLE001
        return AgentTurnResult(
            failed=True,
            error=f"{type(e).__name__}: {e}",
        )

    # dispatcher already stamped ``agent_id`` on the user + assistant
    # rows via ``req.agent_id``. If a label was provided, attach it as
    # a named branch so the right-rail "Branches" panel and the DAG
    # use the human label instead of the bare commit hash.
    if turn.assistant_msg_id and label:
        try:
            store.set_branch_name(session_id, turn.assistant_msg_id, label)
        except Exception:  # noqa: BLE001
            pass

    return AgentTurnResult(
        head_id=turn.assistant_msg_id,
        final_text=turn.final_text or "",
        failed=bool(turn.failed),
        error=turn.error,
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
    model_override: Optional[str] = None,
    thinking_effort: Optional[str] = None,
    source: str = "agent_spawn",
    profile_snapshot: Optional[dict[str, Any]] = None,
    response_format: Optional[dict[str, Any]] = None,
) -> AgentTurnResult:
    """Durably admit one agent turn and wait for its Job result."""
    from openprogram.agent.session_db import default_db
    if default_db().get_session(session_id) is None:
        return AgentTurnResult(
            failed=True,
            error=f"session {session_id!r} not found",
        )
    from openprogram.agent.job import get_runner
    from openprogram.agent.job.types import JobStatus, mint_job_id
    from openprogram.worker.lock import WorkerLock

    runner = get_runner()
    borrow_current_claim = runner.can_borrow_current_claim(session_id)
    job_id = mint_job_id()
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
            borrow_current_claim=borrow_current_claim,
        )
        if borrow_current_claim or owns_worker:
            job = runner.await_job(job_id)
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
    return AgentTurnResult(
        head_id=job.head_id,
        final_text=job.result_text or "",
        failed=job.status != JobStatus.COMPLETED,
        error=job.error,
    )


def emit_spawn_event(
    *,
    session_id: str,
    status: str,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
    card_id: str,
    tool_call_id: Optional[str] = None,
    head_id: Optional[str] = None,
    content: str = "",
    job_id: Optional[str] = None,
) -> None:
    """Push one ``sub_agent`` stream event so the caller's live turn can
    draw the spawn card without waiting for a page reload.

    The ``attach`` payload is deliberately the same dict shape
    ``write_attach_pointer_for_spawn`` persists into ``extra.attach``,
    so the live path in ``chat-stream.ts`` and the reload path in
    ``conv-mapper.ts`` build structurally identical ``attachCards``
    entries. ``card_id`` is stable across the running → terminal pair,
    letting the client patch the card in place instead of appending a
    second one.
    """
    from openprogram.events import emit_ws_frame

    emit_ws_frame({
        "type": "chat_response",
        "data": {
            "type": "stream_event",
            "session_id": session_id,
            "event": {
                "type": "sub_agent",
                # Anchors the card to the execution-timeline row drawn
                # for the spawning tool call. Absent (slash-command
                # spawns) the client falls back to FIFO order, the same
                # way the reload path matches cards to spawn blocks.
                "tool_call_id": tool_call_id,
                "card_id": card_id,
                "content": content,
                "attach": {
                    "session_id": session_id,
                    "head_id": head_id,
                    "label": label or "",
                    "prompt": (prompt or "")[:500],
                    "status": status,
                    "job_id": job_id,
                },
                "agent_id": chosen_agent,
            },
        },
    })


def write_attach_pointer_for_spawn(
    *,
    session_id: str,
    caller_msg_id: str,
    result: AgentTurnResult,
    label: Optional[str],
    prompt: str,
    chosen_agent: str,
    node_id: Optional[str] = None,
) -> Optional[str]:
    """Write an `attach`-function pointer node for a synchronous
    agent() spawn (LLM tool call, foreground). Mirrors the body of
    ``_run_spawn`` in webui/_execute/__init__.py — kept in sync so the
    DAG sees the same node shape whether the user typed ``/spawn`` or
    the LLM called the ``task`` tool.
    """
    import json as _json
    import time as _time
    import uuid as _uuid

    if not result or not result.head_id:
        return None
    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sess_row = store.get_session(session_id) or {}
        head_before = sess_row.get("head_id")
        # Anchor the attach pointer DIRECTLY to the caller turn (the
        # LLM reply that ran the agent() tool call, or the user_msg of
        # a slash-command path). This is the call-edge semantics from
        # docs/design/runtime/dag-node-model.md: attach is a function_call
        # whose ``predecessor`` is the turn that triggered it. Previously
        # this code re-anchored to the caller's parent (the spawn
        # user_msg) which made depth.py collapse attach onto the same
        # row as the LLM reply.
        fork_anchor = caller_msg_id

        source_commit_id = None
        try:
            from openprogram.context.commit.store import load_commit_for_head
            _src = load_commit_for_head(store, session_id, result.head_id)
            if _src is not None:
                source_commit_id = _src.id
        except Exception:
            pass

        # Reuse the id the live spawn event already announced, so the
        # card the client drew mid-stream and the row a reload reads
        # from the DB are the same node — otherwise a refresh would
        # show the spawn twice.
        attach_node_id = node_id or _uuid.uuid4().hex[:12]
        # Attach is a branch-referencing function_call that lives ON
        # the main sequence (per docs/design/runtime/dag-node-model.md), so it
        # must hang off the caller via ``predecessor`` (sequence edge),
        # not ``caller`` (which would put it on a side branch and
        # leave the caller's reply orphaned as its own tip).
        attach_msg = {
            "id": attach_node_id,
            "role": "assistant",
            "display": "runtime",
            "function": "attach",
            "content": (result.final_text or result.error or "(no output)").strip(),
            "predecessor": fork_anchor,
            "timestamp": _time.time(),
            "is_error": bool(result.failed or result.error),
            "agent_id": chosen_agent,
            "extra": _json.dumps({
                "attach": {
                    "session_id": session_id,
                    "head_id": result.head_id,
                    "label": label or "",
                    "prompt": prompt[:500],
                    "source_commit_id": source_commit_id,
                    "status": "completed",
                },
            }, default=str),
        }
        store.append_message(session_id, attach_msg)
        if head_before:
            try:
                store.set_head(session_id, head_before)
            except Exception:
                pass
        store.commit_turn(session_id, f"agent tool spawn: {label or chosen_agent}")
        # Hide the spawned sub-branch from the Branches panel — its
        # content is now reachable from main via the attach pointer.
        # Same retirement the async runner does on completion (see
        # job/runner.py::_update_attach_card).
        try:
            store.mark_merged(session_id, [result.head_id])
        except Exception:
            pass
        # Broadcast session_reload so the UI re-renders the DAG with
        # the new attach pointer + reference edge.
        # 步 4：走总线（ws.frame 事件），不再 import webui；帧内容不变。
        from openprogram.events import emit_ws_frame
        emit_ws_frame({
            "type": "session_reload",
            "data": {"session_id": session_id, "reason": "task_tool_spawn"},
        })
        # The attach pointer changed what the next request carries, so the
        # context ring re-estimates alongside the DAG reload — same pairing
        # the async runner does on its own attach write.
        try:
            from openprogram.webui.server import refresh_context_stats
            refresh_context_stats(session_id)
        except Exception:
            pass
        return attach_node_id
    except Exception:
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
) -> Optional[str]:
    """Write a ``status=running`` placeholder attach card for an async
    spawn, anchored at the CALLING node（在哪调用就锚在哪）. The runner
    patches it on terminal via ``_update_attach_card``. Without this the
    agent(run_in_background=true) path had no card at all — the result later arrived
    as a job_followup with nothing anchoring it in the transcript.
    """
    import json as _json
    import time as _time
    import uuid as _uuid

    try:
        from openprogram.agent.session_db import default_db
        store = default_db()
        sess_row = store.get_session(session_id) or {}
        head_before = sess_row.get("head_id")
        attach_node_id = node_id or _uuid.uuid4().hex[:12]
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
            "extra": _json.dumps({
                "attach": {
                    "session_id": session_id,
                    "head_id": None,
                    "label": label or "",
                    "prompt": prompt[:500],
                    "source_commit_id": None,
                    "status": "running",
                    "job_id": job_id,
                },
            }, default=str),
        })
        if head_before:
            try:
                store.set_head(session_id, head_before)
            except Exception:
                pass
        store.commit_turn(
            session_id, f"agent tool spawn (async): {label or chosen_agent}",
        )
        return attach_node_id
    except Exception:
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
    from openprogram.agent.job import get_runner
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
    )
