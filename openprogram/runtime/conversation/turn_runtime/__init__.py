"""Shared persistent-turn execution for Agent and ChatAgent.

Agent.run_turn and Agent.resume_turn own the public execution contract.
This package implements admission, context binding, streaming, persistence
and finalization through the existing execution driver and agent loop.
``TurnRequest`` is a call-core type: import it from
``openprogram.runtime.agent.turn_request``.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Optional

_log = logging.getLogger(__name__)


# ``TurnResult``, the ``EventCallback`` alias and its no-op default live in
# the sibling ``types`` module.
from openprogram.runtime.conversation.turn_runtime.types import (
    EventCallback,
    TurnResult,
    _noop,
)

if TYPE_CHECKING:
    from openprogram.runtime.agent.turn_request import TurnRequest

# Leaf helpers extracted into sibling modules, re-exported so
# ``dispatcher.<name>`` and every external import resolve unchanged:
#   titles.py         — trigger_compaction
#   forced_tool.py    — dispatch_forced_tool_call (webui/routes/chat.py imports it)
#   runtime_attach.py — _wrap_agentic_runtime_block (process_runner.py imports it)
from openprogram.runtime.conversation.turn_runtime.titles import trigger_compaction
from openprogram.runtime.conversation.turn_runtime.forced_tool import dispatch_forced_tool_call
from openprogram.runtime.conversation.turn_runtime.runtime_attach import _wrap_agentic_runtime_block
# Stage modules called by _process_turn_once (internal — not re-exported
# for external callers):
#   prep.py         — steps 1-2 (session/history/user persist)
#   turn_context.py — step 3 (per-turn ContextVar bindings)
#   stream_tap.py   — the tool-node-persisting on_event wrap
#   persistence.py  — phase-5 assistant persistence
#   finalize.py     — phase-6 bookkeeping
#   error_path.py   — the except branch
from openprogram.runtime.conversation.turn_runtime.prep import prepare_turn
from openprogram.runtime.conversation.turn_runtime.turn_context import TurnBindings
from openprogram.runtime.conversation.turn_runtime.turn_writer import beside_conversation
from openprogram.runtime.conversation.turn_runtime.stream_tap import final_blocks, make_stream_tap, naming_reply_row
from openprogram.runtime.conversation.turn_runtime.error_path import handle_turn_error
from openprogram.runtime.conversation.turn_runtime.finalize import finalize_turn
from openprogram.runtime.conversation.turn_runtime.persistence import persist_assistant_message
from openprogram.operations.self_update.control.handoff import release_prepared_update

# The agent-loop run stage. Bound as a package attribute named
# ``_run_loop_blocking`` — the seam tests patch (patch.object(D,
# "_run_loop_blocking", ...)); the orchestrator below reads the module
# global at call time so the patch always wins.
from openprogram.runtime.conversation.turn_runtime.loop_runner import (
    run_loop_blocking as _run_loop_blocking,
)


# ---------------------------------------------------------------------------
# Approval gate — used by the "ask" permission flow
# ---------------------------------------------------------------------------

from openprogram.runtime.conversation import plan_mode as _plan_mode
from openprogram.runtime.conversation.run_control import run_slot as _run_slot


def _memory_write(session_id: str) -> None:
    """Offer the finished turn to memory (design/memory §"When writing
    happens": one call per turn, after it is persisted).

    Usually cheap — the provider counts what this session has left
    unwritten and does nothing until a batch is worth a model call. The
    turn's text is not passed: the provider reads the conversation back
    out of the session store, which is durable and ordered. Without the
    session id it can do nothing at all: that is what identifies the
    thread whose turns are being counted. Best-effort, memory never
    takes a turn down with it.

    Not in a process that holds side work (``side_work.held``, an
    ``openprogram --print`` run): a batch that crosses the threshold is a
    model run, which would be model work the command did not ask for and
    could be cut off mid-write when the command leaves. The idle watcher
    of the next worker folds the conversation in instead."""
    if not session_id:
        return
    from openprogram.runtime.conversation import side_work

    if not side_work.allowed():
        return
    from openprogram.state.memory import (
        MemoryWriteFailureCode,
        classify_memory_write_failure,
        get_backend,
    )
    from openprogram.state.memory.runtime.writer_status import (
        record_active_workspace_failure,
    )

    try:
        provider = get_backend()
    except Exception:
        record_active_workspace_failure(
            MemoryWriteFailureCode.MEMORY_PROVIDER_RESOLUTION_FAILED,
            retryable=False,
        )
        _log.debug("memory provider unavailable", exc_info=True)
        return
    try:
        left = provider.write(session_id=session_id)
    except Exception as exc:
        failure = classify_memory_write_failure(exc)
        record_active_workspace_failure(
            failure.reason_code,
            retryable=failure.retryable,
        )
        _log.debug("memory write failed for %s", session_id, exc_info=True)
        return
    if left is not None:
        record_active_workspace_failure(
            getattr(left, "reason_code", None),
            retryable=left.retryable,
        )
        # Nothing to do about it here — the next turn comes back around,
        # and the idle watcher is what finally has to finish the session.
        _log.debug("memory write incomplete for %s (%s)", session_id, left.reason)


def _drain_send_message_inbox(session_id: str) -> None:
    """Deliver messages other branches queued for this session while it
    was busy (send_message busy-queueing, agent-collaboration §5.4).
    Runs at turn end — the one point where the session is known to be
    free again. Each queued message becomes one async turn through the
    normal delivery path (run_agent_turn_async → auto-followup back to
    the sender). Best-effort."""
    try:
        from openprogram.runtime.conversation.inbox import drain
        drain(session_id)
    except Exception:
        _log.debug(
            "send_message inbox drain failed for session %s",
            session_id, exc_info=True,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _release_prepared_update(session_id: str, assistant_msg_id: str) -> None:
    """Hand a self-update this turn prepared to its supervisor; never fail the turn."""
    try:
        release_prepared_update(session_id, assistant_msg_id)
    except Exception:
        _log.warning(
            "self-update turn release failed for %s turn %s",
            session_id,
            assistant_msg_id,
            exc_info=True,
        )


def execute_turn(
    req: TurnRequest,
    *,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    execution_context: dict | None = None,
) -> TurnResult:
    """Run one full agent turn, then the ordinary ``turn.stop`` gate.

    Chat Goals use canonical ordinary turns. Their terminal notification
    admits subsequent work or a read-only completion verification turn;
    the dispatcher never invokes a nested Goal Workflow.
    """
    # Same-session spawned turns are components inside another execution,
    # so they do not enter the top-level turn.stop gate.
    if req.source == "agent_spawn":
        return _process_turn_once(
            req, on_event=on_event, cancel_event=cancel_event,
            execution_context=execution_context)
    # Canonical executions own all runtime steering.  Non-canonical callers
    # may still run an ordinary turn, but they never enter a session-scoped
    # steering inbox.  This keeps the public control surface at the
    # execution-command boundary and prevents a second delivery path.
    if (execution_context or {}).get("canonical_execution"):
        return _process_turn_once(
            req, on_event=on_event, cancel_event=cancel_event,
            execution_context=execution_context)
    result = _process_turn_once(
        req, on_event=on_event, cancel_event=cancel_event,
        execution_context=execution_context)
    # Hooks may deny the stop and force ordinary continuation turns.  They
    # remain available to direct non-canonical callers, but queued steering
    # is no longer drained from a session-local inbox.
    try:
        from openprogram.runtime.conversation.turn_runtime.stop_hook import continue_stop_hook_turns
        return continue_stop_hook_turns(
            req, result, run_turn=_process_turn_once,
            on_event=on_event, cancel_event=cancel_event)
    except Exception:
        _log.warning("turn.stop hook continuation failed for session %s",
                     req.session_id, exc_info=True)
        return result


from openprogram.runtime.conversation.memory_execution import scoped_execution as _memory_execution


def _structured_output_fields(req) -> dict:
    """The parsed structured output of a ``response_format`` turn, for the
    result frame. A client that asked for JSON (the CLI's ``--json-schema``)
    reads the value from here instead of re-parsing the reply text."""
    if getattr(req, "structured_output_mode", None) is None:
        return {}
    return {
        "structured_output": req.structured_output,
        "structured_output_mode": req.structured_output_mode,
    }


@_memory_execution
def execute_continuation(
    continuation,
    *,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    execution_context: dict | None = None,
) -> TurnResult:
    """Resume an Agent checkpoint without replaying dispatcher admission.

    A continuation owns an already-persisted user node and assistant
    placeholder.  Re-entering ``_process_turn_once`` would append both again,
    start a second memory write, and run the normal finalizer twice.  This
    path therefore only rebuilds provider context, binds the original
    per-turn identity, executes the durable frontier, and finalizes the
    original assistant id once it really ends.
    """
    from openprogram.runtime.conversation.turn_runtime.persistence import persist_assistant_message
    from openprogram.runtime.conversation.turn_runtime.finalize import finalize_turn
    from openprogram.runtime.conversation.turn_runtime.turn_writer import (
        TurnWriter, mark_session_finished,
    )
    from openprogram.runtime.context.persistence import rendered_history

    req = continuation.request
    on_event = on_event or _noop
    from openprogram.runtime.context import Context
    db = Context.turn_store(req.session_id)
    session = db.get_session(req.session_id)
    if session is None:
        raise RuntimeError("continuation session is missing")
    user_msg_id = continuation.user_message_id
    assistant_msg_id = continuation.assistant_message_id
    on_event = naming_reply_row(on_event, req.session_id, user_msg_id, assistant_msg_id)
    if not db.message_exists(req.session_id, user_msg_id):
        raise RuntimeError("continuation user anchor is missing")
    if not db.message_exists(req.session_id, assistant_msg_id):
        raise RuntimeError("continuation assistant placeholder is missing")
    history = rendered_history(db, req.session_id, head_id=user_msg_id) or []
    from openprogram.state.memory.policy import bind_history
    bind_history(req, history)
    context = execution_context if execution_context is not None else {}
    # Resume is not a new admission: keep the original user node, assistant
    # placeholder, and assistant id. Bind the same per-turn identity the
    # first attempt used.
    _bindings = TurnBindings.bind(
        req=req, assistant_msg_id=assistant_msg_id, db=db,
        snapshot_project_baseline=False,
    )
    _agentic_tool_names: set[str] = set()
    _ordered_blocks: list[dict] = []
    from openprogram.runtime.conversation import bound_reply as _bound_reply_mod
    _bound_reply = _bound_reply_mod.attach(req, assistant_msg_id, resumed=True)
    _on_event_persist = make_stream_tap(
        on_event=_bound_reply.tap(on_event) if _bound_reply else on_event,
        req=req,
        assistant_msg_id=assistant_msg_id,
        placeholder_inserted=True,
        agentic_tool_names=_agentic_tool_names,
    )
    try:
        final_text, usage, tool_calls = _run_loop_blocking(
            req=req,
            history=history,
            on_event=_on_event_persist,
            cancel_event=cancel_event,
            assistant_msg_id=assistant_msg_id,
            agentic_tool_names_out=_agentic_tool_names,
            ordered_blocks_out=_ordered_blocks,
            execution_context=context,
            continuation=continuation,
        )
    except BaseException as exc:
        if _bound_reply is not None:
            _bound_reply.abort()
        raise
    finally:
        _on_event_persist.flush()
        _bindings.release(suspended=bool(context.get("safe_point_committed")))
    if context.get("safe_point_committed"):
        if _bound_reply is not None:
            _bound_reply.pause()
        result = TurnResult(
            final_text="",
            user_msg_id=user_msg_id,
            assistant_msg_id=assistant_msg_id,
        )
        setattr(result, "_execution_safe_point_handoff", True)
        return result

    # The pre-pause trace is part of tool_calls, so files attached
    # before the pause go out too (same step as 4b in _process_turn_once).
    from openprogram.programs.tools.interaction.send_file import turn_attachments
    final_text, attached_files = turn_attachments(
        final_text, tool_calls, req, _turn_started_at(db, req.session_id, user_msg_id))

    assistant_msg, _blocks, tool_calls, usage = persist_assistant_message(
        db=db,
        req=req,
        session=session,
        usage=usage,
        final_text=final_text,
        history=history,
        tool_calls=tool_calls,
        _ordered_blocks=final_blocks(_on_event_persist, _ordered_blocks, cancel_event),
        _agentic_tool_names=_agentic_tool_names,
        _placeholder_inserted=True,
        cancel_event=cancel_event,
        assistant_msg_id=assistant_msg_id,
        user_msg_id=user_msg_id,
    )
    writer = TurnWriter(db, req)
    turn_committed = finalize_turn(
        db=db,
        req=req,
        session=session,
        usage=usage,
        assistant_msg=assistant_msg,
        assistant_msg_id=assistant_msg_id,
        _project_baseline=None,
        agent_profile=None,
        ctx_win=None,
        on_event=on_event,
        head_id=writer.head_for_finalize(assistant_msg_id),
    )
    if req.advance_head and not getattr(req, "background", False):
        mark_session_finished(db, req.session_id, quiet=bool(getattr(req, "quiet_finish", False)))
    # A turn resumed after an approval (self_update prepare's own card among
    # them) ends here, not in _process_turn_once.
    if turn_committed:
        _release_prepared_update(req.session_id, assistant_msg_id)
    _run_slot.finishing(req.session_id, user_msg_id)
    on_event({"type": "chat_response", "data": {
        "type": "result", "session_id": req.session_id,
        "msg_id": user_msg_id, "assistant_msg_id": assistant_msg_id,
        "content": final_text,
        "blocks": _blocks or list(_on_event_persist.blocks),
        **_structured_output_fields(req),
    }})
    if _bound_reply is not None:
        _bound_reply.finish(final_text, attached_files=attached_files)
    return TurnResult(
        final_text=final_text,
        user_msg_id=user_msg_id,
        assistant_msg_id=assistant_msg_id,
        tool_calls=tool_calls,
        attached_files=attached_files,
        last_round_text=req.last_round_text,
        usage=usage,
    )


def _turn_started_at(db, session_id: str, user_msg_id: str) -> float | None:
    """When a resumed turn started: its user message's time. ``None``
    (nothing produced is attached) when the store has no time for it."""
    try:
        rows = db.get_messages_by_id(session_id, [user_msg_id])
        value = rows[0].get("timestamp") if rows else None
        return float(value) if value else None
    except Exception:
        return None


def _bind_usage_branch(db, session_id: str, branch_id: str | None = None) -> None:
    """Name the branch the turn runs on in the usage context, once its user
    message is stored: the prompt-cache check compares a request with the
    last one on its own branch (``models/usage/prefix_fingerprint.py``) and
    the usage tracker files the turn's occupancy under it
    (``context/usage.py``). The branch is the one frozen at admission; the
    selected branch only for a request admitted without one."""
    try:
        from dataclasses import replace as _replace
        from openprogram.models.usage.context import _current as _usage_cur

        branch = branch_id
        if branch is None:
            meta = db.get_session(session_id) or {}
            branch = meta.get("active_branch_id") if isinstance(meta, dict) else None
        current = _usage_cur.get()
        if current.branch_id != branch:
            _usage_cur.set(_replace(current, branch_id=branch))
    except Exception:
        # Observability only: without it the turn is compared across branches.
        _log.debug("usage branch binding failed", exc_info=True)


@_memory_execution
def _process_turn_once(
    req: TurnRequest,
    *,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    execution_context: dict | None = None,
) -> TurnResult:
    """Run the turn and hand the thread back labelled as it came.

    The turn labels this thread's model calls as its own (``chat``, its
    conversation and branch); a pooled thread that kept the label would
    count whatever it runs next as this conversation's chat.
    """
    from openprogram.models.usage.context import _current as _usage_label
    before = _usage_label.get()
    try:
        return _run_turn_once(req, on_event=on_event, cancel_event=cancel_event,
                              execution_context=execution_context)
    finally:
        _usage_label.set(before)


def _run_turn_once(
    req: TurnRequest,
    *,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
    execution_context: dict | None = None,
) -> TurnResult:
    """Synchronous wrapper that runs one full agent turn.

    Why sync: callable from channel worker threads without async
    coloring leaking everywhere. Internally we spin up a fresh asyncio
    loop and run the agent_loop EventStream to completion.

    Pipeline:
      1. Load/create session in SessionDB
      2. Persist the user message (so the turn is recorded even if
         agent_loop crashes mid-stream)
      3. Build AgentContext (system prompt + history + tools)
      4. Run agent_loop, forwarding each event via ``on_event``
         (transformed into ``chat_response`` envelopes that match
         what the webui chat path used to emit, so TUI/web handlers
         work without changes)
      5. Persist the assistant message + any tool_result rows
      6. Update sessions.head_id, last_prompt_tokens, updated_at
      7. Return TurnResult with the final text + usage
    """
    started_at = time.time()
    on_event = on_event or _noop
    user_msg_id = req.user_msg_id or uuid.uuid4().hex[:12]
    req.user_msg_id = user_msg_id
    from openprogram.programs.tools.planning.goal.presentation import event_sink
    on_event = event_sink(on_event, req)
    on_event = naming_reply_row(on_event, req.session_id, user_msg_id, user_msg_id + "_reply")
    from openprogram.operations.self_update.control.maintenance import turn_admission

    # User slash actions are still durable Agent turns. Keep their existing
    # DAG-specific behavior behind the same dispatcher/driver lifecycle so
    # public transports do not start a separate slash-action thread.
    from openprogram.runtime.agent.turn_request import slash_action_of

    slash_action = slash_action_of(req)
    if slash_action is not None:
        try:
            from openprogram.runtime.conversation.turn_runtime.slash_actions import (
                run_merge as _run_merge,
                run_spawn as _run_spawn,
            )

            action_payload = dict(req.structured_output or {})
            if slash_action == "spawn":
                action_ok = _run_spawn(
                    session_id=req.session_id,
                    msg_id=user_msg_id,
                    kwargs=action_payload,
                    agent_id=req.agent_id,
                )
            else:
                action_ok = _run_merge(
                    session_id=req.session_id,
                    msg_id=user_msg_id,
                    kwargs=action_payload,
                    agent_id=req.agent_id,
                    branch_id=getattr(req, "branch_id", None),
                )
            return TurnResult(
                final_text="",
                user_msg_id=user_msg_id,
                assistant_msg_id="",
                failed=action_ok is False,
                error=(f"{req.interaction} failed" if action_ok is False else ""),
            )
        except Exception as exc:
            return TurnResult(
                final_text="",
                user_msg_id=user_msg_id,
                assistant_msg_id="",
                failed=True,
                error=f"{req.interaction} failed: {type(exc).__name__}: {exc}",
            )

    # Usage metering: label every LLM call in this turn with its source.
    # Default to "chat", but DON'T clobber a source an outer scope already
    # set (an Agent method runtime / subagent wraps the turn in
    # ``usage_scope(call_kind="exec"|"subagent")`` before calling us). Set
    # the contextvar directly (not a ``with``) so it spans the whole sync
    # turn, mirroring the plan-mode contextvar set just below.
    try:
        from dataclasses import replace as _replace
        from openprogram.models.usage.context import (
            current_usage_context, _current as _usage_cur,
        )
        _cur = current_usage_context()
        # A turn that runs with other instructions than the conversation's
        # own is compared for prompt-cache breaks with its own kind only.
        _lane = "goal_verification" if req.goal_verification else (
            req.source if (req.source or "").startswith("self_update_") else None)
        if _cur.call_kind is None:
            _usage_cur.set(_replace(_cur,
                call_kind="chat", agent_id=req.agent_id, session_id=req.session_id,
                prefix_lane=_lane))
        else:
            # Keep the outer source (exec/subagent) but fill in this turn's
            # session/agent so nested compaction/summary calls attribute right.
            _usage_cur.set(_replace(
                _cur,
                agent_id=_cur.agent_id or req.agent_id,
                session_id=_cur.session_id or req.session_id,
                prefix_lane=_lane,
            ))
    except Exception:
        # Usage metering is observability, never a reason to fail a turn;
        # a miss only mis-attributes this turn's token counts.
        _log.debug("usage context binding failed", exc_info=True)

    # Plan-mode session context: expose ``req.session_id`` so the
    # enter_plan_mode / exit_plan_mode tool bodies can flip the
    # per-session flag without args plumbing. ContextVars propagate
    # through asyncio tasks, so any coroutine the agent loop spawns
    # from this turn (including tool executes) sees the same value.
    _plan_mode.current_session_id.set(req.session_id)

    # Suffix matches the `/run` path (server.py) and the webui React
    # client's `replyId()` — all three mint the assistant reply id as
    # ``<user_msg_id>_reply`` so the live streaming bubble's
    # ``data-msg-id`` matches the persisted DAG node id without a
    # reload. (Was ``_a``, which only the post-refresh view resolved.)
    assistant_msg_id = user_msg_id + "_reply"

    # Lazy imports — dispatcher is imported by webui at startup; the
    # agent_loop chain pulls in providers + httpx + many heavy deps
    # we don't want to load until first use.
    from openprogram.runtime.context import Context
    db = Context.turn_store(req.session_id)
    from openprogram.runtime.conversation.turn_runtime.turn_writer import (
        TurnWriter, mark_session_finished,
    )
    _writer = TurnWriter(db, req)

    # 1-2. Session ensure + history resolution + user-message persist
    #      (prep.py). Head movement stays inside the TurnWriter.
    with turn_admission(req.source) as admitted:
        if not admitted:
            from openprogram.operations.self_update.control.held_turns import HELD_MESSAGE, HELD_REASON, hold
            if hold(req, locked=True):
                _run_slot.finishing(req.session_id, user_msg_id)
                on_event({"type": "chat_response", "data": {
                    "type": "result", "session_id": req.session_id, "msg_id": user_msg_id,
                    "assistant_msg_id": assistant_msg_id, "content": HELD_MESSAGE, "blocks": [],
                }})
                return TurnResult(
                    final_text=HELD_MESSAGE,
                    user_msg_id=user_msg_id,
                    assistant_msg_id=assistant_msg_id,
                    error_reason=HELD_REASON,
                )
            message = "This turn did not run: OpenProgram is switching to an update. Send it again once the update has finished."
            on_event({
                "type": "chat_response",
                "data": {
                    "type": "error",
                    "session_id": req.session_id,
                    "msg_id": user_msg_id,
                    "content": message,
                    "reason_code": "SELF_UPDATE_MAINTENANCE",
                },
            })
            return TurnResult(
                final_text="",
                user_msg_id=user_msg_id,
                assistant_msg_id=assistant_msg_id,
                failed=True,
                error=message,
                error_reason="SELF_UPDATE_MAINTENANCE",
                error_retryable=True,
            )
        restart_existing = bool(
            execution_context and execution_context.get("restart_initial")
            and db.message_exists(req.session_id, user_msg_id)
        )
        if restart_existing:
            from openprogram.runtime.context.persistence import rendered_history
            session = db.get_session(req.session_id)
            history = rendered_history(db, req.session_id, head_id=user_msg_id) or []
            req.user_already_persisted = True
            from openprogram.state.memory.policy import bind_history
            bind_history(req, history)
        else:
            session, history = prepare_turn(
                db=db, req=req, writer=_writer,
                user_msg_id=user_msg_id, on_event=on_event,
                assistant_msg_id=assistant_msg_id,
            )
        # The admission lock is released only after quiescence can observe us.
        # A background turn runs beside the conversation and leaves its
        # status to the turns the owner sees.
        if not beside_conversation(req):
            db.update_session(req.session_id, status="running")
    _bind_usage_branch(db, req.session_id, getattr(req, "branch_id", None))

    # 事件层：用户轮已落盘，agent loop 即将启动。
    from openprogram.runtime.events import emit_safe as _emit_safe
    _emit_safe("turn.start", "system",
               {"session_id": req.session_id, "user_msg_id": user_msg_id,
                "assistant_msg_id": assistant_msg_id},
               {"session": req.session_id})

    # 3. Per-turn ContextVar bindings (turn_context.py): GraphStore +
    #    DAG runtime + turn id + worktree cwd + deferred-tool set,
    #    plus the project auto-commit baseline snapshot.
    _bindings = TurnBindings.bind(
        req=req, assistant_msg_id=assistant_msg_id, db=db,
    )
    _project_baseline = _bindings.project_baseline

    # 3b. Persist an assistant *placeholder* row so the row exists in
    #     the DB before tool_execution_end events start firing. This
    #     lets the in-flight tool rows (added by the stream tap) hang
    #     off ``caller = assistant_msg_id`` — and lets a mid-turn page
    #     refresh actually find them via the parent aggregation in
    #     webui/persistence._aggregate_tool_messages. We update this
    #     row's content + tool_calls/blocks at turn end (step 5) once
    #     the LLM's final text is known.
    _placeholder_inserted = (
        restart_existing and db.message_exists(req.session_id, assistant_msg_id)
    ) or _writer.open_placeholder(assistant_msg_id, user_msg_id)

    # 4. Run the agent loop. Errors below get caught and reported as
    #    a system message so the conversation isn't left in a stuck
    #    "agent is thinking…" state.
    _bound_reply = None
    try:
        # In both paths we pass history WITHOUT the new user message:
        # * user_already_persisted=False: history was loaded before the
        #   DB append, so it doesn't include user_msg. agent_loop will
        #   add UserMessage prompt to context.messages itself.
        # * user_already_persisted=True: history was reloaded post-append
        #   and DOES include user_msg — but we trim it back off, and
        #   call agent_loop (not _continue) so the prompt mechanism
        #   adds it exactly once. Previously this branch passed history
        #   as-is (with user_msg) to agent_loop_continue which left
        #   the new user msg duplicated at the tail of every request
        #   prefix and broke OpenAI prompt caching.
        if req.user_already_persisted and history and history[-1].get("id") == user_msg_id:
            loop_history = history[:-1]
        else:
            loop_history = history
        # _agentic_tool_names is filled by _run_loop_blocking once it
        # resolves the tool list — used below in step 5 to filter
        # Agent method calls out of the assistant message's
        # tool_calls/blocks (they render as their own runtime-block row
        # instead of as collapsed tool cards under the assistant bubble).
        # The stream tap shares the same set so it can skip persisting
        # those calls as collapsed role=tool entries.
        _agentic_tool_names: set[str] = set()
        _ordered_blocks: list[dict] = []
        # Wrap on_event so we can sniff tool_execution_end envelopes
        # and write each completed tool row to the DB incrementally —
        # without changing _run_loop_blocking's signature (test
        # mocks wrap it positionally and would break on a new kwarg).
        # A chat bound to a channel account sends this reply there too
        # (bound_reply.py); None when the chat is unbound or the turn's
        # source is answered by the channel's inbound path.
        from openprogram.runtime.conversation import bound_reply as _bound_reply_mod
        _bound_reply = _bound_reply_mod.attach(
            req, assistant_msg_id, resumed=bool((execution_context or {}).get("held_replay")))
        _on_event_persist = make_stream_tap(
            on_event=_bound_reply.tap(on_event) if _bound_reply else on_event, req=req,
            assistant_msg_id=assistant_msg_id,
            placeholder_inserted=_placeholder_inserted,
            agentic_tool_names=_agentic_tool_names,
        )
        try:
            final_text, usage, tool_calls = _run_loop_blocking(
                req=req,
                history=loop_history,
                on_event=_on_event_persist,
                cancel_event=cancel_event,
                assistant_msg_id=assistant_msg_id,
                agentic_tool_names_out=_agentic_tool_names,
                ordered_blocks_out=_ordered_blocks,
                execution_context=execution_context,
            )
        finally:
            _on_event_persist.flush()
    except Exception as e:
        # Error fold / standalone error node / taxonomy / error
        # TurnResult — error_path.py. Head movement stays with the
        # TurnWriter (record_failure). An errored turn still ends the
        # turn, so queued cross-branch messages are drained here too.
        _drain_send_message_inbox(req.session_id)
        if _bound_reply is not None:
            _bound_reply.abort()
        return handle_turn_error(
            db=db, req=req, session=session, exc=e,
            writer=_writer,
            user_msg_id=user_msg_id,
            assistant_msg_id=assistant_msg_id,
            placeholder_inserted=_placeholder_inserted,
            project_baseline=_project_baseline,
            on_event=on_event,
            started_at=started_at,
        )
    finally:
        # Release the Agent method runtime hook. Runs on success,
        # exception, AND inside the early-return above (finally fires
        # before return is actually executed).
        _bindings.release(suspended=bool(
            execution_context is not None
            and execution_context.get("safe_point_committed")
        ))

    if execution_context is not None and execution_context.get("safe_point_committed"):
        if _bound_reply is not None:
            _bound_reply.pause()
        result = TurnResult(
            final_text="",
            user_msg_id=user_msg_id,
            assistant_msg_id=assistant_msg_id,
        )
        setattr(result, "_execution_safe_point_handoff", True)
        return result

    # 4b. Files the turn handed back via ``send_file`` become attachment
    #     markers on the end of the reply text. Doing it here — on the
    #     one local every consumer reads — puts them in the stored
    #     message (so web chat renders a file card), in the streamed
    #     result, and in the TurnResult the channel layer uploads from,
    #     in one move. Same lexicon as an inbound attachment, deliberately.
    #     A resumed turn does the same in execute_continuation.
    #     Deliverables the turn produced (a deck a script built, named in
    #     the reply) are attached the same way without a send_file call.
    from openprogram.programs.tools.interaction.send_file import turn_attachments
    final_text, attached_files = turn_attachments(final_text, tool_calls, req, started_at)

    # 5. Persist the assistant message (phase 5) — extracted to
    #    persistence.py (dispatcher-split step 5). Returns the
    #    possibly-rewritten usage + filtered tool_calls + ordered
    #    blocks, which finalize (6) and the TurnResult (7) consume.
    assistant_msg, blocks, tool_calls, usage = persist_assistant_message(
        db=db,
        req=req,
        session=session,
        usage=usage,
        final_text=final_text,
        history=history,
        tool_calls=tool_calls,
        _ordered_blocks=final_blocks(_on_event_persist, _ordered_blocks, cancel_event),
        _agentic_tool_names=_agentic_tool_names,
        _placeholder_inserted=_placeholder_inserted,
        cancel_event=cancel_event,
        assistant_msg_id=assistant_msg_id,
        user_msg_id=user_msg_id,
    )

    # 5b. Hand the finished turn to memory. Must come after step 5:
    #     the provider reads the turn back out of the session store
    #     (memory/writing.py), so calling any earlier
    #     counts a turn whose assistant row is still the empty
    #     placeholder — the reply would only reach the threshold check
    #     one turn late.
    _memory_write(req.session_id)

    # 6. Turn-finalization bookkeeping — head/token update, context-
    #    commit backfill, usage feedback, auto-title, git + project
    #    commit, snapshot eviction. Extracted to finalize.py
    #    (dispatcher-split step 4). The agent profile + real context
    #    window are resolved HERE, under the test-patch seam
    #    (_load_agent_profile / _resolve_model are patched on this
    #    package), and handed down so finalize_turn never calls a
    #    patched helper. Best-effort resolve: None on failure → the
    #    6.4 usage-feedback step is skipped, matching the old inline
    #    try/except fall-through.
    _fin_profile = None
    _fin_ctx_win = None
    try:
        from openprogram.runtime.context.tokens import real_context_window as _rcw
        from copy import deepcopy
        _fin_profile = (
            deepcopy(req.profile_snapshot) if req.profile_snapshot is not None
            else _load_agent_profile(req.agent_id)
        )
        _fin_ctx_win = _rcw(_resolve_model(_fin_profile, req.model_override))
    except Exception:
        _fin_profile = None
        _fin_ctx_win = None
    turn_committed = finalize_turn(
        db=db,
        req=req,
        session=session,
        usage=usage,
        assistant_msg=assistant_msg,
        assistant_msg_id=assistant_msg_id,
        _project_baseline=_project_baseline,
        agent_profile=_fin_profile,
        ctx_win=_fin_ctx_win,
        on_event=on_event,
        head_id=_writer.head_for_finalize(assistant_msg_id),
    )

    # 事件层：finalize 完成，本轮记账收尾。usage 摘要只带 token 计数键。
    _emit_safe("turn.end", "system",
               {"session_id": req.session_id, "user_msg_id": user_msg_id,
                "assistant_msg_id": assistant_msg_id,
                "usage": {k: v for k, v in (usage or {}).items()
                          if isinstance(v, (int, float))}},
               {"session": req.session_id})

    # Mark session idle/done now that the turn completed successfully.
    session_finished = False
    try:
        if not beside_conversation(req):
            mark_session_finished(db, req.session_id, quiet=bool(getattr(req, "quiet_finish", False)))
        session_finished = True
    except Exception:
        # A stuck "running" status is visible in the UI, so log it.
        _log.warning(
            "failed to mark session %s finished", req.session_id, exc_info=True,
        )

    # A self-update may stop this worker in a later phase. Release it only
    # after the final assistant node, saved session fields, and finished status
    # are all durable. A mismatch is a no-op; a storage error leaves the
    # request in PREPARING for explicit inspection/cancellation.
    if turn_committed and session_finished:
        _release_prepared_update(req.session_id, assistant_msg_id)

    # 6.99. Deliver cross-branch messages queued while this turn ran
    #       (send_message busy-queueing) — the turn is over, the session
    #       is free, each queued message now runs its own turn.
    if not getattr(req, "background", False):
        # A background turn's end does not free the conversation: the
        # foreground turn, if any, drains the inbox when it ends.
        _drain_send_message_inbox(req.session_id)

    # 7. Final result event for clients that wait for the synchronous
    #    "the turn is done" signal. It carries the reply as persisted
    #    (content plus the ordered blocks a reload would show), so a client
    #    that missed stream frames can replace the row instead of keeping
    #    a partial one. Without final blocks the row keeps the ones the
    #    stream tap wrote. The slot learns first that the turn is only
    #    finishing up, so a message sent on seeing this frame waits for
    #    the slot instead of being refused (run_slot.finishing).
    _run_slot.finishing(req.session_id, user_msg_id)
    on_event({"type": "chat_response",
              "data": {"type": "result", "session_id": req.session_id,
                       "msg_id": user_msg_id,
                       "assistant_msg_id": assistant_msg_id,
                       "content": final_text,
                       "blocks": blocks or (
                           list(_on_event_persist.blocks)
                           if _placeholder_inserted else []),
                       **_structured_output_fields(req)}})
    if _bound_reply is not None:
        _bound_reply.finish(final_text, attached_files=attached_files)

    return TurnResult(
        final_text=final_text,
        user_msg_id=user_msg_id,
        assistant_msg_id=assistant_msg_id,
        tool_calls=tool_calls,
        attached_files=attached_files,
        last_round_text=req.last_round_text,
        usage=usage,
        duration_ms=int((time.time() - started_at) * 1000),
        stopped=bool(cancel_event and cancel_event.is_set()),
        blocks=blocks,
        structured_output=req.structured_output,
        structured_output_mode=req.structured_output_mode,
        structured_output_attempt=req.structured_output_attempt,
    )


# ---------------------------------------------------------------------------
# The Agent profile and model live in ``model_tools``. ``loop_runner`` reads
# ``_load_agent_profile`` / ``_resolve_model`` through this package at call
# time, so they are the seams tests patch.
# ---------------------------------------------------------------------------
from openprogram.runtime.conversation.model_tools import (
    load_agent_profile as _load_agent_profile,
    resolve_model as _resolve_model,
)


def process_user_turn(req, *, on_event=None, cancel_event=None, execution_context=None):
    """Compatibility entry; production conversations use the ChatAgent type."""
    from openprogram.runtime.conversation.chat import ChatAgent
    return ChatAgent().run_turn(req, on_event=on_event, cancel_event=cancel_event,
                                execution_context=execution_context)


def process_agent_continuation(continuation, *, on_event=None, cancel_event=None,
                               execution_context=None):
    """Resume through the same Agent contract without repeating admission."""
    from openprogram.runtime.conversation.chat import ChatAgent
    return ChatAgent().resume_turn(continuation, on_event=on_event,
                                   cancel_event=cancel_event,
                                   execution_context=execution_context)


__all__ = [
    "_wrap_agentic_runtime_block",
    "dispatch_forced_tool_call",
    "execute_continuation",
    "execute_turn",
    "process_agent_continuation",
    "process_user_turn",
    "trigger_compaction",
]
