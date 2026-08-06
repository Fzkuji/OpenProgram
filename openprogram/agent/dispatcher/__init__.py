"""Single entry point for every conversation turn.

Replaces the two ad-hoc paths that used to call ``runtime.exec(content)``
directly (channels worker + webui chat). Both now go through
``process_user_turn`` → ``agent_loop`` → tool dispatch + streaming
events broadcast as ``chat_response`` envelopes that any TUI / web /
future client subscribes to.

Architectural shape mirrors hermes' ``gateway/run.py:_run_agent``:
build context from durable session state, invoke the agent loop,
forward each emitted event to a broadcast hook, persist the final
turn. The TUI / web frontend doesn't know who triggered the turn —
the same ``chat_response`` envelope arrives whether a wechat message
came in or the user typed in PromptInput.

This file is the orchestrator only — each pipeline stage lives in its
own sibling module (dispatcher-split):
  prep.py         — steps 1-2: session ensure + history + user persist
  turn_context.py — step 3: per-turn ContextVar bindings
  stream_tap.py   — event tap: incremental tool-node persistence
  loop_runner.py  — step 4: agent-loop run (``_run_loop_blocking``)
  persistence.py  — step 5: assistant message persistence
  finalize.py     — step 6: turn-finalization bookkeeping
  error_path.py   — except branch: error fold + taxonomy + TurnResult
  turn_writer.py  — the ONE writer allowed to move the session head
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Optional

_log = logging.getLogger(__name__)


# Type aliases + the parent sentinel + the TurnRequest / TurnResult
# dataclasses live in the sibling ``types`` module (dispatcher-split
# step 1). Re-imported here so ``dispatcher.<Name>`` and every external
# ``from openprogram.agent.dispatcher import ...`` resolve unchanged.
from openprogram.agent.dispatcher.types import (
    EventCallback,
    INHERIT_PARENT,
    PermissionMode,
    TurnRequest,
    TurnResult,
    _InheritParent,
    _noop,
)

# Leaf helpers extracted into sibling modules, re-exported so
# ``dispatcher.<name>`` and every external import resolve unchanged:
#   titles.py         — _default_title / _maybe_auto_title / trigger_compaction
#   forced_tool.py    — dispatch_forced_tool_call (webui/routes/chat.py imports it)
#   runtime_attach.py — _wrap_agentic_runtime_block (process_runner.py imports it)
from openprogram.agent.dispatcher.titles import (
    _default_title,
    _maybe_auto_title,
    _title_from_text,
    trigger_compaction,
)
from openprogram.agent.dispatcher.forced_tool import dispatch_forced_tool_call
from openprogram.agent.dispatcher.runtime_attach import _wrap_agentic_runtime_block
# Stage modules called by _process_turn_once (internal — not re-exported
# for external callers):
#   prep.py         — steps 1-2 (session/history/user persist)
#   turn_context.py — step 3 (per-turn ContextVar bindings)
#   stream_tap.py   — the tool-node-persisting on_event wrap
#   persistence.py  — phase-5 assistant persistence
#   finalize.py     — phase-6 bookkeeping
#   error_path.py   — the except branch
from openprogram.agent.dispatcher.prep import prepare_turn
from openprogram.agent.dispatcher.turn_context import TurnBindings
from openprogram.agent.dispatcher.stream_tap import make_stream_tap
from openprogram.agent.dispatcher.error_path import handle_turn_error
from openprogram.agent.dispatcher.finalize import finalize_error_turn, finalize_turn
from openprogram.agent.dispatcher.persistence import persist_assistant_message

# The agent-loop run stage. Bound as a package attribute named
# ``_run_loop_blocking`` — the seam tests patch (patch.object(D,
# "_run_loop_blocking", ...)); the orchestrator below reads the module
# global at call time so the patch always wins.
from openprogram.agent.dispatcher.loop_runner import (
    run_loop_blocking as _run_loop_blocking,
)


# ---------------------------------------------------------------------------
# Approval registry — used by the "ask" permission flow
# ---------------------------------------------------------------------------

# Approval gate lives in _approval.py — re-exported here so callers
# (tests, server.py WS handler) can keep using
# ``dispatcher.approval_registry()``. 审批已合流到 QuestionRegistry，
# ``approval_registry()`` 返回统一的 QuestionRegistry（不再有 ApprovalRegistry 类）。
from openprogram.agent import plan_mode as _plan_mode
from openprogram.agent.internals._approval import (
    approval_registry,
    wrap_with_approval as _wrap_with_approval,
    await_user_approval as _await_user_approval,
)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def process_user_turn(
    req: TurnRequest,
    *,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> TurnResult:
    """One full agent turn, then the session-goal continuation loop.

    Every caller (webui / channels / CLI / task runner) enters here, so
    an active session goal (``/goal``) is honoured no matter who
    triggered the turn: after the turn — finalize (phase 6/7) included —
    :func:`openprogram.agent.goal.continue_goal_turns` judges the goal
    and, while unmet and under budget, keeps running follow-up turns
    (``source="goal_continue"``). Each continuation runs through
    :func:`_process_turn_once` like any other turn — its own
    persistence, commits and compaction — never a nested
    ``process_user_turn``, so the loop cannot recurse into itself.
    Returns the LAST turn's result.

    Sessions without an active goal pay one meta read and return the
    single turn's result unchanged.
    """
    # Same-session spawned turns (goal decisions, task agents,
    # message_branch) are components inside someone else's turn, not the
    # session's main line: they enter neither the goal loop nor the
    # turn.stop gate. Without this, the goal decision turn itself got
    # judged, which spawned another decision turn — recursion until the
    # agentic-function nesting cap broke the chain.
    if req.source == "agent_spawn":
        return _process_turn_once(
            req, on_event=on_event, cancel_event=cancel_event)
    # Goal sessions never enter the turn.stop gate: the goal loop is the
    # session's sole stop decider (the only external intervention is
    # /goal clear), and the gate is the extension point for sessions
    # WITHOUT a goal. Snapshot participation before the turn — a goal
    # that ends (achieved/capped/error) during this round still counts
    # as a goal session.
    goal_session = False
    try:
        from openprogram.agent.goal import load_goal
        _g = load_goal(req.session_id)
        goal_session = bool(_g and _g.get("status") in ("active",
                                                        "waiting_user"))
    except Exception:
        pass
    result = _process_turn_once(
        req, on_event=on_event, cancel_event=cancel_event)
    continue_goal_turns = None
    try:
        from openprogram.agent.goal import continue_goal_turns
        result = continue_goal_turns(
            req, result, run_turn=_process_turn_once,
            on_event=on_event, cancel_event=cancel_event)
    except Exception:
        # The goal loop must never lose a finished turn's result. A
        # crash below still returns what the user's own turn produced.
        _log.warning("goal continuation failed for session %s",
                     req.session_id, exc_info=True)
        return result
    if goal_session:
        return result
    # turn.stop gate: hooks may deny the stop and force continuation
    # turns (stop_hook.py) — sessions without a goal only. Runs after
    # the turn finished; a crash below still returns the result.
    try:
        from openprogram.agent.dispatcher.stop_hook import (
            continue_stop_hook_turns,
        )
        return continue_stop_hook_turns(
            req, result, run_turn=_process_turn_once,
            goal_continue=continue_goal_turns,
            on_event=on_event, cancel_event=cancel_event)
    except Exception:
        _log.warning("turn.stop hook continuation failed for session %s",
                     req.session_id, exc_info=True)
        return result


def _process_turn_once(
    req: TurnRequest,
    *,
    on_event: Optional[EventCallback] = None,
    cancel_event: Optional[threading.Event] = None,
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

    # Usage metering: label every LLM call in this turn with its source.
    # Default to "chat", but DON'T clobber a source an outer scope already
    # set (an @agentic_function runtime / subagent wraps the turn in
    # ``usage_scope(call_kind="exec"|"subagent")`` before calling us). Set
    # the contextvar directly (not a ``with``) so it spans the whole sync
    # turn, mirroring the plan-mode contextvar set just below.
    try:
        from dataclasses import replace as _replace
        from openprogram.usage.context import (
            UsageContext, current_usage_context, _current as _usage_cur,
        )
        _cur = current_usage_context()
        if _cur.call_kind == "unknown":
            _usage_cur.set(UsageContext(
                call_kind="chat", agent_id=req.agent_id, session_id=req.session_id))
        else:
            # Keep the outer source (exec/subagent) but fill in this turn's
            # session/agent so nested compaction/summary calls attribute right.
            _usage_cur.set(_replace(
                _cur,
                agent_id=_cur.agent_id or req.agent_id,
                session_id=_cur.session_id or req.session_id,
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
    from openprogram.agent.session_db import default_db
    db = default_db()
    from openprogram.agent.dispatcher.turn_writer import TurnWriter
    _writer = TurnWriter(db, req)

    # 1-2. Session ensure + history resolution + user-message persist
    #      (prep.py). Head movement stays inside the TurnWriter.
    session, history = prepare_turn(
        db=db, req=req, writer=_writer,
        user_msg_id=user_msg_id, on_event=on_event,
    )

    # 事件层：用户轮已落盘，agent loop 即将启动。
    from openprogram.events import emit_safe as _emit_safe
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
    _placeholder_inserted = _writer.open_placeholder(
        assistant_msg_id, user_msg_id,
    )

    # Mark session as running before agent loop starts.
    try:
        db.update_session(req.session_id, status="running")
    except Exception:
        _log.warning(
            "failed to mark session %s running", req.session_id, exc_info=True,
        )

    # 4. Run the agent loop. Errors below get caught and reported as
    #    a system message so the conversation isn't left in a stuck
    #    "agent is thinking…" state.
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
        # @agentic_function calls out of the assistant message's
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
        _on_event_persist = make_stream_tap(
            on_event=on_event, req=req,
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
            )
        except Exception as _loop_exc:
            from openprogram.context.reactive import is_overflow_error, reactive_compact
            if is_overflow_error(_loop_exc):
                _agent_profile = _load_agent_profile(req.agent_id)
                _compacted = reactive_compact(
                    agent_profile=_agent_profile,
                    session_id=req.session_id,
                    model=_resolve_model(_agent_profile, req.model_override),
                    history=loop_history,
                    on_event=_on_event_persist,
                )
                if _compacted is not None:
                    # Same set object the stream tap consults — clear in
                    # place so the retry run refills it.
                    _agentic_tool_names.clear()
                    _ordered_blocks = []
                    final_text, usage, tool_calls = _run_loop_blocking(
                        req=req,
                        history=_compacted,
                        on_event=_on_event_persist,
                        cancel_event=cancel_event,
                        assistant_msg_id=assistant_msg_id,
                        agentic_tool_names_out=_agentic_tool_names,
                        ordered_blocks_out=_ordered_blocks,
                    )
                else:
                    raise
            else:
                raise
    except Exception as e:
        # Error fold / standalone error node / taxonomy / error
        # TurnResult — error_path.py. Head movement stays with the
        # TurnWriter (record_failure).
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
        # Release the @agentic_function runtime hook. Runs on success,
        # exception, AND inside the early-return above (finally fires
        # before return is actually executed).
        _bindings.release()

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
        _ordered_blocks=_ordered_blocks,
        _agentic_tool_names=_agentic_tool_names,
        _placeholder_inserted=_placeholder_inserted,
        cancel_event=cancel_event,
        assistant_msg_id=assistant_msg_id,
        user_msg_id=user_msg_id,
    )

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
        from openprogram.context.tokens import real_context_window as _rcw
        _fin_profile = _load_agent_profile(req.agent_id)
        _fin_ctx_win = _rcw(_resolve_model(_fin_profile, req.model_override))
    except Exception:
        _fin_profile = None
        _fin_ctx_win = None
    finalize_turn(
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
    try:
        if req.source in {"wechat", "telegram", "discord", "slack"}:
            db.update_session(req.session_id, status="done", unread=True)
        else:
            db.update_session(req.session_id, status="idle")
    except Exception:
        # A stuck "running" status is visible in the UI, so log it.
        _log.warning(
            "failed to mark session %s finished", req.session_id, exc_info=True,
        )

    # 7. Final result event for clients that wait for the synchronous
    #    "the turn is done" signal.
    on_event({"type": "chat_response",
              "data": {"type": "result", "session_id": req.session_id,
                       "content": final_text}})

    return TurnResult(
        final_text=final_text,
        user_msg_id=user_msg_id,
        assistant_msg_id=assistant_msg_id,
        tool_calls=tool_calls,
        usage=usage,
        duration_ms=int((time.time() - started_at) * 1000),
        blocks=blocks,
    )


# Event/usage parsing helpers live in _event_parsing.py.
from openprogram.agent.internals._event_parsing import (
    agent_event_to_envelope as _agent_event_to_envelope,
    aiter_event_stream as _aiter_event_stream,
    extract_text as _extract_text,
    extract_usage as _extract_usage,
    shorten as _shorten,
    stringify_tool_result as _stringify_tool_result,
)


# ---------------------------------------------------------------------------
# Agent profile + tools — live in _model_tools.py; re-exported here so
# ``dispatcher._load_agent_profile`` / ``dispatcher._resolve_model``
# stay the package attributes the tests patch (loop_runner.py reads
# them through this package at call time).
# ---------------------------------------------------------------------------
from openprogram.agent.internals._model_tools import (
    load_agent_profile as _load_agent_profile,
    is_anthropic_family as _is_anthropic_family,
    resolve_model as _resolve_model,
    with_tool_runtime_prompt as _with_tool_runtime_prompt,
    log_resolved_tools as _log_resolved_tools,
    resolve_tools as _resolve_tools,
    history_to_agent_messages as _history_to_agent_messages,
)
