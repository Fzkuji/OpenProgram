"""Agentic runtime: providers."""
from __future__ import annotations

import asyncio




import logging



import time

from dataclasses import replace

from typing import Any


from .shared import (
    _adapt_tools,
    _assistant_text,
    _build_pi_context,
    _current_call_model,
    _current_effort,
    _current_loop_opts,
    _current_model_call_budget,
    _current_response_format,
    _current_stream_fn,
    _current_tool_policy,
    _current_tools,
    _exec_system_prompt,
    _run_async,
    _situational_prefix,
)

class ProvidersOperations:
    def _call(
        self, content: list[dict], model: str = "default", response_format: dict = None
    ) -> Any:
        """
        Call the LLM. Override this in subclasses.

        Single path: everything goes through ``_call_via_providers`` (the
        AgentSession + agent_loop path). ``Runtime(call=fn)`` is handled by a
        CallableModel set on ``api_model`` at construction, so there is no
        separate ``_call_fn`` branch anymore.

        Args:
            content:          List of content blocks (text, image, audio, file).
            model:            Model name.
            response_format:  Output format constraint (JSON schema).

        Returns:
            str — the LLM's reply text.
        """
        if self.api_model is not None:
            return self._call_via_providers(content, response_format=response_format)
        raise NotImplementedError(
            "No LLM provider configured. Either pass `call=your_function` to Runtime(), "
            "use model='provider:model_id' form, or subclass Runtime and override _call()."
        )


    def _gate_inner_tools(self, agent_tools):
        """Wrap an inner session's tools with the outer turn's approval gate.

        Returns the list unchanged when no turn is bound above us — a
        library-only ``Runtime()`` has no execution context to inherit and
        no user to approve anything, so there is nothing to enforce.
        Failures here are never silent: an inner session that cannot be
        gated runs with no tools rather than with ungated ones.
        """
        if not agent_tools:
            return agent_tools
        from openprogram.agent.turn_request_context import (
            get_turn_request,
            inner_turn_request,
        )

        if get_turn_request() is None:
            return agent_tools
        try:
            from openprogram.agent.permissions.approval import wrap_with_approval

            req = inner_turn_request("program")
            if req is None:
                return None
            # No approval surface down here — the inner request is
            # non-interactive by construction, so every gate that would
            # ask instead denies. The sink keeps sandbox.violation events
            # from crashing the wrapper.
            return [wrap_with_approval(t, req, lambda _env: None) for t in agent_tools]
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "inner tool gating failed; running this call without tools "
                "rather than ungated"
            )
            return None


    def _call_via_providers(
        self,
        content: list[dict],
        response_format: dict = None,
    ) -> Any:
        call = self._async_call_via_providers(content, response_format)
        # The lower transport layers consult the shared deadline before
        # retrying, but a provider can remain silent after opening a stream.
        # Bound the whole coroutine as well so Runtime.exec(timeout_s=...) is
        # a real wall-clock limit rather than only a retry-boundary check.
        from openprogram.providers.utils.deadline import remaining

        time_left = remaining()
        if time_left is not None:
            call = asyncio.wait_for(call, timeout=max(0.0, time_left))
        return _run_async(call)


    async def _async_call_via_providers(
        self,
        content: list[dict],
        response_format: dict = None,
        *,
        offload_sync_callable: bool = False,
    ) -> Any:
        """
        Default _call implementation for ``model="provider:model_id"`` usage.

        When invoked from inside ``Runtime.exec()``, reads the running exec
        node from ``_current_exec_ctx`` and uses ``exec_ctx.render_messages()``
        to run a multi-turn conversation through ``AgentSession``. Tools
        passed to ``exec(tools=...)`` reach the session via ``_current_tools``
        so the agent loop runs a tool-use cycle automatically. The message
        prefix stays stable across successive ``exec()`` calls, which is what
        lets provider prompt caches hit.

        When invoked without an exec node in scope (direct ``_call`` use),
        wraps ``content`` into a single ``UserMessage`` and calls
        ``complete_simple`` — single-turn behaviour.

        ``content`` is ignored in the multi-turn path: it was built by
        ``_merge_content`` for the text-prompt pathway and would duplicate
        history already present in the message list.
        """
        from openprogram.agent import AgentSession

        session_model = self.api_model
        requested_model = _current_call_model.get(None)
        if (
            self._call_fn is None
            and requested_model
            and requested_model != self.model
        ):
            if ":" in requested_model:
                provider_id, model_id = requested_model.split(":", 1)
            else:
                provider_id = self.provider_id or getattr(
                    self.api_model, "provider", ""
                )
                model_id = requested_model
            from openprogram.providers import get_model

            session_model = get_model(provider_id, model_id)
            if session_model is None:
                raise ValueError(f"Unknown model override {requested_model!r}")

        raw_tools = _current_tools.get(None)
        policy = _current_tool_policy.get(None) or {}
        # Unattended mode: subtract the user-question tool no matter which
        # toolset a function requested, so a background run can't block asking
        # a question nobody is there to answer. Merged into the policy deny so
        # both resolution paths below honour it.
        from openprogram.agent.attended import denied_ask_tools as _denied_ask

        _deny = list(policy.get("deny") or []) + _denied_ask(
            getattr(self, "session_id", None)
        )
        _deny = _deny or None
        # Tools are ON BY DEFAULT. A bare `runtime.exec(content=...)` with no
        # `tools=` / `toolset=` gets the FULL toolset, so any function can
        # search, fetch, run code, and write files without each one having to
        # opt in (the recurring "function needed a tool but wasn't given one"
        # bug). Tool-call results live only in the run history — they don't
        # leak into later prompt context — so handing tools out broadly is
        # safe. A pure-reasoning / pure-choice call that genuinely wants NO
        # tools opts out explicitly with `toolset="none"`.
        DEFAULT_TOOLSET = "full"
        if raw_tools is None:
            preset = policy.get("toolset") if policy else None
            # CallableModel exposes only the supplied callable contract. It
            # cannot execute Runtime's process-wide default tool registry.
            if preset == "none" or self._call_fn is not None:
                # Explicit opt-out — reasoning-only call, no tools.
                agent_tools = None
            else:
                from openprogram.programs import (
                    agent_tools as _resolve_agent_tools,
                )

                tools_for_session = _resolve_agent_tools(
                    toolset=preset or DEFAULT_TOOLSET,
                    source=policy.get("source") if policy else None,
                    allow=policy.get("allow") if policy else None,
                    deny=_deny,
                )
                agent_tools = tools_for_session or None
        elif raw_tools:
            from openprogram.programs import apply_tool_policy as _apply_policy

            adapted = _adapt_tools(raw_tools) or []
            # Caller-supplied tools (exec(tools=[...])) are self-authorized:
            # skip the exposure whitelist (it lists only registry tools, so it
            # would drop every ad-hoc tool — the bug that made
            # call_with_schema / forced-submit calls reach codex with tools=[]).
            # Channel-source / allow / deny still apply.
            adapted = _apply_policy(
                adapted,
                source=policy.get("source") if policy else None,
                allow=policy.get("allow") if policy else None,
                deny=_deny,
                exposure_filter=False,
            )
            agent_tools = adapted or None
        else:
            # Explicit `tools=[]` — caller wanted no tools, honour it.
            agent_tools = None

        # Prompt-composition: prefer DAG-derived history when a store
        # is installed; fall back to wrapping ``content`` as a single
        # UserMessage for standalone runs.
        dag_messages = self._render_history_messages(content)
        if dag_messages is not None:
            history = dag_messages[:-1]
            current = dag_messages[-1]
        else:
            # Standalone / no-store fallback: there is no DAG to read the
            # current frame's name+doc from, but exec() is still running
            # inside some agentic function body. Recover that function's
            # name from the recursion-depth tracker (innermost = deepest)
            # so the situational/self-recursion guidance is injected here
            # too — otherwise standalone calls receive no situational guidance.
            standalone_prefix: list = []
            try:
                from openprogram.agentic_programming.function import (
                    _recursion_depth,
                )

                _depths = _recursion_depth.get(None) or {}
                if _depths:
                    _cur_fn = max(_depths, key=_depths.get)
                    standalone_prefix = [
                        {
                            "type": "text",
                            "text": _situational_prefix(_cur_fn, ""),
                        }
                    ]
            except Exception:
                standalone_prefix = []
            ctx, _sp_unused = _build_pi_context(standalone_prefix + (content or []))
            history = []
            current = ctx.messages[0]
        # One assembler (dag/overview.md §7): a model call inside a function
        # body gets the same project background as the chat model, so the
        # provider prefix cache is shared. ``self.system`` — the per-runtime
        # override — feeds the assembler's inline_prompt layer; the skills
        # block stays appended here because it is exec's *own* skill dirs
        # (``Runtime(skills=...)``), which the agent-level skills_index
        # component doesn't know about.
        system_prompt = _exec_system_prompt(
            getattr(self, "system", "") or "",
            agent_tools,
        )

        skills_block = self._skills_block()
        if skills_block:
            system_prompt = (
                (system_prompt + skills_block)
                if system_prompt
                else skills_block.lstrip("\n")
            )

        structured_format = _current_response_format.get(None)
        if structured_format is None and response_format is not None:
            from openprogram.providers.structured_output import (
                normalize_response_format,
            )

            structured_format = normalize_response_format(response_format)
        model_call_budget = _current_model_call_budget.get(None)
        if structured_format is not None and model_call_budget is not None:
            structured_format = replace(
                structured_format,
                max_validation_retries=max(
                    0,
                    structured_format.max_validation_retries
                    - model_call_budget["validation_repairs_used"],
                ),
            )
        # 现算输入分类分解 + 采集工具名单（论文仓库 spec §5 ①③）。best-effort，
        # 算失败置 None/[]，绝不影响 LLM 调用。breakdown 挂 last_usage（见下），
        # 工具名单跟着 exec 收尾的 usage→DAG 节点通道进 history.metadata。
        self._pending_breakdown = None
        self._pending_tool_names = []
        self._pending_system_prompt = ""
        try:
            from openprogram.context.breakdown import compute_call_breakdown
            from openprogram.context.tokens import real_context_window

            _hist_for_bd = list(history) + [current]
            self._pending_breakdown = compute_call_breakdown(
                system_prompt=system_prompt,
                history=_hist_for_bd,
                tools=agent_tools,
                context_window=real_context_window(session_model),
            )
            self._pending_tool_names = [
                getattr(t, "name", "") for t in (agent_tools or [])
            ]
            # 这次调用真实的 system prompt —— 和工具名单、content 一样是输入原料，
            # 存进节点供 web /context 重算 system 类 token（截断同 prompt_text）。
            self._pending_system_prompt = (system_prompt or "")[:8000]
        except Exception:
            self._pending_breakdown = None
            self._pending_tool_names = []
            self._pending_system_prompt = ""

        loop_opts = _current_loop_opts.get(None) or {}
        # stream_fn injection: a per-call override (set by exec via the
        # _current_stream_fn contextvar, used by the dispatcher / tests) wins;
        # otherwise the runtime's own _stream_fn (set when Runtime(call=fn)
        # wraps a callable into a CallableModel). None → real provider.
        _stream_fn = _current_stream_fn.get(None) or getattr(self, "_stream_fn", None)
        if offload_sync_callable and self._call_fn is not None:
            from openprogram.providers.callable_model import make_callable_stream_fn

            _stream_fn = make_callable_stream_fn(self._call_fn, offload_sync=True)
        budget_context_transform = None
        if model_call_budget is not None:
            def check_model_call_budget() -> None:
                if model_call_budget["remaining"] < model_call_budget["limit"]:
                    deadline = model_call_budget["deadline"]
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError("structured output model-call deadline expired")
                    from openprogram.agentic_programming.function import (
                        CancelledError as _CE,
                        check_cancelled,
                    )

                    try:
                        check_cancelled()
                    except _CE:
                        from openprogram.providers.utils.errors import ExecInterrupt

                        raise ExecInterrupt("cancelled") from None
                if model_call_budget["remaining"] <= 0:
                    raise RuntimeError("structured output model-call budget exhausted")
                model_call_budget["remaining"] -= 1

            if _stream_fn is None:
                async def budget_context_transform(messages, _cancel_event):
                    check_model_call_budget()
                    return messages
            else:
                unbudgeted_stream_fn = _stream_fn

                async def budgeted_stream_fn(model, context, options=None):
                    check_model_call_budget()
                    async for event in unbudgeted_stream_fn(model, context, options):
                        yield event

                _stream_fn = budgeted_stream_fn
        # Inner tools go through the SAME gate as the outer agent loop.
        # Without this a program spawned from a turn handed its agent raw
        # tools: no hard constraints, no authority tier, no deny rules —
        # the outer turn's restrictions vanished one level down. The inner
        # request inherits the outer one (turn_request_context), so nothing
        # here can widen what the caller was allowed to do.
        agent_tools = self._gate_inner_tools(agent_tools)
        session_max_iterations = loop_opts.get("max_iterations")
        if model_call_budget is not None:
            remaining = max(1, model_call_budget["remaining"])
            session_max_iterations = (
                min(session_max_iterations, remaining)
                if session_max_iterations is not None
                else remaining
            )
        session = AgentSession(
            model=session_model,
            tools=agent_tools,
            system_prompt=system_prompt,
            api_key=self.api_key,
            session_id=self.session_id,
            thinking_level=_current_effort.get(None) or self.thinking_level,
            tool_choice=loop_opts.get("tool_choice"),
            parallel_tool_calls=loop_opts.get("parallel_tool_calls"),
            max_iterations=session_max_iterations,
            web_search=loop_opts.get("web_search"),
            response_format=structured_format,
            stream_fn=_stream_fn,
            transform_context=budget_context_transform,
        )

        # Forward agent stream events to self.on_stream so callers (the webui
        # server) can relay partial text/tool-call updates to the frontend
        # in real time. Without this the UI only sees the final result.
        import time as _t_stream

        _stream_start = _t_stream.time()
        _unsub = None
        # Accumulate structured blocks (thinking / tool calls) for persistence.
        # This is what the UI reloads from conv history on refresh — the
        # streamed scaffold only exists live in the DOM.
        self.last_blocks = []
        _thinking_buf = {"text": ""}
        _tool_index = {}
        _agent_iteration_count = {"value": 0}
        # Subscribe even if on_stream is None so persistence accumulation
        # still runs (callers that reload history want thinking/tool blocks
        # even when they didn't watch the live stream).
        if True:

            def _elapsed() -> str:
                return f"{_t_stream.time() - _stream_start:.1f}"

            def _forward(ev):
                cb = self.on_stream
                t = getattr(ev, "type", None)
                try:
                    if t == "turn_end":
                        _agent_iteration_count["value"] += 1
                    if t == "message_update":
                        inner = getattr(ev, "assistant_message_event", None)
                        inner_type = getattr(inner, "type", None)
                        if inner_type == "text_delta":
                            if cb:
                                event = {
                                    "type": "text",
                                    "text": getattr(inner, "delta", "") or "",
                                    "elapsed": _elapsed(),
                                }
                                output_attempt = getattr(inner, "output_attempt", None)
                                if output_attempt is not None:
                                    event["output_attempt"] = output_attempt
                                cb(event)
                        elif inner_type in (
                            "structured_output_retry",
                            "structured_output_end",
                        ):
                            forward_structured_event = True
                            if inner_type == "structured_output_retry":
                                if model_call_budget is not None:
                                    deadline = model_call_budget["deadline"]
                                    forward_structured_event = not (
                                        deadline is not None
                                        and time.monotonic() >= deadline
                                    )
                                    if forward_structured_event:
                                        from openprogram.agentic_programming.function import (
                                            CancelledError as _CE,
                                            check_cancelled,
                                        )

                                        try:
                                            check_cancelled()
                                        except _CE:
                                            forward_structured_event = False
                                if model_call_budget is not None and forward_structured_event:
                                    model_call_budget["validation_repairs_used"] += 1
                                _thinking_buf["text"] = ""
                                _tool_index.clear()
                            if cb and forward_structured_event:
                                cb(inner.model_dump(exclude_none=True))
                        elif inner_type == "done":
                            if cb and getattr(
                                inner.message, "structured_output_mode", None
                            ):
                                cb({"type": "done"})
                        elif inner_type == "thinking_delta":
                            delta = getattr(inner, "delta", "") or ""
                            _thinking_buf["text"] += delta
                            if cb:
                                cb(
                                    {
                                        "type": "thinking",
                                        "text": delta,
                                        "elapsed": _elapsed(),
                                    }
                                )
                    elif t == "tool_execution_start":
                        call_id = getattr(ev, "tool_call_id", "") or ""
                        tool_name = getattr(ev, "tool_name", "?") or "?"
                        input_str = str(getattr(ev, "args", "") or "")
                        _tool_index[call_id] = {
                            "type": "tool",
                            "tool_call_id": call_id,
                            "tool": tool_name,
                            "input": input_str,
                            "result": "",
                            "is_error": False,
                            "elapsed": _elapsed(),
                        }
                        if cb:
                            cb(
                                {
                                    "type": "tool_use",
                                    "tool_call_id": call_id,
                                    "tool": tool_name,
                                    "input": input_str,
                                    "elapsed": _elapsed(),
                                }
                            )
                    elif t == "tool_execution_end":
                        result = getattr(ev, "result", "")
                        try:
                            result_str = (
                                result if isinstance(result, str) else str(result)
                            )
                        except Exception:
                            result_str = ""
                        call_id = getattr(ev, "tool_call_id", "") or ""
                        is_error = bool(getattr(ev, "is_error", False))
                        block = _tool_index.get(call_id)
                        if block is not None:
                            block["result"] = result_str
                            block["is_error"] = is_error
                            block["elapsed_end"] = _elapsed()
                        if cb:
                            cb(
                                {
                                    "type": "tool_result",
                                    "tool_call_id": call_id,
                                    "tool": getattr(ev, "tool_name", "?") or "?",
                                    "result": result_str,
                                    "is_error": is_error,
                                    "elapsed": _elapsed(),
                                }
                            )
                except Exception:
                    pass

            _unsub = session.agent.subscribe(_forward)

        # Repoint _call_id to this exec's llm node for the tool loop, NOW
        # that the prompt history is already built (history rendering above
        # needed _call_id pointing at the enclosing function frame). Any
        # tool the model calls during session.run records caller = this
        # llm node, giving the DAG a correct code → llm → code chain
        # instead of code → code. Reset in finally.
        _frame_token = None
        _llm_id = getattr(self, "_active_llm_node_id", None)
        if _llm_id is not None:
            try:
                from openprogram.agentic_programming.function import _call_id

                _frame_token = _call_id.set(_llm_id)
            except Exception:
                _frame_token = None

        try:
            session.replace_messages(history)
            final = await session.run(current)
        finally:
            if _frame_token is not None:
                try:
                    from openprogram.agentic_programming.function import _call_id

                    _call_id.reset(_frame_token)
                except Exception:
                    pass
            if _unsub is not None:
                try:
                    _unsub()
                except Exception:
                    pass
            session.close()

        # Freeze streaming blocks into `last_blocks` for persistence.
        if _thinking_buf["text"]:
            self.last_blocks.append({"type": "thinking", "text": _thinking_buf["text"]})
        for _blk in _tool_index.values():
            self.last_blocks.append(_blk)
        self.last_agent_iteration_count = _agent_iteration_count["value"]

        if final is None:
            raise RuntimeError("Agent session produced no assistant message")
        if final.stop_reason == "error":
            error = RuntimeError(
                final.error_message
                or f"Agent session ended with stop_reason='error' but no "
                f"error_message (model={final.model!r})"
            )
            if getattr(final, "error_transport_exhausted", False):
                error.transport_exhausted = True  # type: ignore[attr-defined]
            raise error

        if final.usage is not None:
            # `final.usage.input` is already net of cache reads (see
            # _shared.openai_responses — we subtract cached_tokens). Surface
            # cache separately so the UI doesn't flicker on prompt-cache hits.
            self.last_usage = {
                "input_tokens": final.usage.input,
                "output_tokens": final.usage.output,
                "total_tokens": final.usage.total_tokens,
                "cache_read": getattr(final.usage, "cache_read", 0) or 0,
                "cache_create": getattr(final.usage, "cache_write", 0) or 0,
                "breakdown": getattr(self, "_pending_breakdown", None),
            }
        if structured_format is not None:
            if final.structured_output_mode is None:
                raise RuntimeError(
                    "Agent session produced no validated structured output"
                )
            return final.structured_output
        return _assistant_text(final)


    def list_models(self) -> list[str]:
        """Return available models for this runtime.

        A registry-backed runtime (``model="provider:id"``) lists every
        enabled model under its provider namespace; anything else falls
        back to its own model id. Subclasses with a richer source (the
        Codex account endpoint, the Gemini CLI set) override.
        """
        if self.api_model is not None and getattr(self.api_model, "provider", None):
            from openprogram.providers.enabled_models import ENABLED_MODELS

            ids = sorted(
                m.id
                for m in ENABLED_MODELS.values()
                if m.provider == self.api_model.provider
            )
            if ids:
                return ids
        return [self.model] if self.model and self.model != "default" else []


    async def _async_call(
        self, content: list[dict], model: str = "default", response_format: dict = None
    ) -> Any:
        """Async version of _call(). Override for async providers."""
        if response_format is None and self._call_fn is not None:
            result = self._call_fn(content, model=model, response_format=None)
            if asyncio.iscoroutine(result):
                return await result
            return result
        if self.api_model is not None:
            return await self._async_call_via_providers(
                content,
                response_format,
                offload_sync_callable=True,
            )
        raise NotImplementedError(
            "No async LLM provider configured. Either pass an async `call` to Runtime(), "
            "or subclass Runtime and override _async_call()."
        )

