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
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
import uuid
from typing import Any, Iterable, Optional

from openprogram.agent.event_bus import emit_safe
from openprogram.agent.session_config import reasoning_from_config, SessionRunConfig


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

# Leaf helpers extracted into sibling modules (dispatcher-split steps
# 2–3), re-exported so ``dispatcher.<name>`` and every external import
# resolve unchanged:
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
# finalize.py / persistence.py hold phase-6 bookkeeping and phase-5
# assistant persistence; called by process_user_turn (internal — not
# re-exported for external callers).
from openprogram.agent.dispatcher.finalize import finalize_turn
from openprogram.agent.dispatcher.persistence import persist_assistant_message

# Lifecycle helpers (placeholder insert / status flip / error fold) —
# see openprogram/agent/_turn_lifecycle.py for the per-turn DB
# protocol. Kept in a sibling module so this file doesn't grow past
# its already-1500-line mark.
from openprogram.agent.internals._turn_lifecycle import (
    insert_placeholder as _insert_placeholder,
    mark_terminal_status as _mark_terminal_status,
    fold_error_into_placeholder as _fold_error_into_placeholder,
    write_standalone_error_node as _write_standalone_error_node,
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
        pass

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

    # 1. Ensure session exists. Load history along the *active branch*
    #    (parent-walked from head_id) instead of the full append log,
    #    so retried / forked branches don't pollute the LLM context.
    session = db.get_session(req.session_id)
    if session is None:
        db.create_session(
            req.session_id, req.agent_id,
            title=_default_title(req),
            source=req.source,
            channel=req.source if req.source in {"wechat", "telegram", "discord", "slack"} else None,
            peer_display=req.peer_display,
            peer_id=req.peer_id,
        )
        session = db.get_session(req.session_id) or {}
    if req.history_override is not None:
        history = list(req.history_override)
    elif isinstance(req.branch_from, _InheritParent):
        # Normal append — walk the active branch.
        history = db.get_branch(req.session_id) or db.get_messages(req.session_id)
    elif req.branch_from is None:
        # Root-level fork — LLM starts with empty history.
        history = []
    else:
        # Sibling fork — history is the branch ending at the explicit
        # parent. LLM sees what existed up to the fork point, not
        # what's currently on the active branch.
        history = db.get_branch(req.session_id, req.branch_from)

    # 2. Persist user message immediately (so a crash mid-stream still
    #    leaves the user's input recorded). Resolve called_by:
    #      INHERIT_PARENT → tail of active branch, or NULL if empty
    #      explicit None  → NULL (root-level fork)
    #      explicit str   → that string (sibling fork)
    if isinstance(req.branch_from, _InheritParent):
        if history:
            user_caller_id = history[-1].get("id")
        else:
            user_caller_id = session.get("head_id")
    else:
        user_caller_id = req.branch_from
    user_msg: dict[str, Any] = {
        "id": user_msg_id,
        "role": "user",
        "content": req.user_text,
        "timestamp": time.time(),
        "called_by": user_caller_id,
        "source": req.source,
        "peer_display": req.peer_display,
        "peer_id": req.peer_id,
        # Stamp which agent this turn was sent to so the UI can render
        # per-agent avatar / label / colour. Same field on assistant
        # below — the pair lets the UI tag both halves of a turn.
        "agent_id": req.agent_id,
    }
    # System-internal triggers — task_followup auto-notification,
    # merge prompt assembly — write a user-role node so the LLM
    # treats it as a turn, but they're NOT chats the human typed.
    # Mark display="runtime" so the chat panel renders them as a
    # quiet system marker (transparent surface, robot avatar) rather
    # than as a regular blue You-bubble that makes it look like the
    # user sent two messages in a row.
    #
    # Note: ``agent_spawn`` (the sub-agent's own first user msg) is
    # intentionally NOT in this set. When the user checks out the
    # sub branch, they want to see the prompt that started it — it's
    # the natural "You" message on that branch's HEAD path. On main,
    # the linear_history walk doesn't reach it (it's only on the sub
    # branch chain), so leaving it visible doesn't pollute main.
    if req.source in {"task_followup", "merge_turn"}:
        user_msg["display"] = "runtime"
    # Persist a lightweight attachment manifest (count + media types)
    # so /resume + the search picker can show "[2 images]" badges
    # without re-loading the base64 blobs. Full data still goes to
    # the LLM via the in-context UserMessage but doesn't need to live
    # in SessionDB rows — that would bloat the FTS5 index with base64.
    if req.attachments:
        manifest = []
        for att in req.attachments:
            if isinstance(att, dict):
                manifest.append({
                    "type": att.get("type"),
                    "media_type": att.get("media_type"),
                    "size_b64": len(att.get("data") or ""),
                })
        user_msg["extra"] = json.dumps({"attachments": manifest},
                                         default=str)
    # Ensure ROOT node exists (session DAG root). Idempotent.
    _ROOT_ID = "ROOT"
    try:
        from openprogram.context.nodes import Call as _RCall, ROLE_USER as _RU
        from openprogram.store import GraphStoreShim as _GShim0
        if not db.message_exists(req.session_id, _ROOT_ID):
            _GShim0(db, req.session_id).append(_RCall(
                id=_ROOT_ID, created_at=time.time(), role=_RU,
                output="", metadata={"display": "root"},
            ))
    except Exception:
        pass

    if not req.user_already_persisted:
        # Write the user node as a Call directly — same shape the DAG
        # uses everywhere (session-dag.md step 5). GraphStoreShim
        # .append already calls set_head for non-caller nodes, so no
        # separate set_head needed.
        try:
            from openprogram.context.nodes import Call, ROLE_USER
            from openprogram.store import GraphStoreShim as _GShim

            _user_meta = {
                k: v for k, v in user_msg.items()
                if k not in {"id", "role", "content", "timestamp", "extra"}
                and v is not None
            }
            _raw_extra = user_msg.get("extra")
            if _raw_extra:
                try:
                    _decoded = json.loads(_raw_extra) if isinstance(
                        _raw_extra, str) else _raw_extra
                    _user_meta.update(_decoded)
                except (json.JSONDecodeError, TypeError):
                    _user_meta["extra"] = _raw_extra
            _user_node = Call(
                id=user_msg_id,
                created_at=user_msg.get("timestamp") or time.time(),
                role=ROLE_USER,
                output=req.user_text,
                called_by=_ROOT_ID,
                metadata=_user_meta,
            )
            _shim.append(_user_node)
        except Exception:
            db.append_message(req.session_id, user_msg)
            db.set_head(req.session_id, user_msg_id)
        on_event({
            "type": "chat_ack",
            "data": {"session_id": req.session_id, "msg_id": user_msg_id},
        })
        # Broadcast the inbound user message itself so any UI tailing
        # this session (web sidebar transcript, TUI mirror) shows it
        # in real time — without this, channel-sourced messages
        # (wechat / discord) only appeared after the LLM started
        # replying. Carries source + peer_display so the UI can label
        # it appropriately and dedup against optimistic renders.
        on_event({
            "type": "chat_response",
            "data": {
                "type": "user_message",
                "session_id": req.session_id,
                "msg_id": user_msg_id,
                "content": req.user_text or "",
                "source": req.source,
                "peer_display": req.peer_display,
                "timestamp": user_msg.get("timestamp"),
                "called_by": user_msg.get("called_by"),
            },
        })
    else:
        # Caller already wrote the user msg + emitted ack (webui
        # path). Make sure history reflects that — load from DB if
        # the caller didn't pass a history_override.
        if req.history_override is None:
            history = db.get_branch(req.session_id) or history

    # 事件层 tap：无论哪条持久化路径（webui 先存 / dispatcher 自己存），
    # "用户消息提交了"都成立，所以放在分支外。
    emit_safe(
        "user.prompt_submitted", "user",
        {"msg_id": user_msg_id, "chars": len(req.user_text or "")},
        {"session": req.session_id},
    )

    # 3. Attach a Runtime with the session's GraphStore so any
    #    @agentic_function the agent_loop invokes records its
    #    placeholder / internal / exit nodes into the same DAG. The
    #    Runtime is shared via the ``_current_runtime`` ContextVar
    #    that @agentic_function's _inject_runtime consults.
    #
    #    Critical: we use ``create_runtime()`` (real provider) instead
    #    of a stub. @agentic_function's _inject_runtime would otherwise
    #    pick up our stub and any ``runtime.exec`` inside the function
    #    body would return whatever the stub's ``call`` does (a fixed
    #    string or empty) rather than actually calling an LLM. If
    #    real-runtime construction fails (e.g. no provider configured),
    #    fall back to NOT setting _current_runtime so @agentic_function
    #    can create its own runtime as before — DAG persistence
    #    gracefully degrades to off for this turn.
    from openprogram.store import (
        GraphStoreShim as _GraphStore,
        _store as _store_var,
        _current_turn_id as _turn_id_var,
    )
    from openprogram.agentic_programming.function import (
        _current_runtime as _current_runtime_var,
    )
    _dag_runtime = None
    _runtime_token = None
    _store_token = None
    # Tag this turn so file-mutating tools can attribute backups to
    # the right assistant message via checkpoint.helpers.
    _turn_id_token = _turn_id_var.set(assistant_msg_id)
    # Bind the session's active agent worktree (if any) to the
    # _current_worktree_path ContextVar for the duration of this turn.
    # bash / edit / write / read consult that var to default their cwd
    # to the worktree root. The binding is per-turn so a worktree
    # created mid-turn is picked up by the next turn entry — within
    # the same turn the tool that ran worktree_create also calls
    # set_worktree explicitly so the rest of that turn sees it.
    _worktree_token = None
    try:
        from openprogram.worktree.context import set_worktree as _set_wt
        from openprogram.worktree.manager import get_manager as _get_wt_mgr
        _wt_mgr = _get_wt_mgr()
        _active_wt = _wt_mgr.find_active_for_session(req.session_id)
        if _active_wt is not None:
            _worktree_token = _set_wt(_active_wt.worktree_path)
    except Exception:
        _worktree_token = None
    # Layer 6 (Claude Code's shouldDefer / ToolSearch): install a
    # session-scoped "loaded deferred tools" set so tool_search can
    # mutate it and subsequent turns see the updated set.
    from openprogram.functions import install_loaded_deferred
    install_loaded_deferred()

    # Project auto-commit (entity layer): snapshot which paths are
    # already dirty in the session's bound project BEFORE the agent
    # touches anything, so the turn-end commit can tell the user's
    # uncommitted work apart from the agent's edits (Strategy A). None
    # when disabled / ad-hoc session. Best-effort — never blocks a turn.
    _project_baseline = None
    try:
        from openprogram.store import project_commit as _pc
        _project_baseline = _pc.snapshot_baseline(req.session_id)
    except Exception:
        _project_baseline = None
    try:
        from openprogram.providers.registry import create_runtime as _create_rt
        _dag_runtime = _create_rt()
        _runtime_token = _current_runtime_var.set(_dag_runtime)
        # Expose the GraphStore via ContextVar so deep code
        # (Runtime.exec, ask_user, @agentic_function decorator)
        # writes land in the same SQLite DAG without threading the
        # store through every layer.
        _store_token = _store_var.set(_GraphStore(db, req.session_id))
    except Exception:
        # No provider configured / runtime construction blew up.
        # Skip the install; @agentic_function will still work, just
        # without its nodes landing in the DAG.
        _dag_runtime = None
        _runtime_token = None
        _store_token = None

    # 3b. Persist an assistant *placeholder* row so the row exists in
    #     the DB before tool_execution_end events start firing. This
    #     lets the in-flight tool rows (added below in the agent loop)
    #     hang off ``called_by = assistant_msg_id`` — and lets a mid-
    #     turn page refresh actually find them via the parent
    #     aggregation in webui/persistence._aggregate_tool_messages.
    #     We update this row's content + tool_calls/blocks at turn
    #     end (step 5) once the LLM's final text is known.
    _placeholder_inserted = _insert_placeholder(
        db, req.session_id, assistant_msg_id, user_msg_id, req.source,
    )
    if _placeholder_inserted:
        db.set_head(req.session_id, assistant_msg_id)

    # Mark session as running before agent loop starts.
    try:
        db.update_session(req.session_id, status="running")
    except Exception:
        pass

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
        # Wrap on_event so we can sniff tool_execution_end envelopes
        # and write each completed tool row to the DB incrementally —
        # without changing _run_loop_blocking's signature (test
        # mocks wrap it positionally and would break on a new kwarg).
        _tool_args_by_id: dict[str, dict] = {}

        def _on_event_persist(env: dict) -> None:
            on_event(env)
            if not _placeholder_inserted:
                return
            try:
                if env.get("type") != "chat_response":
                    return
                payload = env.get("data") or {}
                if payload.get("type") != "stream_event":
                    return
                evt = payload.get("event") or {}
                etype = evt.get("type")
                if etype == "tool_use":
                    tid = evt.get("tool_call_id")
                    if tid:
                        _tool_args_by_id[tid] = {
                            "tool": evt.get("tool"),
                            "input": evt.get("input"),
                        }
                elif etype == "tool_result":
                    tid = evt.get("tool_call_id")
                    if not tid:
                        return
                    meta = _tool_args_by_id.get(tid, {})
                    # @agentic_function tool calls are rendered as a
                    # runtime-block row (persisted by the wrapper in
                    # _wrap_agentic_runtime_block) — don't ALSO persist
                    # them as collapsed role=tool entries, that would
                    # duplicate the call in chat.
                    _tname = meta.get("tool") or evt.get("tool") or ""
                    if _tname in _agentic_tool_names:
                        return
                    try:
                        from openprogram.agent.session_db import (
                            default_db as _db,
                        )
                        from openprogram.context.nodes import Call, ROLE_CODE
                        from openprogram.store import GraphStoreShim

                        _tool_name = (meta.get("tool")
                                      or evt.get("tool") or "")
                        _node = Call(
                            id=f"{assistant_msg_id}_t_{tid}",
                            created_at=time.time(),
                            role=ROLE_CODE,
                            name=_tool_name,
                            input=meta.get("input") or {},
                            output=str(evt.get("result") or ""),
                            called_by=assistant_msg_id,
                            metadata={
                                "tool_call_id": tid,
                                "is_error": bool(evt.get("is_error")),
                            },
                        )
                        GraphStoreShim(
                            _db(), req.session_id,
                        ).append(_node)
                    except Exception:
                        pass
            except Exception:
                pass

        # _agentic_tool_names is filled by _run_loop_blocking once it
        # resolves the tool list — used below in step 5 to filter
        # @agentic_function calls out of the assistant message's
        # tool_calls/blocks (they render as their own runtime-block row
        # instead of as collapsed tool cards under the assistant bubble).
        _agentic_tool_names: set[str] = set()
        _ordered_blocks: list[dict] = []
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
                    _agentic_tool_names = set()
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
        # Two paths, both delegated to _turn_lifecycle:
        #   * placeholder present → fold the error into the same row
        #     so the chat UI shows a red assistant bubble (not an
        #     orphan system message next to an empty bubble).
        #   * placeholder missing → standalone system error node.
        head_for_next: Optional[str] = None
        err_text: Optional[str] = None
        if _placeholder_inserted:
            err_text = _fold_error_into_placeholder(
                db.db_path, req.session_id, assistant_msg_id, e,
            )
            if err_text is not None:
                head_for_next = assistant_msg_id
        if err_text is None:
            err_id = _write_standalone_error_node(
                db, req.session_id, user_msg_id, req.source, e,
            )
            err_text = f"[error] {type(e).__name__}: {e}"
            head_for_next = err_id
        # Move head to the failed turn so the next user message
        # chains off it, not off the user message that triggered it.
        try:
            db.update_session(req.session_id, head_id=head_for_next, status="failed")
        except Exception:
            pass
        # Classify the failure into the structured taxonomy (an LLMError
        # carries its own reason; anything else is classified) so the webui can
        # render a retryable rate-limit differently from a fatal auth/context
        # failure. See docs/design/providers/reliability/error-taxonomy-propagation.md.
        try:
            from openprogram.providers.utils.errors import taxonomy_fields
            _e_reason, _e_retryable, _e_retry_after = taxonomy_fields(e)
        except Exception:
            _e_reason = _e_retryable = _e_retry_after = None
        on_event({"type": "chat_response",
                  "data": {"type": "error", "session_id": req.session_id,
                           "content": err_text, "reason": _e_reason,
                           "retryable": _e_retryable,
                           "retry_after_s": _e_retry_after}})
        return TurnResult(
            final_text="",
            user_msg_id=user_msg_id,
            assistant_msg_id=(
                assistant_msg_id if _placeholder_inserted else ""),
            failed=True,
            error=str(e),
            error_reason=_e_reason,
            error_retryable=_e_retryable,
            error_retry_after_s=_e_retry_after,
            duration_ms=int((time.time() - started_at) * 1000),
        )
    finally:
        # Release the @agentic_function runtime hook. Runs on success,
        # exception, AND inside the early-return above (finally fires
        # before return is actually executed). Guarded because attach
        # may have silently failed (no provider configured).
        try:
            if _runtime_token is not None:
                _current_runtime_var.reset(_runtime_token)
            if _store_token is not None:
                _store_var.reset(_store_token)
            if _turn_id_token is not None:
                _turn_id_var.reset(_turn_id_token)
            if _worktree_token is not None:
                try:
                    from openprogram.worktree.context import reset_worktree
                    reset_worktree(_worktree_token)
                except Exception:
                    pass
        except Exception:
            pass

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
    )

    # Mark session idle/done now that the turn completed successfully.
    try:
        if req.source in {"wechat", "telegram", "discord", "slack"}:
            db.update_session(req.session_id, status="done", unread=True)
        else:
            db.update_session(req.session_id, status="idle")
    except Exception:
        pass

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_loop_blocking(
    *,
    req: TurnRequest,
    history: list[dict],
    on_event: EventCallback,
    cancel_event: Optional[threading.Event],
    stream_fn=None,
    assistant_msg_id: Optional[str] = None,
    agentic_tool_names_out: Optional[set[str]] = None,
    ordered_blocks_out: Optional[list[dict]] = None,
) -> tuple[str, dict, list[dict]]:
    """Build AgentContext, kick off agent_loop, drain its EventStream.

    Returns (final_text, usage, tool_calls).

    `ordered_blocks_out`, if provided, is mutated in place to hold the
    per-turn ordered block list (``[{"type":"thinking"|"text"|"tool",
    ...}, ...]``) reconstructed from the final AssistantMessage's
    content. Used by the webui to render LLM text / thinking / tool
    cards in the order they appeared, instead of stacking all tools
    at the bottom of the bubble.

    Runs synchronously inside a fresh asyncio loop so callers don't
    need to be async. Cancel via cancel_event flips an asyncio.Event
    inside the loop.

    `stream_fn` is the seam tests use to inject a fake provider —
    see tests/unit/test_dispatcher_integration.py. None means use
    the default (real provider via stream_simple).
    """
    from openprogram.agent.agent_loop import agent_loop, agent_loop_continue
    from openprogram.agent.types import AgentContext, AgentLoopConfig

    # Resolve agent profile → tools, system_prompt, model.
    agent_profile = _load_agent_profile(req.agent_id)
    tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)
    # Plan mode: hide write/mutate tools when the session is currently
    # in plan mode. ``apply_tool_policy(source="plan", ...)`` filters
    # out every tool that lists "plan" in its ``unsafe_in`` set — see
    # the write tools (bash, write, edit, apply_patch, execute_code,
    # process). Applied AFTER channel filtering so both restrictions
    # compose: a wechat turn in plan mode hides the union of both
    # blacklists.
    if tools and _plan_mode.is_plan_mode(req.session_id):
        from openprogram.functions import apply_tool_policy as _apply_policy
        tools = _apply_policy(tools, source="plan")
    _log_resolved_tools(req, tools)
    if tools:
        tools = [_wrap_with_approval(t, req, on_event) for t in tools]
        # Route @agentic_function calls through the runtime-block
        # rendering path (same UX as the manual /run handler): persist
        # a display=runtime placeholder, set _call_id so the DAG
        # subtree anchors under it, finalize with the rebuilt exec DAG.
        if assistant_msg_id is not None:
            _wrapped: list = []
            for _t in tools:
                if getattr(_t, "_is_agentic", False):
                    if agentic_tool_names_out is not None:
                        agentic_tool_names_out.add(_t.name)
                    _wrapped.append(_wrap_agentic_runtime_block(
                        _t, req, on_event, assistant_msg_id,
                    ))
                else:
                    _wrapped.append(_t)
            tools = _wrapped
    # Layer 6 catalog text in the system prompt. We do NOT split
    # ``tools`` here — the agent_loop re-splits before every provider
    # call so newly-loaded deferred tools show up on the next turn
    # automatically. We only peek at the *initial* catalog so the LLM
    # sees the deferred names from turn 1. After tool_search loads
    # a name, that name still appears in this catalog block (the
    # prompt is fixed for the dispatcher turn), but its full schema
    # arrives via the agent_loop's per-call split — so the LLM has
    # both the listing for discoverability and the schema for use.
    deferred_catalog: list[tuple[str, str]] = []
    if tools:
        from openprogram.functions import (
            deferred_catalog_text,
            split_tools_for_dispatch,
        )
        _, deferred_catalog = split_tools_for_dispatch(tools)
    system_prompt = _with_tool_runtime_prompt(
        agent_profile.get("system_prompt") or "",
        tools,
    )
    if deferred_catalog:
        block = deferred_catalog_text(deferred_catalog)
        if block:
            system_prompt = (
                f"{system_prompt.rstrip()}\n\n{block}".strip()
            )
    # Plan-mode reminder. Text adapted from Anthropic's Claude Code
    # plan-mode attachment (``references/claude-code-leaked/src/utils/
    # messages.ts``) — the opening "Plan mode is active... supercedes
    # any other instructions" sentence is theirs, kept because it
    # phrases the override priority unambiguously.
    if _plan_mode.is_plan_mode(req.session_id):
        system_prompt = (
            f"{system_prompt.rstrip()}\n\n"
            "<plan-mode>\n"
            "Plan mode is active. The user indicated that they do not "
            "want you to execute yet — you MUST NOT make any edits, "
            "run any non-readonly tools (including changing configs, "
            "running shell commands, or making commits), or otherwise "
            "make any changes to the system. This supercedes any other "
            "instructions you have received.\n\n"
            "Workflow:\n"
            "1. Explore the codebase with read, glob, grep until you "
            "understand the existing structure.\n"
            "2. Draft a concrete implementation plan.\n"
            "3. Submit the plan via `exit_plan_mode(plan=...)` for "
            "user approval. Do NOT ask the user about the plan in "
            "free-form text — exit_plan_mode IS how you ask.\n\n"
            "If the user rejects the plan, revise it based on the "
            "rejection message and call exit_plan_mode again. Stay in "
            "plan mode until exit_plan_mode succeeds.\n"
            "</plan-mode>"
        ).strip()
    model = _resolve_model(agent_profile, req.model_override)

    # Route history through the context engine: applies tool-result
    # aging in-memory, computes an accurate token budget against the
    # model's real context window, surfaces whether auto-compact should
    # fire before we burn tokens on this turn.
    from openprogram.context import resolve_engine_for
    from openprogram.agent.session_db import default_db
    _ctx_engine = resolve_engine_for(agent_profile)
    _ctx_engine.on_session_start(req.session_id)
    db = default_db()
    session = db.get_session(req.session_id) or {}
    prep = _ctx_engine.prepare(
        agent=agent_profile,
        session=session,
        history=history,
        model=model,
        tools=tools,
    )

    # Snip: free operation — remove oldest turns before trying the
    # expensive LLM compact.  Runs only when auto-compact threshold
    # is crossed and history_override is not set.
    if req.history_override is None and _ctx_engine.should_auto_compact(prep):
        try:
            from openprogram.context.snip import snip
            from openprogram.context.tokens import count_tokens
            snipped, n_snipped = snip(
                prep.history_dicts,
                token_counter=lambda msgs: count_tokens(msgs, model),
                context_window=prep.context_window,
            )
            if n_snipped > 0:
                history = snipped
                prep = _ctx_engine.prepare(
                    agent=agent_profile,
                    session=db.get_session(req.session_id) or session,
                    history=history,
                    model=model,
                    tools=tools,
                )
                on_event({"type": "chat_response",
                          "data": {"type": "snip",
                                   "session_id": req.session_id,
                                   "turns_removed": n_snipped}})
        except Exception:
            pass

    # Tier 3 Context Collapse: segmented LLM summary.
    # Runs after Snip, before full Auto-Compact.
    if req.history_override is None and _ctx_engine.should_auto_compact(prep):
        try:
            from openprogram.context.collapse import collapse
            from openprogram.context.tokens import count_tokens

            def _llm_summarize(prompt: str) -> str:
                from openprogram.agentic_programming.runtime import Runtime
                rt = Runtime(call=req.call, model=model)
                return rt.exec(content=[{"type": "text", "text": prompt}]) or ""

            collapsed, _originals, n_collapsed = collapse(
                prep.history_dicts,
                llm_call=_llm_summarize,
                token_counter=lambda msgs: count_tokens(msgs, model),
                context_window=prep.context_window,
            )
            if n_collapsed > 0:
                history = collapsed
                prep = _ctx_engine.prepare(
                    agent=agent_profile,
                    session=db.get_session(req.session_id) or session,
                    history=history,
                    model=model,
                    tools=tools,
                )
                on_event({"type": "chat_response",
                          "data": {"type": "context_collapse",
                                   "session_id": req.session_id,
                                   "segments_collapsed": n_collapsed}})
        except Exception:
            pass

    # Tier 4 Auto-compact: when budget STILL crosses the threshold after
    # snip + collapse, run the full LLM summariser INLINE.
    if req.history_override is None and _ctx_engine.should_auto_compact(prep):
        try:
            loop = asyncio.new_event_loop()
            try:
                compact_res = loop.run_until_complete(
                    _ctx_engine.compact(
                        agent=agent_profile,
                        session_id=req.session_id,
                        model=model,
                        on_event=on_event,
                        user_initiated=False,
                    )
                )
            finally:
                loop.close()
            if compact_res.summary_id:
                # Re-load the post-compact branch so the LLM call sees
                # the shorter chain.
                history = db.get_branch(req.session_id) or history
                prep = _ctx_engine.prepare(
                    agent=agent_profile,
                    session=db.get_session(req.session_id) or session,
                    history=history,
                    model=model,
                    tools=tools,
                )
        except Exception as e:  # noqa: BLE001
            # Auto-compact must never crash the turn.
            on_event({"type": "chat_response",
                      "data": {"type": "compaction_failed",
                               "session_id": req.session_id,
                               "error": f"{type(e).__name__}: {e}",
                               "user_initiated": False}})

    context = AgentContext(
        system_prompt=system_prompt,
        messages=prep.agent_messages,
        tools=tools,
    )

    # _default_convert_to_llm filters out non-LLM messages (e.g. our
    # custom error / system entries) — agent.py already provides this.
    from openprogram.agent.agent import _default_convert_to_llm

    config = AgentLoopConfig(
        model=model,
        convert_to_llm=_default_convert_to_llm,
        # Pass session_id so providers that support it
        # (openai_codex/openai_responses/azure) set prompt_cache_key on
        # every request. Without it OpenAI prompt cache can only match
        # the anonymous static prefix (~ instructions), so longer
        # conversations sit at ~10-20% hit rate even though the message
        # tail is identical turn-to-turn.
        session_id=req.session_id,
        reasoning=reasoning_from_config(SessionRunConfig(
            thinking_effort=req.thinking_effort
            if req.thinking_effort is not None
            else agent_profile.get("thinking_effort"),
        )),
        # Per-turn speed tier → SimpleStreamOptions.service_tier →
        # provider request body. Per-turn value wins; else the agent
        # profile's stored default; else None (provider default).
        service_tier=(
            req.service_tier
            if req.service_tier is not None
            else agent_profile.get("service_tier")
        ),
    )

    # Async drain that forwards each AgentEvent → on_event envelope.
    async def _drain() -> tuple[str, dict, list[dict]]:
        loop_cancel = asyncio.Event()
        if cancel_event is not None:
            # Bridge thread-side cancel into asyncio. Capture the
            # running loop here (the watch thread can't call
            # ``get_event_loop`` — Python 3.12+ raises in non-main
            # threads with no loop set).
            asyncio_loop = asyncio.get_running_loop()

            def _watch():
                cancel_event.wait()
                asyncio_loop.call_soon_threadsafe(loop_cancel.set)
            threading.Thread(target=_watch, daemon=True).start()

        # Single code path: history (trimmed of the new user_msg)
        # plus UserMessage prompt added by agent_loop exactly once.
        # The old user_already_persisted branch used agent_loop_continue
        # with history that included the duplicated user_msg as both
        # the tail of context.messages AND the "current turn" prompt,
        # which broke OpenAI prompt cache because the prefix's last
        # item flipped between turns (user N's duplicate → user N's
        # assistant reply).
        from openprogram.providers.types import (
            ImageContent, TextContent, UserMessage,
        )
        content_blocks: list = []
        if req.user_text:
            content_blocks.append(TextContent(text=req.user_text))
        for att in (req.attachments or []):
            if not isinstance(att, dict):
                continue
            if att.get("type") == "image":
                try:
                    content_blocks.append(ImageContent(
                        data=att.get("data") or "",
                        mime_type=att.get("media_type") or "image/png",
                    ))
                except Exception:
                    # Malformed attachment — skip silently rather
                    # than aborting the whole turn.
                    pass
        if not content_blocks:
            content_blocks = [TextContent(text="")]
        prompt = UserMessage(
            content=content_blocks,
            timestamp=int(time.time() * 1000),
        )
        ev_stream = agent_loop([prompt], context, config,
                                loop_cancel, stream_fn)

        final_text_parts: list[str] = []
        usage_total: dict[str, int] = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        }
        tool_calls: list[dict] = []
        # Capture tool_use inputs so we can rebuild the same
        # collapsible scaffold on reload. tool_execution_end events
        # don't carry the input args, so we stash them at start time.
        tool_inputs_by_id: dict[str, dict] = {}

        async for ev in _aiter_event_stream(ev_stream):
            envelope = _agent_event_to_envelope(ev, req)
            if envelope is not None:
                on_event(envelope)
            # Side-effects we care about for the final result.
            # Approval is gated INSIDE the wrapped tool execute (see
            # _wrap_with_approval) — by the time tool_execution_start
            # fires, the user has already approved (or the wrapper
            # short-circuited with a denial result).
            if hasattr(ev, "type"):
                if ev.type == "tool_execution_start":
                    _tid = getattr(ev, "tool_call_id", None)
                    _args = getattr(ev, "args", None)
                    if _tid is not None:
                        tool_inputs_by_id[_tid] = {
                            "tool": getattr(ev, "tool_name", None),
                            "input": json.dumps(_args, default=str)
                                     if _args is not None else None,
                        }
                if ev.type == "tool_execution_end":
                    _tid = getattr(ev, "tool_call_id", None)
                    _meta = tool_inputs_by_id.get(_tid, {})
                    _tc = {
                        "id": _tid,
                        "tool_call_id": _tid,
                        "tool": getattr(ev, "tool_name", None) or _meta.get("tool"),
                        "input": _meta.get("input"),
                        "result": _shorten(getattr(ev, "result", "")),
                        "is_error": bool(getattr(ev, "is_error", False)),
                    }
                    tool_calls.append(_tc)
                if ev.type == "turn_end":
                    msg = getattr(ev, "message", None)
                    if getattr(msg, "stop_reason", None) == "error":
                        # Stream-level provider failure (HTTP 4xx/5xx
                        # surfaced as an error event, not an exception).
                        # Without this the turn "succeeds" with empty
                        # text — a blank assistant bubble. Re-raise as
                        # LLMError so the dispatcher's exception path
                        # renders the red error bubble with taxonomy,
                        # same as a synchronously-raised failure.
                        from openprogram.providers.utils.errors import (
                            ErrorReason, LLMError,
                        )
                        _reason_val = getattr(msg, "error_reason", None)
                        try:
                            _reason = (ErrorReason(_reason_val)
                                       if _reason_val else ErrorReason.UNKNOWN)
                        except ValueError:
                            _reason = ErrorReason.UNKNOWN
                        raise LLMError(
                            message=(getattr(msg, "error_message", "") or
                                     "provider returned an error"),
                            reason=_reason,
                            retryable=bool(
                                getattr(msg, "error_retryable", None) or False),
                            retry_after_s=getattr(
                                msg, "error_retry_after_s", None),
                            provider=getattr(msg, "provider", None),
                            model=getattr(msg, "model", None),
                        )
                    text = _extract_text(msg)
                    if text:
                        final_text_parts.append(text)
                    usage = _extract_usage(msg)
                    for k in ("input_tokens", "output_tokens",
                              "cache_read_tokens", "cache_write_tokens"):
                        usage_total[k] += usage.get(k, 0)
                    # Build ordered blocks from msg.content so the
                    # webui can render thinking / text / tool cards
                    # interleaved in their original LLM emission
                    # order. Without this the bubble shows all LLM
                    # text first and then every tool card stacked at
                    # the bottom — wrong when the LLM said something,
                    # called a tool, then kept narrating.
                    if ordered_blocks_out is not None and msg is not None:
                        try:
                            for blk in getattr(msg, "content", None) or []:
                                btype = getattr(blk, "type", None)
                                if btype == "text":
                                    _t = getattr(blk, "text", "") or ""
                                    if _t:
                                        ordered_blocks_out.append(
                                            {"type": "text", "text": _t}
                                        )
                                elif btype == "thinking":
                                    _t = getattr(blk, "thinking", "") or ""
                                    if _t:
                                        ordered_blocks_out.append(
                                            {"type": "thinking", "text": _t}
                                        )
                                elif btype == "toolCall":
                                    _tid = getattr(blk, "id", None)
                                    _name = getattr(blk, "name", None)
                                    _args = getattr(blk, "arguments", None)
                                    try:
                                        _input = (
                                            json.dumps(_args, default=str)
                                            if _args is not None else None
                                        )
                                    except Exception:
                                        _input = None
                                    ordered_blocks_out.append({
                                        "type": "tool",
                                        "tool": _name,
                                        "tool_call_id": _tid,
                                        "input": _input,
                                    })
                        except Exception:
                            pass

        return "".join(final_text_parts).strip(), usage_total, tool_calls

    # Run the async drain in a fresh loop (we're in a thread).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drain())
    finally:
        loop.close()


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
# the dispatcher body keeps reading as before.
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


