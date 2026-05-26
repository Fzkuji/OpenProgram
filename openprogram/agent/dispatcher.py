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
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Optional

from openprogram.agent.session_config import reasoning_from_config, SessionRunConfig


PermissionMode = Literal["ask", "auto", "bypass"]
EventCallback = Callable[[dict], None]

# Lifecycle helpers (placeholder insert / status flip / error fold) —
# see openprogram/agent/_turn_lifecycle.py for the per-turn DB
# protocol. Kept in a sibling module so this file doesn't grow past
# its already-1500-line mark.
from openprogram.agent._turn_lifecycle import (
    insert_placeholder as _insert_placeholder,
    mark_terminal_status as _mark_terminal_status,
    fold_error_into_placeholder as _fold_error_into_placeholder,
    write_standalone_error_node as _write_standalone_error_node,
)


# Sentinel: "caller did not specify parent_id, dispatcher should pick"
# vs explicit ``None`` which means "fork from root". The two cases need
# different behavior — see TurnRequest.parent_id.
class _InheritParent:
    __slots__ = ()
    def __repr__(self) -> str: return "<INHERIT>"


INHERIT_PARENT: Any = _InheritParent()


@dataclass
class TurnRequest:
    session_id: str
    user_text: str
    agent_id: str
    source: str                                  # "tui" / "web" / "wechat" / ...
    peer_display: Optional[str] = None
    peer_id: Optional[str] = None
    model_override: Optional[str] = None
    thinking_effort: Optional[str] = None
    permission_mode: PermissionMode = "ask"
    # Optional explicit tool whitelist that overrides the agent's
    # configured tools. Channels can opt out of risky tools per turn
    # (e.g. wechat shouldn't ever hit destructive bash).
    tools_override: Optional[list[str]] = None
    # Branching: parent_id of the user message we're about to write.
    #   - INHERIT_PARENT (default) → dispatcher uses the active
    #     branch's tail (head_id walk). Normal append.
    #   - explicit string → fork sibling branch off that message.
    #     Retry / edit flows pass the parent of the message being
    #     replaced.
    #   - explicit None → root-level fork (the very first turn of a
    #     new conversation tree, or "retry the very first user
    #     message" case from contextgit/dag.py).
    # Mirrors Claude Code's parentUuid chain: append-only, no mutation
    # of historical messages.
    parent_id: Any = INHERIT_PARENT
    # When the caller has already linearized "the branch the user
    # currently sees" (e.g. webui has its in-memory active-branch
    # walk), pass it here so the dispatcher uses it as the LLM
    # context instead of re-querying SessionDB. Each entry is a row-
    # shaped dict with role/content/timestamp/id at minimum. Passing
    # None means "load history from SessionDB via get_branch".
    history_override: Optional[list[dict]] = None
    # Caller-supplied id for the user message. When omitted dispatcher
    # mints one. Useful for webui where the WS handler pre-emits a
    # ``chat_ack`` envelope tied to a frontend-known msg_id.
    user_msg_id: Optional[str] = None
    # When True, the caller has already persisted the user message
    # under ``user_msg_id`` and advanced head — dispatcher should
    # NOT re-write it. Used by webui where the WS handler appends
    # the user msg before kicking off the agent thread.
    user_already_persisted: bool = False
    # Multimodal attachments to include in the user message. Each
    # entry is ``{"type": "image", "data": <base64>, "media_type":
    # "image/png"}`` (or jpeg/webp/gif). The dispatcher attaches
    # these as ImageContent blocks alongside the text TextContent.
    # Providers that don't support vision will reject; the dispatcher
    # surfaces that as an error envelope, not a crash.
    attachments: Optional[list[dict]] = None


@dataclass
class TurnResult:
    final_text: str
    user_msg_id: str
    assistant_msg_id: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0
    failed: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Approval registry — used by the "ask" permission flow
# ---------------------------------------------------------------------------

# Approval gate lives in _approval.py — re-exported here so callers
# (tests, server.py WS handler) can keep using
# ``dispatcher.approval_registry()`` / ``dispatcher.ApprovalRegistry``.
from openprogram.agent import plan_mode as _plan_mode
from openprogram.agent._approval import (
    ApprovalRegistry,
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
    elif isinstance(req.parent_id, _InheritParent):
        # Normal append — walk the active branch.
        history = db.get_branch(req.session_id) or db.get_messages(req.session_id)
    elif req.parent_id is None:
        # Root-level fork — LLM starts with empty history.
        history = []
    else:
        # Sibling fork — history is the branch ending at the explicit
        # parent. LLM sees what existed up to the fork point, not
        # what's currently on the active branch.
        history = db.get_branch(req.session_id, req.parent_id)

    # 2. Persist user message immediately (so a crash mid-stream still
    #    leaves the user's input recorded). Resolve parent_id:
    #      INHERIT_PARENT → tail of active branch, or NULL if empty
    #      explicit None  → NULL (root-level fork)
    #      explicit str   → that string (sibling fork)
    if isinstance(req.parent_id, _InheritParent):
        if history:
            user_parent_id = history[-1].get("id")
        else:
            user_parent_id = session.get("head_id")
    else:
        user_parent_id = req.parent_id
    user_msg: dict[str, Any] = {
        "id": user_msg_id,
        "role": "user",
        "content": req.user_text,
        "timestamp": time.time(),
        "parent_id": user_parent_id,
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
    if not req.user_already_persisted:
        db.append_message(req.session_id, user_msg)
        # Advance head to the user message. Crucial for branching: if
        # the caller passed parent_id pointing at an older message,
        # we're now on a NEW leaf and head must reflect that —
        # otherwise the next get_branch call would still walk down
        # the old branch.
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
                "parent_id": user_msg.get("parent_id"),
            },
        })
    else:
        # Caller already wrote the user msg + emitted ack (webui
        # path). Make sure history reflects that — load from DB if
        # the caller didn't pass a history_override.
        if req.history_override is None:
            history = db.get_branch(req.session_id) or history

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
    # the right assistant message via file_backup.helpers.
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
    #     hang off ``parent_id = assistant_msg_id`` — and lets a mid-
    #     turn page refresh actually find them via the parent
    #     aggregation in webui/persistence._aggregate_tool_messages.
    #     We update this row's content + tool_calls/blocks at turn
    #     end (step 5) once the LLM's final text is known.
    _placeholder_inserted = _insert_placeholder(
        db, req.session_id, assistant_msg_id, user_msg_id, req.source,
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
                    try:
                        from openprogram.agent.session_db import (
                            default_db as _db,
                        )
                        _db().append_message(req.session_id, {
                            "id": f"{assistant_msg_id}_t_{tid}",
                            "role": "tool",
                            "content": str(evt.get("result") or ""),
                            "function": meta.get("tool") or evt.get("tool") or "",
                            "parent_id": assistant_msg_id,
                            "timestamp": time.time(),
                            "is_error": bool(evt.get("is_error")),
                            "extra": json.dumps({
                                "tool_use": {
                                    "name": meta.get("tool")
                                            or evt.get("tool") or "",
                                    "arguments": meta.get("input") or "",
                                    "called_by": assistant_msg_id,
                                },
                            }, default=str),
                        })
                    except Exception:
                        pass  # canonical write in step 5 covers it
            except Exception:
                pass

        final_text, usage, tool_calls = _run_loop_blocking(
            req=req,
            history=loop_history,
            on_event=_on_event_persist,
            cancel_event=cancel_event,
        )
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
            db.update_session(req.session_id, head_id=head_for_next)
        except Exception:
            pass
        on_event({"type": "chat_response",
                  "data": {"type": "error", "session_id": req.session_id,
                           "content": err_text}})
        return TurnResult(
            final_text="",
            user_msg_id=user_msg_id,
            assistant_msg_id=(
                assistant_msg_id if _placeholder_inserted else ""),
            failed=True,
            error=str(e),
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

    # 5. Persist assistant message.
    # Attach usage + model so session_db.append_message stamps real
    # provider numbers (input/output/cache_read/cache_write) into the
    # messages.* token columns. If provider didn't report usage, leave
    # the columns NULL — we never fabricate counts.
    model_str = req.model_override or session.get("model") or ""
    if isinstance(model_str, dict):
        model_id = model_str.get("id") or model_str.get("model")
        provider_id = model_str.get("provider")
    elif isinstance(model_str, str) and ("/" in model_str or ":" in model_str):
        sep = "/" if "/" in model_str else ":"
        provider_id, model_id = model_str.split(sep, 1)
    else:
        model_id = model_str or None
        provider_id = None
    has_usage = bool(usage.get("input_tokens") or usage.get("output_tokens"))
    # Fallback for Anthropic-family models when the upstream proxy
    # (meridian / claude-max-api-proxy) doesn't forward usage chunks. Hit
    # Anthropic's
    # /v1/messages/count_tokens — it's a real, authoritative count for the
    # full message list we just sent, and it's free.
    token_source = "provider_usage"
    if not has_usage and _is_anthropic_family(model_id, provider_id):
        try:
            from openprogram.providers._shared.anthropic_token_count import (
                count_tokens_via_anthropic,
            )
            counted = count_tokens_via_anthropic(
                history + [{"role": "user", "content": req.user_text},
                           {"role": "assistant", "content": final_text}],
                model_id or "claude-sonnet-4-5",
            )
            if counted and counted.get("input_tokens"):
                usage = {
                    "input_tokens": int(counted["input_tokens"]),
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                }
                has_usage = True
                token_source = "anthropic_count_api"
        except Exception:
            pass
    assistant_msg = {
        "id": assistant_msg_id,
        "role": "assistant",
        "content": final_text,
        "timestamp": time.time(),
        "parent_id": user_msg_id,
        "source": req.source,
        "model": model_id,
        "provider": provider_id,
        # Which agent produced this reply — same field as the matching
        # user_msg above. Lets the UI colour / label both halves of
        # the turn consistently when multiple peer agents live in the
        # same session.
        "agent_id": req.agent_id,
    }
    # Stamp terminal lifecycle status — see _turn_lifecycle for the
    # state machine. ``cancel_event.is_set()`` here means the user
    # clicked stop mid-stream and the agent loop returned early with
    # partial output → record as "cancelled", not "completed".
    _mark_terminal_status(
        assistant_msg,
        cancelled=bool(cancel_event and cancel_event.is_set()),
    )
    if has_usage:
        assistant_msg.update({
            "input_tokens":  int(usage.get("input_tokens")  or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens":  int(usage.get("cache_read_tokens")  or 0),
            "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
            "token_source": token_source,
            "token_model":  model_id,
        })
    if tool_calls:
        # Persist BOTH shapes:
        #   * tool_calls — legacy slim list (id/tool/result/is_error)
        #     still consumed by older code paths.
        #   * blocks — the structured form _renderAssistantBlocks /
        #     _buildAssistantMessage expect, so the webui can rebuild
        #     the same collapsible scaffold after refresh instead of
        #     showing a plain text reply with no tool history.
        blocks = [
            {
                "type": "tool",
                "tool": t.get("tool"),
                "tool_call_id": t.get("tool_call_id") or t.get("id"),
                "input": t.get("input"),
                "result": t.get("result"),
                "is_error": t.get("is_error"),
            }
            for t in tool_calls
        ]
        assistant_msg["extra"] = json.dumps(
            {"tool_calls": tool_calls, "blocks": blocks},
            default=str,
        )
    if _placeholder_inserted:
        # Update the placeholder row (step 3b) in place — same id,
        # now with final content + tool_calls/blocks.
        try:
            from openprogram.store._msg_adapter import _msg_to_node as _to_node
            from openprogram.store import GraphStoreShim
            _shim = GraphStoreShim(db, req.session_id)
            _node = _to_node(assistant_msg)
            _shim.update(
                assistant_msg["id"],
                output=_node.output,
                metadata=_node.metadata,
            )
        except Exception:
            db.append_message(req.session_id, assistant_msg)
    else:
        db.append_message(req.session_id, assistant_msg)

    # 6. Update session bookkeeping (head_id, token tracking, model).
    db.update_session(
        req.session_id,
        head_id=assistant_msg_id,
        last_prompt_tokens=int(usage.get("input_tokens") or 0),
        model=req.model_override or session.get("model"),
    )

    # 6.1. Backfill the latest context commit's placeholder item with the
    # final assistant output. The turn-start context commit saw the assistant
    # row as a placeholder (output=""), so the Context panel would
    # otherwise show "(empty)" for every assistant turn. We patch the
    # already-saved context commit in place — keeps the per-turn commit_id
    # stable and avoids ballooning the timeline with a duplicate.
    try:
        from openprogram.context.commit.store import (
            load_commit_for_head,
            save_commit,
        )
        from openprogram.context.commit.types import ContextItem
        _final_text = assistant_msg.get("content") or ""
        # Look up the commit on THIS branch (load_commit_for_head walks
        # the DAG ancestry from assistant_msg_id); the legacy
        # load_latest_commit returns whichever commit was saved last
        # session-wide, which is wrong when N agents are running
        # concurrently on different branches.
        _commit = load_commit_for_head(db, req.session_id, assistant_msg_id)
        if _commit is not None:
            _patched = False
            _assistant_idx = -1
            for _i, _item in enumerate(_commit.items):
                if _item.source_node_id == assistant_msg_id:
                    if _final_text and _item.rendered != _final_text:
                        _item.rendered = _final_text
                        # tokens were estimated from "" at turn-start;
                        # recompute against the final text.
                        _item.tokens = max(4, len(_final_text) // 4)
                    _assistant_idx = _i
                    _patched = True
                    break
            # Also splice in tool sub-calls written during the LLM loop
            # (called_by=assistant_msg_id). ensure_latest_commit ran at
            # turn-start before any tool node existed, so the context commit
            # has no tool items — the Context panel was showing a fake
            # "user → assistant" pair instead of the real "user →
            # assistant_with_tool_calls → tool_result(s)" sequence.
            if _assistant_idx >= 0:
                _all = db.get_messages(req.session_id) or []
                _subs = [m for m in _all if (m.get("caller") or "") == assistant_msg_id]
                _subs.sort(key=lambda x: x.get("seq") or 0)
                _existing_ids = {it.source_node_id for it in _commit.items}
                _to_insert: list[ContextItem] = []
                for _sub in _subs:
                    _sid = _sub.get("id")
                    if not _sid or _sid in _existing_ids:
                        continue
                    _content = _sub.get("content") or ""
                    if not isinstance(_content, str):
                        import json as _json
                        try:
                            _content = _json.dumps(_content, ensure_ascii=False, default=str)
                        except Exception:
                            _content = str(_content)
                    _to_insert.append(ContextItem(
                        source_node_id=_sid,
                        role="tool",
                        state="full",
                        locked=False,
                        rendered=_content,
                        tokens=max(4, len(_content) // 4),
                        state_set_at=_commit.id,
                        reason="new",
                    ))
                if _to_insert:
                    _commit.items = (
                        _commit.items[: _assistant_idx + 1]
                        + _to_insert
                        + _commit.items[_assistant_idx + 1 :]
                    )
                    _commit.total_tokens = sum(
                        i.tokens for i in _commit.items if i.state != "summarized"
                    )
                    _patched = True
            if _patched:
                save_commit(db, _commit)
    except Exception:
        # ContextCommit backfill is best-effort: the conversation persists
        # regardless, and the next turn will rebuild the chain.
        pass

    # 6.4. Feed real provider usage back into the context engine so
    # subsequent prepare() calls budget against true numbers instead of
    # our estimate. We re-resolve the engine here (cheap registry
    # lookup) because _run_loop_blocking's local _ctx_engine is out of
    # scope — and pass a lightweight prep-equivalent so the engine can
    # still decide whether to emit a recommendation event.
    try:
        from openprogram.context import resolve_engine_for as _resolve_eng
        from openprogram.context.types import (
            BudgetAllocation as _BA, TurnPrep as _TurnPrep,
        )
        from openprogram.context.tokens import real_context_window as _rcw
        _profile = _load_agent_profile(req.agent_id)
        _engine = _resolve_eng(_profile)
        _ctx_win = _rcw(_resolve_model(_profile, req.model_override))
        _shim_prep = _TurnPrep(
            system_prompt="",
            budget=_BA(context_window=_ctx_win),
        )
        _engine.after_turn(
            req.session_id,
            usage=usage,
            prep=_shim_prep,
            on_event=on_event,
        )
    except Exception:
        pass

    # 6.5. Auto-title: if the session is still using the placeholder
    # title (or hasn't been titled by an explicit user action), set a
    # readable label from the user's first message. Cheap version —
    # just take the first 50 chars; LLM-summarized titles are a
    # future upgrade. Fires once per session (idempotent via
    # extra_meta._titled flag).
    _maybe_auto_title(db, req.session_id, session, req.user_text)

    # 6.6. Compaction signal: when context is approaching the model's
    # window, surface a "compaction_recommended" event so the UI can
    # offer the user a /compact action. We don't auto-compact mid-
    # turn — that would block the response. The actual compaction
    # call is exposed as ``trigger_compaction(session_id)`` for clients
    # to invoke explicitly.
    #
    # Context-window resolution via context.tokens — reads
    # ``model.context_window`` (the truth), not ``model.max_tokens``
    # (which is the OUTPUT cap, typically 10-30% of the real window
    # and would fire compaction at ~10-30% utilization).
    # (Compaction-recommended emission moved into ctx_engine.after_turn,
    # which uses provider-reported usage instead of re-estimating the
    # whole branch here.)

    # 6.8. Git commit the turn — the session's git repo is the source
    # of truth (git-as-truth). Every successful turn becomes one
    # commit on the session's branch, picking up new history files +
    # rewritten context/messages.json + context/commit.json + meta.json
    # in a single diff. Best-effort: if git fails the data is still
    # on disk, next turn's commit will sweep it up.
    try:
        from openprogram.store import default_store
        _store = default_store()
        if _store is db or hasattr(db, "commit_turn"):
            _msg = (req.user_text or "").strip().splitlines()[0][:60] or "turn"
            db.commit_turn(req.session_id, f"turn: {_msg}")
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
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _noop(_: dict) -> None:
    pass


def _default_title(req: TurnRequest) -> str:
    text = req.user_text.strip().splitlines()[0] if req.user_text else ""
    return text[:50] + ("…" if len(text) > 50 else "") or "New chat"


def _maybe_auto_title(db, session_id: str, session: dict,
                      user_text: str) -> None:
    """Stamp a readable session title once, on the first turn that
    has a non-empty user message. Idempotent — once
    ``extra_meta._titled`` is True we never touch the title again so
    user-set titles via /rename win.

    Skips when:
      - The session was never created (missing row)
      - User already explicitly titled it
      - This wasn't a real text turn (e.g. tool-only follow-up)
    """
    extra = (session.get("extra_meta") or {})
    if extra.get("_titled"):
        return
    stripped = (user_text or "").strip()
    if not stripped:
        return
    text = stripped.splitlines()[0] if stripped.splitlines() else stripped
    if not text:
        return
    title = text[:50] + ("…" if len(text) > 50 else "")
    try:
        db.update_session(session_id, title=title, _titled=True)
    except Exception:
        pass


def trigger_compaction(session_id: str, agent_id: str = "main",
                        on_event: Optional[EventCallback] = None,
                        *,
                        keep_recent_tokens: Optional[int] = None) -> dict:
    """User-initiated compaction. Synchronous — the caller is responsible
    for running this off the request thread if it cares about latency
    (compaction calls the LLM to generate a summary).

    Pipeline:
      1. Load active branch from SessionDB.
      2. Run compact_context to get summary text + recent kept tail.
      3. Persist a synthetic ``compactionSummary`` row chained off
         the current head's parent (so it sits at the same fork
         point as the original first kept message).
      4. set_head to the new summary row.
      5. Re-link the kept tail: each kept message gets a new id and
         parent_id pointing back through the new chain.

    Mirrors Claude Code's compaction model (a real "summary" message
    in the transcript) but stays SQL-native — no JSONL fork needed.
    Old pre-summary messages remain in SessionDB but are off the
    active branch (you can still get_descendants from them for
    audit).

    Returns ``{"summary": str, "kept_count": int, "summary_id": str}``.
    """
    on_event = on_event or _noop
    from openprogram.agent.session_db import default_db
    from openprogram.context import resolve_engine_for

    db = default_db()
    sess = db.get_session(session_id)
    if sess is None:
        raise ValueError(f"Unknown conversation {session_id!r}")
    history = db.get_branch(session_id) or []
    if len(history) < 4:
        return {"summary": "", "kept_count": len(history), "summary_id": ""}

    profile = _load_agent_profile(agent_id)
    model = _resolve_model(profile, None)
    engine = resolve_engine_for(profile)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            engine.compact(
                agent=profile,
                session_id=session_id,
                model=model,
                on_event=on_event,
                user_initiated=True,
                keep_recent_tokens=keep_recent_tokens,
            )
        )
    finally:
        loop.close()

    return {
        "summary": result.summary_text or "",
        "kept_count": result.summarised_count,
        "summary_id": result.summary_id or "",
    }

def _run_loop_blocking(
    *,
    req: TurnRequest,
    history: list[dict],
    on_event: EventCallback,
    cancel_event: Optional[threading.Event],
    stream_fn=None,
) -> tuple[str, dict, list[dict]]:
    """Build AgentContext, kick off agent_loop, drain its EventStream.

    Returns (final_text, usage, tool_calls).

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

    # Auto-compact: when budget crosses the engine's threshold, run the
    # LLM summariser INLINE so the request that follows fits the window.
    # Manual /compact still works (see ``trigger_compaction`` below) —
    # the threshold here only catches the "agent loop overflows mid-
    # turn" case. We disable auto-compact when the caller passed a
    # history_override (retry / branch flows) because that history is
    # often a curated subset we shouldn't second-guess.
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
                    text = _extract_text(msg)
                    if text:
                        final_text_parts.append(text)
                    usage = _extract_usage(msg)
                    for k in ("input_tokens", "output_tokens",
                              "cache_read_tokens", "cache_write_tokens"):
                        usage_total[k] += usage.get(k, 0)

        return "".join(final_text_parts).strip(), usage_total, tool_calls

    # Run the async drain in a fresh loop (we're in a thread).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drain())
    finally:
        loop.close()


# Event/usage parsing helpers live in _event_parsing.py.
from openprogram.agent._event_parsing import (
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
from openprogram.agent._model_tools import (
    load_agent_profile as _load_agent_profile,
    is_anthropic_family as _is_anthropic_family,
    resolve_model as _resolve_model,
    with_tool_runtime_prompt as _with_tool_runtime_prompt,
    log_resolved_tools as _log_resolved_tools,
    resolve_tools as _resolve_tools,
    history_to_agent_messages as _history_to_agent_messages,
)


