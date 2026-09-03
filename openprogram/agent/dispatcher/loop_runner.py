"""Agent-loop run stage — pipeline step 4 (dispatcher-split).

``run_loop_blocking`` builds the AgentContext (profile → tools → system
prompt → model), routes history through the context engine (snip +
auto-compact), kicks off ``agent_loop`` and drains its EventStream to
completion inside a fresh asyncio loop.

Re-exported by ``__init__.py`` as ``_run_loop_blocking`` — the seam
tests patch on the dispatcher package. The profile / model helpers are
also patched on the package (``dispatcher._load_agent_profile`` /
``dispatcher._resolve_model``), so this module reads them through the
package attribute at call time instead of freezing them with a
module-level from-import.
"""
from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import json
import logging
import threading
import time
import uuid
from typing import Optional, TYPE_CHECKING

from openprogram.agent import plan_mode as _plan_mode
from openprogram.agent.session_config import reasoning_from_config, SessionRunConfig
from openprogram.agent.internals._approval import (
    wrap_with_approval as _wrap_with_approval,
)
from openprogram.agent.internals._event_parsing import (
    agent_event_to_envelope as _agent_event_to_envelope,
    aiter_event_stream as _aiter_event_stream,
    extract_text as _extract_text,
    extract_usage as _extract_usage,
    shorten as _shorten,
)
from openprogram.agent.internals._model_tools import (
    log_resolved_tools as _log_resolved_tools,
    resolve_tools as _resolve_tools,
)
from openprogram.agent.dispatcher.runtime_attach import _wrap_agentic_runtime_block

if TYPE_CHECKING:
    from openprogram.agent.dispatcher.types import EventCallback, TurnRequest

_log = logging.getLogger(__name__)

_INDEPENDENT_BROWSER_TOOLS = {
    "agent_browser", "browser_agent", "playwright_browser",
}


def _configure_web_use_tools(tools, surface_context):
    """Expose one in-app browser contract when Desktop Page inventory exists."""
    from openprogram.agent.surface_context import tool_enabled, web_use_available

    bound = tool_enabled(surface_context)
    enabled = bound or (bool(tools) and web_use_available(surface_context))
    current = list(tools or [])
    if not enabled:
        return [tool for tool in current if tool.name != "web_use"], False

    from openprogram.programs import get_agent_tool

    web_use_tool = next((tool for tool in current if tool.name == "web_use"), None)
    if web_use_tool is None:
        web_use_tool = get_agent_tool("web_use")
    current = [
        tool for tool in current
        if tool.name not in _INDEPENDENT_BROWSER_TOOLS and tool.name != "web_use"
    ]
    if web_use_tool is not None:
        current.append(web_use_tool)
    return current, web_use_tool is not None


def run_loop_blocking(
    *,
    req: "TurnRequest",
    history: list[dict],
    on_event: "EventCallback",
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
    see tests/unit/agent/test_dispatcher_integration.py. None means use
    the default (real provider via stream_simple).
    """
    from openprogram.agent.agent_loop import agent_loop, agent_loop_continue
    from openprogram.agent.types import AgentContext, AgentLoopConfig
    # Profile / model resolution goes through the package attribute so
    # test monkeypatches on ``dispatcher._load_agent_profile`` /
    # ``dispatcher._resolve_model`` keep applying here.
    from openprogram.agent import dispatcher as _dispatcher

    # Resolve agent profile → tools, system_prompt, model.
    agent_profile = (
        deepcopy(req.profile_snapshot) if req.profile_snapshot is not None
        else _dispatcher._load_agent_profile(req.agent_id)
    )
    from openprogram.agent.surface_context import (
        render_for_model as _render_surface_context,
    )
    tools = _resolve_tools(agent_profile, req.tools_override, source=req.source)
    tools, web_use_enabled = _configure_web_use_tools(tools, req.surface_context)
    if req.source == "self_update_verify":
        from openprogram.programs import apply_tool_policy
        tools = apply_tool_policy(tools or [], source=req.source)
        web_use_enabled = False
    # Plan mode: hide write/mutate tools when the session is currently
    # in plan mode. ``apply_tool_policy(source="plan", ...)`` filters
    # out every tool that lists "plan" in its ``unsafe_in`` set — see
    # the write tools (bash, write, edit, apply_patch, execute_code,
    # process). Applied AFTER channel filtering so both restrictions
    # compose: a wechat turn in plan mode hides the union of both
    # blacklists.
    if tools and _plan_mode.is_plan_mode(req.session_id):
        from openprogram.programs import apply_tool_policy as _apply_policy
        tools = _apply_policy(tools, source="plan")
    from openprogram.programs import install_allowed_tool_names
    install_allowed_tool_names({tool.name for tool in tools or []})
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
    # One assembler (dag/overview.md §7). The tool-runtime block, the Layer 6
    # deferred-tool catalog and the plan-mode reminder are registered
    # components now — the dispatcher no longer hand-appends anything, so the
    # string the engine budgets is the string that ships.
    #
    # We do NOT split ``tools`` for dispatch here — the agent_loop re-splits
    # before every provider call so newly-loaded deferred tools show up with
    # full schema on the next call. The catalog component only lists the
    # *initial* deferred names so the LLM can discover them from turn 1.
    from openprogram.context.components import build_system_prompt
    system_prompt = build_system_prompt(
        agent_profile,
        tools=tools,
        additional_working_dirs=getattr(req, "additional_working_dirs", None),
        plan_mode=_plan_mode.is_plan_mode(req.session_id),
    )
    recordable_system_prompt = system_prompt
    surface_prompt = _render_surface_context(
        req.surface_context, web_use_enabled=web_use_enabled,
    )
    if surface_prompt:
        system_prompt = f"{system_prompt}\n\n{surface_prompt}"
    model = _dispatcher._resolve_model(agent_profile, req.model_override)

    # Route history through the context engine: applies tool-result
    # aging in-memory, computes an accurate token budget against the
    # model's real context window, surfaces whether auto-compact should
    # fire before we burn tokens on this turn.
    from openprogram.context import resolve_engine_for
    from openprogram.agent.session_db import default_db
    _ctx_engine = resolve_engine_for(agent_profile)
    _ctx_engine.on_session_start(req.session_id)
    db = default_db()
    # The prompt is recorded, not implied (dag/overview.md §7): append a
    # context/system_prompt node whenever the assembled text's hash moves.
    # No-op when it didn't — a stable prompt records once per session.
    from openprogram.context.system_prompt_node import record_system_prompt
    record_system_prompt(db, req.session_id, recordable_system_prompt)
    session = db.get_session(req.session_id) or {}
    prep = _ctx_engine.prepare(
        agent=agent_profile,
        session=session,
        history=history,
        model=model,
        tools=tools,
        system_prompt=system_prompt,
    )

    # Auto-compact FIRST: the durable fix. It writes a summary node
    # into the DAG, so the shrink survives this turn (panel, next
    # turns, reload). Snip runs only as a fallback below — the old
    # snip-first order let the free in-memory trim drop the budget
    # back under the threshold every turn, so the LLM compact never
    # fired and the graph grew without bound.
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
                # Re-load the post-compact view (summary + kept
                # tail) so the LLM call sees the shorter context.
                from openprogram.context.persistence import rendered_history
                history = rendered_history(db, req.session_id) or history
                prep = _ctx_engine.prepare(
                    agent=agent_profile,
                    session=db.get_session(req.session_id) or session,
                    history=history,
                    model=model,
                    tools=tools,
                    system_prompt=system_prompt,
                )
        except Exception as e:  # noqa: BLE001
            # Auto-compact must never crash the turn.
            on_event({"type": "chat_response",
                      "data": {"type": "compaction_failed",
                               "session_id": req.session_id,
                               "error": f"{type(e).__name__}: {e}",
                               "user_initiated": False}})

    # Snip fallback: compact failed, was skipped (<4 messages), or the
    # summary alone didn't free enough. Free in-memory trim of the
    # oldest turns so THIS request still fits the window. Not written
    # to the DAG — it only shapes what ships now.
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
                    system_prompt=system_prompt,
                )
                on_event({"type": "chat_response",
                          "data": {"type": "snip",
                                   "session_id": req.session_id,
                                   "turns_removed": n_snipped}})
        except Exception:
            # Failing to snip means the oversized context goes to the model
            # and the provider rejects it — worth knowing about.
            _log.warning(
                "history snip failed for session %s",
                req.session_id, exc_info=True,
            )

    # Memory recalled for this turn (dag/overview.md §7). ``process_user_turn``
    # stamped it on the user node; read it back from the branch so the block
    # the loop renders is byte-identical to the one replay will reproduce.
    _memory_prefetch = ""
    _prefetch_history = history
    _prefetch_head = assistant_msg_id or req.user_msg_id
    if _prefetch_head:
        # ``history`` intentionally excludes the current user node. Follow the
        # current branch to that node so we reuse the exact persisted recall
        # instead of issuing a second search.
        _prefetch_history = (
            db.get_branch(req.session_id, _prefetch_head) or history
        )
    for _m in reversed(_prefetch_history or []):
        if _m.get("role") == "user":
            _memory_prefetch = _m.get("memory_prefetch") or ""
            break

    context = AgentContext(
        system_prompt=system_prompt,
        messages=prep.agent_messages,
        tools=tools,
        memory_prefetch=_memory_prefetch,
    )

    # _default_convert_to_llm filters out non-LLM messages (e.g. our
    # custom error / system entries) — agent.py already provides this.
    from openprogram.agent.agent import _default_convert_to_llm

    async def _get_steering_messages():
        # Same-session spawned turns are side-branch machinery. Let the
        # foreground chat turn remain the only consumer of its session inbox.
        if req.source == "agent_spawn":
            return []
        from openprogram.agent import steering

        text = steering.pop(req.session_id)
        if text is None:
            return []
        from openprogram.context.nodes import Call, ROLE_USER
        from openprogram.providers.types import TextContent, UserMessage
        from openprogram.store import SessionNodeWriter

        message_id = uuid.uuid4().hex[:12]
        timestamp = time.time()
        predecessor = (
            getattr(req, "_steering_tail_id", None)
            or req.user_msg_id
            or "ROOT"
        )
        metadata = {
            "source": "web",
            "steering": True,
            "agent_id": req.agent_id,
        }
        try:
            from openprogram.agent.authority import normalize_authority, stamp_schema

            metadata.update(normalize_authority(req))
            stamp_schema(metadata)
            writer = SessionNodeWriter(db, req.session_id, advance_head=False)
            writer.append(Call(
                id=message_id,
                created_at=timestamp,
                role=ROLE_USER,
                output=text,
                predecessor=predecessor,
                metadata=metadata,
            ))
            if not db.message_exists(req.session_id, message_id):
                raise RuntimeError("steering user message was not persisted")
            writer.update(assistant_msg_id, predecessor=message_id)
        except Exception:
            # Persistence is part of acceptance. Put the text back so the
            # turn-end sweep can deliver it as an ordinary next turn.
            steering.push(req.session_id, text)
            _log.warning(
                "failed to persist steering for session %s",
                req.session_id,
                exc_info=True,
            )
            return []
        req._steering_tail_id = message_id
        on_event({
            "type": "chat_response",
            "data": {
                "type": "user_message",
                "session_id": req.session_id,
                "msg_id": message_id,
                "content": text,
                "source": "web",
                "steering": True,
                "timestamp": timestamp,
                "predecessor": predecessor,
            },
        })
        return [UserMessage(
            content=[TextContent(text=text)],
            timestamp=int(timestamp * 1000),
        )]

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
        response_format=req.response_format,
        get_steering_messages=_get_steering_messages,
    )

    # Async drain that forwards each AgentEvent → on_event envelope.
    # Released once the drain loop is done so the cancel-bridge thread
    # exits when the turn ends normally. Without it the thread parks on
    # ``cancel_event.wait()`` for the life of the process — one leaked
    # thread per turn.
    _turn_over = threading.Event()

    async def _drain() -> tuple[str, dict, list[dict]]:
        loop_cancel = asyncio.Event()
        if cancel_event is not None:
            # Bridge thread-side cancel into asyncio. Capture the
            # running loop here (the watch thread can't call
            # ``get_event_loop`` — Python 3.12+ raises in non-main
            # threads with no loop set).
            asyncio_loop = asyncio.get_running_loop()

            def _watch():
                while not (cancel_event.wait(0.1) or _turn_over.is_set()):
                    pass
                if cancel_event.is_set():
                    try:
                        asyncio_loop.call_soon_threadsafe(loop_cancel.set)
                    except RuntimeError:
                        # Loop already closed — the turn finished first.
                        pass
            threading.Thread(
                target=_watch, daemon=True, name="turn-cancel-bridge",
            ).start()

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
            from openprogram.agent.authority import render_model_input_from
            content_blocks.append(TextContent(
                text=render_model_input_from(req, req.user_text)
            ))
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
                    # Malformed attachment — drop it rather than abort the
                    # whole turn. The user sent an image and the model will
                    # not see it, so this is not a quiet loss.
                    _log.warning("image attachment dropped for session %s",
                                 req.session_id, exc_info=True)
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
            "provider_request_count": 0, "agent_iteration_count": 0,
        }
        tool_calls: list[dict] = []
        # Capture tool_use inputs so we can rebuild the same
        # collapsible scaffold on reload. tool_execution_end events
        # don't carry the input args, so we stash them at start time.
        tool_inputs_by_id: dict[str, dict] = {}

        # aclosing so an early exit (the LLMError raise below) runs the
        # generator's finally/aclose here, instead of leaving it for the
        # GC to schedule on a loop we are about to close.
        async with contextlib.aclosing(
                _aiter_event_stream(ev_stream)) as _events:
            async for ev in _events:
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
                        usage_total["provider_request_count"] += 1
                        usage_total["agent_iteration_count"] += 1
                        msg = getattr(ev, "message", None)
                        if getattr(msg, "structured_output_mode", None) is not None:
                            req.structured_output = getattr(msg, "structured_output", None)
                            req.structured_output_mode = msg.structured_output_mode
                            req.structured_output_attempt = getattr(
                                msg, "structured_output_attempt", None
                            )
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
                        # 当前上下文占用 ≈ 最后一次调用的 prompt 体积
                        # （input + cache_read）。turn 内多次调用的 input
                        # 之和会远超窗口，只能用于计费，不能用于占用率。
                        usage_total["context_tokens"] = (
                            usage.get("input_tokens", 0)
                            + usage.get("cache_read_tokens", 0)
                        )
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
                                        except (TypeError, ValueError):
                                            _input = None
                                        ordered_blocks_out.append({
                                            "type": "tool",
                                            "tool": _name,
                                            "tool_call_id": _tid,
                                            "input": _input,
                                        })
                            except Exception:
                                # Provider block shapes vary; a normalisation miss
                                # costs one rendered block, not the turn.
                                _log.debug(
                                    "provider block normalisation failed",
                                    exc_info=True,
                                )

        return "".join(final_text_parts).strip(), usage_total, tool_calls

    # Run the async drain in a fresh loop (we're in a thread).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drain())
    finally:
        # Let the cancel-bridge thread exit before the loop it would post to
        # is gone.
        _turn_over.set()
        # Close any async generator the provider/agent layers left open
        # (an abandoned one otherwise schedules its aclose onto this loop
        # right as we close it, which never runs and warns at GC time).
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            _log.debug("asyncgen shutdown failed", exc_info=True)
        loop.close()
