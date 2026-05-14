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

class ApprovalRegistry:
    """Process-wide registry of pending tool-approval requests.

    Dispatcher posts an ``approval_request`` event with a request_id;
    the WS handler resolves the matching future when an
    ``approval_response`` action arrives. Times out at 5min so a
    forgotten approval doesn't pin a worker thread forever.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        self._answer: dict[str, bool] = {}

    def register(self, request_id: str) -> threading.Event:
        ev = threading.Event()
        with self._lock:
            self._pending[request_id] = ev
        return ev

    def resolve(self, request_id: str, approved: bool) -> bool:
        """Return True if the request_id was waiting; False otherwise."""
        with self._lock:
            ev = self._pending.pop(request_id, None)
            if ev is None:
                return False
            self._answer[request_id] = approved
        ev.set()
        return True

    def consume(self, request_id: str) -> Optional[bool]:
        """Read the resolution after the wait completes. Pops the slot."""
        with self._lock:
            return self._answer.pop(request_id, None)


_approvals = ApprovalRegistry()


def approval_registry() -> ApprovalRegistry:
    return _approvals


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
    assistant_msg_id = user_msg_id + "_a"

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
    }
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
    from openprogram.context.storage import GraphStore as _GraphStore
    from openprogram.agentic_programming.function import (
        _current_runtime as _current_runtime_var,
    )
    _dag_runtime = None
    _runtime_token = None
    try:
        from openprogram.providers.registry import create_runtime as _create_rt
        _dag_runtime = _create_rt()
        _dag_runtime.attach_store(
            _GraphStore(db.db_path, req.session_id),
            head_id=user_msg_id,
        )
        _runtime_token = _current_runtime_var.set(_dag_runtime)
    except Exception:
        # No provider configured / runtime construction blew up.
        # Skip the attach; @agentic_function will still work, just
        # without its nodes landing in the DAG.
        _dag_runtime = None
        _runtime_token = None

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
        final_text, usage, tool_calls = _run_loop_blocking(
            req=req,
            history=loop_history,
            on_event=on_event,
            cancel_event=cancel_event,
        )
    except Exception as e:
        err_text = f"[error] {type(e).__name__}: {e}"
        # Persist error as a system message — visible in resume + indexed in FTS
        err_id = uuid.uuid4().hex[:12]
        db.append_message(req.session_id, {
            "id": err_id,
            "role": "system",
            "content": err_text,
            "timestamp": time.time(),
            "parent_id": user_msg_id,
            "source": req.source,
            "extra": json.dumps({"trace": traceback.format_exc()[:2000]}),
        })
        on_event({"type": "chat_response",
                  "data": {"type": "error", "session_id": req.session_id,
                           "content": err_text}})
        return TurnResult(
            final_text="",
            user_msg_id=user_msg_id,
            assistant_msg_id="",
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
            if _dag_runtime is not None:
                _dag_runtime.detach_store()
            if _runtime_token is not None:
                _current_runtime_var.reset(_runtime_token)
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
    # Fallback for Anthropic-family models when the upstream proxy (e.g.
    # claude-max-api-proxy) doesn't forward usage chunks. Hit Anthropic's
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
    }
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
    db.append_message(req.session_id, assistant_msg)

    # 6. Update session bookkeeping (head_id, token tracking, model).
    db.update_session(
        req.session_id,
        head_id=assistant_msg_id,
        last_prompt_tokens=int(usage.get("input_tokens") or 0),
        model=req.model_override or session.get("model"),
    )

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
    _log_resolved_tools(req, tools)
    if tools:
        tools = [_wrap_with_approval(t, req, on_event) for t in tools]
    system_prompt = _with_tool_runtime_prompt(
        agent_profile.get("system_prompt") or "",
        tools,
    )
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
                    tool_calls.append({
                        "id": _tid,
                        "tool_call_id": _tid,
                        "tool": getattr(ev, "tool_name", None) or _meta.get("tool"),
                        "input": _meta.get("input"),
                        "result": _shorten(getattr(ev, "result", "")),
                        "is_error": bool(getattr(ev, "is_error", False)),
                    })
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


def _wrap_with_approval(
    agent_tool,
    req: TurnRequest,
    on_event: EventCallback,
):
    """Return a copy of ``agent_tool`` whose ``execute`` first checks
    approval, awaiting (not blocking) the user's response. Falls back
    to the original tool when permission_mode is "bypass" or the
    tool's per-tool gate decides no approval is needed.

    Why a wrapper layer (vs. inspecting tool_execution_start in the
    drain): agent_loop schedules ``await tool.execute(...)`` directly
    after pushing tool_execution_start. The dispatcher's async-for
    consumer can't reliably block the tool from running because the
    tool already runs as a thread-pool task in parallel. Gating
    inside the tool's own coroutine is the only safe seam.
    """
    from openprogram.agent.types import AgentTool, AgentToolResult
    from openprogram.providers.types import TextContent
    from openprogram.tools._runtime import tool_requires_approval

    orig_execute = agent_tool.execute

    async def _gated_execute(call_id, args, cancel, on_update):
        if req.permission_mode == "bypass":
            return await orig_execute(call_id, args, cancel, on_update)

        per_tool_required, _per_tool_reason = tool_requires_approval(agent_tool, args)
        if req.permission_mode == "auto":
            risky_default = agent_tool.name in {"bash", "exec", "shell",
                                                  "execute_code", "process"}
            if not per_tool_required and not risky_default:
                return await orig_execute(call_id, args, cancel, on_update)

        # "ask" mode (or auto-mode hitting a risky tool): post the
        # approval envelope and await resolution off the event loop.
        approved = await _await_user_approval(
            req=req,
            tool_name=agent_tool.name,
            args=args,
            on_event=on_event,
        )
        if not approved:
            return AgentToolResult(
                content=[TextContent(text=f"[denied] user did not approve {agent_tool.name}")],
                details={"is_error": True, "denied": True},
            )
        return await orig_execute(call_id, args, cancel, on_update)

    return AgentTool(
        name=agent_tool.name,
        description=agent_tool.description,
        parameters=agent_tool.parameters,
        label=getattr(agent_tool, "label", agent_tool.name) or agent_tool.name,
        execute=_gated_execute,
    )


async def _await_user_approval(
    *,
    req: TurnRequest,
    tool_name: str,
    args: dict,
    on_event: EventCallback,
    timeout: float = 300.0,
) -> bool:
    """Post an approval_request envelope, await the user's response.

    Uses ``asyncio.to_thread`` to wait on the threading.Event so the
    asyncio loop stays free to process other events (e.g. tool
    progress updates from concurrent tools).
    """
    request_id = uuid.uuid4().hex[:12]
    waiter = _approvals.register(request_id)
    on_event({
        "type": "approval_request",
        "data": {
            "request_id": request_id,
            "session_id": req.session_id,
            "tool": tool_name,
            "args": args,
        },
    })
    fired = await asyncio.to_thread(waiter.wait, timeout)
    if not fired:
        return False
    return bool(_approvals.consume(request_id))


def _agent_event_to_envelope(ev, req: TurnRequest) -> Optional[dict]:
    """Convert an AgentEvent → chat_response envelope (the same shape
    the legacy webui chat path emitted), so TUI/web handlers work
    unchanged."""
    t = getattr(ev, "type", None)

    # Conv-id tag attached to every envelope so multi-conv consumers
    # (TUI watching a different conv, browser sidebar, ...) can route
    # the stream to the right buffer instead of treating every delta
    # as belonging to whatever they're currently viewing.
    cid = req.session_id

    if t == "message_update":
        ame = getattr(ev, "assistant_message_event", None)
        if ame is None:
            return None
        ame_type = getattr(ame, "type", None)
        # Provider events use snake_case (text_delta, thinking_delta).
        if ame_type == "text_delta":
            return {
                "type": "chat_response",
                "data": {"type": "stream_event",
                         "session_id": cid,
                         "event": {"type": "text",
                                   "text": getattr(ame, "delta", "")}},
            }
        return None

    if t == "tool_execution_start":
        args = getattr(ev, "args", None)
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "session_id": cid,
                     "event": {"type": "tool_use",
                               "tool": getattr(ev, "tool_name", "?"),
                               "input": json.dumps(args, default=str)
                                        if args is not None else None,
                               "tool_call_id": getattr(ev, "tool_call_id", None)}},
        }

    if t == "tool_execution_end":
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "session_id": cid,
                     "event": {"type": "tool_result",
                               "tool": getattr(ev, "tool_name", "?"),
                               "result": _shorten(getattr(ev, "result", "")),
                               "is_error": bool(getattr(ev, "is_error", False)),
                               "tool_call_id": getattr(ev, "tool_call_id", None)}},
        }

    return None


async def _aiter_event_stream(ev_stream) -> "asyncio.AsyncIterator":
    """Iterate an EventStream as an async generator.

    EventStream from agent_loop has `__aiter__` already; this wrapper
    is a seam tests can monkey-patch with a list of events.
    """
    async for ev in ev_stream:
        yield ev


def _extract_text(msg) -> str:
    """Pull plain text out of an AssistantMessage's content list."""
    if msg is None:
        return ""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for c in content:
        ctype = getattr(c, "type", None)
        if ctype == "text":
            parts.append(getattr(c, "text", "") or "")
    return "".join(parts)


def _extract_usage(msg) -> dict:
    """Pull a usage dict from a final assistant message.

    Handles three shapes providers actually emit:
      * pydantic Usage object: attribute access
      * plain dict (gemini_cli, claude-max proxy, etc.): subscript
      * AssistantMessage with .usage attr that's itself a dict OR object

    Field name aliases too — Anthropic uses input/output, others
    input_tokens/output_tokens, cache_read vs cache_read_tokens, etc.
    """
    if msg is None:
        return {}
    # msg can be a dict (gemini-cli pushes plain dict as "message") or
    # an object with .usage attribute.
    usage = None
    if isinstance(msg, dict):
        usage = msg.get("usage")
    else:
        usage = getattr(msg, "usage", None)
        if usage is not None and hasattr(usage, "model_dump"):
            usage = usage.model_dump()
    if usage is None:
        return {}

    def _g(*names):
        for n in names:
            if isinstance(usage, dict):
                v = usage.get(n)
            else:
                v = getattr(usage, n, None)
            if v:
                return int(v)
        return 0
    input_tokens = _g("input_tokens", "input", "prompt_tokens")
    output_tokens = _g("output_tokens", "output", "completion_tokens")
    cache_read = _g("cache_read_tokens", "cache_read", "cached_tokens")
    cache_write = _g("cache_write_tokens", "cache_write", "cache_creation_input_tokens")
    # OpenAI semantics: prompt_tokens INCLUDES cached_tokens. Anthropic
    # semantics: input_tokens EXCLUDES cache_read_input_tokens. Normalize
    # to Anthropic shape (input = fresh only) so the downstream
    # cache_hit_rate = cache_read / (input + cache_read) formula is
    # correct for both providers.
    def _has(*names):
        for n in names:
            if isinstance(usage, dict):
                if usage.get(n):
                    return True
            elif getattr(usage, n, None):
                return True
        return False
    if _has("prompt_tokens") and cache_read and input_tokens >= cache_read:
        input_tokens -= cache_read
    return {
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens":  cache_read,
        "cache_write_tokens": cache_write,
    }


def _shorten(value, limit: int = 4000) -> str:
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(s) <= limit:
        return s
    return s[:limit] + f"... (+{len(s) - limit} more)"


# ---------------------------------------------------------------------------
# Agent profile + tools
# ---------------------------------------------------------------------------

def _load_agent_profile(agent_id: str) -> dict:
    """Load agent.json. Returns at least {"id": agent_id} so callers
    don't have to null-guard."""
    try:
        from openprogram.agents import manager as _A
        agent = _A.get(agent_id) if hasattr(_A, "get") else None
        if agent and hasattr(agent, "to_dict"):
            return agent.to_dict()
        if agent and hasattr(agent, "__dict__"):
            return dict(agent.__dict__)
    except Exception:
        pass
    return {"id": agent_id}


def _is_anthropic_family(model_id: Optional[str], provider_id: Optional[str]) -> bool:
    """True if this message should be counted by Anthropic's count_tokens.

    Covers direct ``anthropic`` provider, the ``claude-max`` /
    ``claude-code`` proxy paths, and any model id starting with
    ``claude-``.
    """
    if provider_id in ("anthropic", "claude-code", "claude-max"):
        return True
    if model_id and model_id.lower().startswith("claude"):
        return True
    return False


def _resolve_model(profile: dict, override: Optional[str] = None):
    """Resolve a Model instance from the agent profile or per-turn override.

    Falls back to a stub Model if the profile's identifier doesn't
    map to anything in the registry — keeps tests / orphaned agents
    from blowing up at construction time. The actual provider call
    will fail later if the stub doesn't have a real backend, but the
    failure surface is then `[error] ProviderNotFound: ...` which the
    dispatcher persists as a system message — recoverable, not a
    crash.
    """
    from openprogram.providers.types import Model
    try:
        from openprogram.providers.models import get_model
    except Exception:
        get_model = None  # type: ignore[assignment]

    requested = override or profile.get("model")
    # agent.json stores ``model`` either as the legacy ``"<provider>/<id>"``
    # string or as the newer ``{"provider": ..., "id": ...}`` dict
    # (cli_chat.py and setup.py both write the dict form). Normalize
    # to a single string shape here so the rest of this function — and
    # the eventual ``Model(id=requested, ...)`` fallback — only ever
    # sees a str. Without this, a dict reached the pydantic ctor and
    # blew up with "Input should be a valid string" the moment a
    # channel message arrived.
    provider_hint: Optional[str] = None
    if isinstance(requested, dict):
        provider_hint = requested.get("provider") or None
        model_id = requested.get("id") or requested.get("model") or None
        if provider_hint and model_id:
            requested = f"{provider_hint}/{model_id}"
        else:
            requested = model_id

    if get_model and requested:
        # Profile model can be "<provider>/<id>" or just "<id>".
        model_id_only: Optional[str] = None
        if "/" in requested:
            provider, model_id_only = requested.split("/", 1)
            m = get_model(provider, model_id_only)
            if m:
                return m
            # The legacy provider-prefix form may not match: e.g.
            # `claude-code/claude-sonnet-4-6` is a RUNTIME prefix
            # whose actual model row lives under `anthropic/`. Fall
            # through to the model-id probe below so we still find it.
            if provider_hint is None:
                provider_hint = provider
        else:
            model_id_only = requested

        # Probe known providers using just the model id. Biased toward
        # the dict's ``provider`` field if present so an entry like
        # ``{"provider": "openai-codex", "id": "gpt-5.5"}`` still
        # tries the right backend first.
        order = ["openai", "anthropic", "google", "amazon-bedrock",
                 "cerebras", "claude-code", "github-copilot",
                 "openai-codex", "openai-codex",
                 "gemini-subscription", "openrouter"]
        if provider_hint and provider_hint not in order:
            order.insert(0, provider_hint)
        for provider in order:
            m = get_model(provider, model_id_only)
            if m:
                return m

    # Fallback stub — agent_loop validates pydantic but doesn't dial
    # the provider until stream_fn fires; tests stub stream_fn so
    # this stub never actually hits a network call.
    return Model(
        id=requested or "stub",
        name=requested or "stub",
        api="completion",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )


def _with_tool_runtime_prompt(system_prompt: str, tools: Optional[list]) -> str:
    if not tools:
        return system_prompt

    names = [getattr(t, "name", "") for t in tools]
    names = [n for n in names if n]
    if not names:
        return system_prompt

    from openprogram.paths import get_default_workdir
    cwd = get_default_workdir()
    has_bash = "bash" in names
    lines = [
        "Runtime tool context:",
        f"- Current working directory: {cwd}",
        f"- Available tools for this turn: {', '.join(names)}",
        "- Scope every filesystem operation (read/list/glob/grep/bash) "
        "to the smallest known target, ideally under the working "
        "directory above. Do NOT recurse over `$HOME` or `/`; recursive "
        "`**` walks over a home directory take minutes and exhaust the "
        "turn budget.",
        "- If the user asks for the current directory, answer from the Current working directory line above.",
        "- If the user asks to list the current directory, call the list tool with that absolute path.",
        "- When the user asks to inspect files, directories, or program state, call the relevant available tool instead of saying no tools are available.",
    ]
    if has_bash:
        lines.append("- Shell command execution is available through the bash tool.")
    else:
        lines.append("- Shell command execution is not available in this transport; use filesystem/search tools such as list, read, glob, and grep when possible.")

    tool_prompt = "\n".join(lines)
    return f"{system_prompt.rstrip()}\n\n{tool_prompt}".strip()


def _log_resolved_tools(req: TurnRequest, tools: Optional[list]) -> None:
    try:
        names = sorted(
            getattr(t, "name", "")
            for t in (tools or [])
            if getattr(t, "name", "")
        )
        override_state = "explicit" if req.tools_override is not None else "profile"
        print(
            f"[dispatcher tools] source={req.source!r} agent={req.agent_id!r} "
            f"mode={override_state} tools={names}",
            flush=True,
        )
    except Exception:
        pass


def _resolve_tools(
    profile: dict,
    override: Optional[list[str]] = None,
    *,
    source: Optional[str] = None,
) -> Optional[list]:
    """Resolve the AgentTool list for this turn.

    `override` (per-turn) > profile.tools (per-agent).
    `source` hides tools marked unsafe for channel transports.
    Returns None when no tools are configured (caller gives agent_loop
    a tools-free context — it's a pure chat then).
    """
    wanted = override if override is not None else profile.get("tools")
    if wanted is None:
        # Default-on: match runtime.Runtime._call_via_providers and
        # Hermes/OpenClaw — when the agent profile didn't pin a tool
        # list, expose DEFAULT_TOOLS (filtered by `source` so channel
        # transports still drop unsafe ones via `unsafe_in`). This
        # used to return None ("pure chat"), which broke parity with
        # the deleted claude-code CLI provider that always shipped
        # Read/Write/Edit. Set `tools: []` in agent.json to opt out
        # explicitly.
        try:
            from openprogram.tools import agent_tools as _agent_tools
            return _agent_tools(source=source, only_available=True)
        except Exception:
            return None
    if wanted == []:
        return []
    try:
        from openprogram.tools import DEFAULT_TOOLS, agent_tools
        if isinstance(wanted, dict):
            enabled = wanted.get("enabled")
            if isinstance(enabled, list):
                names = [str(n) for n in enabled]
            else:
                disabled = {str(n) for n in (wanted.get("disabled") or [])}
                names = [n for n in DEFAULT_TOOLS if n not in disabled]
            toolset = wanted.get("toolset")
            if isinstance(toolset, str) and not isinstance(enabled, list):
                return agent_tools(toolset=toolset, source=source, only_available=True)
            return agent_tools(names=names, source=source, only_available=True)

        # Caller passed an explicit name list — preserve their order,
        # drop missing names, then apply availability + source gates.
        if isinstance(wanted, list) and wanted and isinstance(wanted[0], str):
            return agent_tools(
                names=[str(n) for n in wanted],
                source=source,
                only_available=True,
            )
        # Fallback: caller already passed AgentTool instances
        return [t for t in wanted if hasattr(t, "name")]
    except Exception:
        return None


def _history_to_agent_messages(history: list[dict]) -> list:
    """Turn SessionDB rows into AgentMessage list (for AgentContext)."""
    from openprogram.providers.types import (
        AssistantMessage, TextContent, UserMessage,
    )
    out: list = []
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        ts = int((m.get("timestamp") or time.time()) * 1000)
        if role == "user":
            out.append(UserMessage(
                content=[TextContent(text=content)],
                timestamp=ts,
            ))
        elif role == "assistant":
            # Best-effort — we lost the structured tool calls info,
            # but for context replay plain text is enough.
            try:
                out.append(AssistantMessage(
                    content=[TextContent(text=content)],
                    api="completion",
                    provider="openai",
                    model="gpt-5",
                    timestamp=ts,
                ))
            except Exception:
                # Different providers reject some fields — fall back to
                # skipping rather than crashing replay.
                pass
        # system messages skipped — they're surfaced as visible logs,
        # not part of the LLM context.
    return out
