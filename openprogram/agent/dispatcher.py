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
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Optional


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
    conv_id: str
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
    session = db.get_session(req.conv_id)
    if session is None:
        db.create_session(
            req.conv_id, req.agent_id,
            title=_default_title(req),
            source=req.source,
            channel=req.source if req.source in {"wechat", "telegram", "discord", "slack"} else None,
            peer_display=req.peer_display,
            peer_id=req.peer_id,
        )
        session = db.get_session(req.conv_id) or {}
    if req.history_override is not None:
        history = list(req.history_override)
    elif isinstance(req.parent_id, _InheritParent):
        # Normal append — walk the active branch.
        history = db.get_branch(req.conv_id) or db.get_messages(req.conv_id)
    elif req.parent_id is None:
        # Root-level fork — LLM starts with empty history.
        history = []
    else:
        # Sibling fork — history is the branch ending at the explicit
        # parent. LLM sees what existed up to the fork point, not
        # what's currently on the active branch.
        history = db.get_branch(req.conv_id, req.parent_id)

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
        db.append_message(req.conv_id, user_msg)
        # Advance head to the user message. Crucial for branching: if
        # the caller passed parent_id pointing at an older message,
        # we're now on a NEW leaf and head must reflect that —
        # otherwise the next get_branch call would still walk down
        # the old branch.
        db.set_head(req.conv_id, user_msg_id)
        on_event({
            "type": "chat_ack",
            "data": {"conv_id": req.conv_id, "msg_id": user_msg_id},
        })
    else:
        # Caller already wrote the user msg + emitted ack (webui
        # path). Make sure history reflects that — load from DB if
        # the caller didn't pass a history_override.
        if req.history_override is None:
            history = db.get_branch(req.conv_id) or history

    # 3. Run the agent loop. Errors below get caught and reported as
    #    a system message so the conversation isn't left in a stuck
    #    "agent is thinking…" state.
    try:
        # When the caller pre-persisted the user msg, ``history`` was
        # reloaded above and already includes it — passing it twice
        # would prepend a duplicate user turn into the LLM context.
        if req.user_already_persisted:
            loop_history = history
        else:
            loop_history = history + [user_msg]
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
        db.append_message(req.conv_id, {
            "id": err_id,
            "role": "system",
            "content": err_text,
            "timestamp": time.time(),
            "parent_id": user_msg_id,
            "source": req.source,
            "extra": json.dumps({"trace": traceback.format_exc()[:2000]}),
        })
        on_event({"type": "chat_response",
                  "data": {"type": "error", "content": err_text}})
        return TurnResult(
            final_text="",
            user_msg_id=user_msg_id,
            assistant_msg_id="",
            failed=True,
            error=str(e),
            duration_ms=int((time.time() - started_at) * 1000),
        )

    # 5. Persist assistant message.
    assistant_msg = {
        "id": assistant_msg_id,
        "role": "assistant",
        "content": final_text,
        "timestamp": time.time(),
        "parent_id": user_msg_id,
        "source": req.source,
    }
    if tool_calls:
        assistant_msg["extra"] = json.dumps({"tool_calls": tool_calls},
                                            default=str)
    db.append_message(req.conv_id, assistant_msg)

    # 6. Update session bookkeeping (head_id, token tracking, model).
    db.update_session(
        req.conv_id,
        head_id=assistant_msg_id,
        last_prompt_tokens=int(usage.get("input_tokens") or 0),
        model=req.model_override or session.get("model"),
    )

    # 6.5. Auto-title: if the session is still using the placeholder
    # title (or hasn't been titled by an explicit user action), set a
    # readable label from the user's first message. Cheap version —
    # just take the first 50 chars; LLM-summarized titles are a
    # future upgrade. Fires once per session (idempotent via
    # extra_meta._titled flag).
    _maybe_auto_title(db, req.conv_id, session, req.user_text)

    # 6.6. Compaction signal: when context is approaching the model's
    # window, surface a "compaction_recommended" event so the UI can
    # offer the user a /compact action. We don't auto-compact mid-
    # turn — that would block the response. The actual compaction
    # call is exposed as ``trigger_compaction(conv_id)`` for clients
    # to invoke explicitly.
    #
    # Best-effort resolution of the context window; conservative
    # 200k default if anything goes wrong (signal stays late but
    # never crashes the turn).
    try:
        _ctx_window = int(getattr(
            _resolve_model(_load_agent_profile(req.agent_id),
                            req.model_override),
            "max_tokens", 0,
        )) or 200_000
    except Exception:
        _ctx_window = 200_000
    _maybe_signal_compaction(db, req.conv_id, on_event,
                              context_window=_ctx_window)

    # 7. Final result event for clients that wait for the synchronous
    #    "the turn is done" signal.
    on_event({"type": "chat_response",
              "data": {"type": "result", "content": final_text}})

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


def _maybe_auto_title(db, conv_id: str, session: dict,
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
        db.update_session(conv_id, title=title, _titled=True)
    except Exception:
        pass


# Conservative threshold: emit "compaction_recommended" once we're at
# 70% of the context window. Lower than Hermes' 80% so the UI has
# room to react before truncation hits.
_COMPACTION_RECOMMEND_THRESHOLD = 0.7


def _maybe_signal_compaction(db, conv_id: str,
                              on_event: EventCallback,
                              *, context_window: int) -> None:
    """Estimate current branch token usage; emit a recommendation
    envelope when it crosses the threshold. Single signal per turn —
    debounced via session.extra_meta._compaction_recommended_at so
    the UI doesn't get spammed."""
    try:
        from openprogram.agent.compaction import should_compact
    except Exception:
        return
    try:
        msgs = db.get_branch(conv_id) or []
    except Exception:
        return
    if not msgs:
        return
    # Convert SessionDB row dicts to the shape should_compact expects
    # (objects with role + content). The estimator is lenient — plain
    # dicts work too.
    if not should_compact(msgs, context_window=context_window,
                           threshold=_COMPACTION_RECOMMEND_THRESHOLD):
        return
    on_event({
        "type": "chat_response",
        "data": {
            "type": "compaction_recommended",
            "conv_id": conv_id,
            "branch_messages": len(msgs),
        },
    })


def trigger_compaction(conv_id: str, agent_id: str = "main",
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
    from openprogram.agent.compaction import compact_context

    db = default_db()
    sess = db.get_session(conv_id)
    if sess is None:
        raise ValueError(f"Unknown conversation {conv_id!r}")
    history = db.get_branch(conv_id) or []
    if len(history) < 4:
        return {"summary": "", "kept_count": len(history), "summary_id": ""}

    profile = _load_agent_profile(agent_id)
    model = _resolve_model(profile, None)
    system_prompt = profile.get("system_prompt") or ""

    # compact_context expects dict-shaped messages — its internal
    # cut-point logic does ``entry["message"].get("role", "")``, which
    # blows up on pydantic Message objects. Pass the raw SessionDB
    # rows (already dicts) and let the summarizer build its own
    # serialized form.
    #
    # Async-in-sync: run in a fresh loop off any caller async loop.
    settings: Optional[dict] = None
    if keep_recent_tokens is not None:
        settings = {"enabled": True,
                    "keepRecentTokens": int(keep_recent_tokens),
                    "reserveTokens": 16384}
    loop = asyncio.new_event_loop()
    try:
        new_messages, summary = loop.run_until_complete(
            compact_context(
                messages=history,
                system_prompt=system_prompt,
                stream_fn=None,
                model=model,
                settings=settings,
            )
        )
    finally:
        loop.close()

    if not summary:
        return {"summary": "", "kept_count": len(history), "summary_id": ""}

    # Persist the summary as a new root-level message (parent_id=None).
    # Old chain stays in DB; readers walk only the new branch.
    summary_id = uuid.uuid4().hex[:12]
    db.append_message(conv_id, {
        "id": summary_id,
        "role": "user",
        "content": f"[Previous conversation summary]\n{summary}",
        "timestamp": time.time(),
        "parent_id": None,
        "source": "compaction",
        "extra": json.dumps({
            "compaction": True,
            "logical_parent_id": sess.get("head_id"),
            "summarized_count": len(history) - max(0, len(new_messages) - 1),
        }, default=str),
    })
    # The kept tail (new_messages[1:]) gets re-parented to the summary.
    # Each kept message gets a fresh id so it doesn't collide with the
    # original row. Sequential parent chain through the tail.
    last_id = summary_id
    for src_msg in (new_messages[1:] if len(new_messages) > 1 else []):
        new_id = uuid.uuid4().hex[:12]
        # src_msg may be a SessionDB row dict (we passed dict-shaped
        # history) OR a pydantic Message (compact_context's prepended
        # summary block — already handled above as new_messages[0]).
        # Normalize both.
        if isinstance(src_msg, dict):
            role = src_msg.get("role", "user")
            content = src_msg.get("content", "")
        else:
            role = getattr(src_msg, "role", "user")
            c = getattr(src_msg, "content", None)
            if isinstance(c, str):
                content = c
            elif isinstance(c, list):
                parts = []
                for blk in c:
                    t = getattr(blk, "text", None)
                    if t:
                        parts.append(t)
                content = "\n".join(parts)
            else:
                content = str(c) if c is not None else ""
        db.append_message(conv_id, {
            "id": new_id,
            "role": role if role != "toolResult" else "assistant",
            "content": content,
            "timestamp": time.time(),
            "parent_id": last_id,
            "source": "compaction",
        })
        last_id = new_id

    db.set_head(conv_id, last_id)
    on_event({
        "type": "chat_response",
        "data": {"type": "compaction_done",
                 "conv_id": conv_id,
                 "summary_id": summary_id,
                 "summary": summary},
    })
    return {"summary": summary,
             "kept_count": max(0, len(new_messages) - 1),
             "summary_id": summary_id}


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
    tools = _resolve_tools(agent_profile, req.tools_override)
    if tools:
        tools = [_wrap_with_approval(t, req, on_event) for t in tools]
    system_prompt = agent_profile.get("system_prompt") or ""
    model = _resolve_model(agent_profile, req.model_override)

    context = AgentContext(
        system_prompt=system_prompt,
        messages=_history_to_agent_messages(history),
        tools=tools,
    )

    # _default_convert_to_llm filters out non-LLM messages (e.g. our
    # custom error / system entries) — agent.py already provides this.
    from openprogram.agent.agent import _default_convert_to_llm

    config = AgentLoopConfig(
        model=model,
        convert_to_llm=_default_convert_to_llm,
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

        if req.user_already_persisted:
            # User msg is already the tail of ``context.messages``
            # (loaded from SessionDB above). agent_loop_continue uses
            # it as-is without inserting another prompt — no duplicate
            # user turn in the LLM context.
            ev_stream = agent_loop_continue(context, config,
                                              loop_cancel, stream_fn)
        else:
            # Channels / TUI / first-time webui call: history excludes
            # the new user turn. Wrap user_text as a UserMessage prompt
            # plus any attached images. Agent_loop appends to
            # context.messages internally.
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
                        # than aborting the whole turn. Provider will
                        # see the text-only fallback.
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
        usage_total: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        tool_calls: list[dict] = []

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
                if ev.type == "tool_execution_end":
                    tool_calls.append({
                        "id": getattr(ev, "tool_call_id", None),
                        "tool": getattr(ev, "tool_name", None),
                        "result": _shorten(getattr(ev, "result", "")),
                        "is_error": bool(getattr(ev, "is_error", False)),
                    })
                if ev.type == "turn_end":
                    msg = getattr(ev, "message", None)
                    text = _extract_text(msg)
                    if text:
                        final_text_parts.append(text)
                    usage = _extract_usage(msg)
                    usage_total["input_tokens"] += usage.get("input_tokens", 0)
                    usage_total["output_tokens"] += usage.get("output_tokens", 0)

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
            "conv_id": req.conv_id,
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
                         "event": {"type": "text",
                                   "text": getattr(ame, "delta", "")}},
            }
        return None

    if t == "tool_execution_start":
        args = getattr(ev, "args", None)
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "event": {"type": "tool_use",
                               "tool": getattr(ev, "tool_name", "?"),
                               "input": json.dumps(args, default=str)
                                        if args is not None else None,
                               "id": getattr(ev, "tool_call_id", None)}},
        }

    if t == "tool_execution_end":
        return {
            "type": "chat_response",
            "data": {"type": "stream_event",
                     "event": {"type": "tool_result",
                               "tool": getattr(ev, "tool_name", "?"),
                               "result": _shorten(getattr(ev, "result", "")),
                               "is_error": bool(getattr(ev, "is_error", False))}},
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
    if msg is None:
        return {}
    usage = getattr(msg, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
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
        if "/" in requested:
            provider, model_id = requested.split("/", 1)
            m = get_model(provider, model_id)
            if m:
                return m
        else:
            # Probe known providers, biased toward the dict's
            # ``provider`` field if present so a malformed entry like
            # ``{"provider": "openai-codex", "id": "gpt-5.4"}`` still
            # tries the right backend first.
            order = ["openai", "anthropic", "google", "amazon-bedrock",
                     "cerebras", "claude-code", "github-copilot"]
            if provider_hint and provider_hint not in order:
                order.insert(0, provider_hint)
            for provider in order:
                m = get_model(provider, requested)
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


def _resolve_tools(profile: dict,
                   override: Optional[list[str]] = None) -> Optional[list]:
    """Resolve the AgentTool list for this turn.

    `override` (per-turn) > profile.tools (per-agent) > all registered.
    Returns None when no tools are configured (caller gives agent_loop
    a tools-free context — it's a pure chat then).
    """
    wanted = override if override is not None else profile.get("tools")
    if wanted is None:
        # No explicit list: leave None so agent_loop runs in pure chat
        # mode. We could default to "all tools" but that's a security
        # decision better made explicit (set tools in agent.json).
        return None
    if wanted == []:
        return []
    try:
        from openprogram.tools import agent_tools, get_agent_tool
        # Caller passed an explicit name list — preserve their order
        # and drop names that aren't in the AgentTool registry.
        if isinstance(wanted, list) and wanted and isinstance(wanted[0], str):
            picked = [get_agent_tool(n) for n in wanted]
            return [t for t in picked if t is not None]
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
