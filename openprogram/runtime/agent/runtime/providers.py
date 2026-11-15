"""Agentic runtime: providers."""
from __future__ import annotations

import asyncio







import time

from dataclasses import dataclass, field, replace

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
)
import logging

_log = logging.getLogger(__name__)

@dataclass
class _StreamCapture:
    """Blocks and counters the stream subscriber accumulates during one call."""

    thinking_buffer: dict = field(default_factory=lambda: {"text": ""})
    tool_index: dict = field(default_factory=dict)
    hidden_tool_occurrences: set = field(default_factory=set)
    iteration_count: dict = field(default_factory=lambda: {"value": 0})
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
        from openprogram.runtime.agent.turn_request import get_turn_request, inner_turn_request

        if get_turn_request() is None:
            return agent_tools
        try:
            from openprogram.runtime.permissions.approval import wrap_with_approval

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
        from openprogram.models.providers.utils.deadline import remaining

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
        if self._call_fn is None:
            # A standalone run is the profile owner's once it uses the
            # profile's model; settled before its tools are gated.
            from openprogram.runtime.agent.runtime_scope import profile_model_in_use
            profile_model_in_use()
        session_model = self._resolve_session_model()
        agent_tools, web_search_option = self._resolve_request_tools(session_model)
        request_context, request_doc_text, history, current = (
            self._render_request_messages(content, session_model)
        )
        workflow_role, system_prompt = self._compose_system_prompt(agent_tools)
        model_call_budget = _current_model_call_budget.get(None)
        structured_format = self._resolve_structured_format(
            response_format, model_call_budget
        )
        self._record_pending_breakdown(
            system_prompt, history, current, agent_tools, session_model
        )

        loop_opts = _current_loop_opts.get(None) or {}
        _stream_fn, budget_context_transform = self._resolve_stream_function(
            offload_sync_callable, model_call_budget
        )
        context_transform = self._make_context_transform(
            request_context, request_doc_text, budget_context_transform
        )
        session, session_max_iterations = self._open_agent_session(
            session_model=session_model,
            agent_tools=agent_tools,
            system_prompt=system_prompt,
            loop_opts=loop_opts,
            model_call_budget=model_call_budget,
            web_search_option=web_search_option,
            structured_format=structured_format,
            stream_fn=_stream_fn,
            context_transform=context_transform,
            # A workflow role sees only its instructions: no recalled memory
            # and no date. A model call built on a prompt of its own is told
            # the date beside its message; a host-supplied ``call`` gets the
            # content it was given.
            recall=not workflow_role,
            dated=not workflow_role and self._call_fn is None,
        )
        _unsub, capture = self._subscribe_stream_forwarding(session, model_call_budget)
        _frame_token = self._bind_llm_call_frame()

        try:
            session.replace_messages(history)
            final = await session.run(current)
        finally:
            if _frame_token is not None:
                try:
                    from openprogram.runtime.context.scope import _call_id

                    _call_id.reset(_frame_token)
                except (ValueError, RuntimeError):
                    # ContextVar.reset raises ValueError for a token from another
                    # context and RuntimeError for one already spent.
                    _log.debug("call id token already spent or foreign", exc_info=True)
            if _unsub is not None:
                try:
                    _unsub()
                except Exception:
                    # Cleanup: the session is closed right after regardless.
                    _log.debug("agent subscription not removed", exc_info=True)
            session.close()

        self._freeze_stream_blocks(final, capture)
        self._raise_for_session_error(final, model_call_budget, session_max_iterations)
        return self._session_result(final, structured_format)


    def _resolve_session_model(self):
        """Pick the model for this call: the runtime's, or a per-call override."""
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
            from openprogram.models.providers import get_model

            session_model = get_model(provider_id, model_id)
            if session_model is None:
                from openprogram.models.providers.catalog.suggestions import unknown_model_message

                raise ValueError(unknown_model_message(provider_id, model_id).replace(
                    "Unknown model", "Unknown model override", 1))
        return session_model


    def _resolve_request_tools(self, session_model):
        """Resolve the tools this request offers and its provider-side search option."""
        raw_tools = _current_tools.get(None)
        policy = _current_tool_policy.get(None) or {}
        # Unattended mode: subtract the user-question tool no matter which
        # toolset a function requested, so a background run can't block asking
        # a question nobody is there to answer. Merged into the policy deny so
        # both resolution paths below honour it.
        from openprogram.runtime.permissions.attended import denied_ask_tools as _denied_ask

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

        for constraint in policy.get("allow_constraints") or []:
            from openprogram.programs import apply_tool_policy as _apply_policy

            agent_tools = _apply_policy(agent_tools or [], allow=constraint,
                                        exposure_filter=False) or None

        # Provider-side search: an explicit ``web_search=True`` becomes the
        # request option when the session model's provider runs the search
        # itself, and the local web_search tool leaves this request's tools.
        from openprogram.runtime.agent.provider_search import resolve_web_search

        web_search_option, agent_tools = resolve_web_search(
            session_model,
            agent_tools,
            bool((_current_loop_opts.get(None) or {}).get("web_search")),
            explicit=True,
        )
        agent_tools = agent_tools or None
        return agent_tools, web_search_option


    def _render_request_messages(self, content, session_model=None):
        """Build the request context and split the prompt into history and current message."""
        # Prompt-composition: prefer DAG-derived history when a store
        # is installed; fall back to wrapping ``content`` as a single
        # UserMessage for standalone runs.
        from openprogram.runtime.context.model import Context

        request_context = Context.current()
        request_context = request_context.derive() if request_context is not None else Context()
        from openprogram.runtime.agent.workflow_site import request_doc, request_history
        # The docstring of the function that made this call, for this
        # request only. Read before the tool loop repoints the call frame.
        request_doc_text = request_doc()
        dag_messages = self._render_history_messages(content, request_context, session_model)
        if dag_messages is not None:
            history = dag_messages[:-1]
            current = dag_messages[-1]
        else:
            ctx, _sp_unused = _build_pi_context(content or [])
            history = []
            current = ctx.messages[0]
        history = request_history(request_context, history)
        return request_context, request_doc_text, history, current


    def _compose_system_prompt(self, agent_tools):
        """Compose the system prompt; a workflow role sees only its own instructions."""
        # One assembler (dag/overview.md §7): a model call inside a function
        # body gets the same project background as the chat model, so the
        # provider prefix cache is shared. ``self.system`` — the per-runtime
        # override — feeds the assembler's inline_prompt layer; the skills
        # block stays appended here because it is exec's *own* skill dirs
        # (``Runtime(skills=...)``), which the agent-level capabilities
        # component doesn't know about.
        from openprogram.runtime.agent.workflow_site import in_role_call
        workflow_role = in_role_call() is not None
        if workflow_role:
            # A workflow role sees its own instructions and nothing else.
            from openprogram.runtime.agent.runtime.shared import _current_instructions
            system_prompt = _current_instructions.get() or ""
        else:
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
        return workflow_role, system_prompt


    def _resolve_structured_format(self, response_format, model_call_budget):
        """Resolve the structured output format, less the repairs the budget already spent."""
        structured_format = _current_response_format.get(None)
        if structured_format is None and response_format is not None:
            from openprogram.models.providers.structured_output import (
                normalize_response_format,
            )

            structured_format = normalize_response_format(response_format)
        if structured_format is not None and model_call_budget is not None:
            structured_format = replace(
                structured_format,
                max_validation_retries=max(
                    0,
                    structured_format.max_validation_retries
                    - model_call_budget["validation_repairs_used"],
                ),
            )
        return structured_format


    def _record_pending_breakdown(
        self, system_prompt, history, current, agent_tools, session_model
    ):
        """Record the input breakdown, tool names and system prompt for the call's node."""
        # 现算输入分类分解 + 采集工具名单（论文仓库 spec §5 ①③）。best-effort，
        # 算失败置 None/[]，绝不影响 LLM 调用。breakdown 挂 last_usage（见下），
        # 工具名单跟着 exec 收尾的 usage→DAG 节点通道进 history.metadata。
        self._pending_breakdown = None
        self._pending_tool_names = []
        self._pending_system_prompt = ""
        try:
            from openprogram.runtime.context.breakdown import compute_call_breakdown
            from openprogram.runtime.context.tokens import real_context_window

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
    def _resolve_stream_function(self, offload_sync_callable, model_call_budget):
        """Pick the stream function and wrap it, or the context, in the model-call budget check."""
        # stream_fn injection: a per-call override (set by exec via the
        # _current_stream_fn contextvar, used by the dispatcher / tests) wins;
        # otherwise the runtime's own _stream_fn (set when Runtime(call=fn)
        # wraps a callable into a CallableModel). None → real provider.
        _stream_fn = _current_stream_fn.get(None) or getattr(self, "_stream_fn", None)
        if offload_sync_callable and self._call_fn is not None:
            from openprogram.runtime.agent.runtime.callable_model import make_callable_stream_fn

            _stream_fn = make_callable_stream_fn(self._call_fn, offload_sync=True)
        budget_context_transform = None
        if model_call_budget is not None:
            def check_model_call_budget(messages) -> None:
                if model_call_budget.get("started"):
                    deadline = model_call_budget["deadline"]
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError("structured output model-call deadline expired")
                    from openprogram.host.cancellation import (
                        CancelledError as _CE,
                        check_cancelled,
                    )
                    try:
                        check_cancelled()
                    except _CE:
                        from openprogram.models.providers.utils.errors import ExecInterrupt
                        raise ExecInterrupt("cancelled") from None
                # A new trailing tool-result round permits the next normal
                # Agent turn. Credit it once: retries see the same call ids
                # and must still consume the shared failure allowance.
                tool_ids = []
                for message in reversed(messages):
                    if getattr(message, "role", None) != "toolResult":
                        break
                    tool_ids.append(message.tool_call_id)
                round_key = tuple(sorted(tool_ids))
                credited = model_call_budget.setdefault("credited_tool_rounds", set())
                if model_call_budget.get("started") and round_key and round_key not in credited:
                    credited.add(round_key)
                    model_call_budget["remaining"] += 1
                # Transport retries have their own allowance on the shared
                # recovery state; a reconnect must not spend an answer attempt.
                from openprogram.models.providers.utils.recovery import current_recovery
                recovery = current_recovery.get()
                if recovery is not None:
                    seen = model_call_budget.get("transport_reserves_seen", 0)
                    if recovery.transport_reserves > seen:
                        model_call_budget["remaining"] += recovery.transport_reserves - seen
                    model_call_budget["transport_reserves_seen"] = recovery.transport_reserves
                if model_call_budget["remaining"] <= 0:
                    raise RuntimeError("structured output model-call budget exhausted")
                model_call_budget["started"] = True
                model_call_budget["remaining"] -= 1

            if _stream_fn is None:
                async def budget_context_transform(messages, _cancel_event):
                    check_model_call_budget(messages)
                    return messages
            else:
                unbudgeted_stream_fn = _stream_fn

                async def budgeted_stream_fn(model, context, options=None):
                    check_model_call_budget(context.messages)
                    async for event in unbudgeted_stream_fn(model, context, options):
                        yield event

                _stream_fn = budgeted_stream_fn
        return _stream_fn, budget_context_transform


    def _make_context_transform(self, request_context, request_doc_text, previous_transform):
        """Build the per-request context transform that adds context blocks and records them."""
        async def context_transform(messages, cancel_event):
            # Resolve on each provider request, before its budget checks.
            rendered_blocks = request_context.render_blocks()
            named_blocks = [block.model_dump() for block in rendered_blocks]
            if request_doc_text:
                from openprogram.models.providers.types import TextContent
                rendered_blocks = [TextContent(text=request_doc_text), *rendered_blocks]
            result = request_context.add_to_messages(messages, blocks=rendered_blocks)
            if previous_transform is not None:
                result = await previous_transform(result, cancel_event)
            # Save the values used by this request, not provider callables.
            from openprogram.state.store import _store
            store = _store.get()
            node_id = getattr(self, "_active_llm_node_id", None)
            if store is not None and node_id:
                node = store.load().nodes.get(node_id)
                if node is not None:
                    requests = list((node.metadata or {}).get("context_requests") or [])
                    requests.append({"blocks": [dict(block, text=block["text"][:8000]) for block in named_blocks]})
                    request_content = next((message.model_dump(mode="json")
                                            for message in reversed(result)
                                            if getattr(message, "role", None) == "user"), None)
                    store.update(node_id, metadata={"context_requests": requests,
                                                    "request_content": request_content})
            return result
        return context_transform


    def _open_agent_session(
        self,
        *,
        session_model,
        agent_tools,
        system_prompt,
        loop_opts,
        model_call_budget,
        web_search_option,
        structured_format,
        stream_fn,
        context_transform,
        recall=True,
        dated=False,
    ):
        """Gate the tools and open the AgentSession that runs this call."""
        from openprogram.runtime.agent.loop.session import AgentSession
        # Inner tools go through the SAME gate as the outer agent loop.
        # Without this a program spawned from a turn handed its agent raw
        # tools: no hard constraints, no authority tier, no deny rules —
        # the outer turn's restrictions vanished one level down. The inner
        # request inherits the outer one (turn_request_context), so nothing
        # here can widen what the caller was allowed to do.
        agent_tools = self._gate_inner_tools(agent_tools)
        session_max_iterations = loop_opts.get("max_iterations")
        if model_call_budget is not None and not agent_tools:
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
            session_id=self._provider_session_id(session_model, system_prompt, agent_tools),
            thinking_level=_current_effort.get(None) or self.thinking_level,
            tool_choice=loop_opts.get("tool_choice"),
            parallel_tool_calls=loop_opts.get("parallel_tool_calls"),
            max_iterations=session_max_iterations,
            stop_after_tool_round=bool(loop_opts.get("stop_after_tool_round")),
            web_search=web_search_option,
            response_format=structured_format,
            stream_fn=stream_fn,
            transform_context=context_transform,
            recall=recall,
            dated=dated,
        )
        return session, session_max_iterations


    def _provider_session_id(self, model, system_prompt, tools) -> str:
        """The id the provider keys this call's prompt cache (and an OpenCode
        session header) by; the derivation is shared with calls made straight
        to an adapter (``providers.shared.cache_key``).

        A Runtime is usually made for one call (a title, a topic sort, a
        program run), so its own random id would give every call a cache
        route of its own and none would reuse the prefix the last one sent.
        Calls with the same model, system prompt and tools share that
        prefix, so they share an id derived from it. An id a caller set on
        the Runtime is used as given.
        """
        if self.session_id != getattr(self, "_own_session_id", self.session_id):
            return self.session_id
        from openprogram.models.providers.shared.cache_key import prefix_cache_key

        return prefix_cache_key(getattr(model, "provider", None), getattr(model, "id", None),
                                system_prompt, [getattr(tool, "name", None) for tool in tools or []])


    def _subscribe_stream_forwarding(self, session, model_call_budget):
        """Subscribe to the session's events: relay them live and accumulate blocks to persist."""
        # Forward agent stream events to self.on_stream so callers (the webui
        # server) can relay partial text/tool-call updates to the frontend
        # in real time. Without this the UI only sees the final result.
        import time as _t_stream

        _stream_start = _t_stream.time()
        # Accumulate structured blocks (thinking / tool calls) for persistence.
        # This is what the UI reloads from conv history on refresh — the
        # streamed scaffold only exists live in the DOM.
        self.last_blocks = []
        capture = _StreamCapture()
        _thinking_buf = capture.thinking_buffer
        _tool_index = capture.tool_index
        _hidden_tool_occurrences = capture.hidden_tool_occurrences
        _agent_iteration_count = capture.iteration_count

        def _elapsed() -> str:
            return f"{_t_stream.time() - _stream_start:.1f}"

        def _forward_server_tool(inner, inner_type, cb, _project):
            """Project a provider-executed search as a tool step.

            The provider already ran it: no local execution, but the
            same ``tool_use`` / ``tool_result`` events, nested DAG node
            and persisted block a function tool gets, marked
            ``provider_executed``.
            """
            from openprogram.runtime.agent import provider_search as ps

            block = ps.server_tool_block_at(inner)
            if block is None:
                return
            call_id = str(ps._get(block, "id") or "")
            if not call_id:
                return
            tool_name = ps.server_tool_name(block)
            input_str = ps.server_tool_input(block)
            arguments = {"query": ps.server_tool_query(block)}
            parent_node_id = getattr(self, "_active_llm_node_id", None) or ""
            entry = _tool_index.get(call_id)
            if entry is None:
                ref_node_id = self._ensure_nested_tool_node(
                    parent_node_id=parent_node_id,
                    tool_call_id=call_id,
                    occurrence_id=call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    expose="full",
                )
                _mark_provider_executed(ref_node_id)
                entry = {
                    "type": "tool",
                    "tool_call_id": call_id,
                    "occurrence_id": call_id,
                    "tool": tool_name,
                    "input": input_str,
                    "expose": "full",
                    "provider_executed": True,
                    "result": "",
                    "is_error": False,
                    "ref_node_id": ref_node_id,
                    "group_id": "",
                    "elapsed": _elapsed(),
                }
                _tool_index[call_id] = entry
                tool_ev = {
                    "type": "tool_use",
                    "tool_call_id": call_id,
                    "occurrence_id": call_id,
                    "tool": tool_name,
                    "input": input_str,
                    "expose": "full",
                    "provider_executed": True,
                    "node_id": ref_node_id,
                    "ref_node_id": ref_node_id,
                    "group_id": "",
                    "elapsed": _elapsed(),
                }
                _project(tool_ev)
                if cb:
                    cb(tool_ev)
            if inner_type != "servertool_end":
                return
            ref_node_id = entry.get("ref_node_id") or ""
            if ps.server_tool_query(block) and entry.get("input") != input_str:
                # The query of a provider search often arrives at its end.
                entry["input"] = input_str
                _set_node_input(ref_node_id, arguments)
            result_str = ps.server_tool_result(block)
            is_error = ps.server_tool_failed(block)
            outcome = "failed" if is_error else "completed"
            entry["result"] = result_str
            entry["is_error"] = is_error
            entry["elapsed_end"] = _elapsed()
            self._finish_nested_tool_node(
                parent_node_id=parent_node_id,
                tool_call_id=call_id,
                occurrence_id=call_id,
                node_id=ref_node_id,
                result=result_str,
                is_error=is_error,
                outcome=outcome,
            )
            result_ev = {
                "type": "tool_result",
                "tool_call_id": call_id,
                "occurrence_id": call_id,
                "tool": tool_name,
                "input": entry["input"],
                "result": result_str,
                "expose": "full",
                "provider_executed": True,
                "is_error": is_error,
                "outcome": outcome,
                "node_id": ref_node_id,
                "ref_node_id": ref_node_id,
                "group_id": "",
                "elapsed": _elapsed(),
            }
            _project(result_ev)
            if cb:
                cb(result_ev)

        def _store_update(node_id, **fields):
            if not node_id:
                return
            try:
                from openprogram.state.store import _store

                store = _store.get()
                if store is not None:
                    store.update(node_id, **fields)
            except Exception:
                # Event callback inside the agent loop: a failed write must not stop it.
                _log.warning("node %s not updated", node_id, exc_info=True)

        def _mark_provider_executed(node_id):
            # History rendering skips these: the provider's own block in
            # the reply carries the search, not a local ToolCall.
            _store_update(node_id, metadata={"provider_executed": True})

        def _set_node_input(node_id, arguments):
            _store_update(node_id, input=arguments)

        def _call_stream():
            try:
                return getattr(self._exec_state(), "call_stream", None)
            except Exception:
                return None

        def _project(event_dict):
            st = _call_stream()
            if st is not None:
                try:
                    from openprogram.runtime.agent.runtime.execution_stream.adapter import (
                        project_provider_event,
                    )
                    project_provider_event(st, event_dict)
                except Exception:
                    # Projection feeds the live view only; the turn continues without it.
                    _log.warning("provider event not projected to call stream", exc_info=True)

        def _on_turn_end(ev):
            _agent_iteration_count["value"] += 1
            # A turn that produced tool results continues the same
            # logical attempt. Only a provider turn with no tool
            # results closes the attempt; explicit repair and
            # transport retry paths create attempts themselves.
            tool_results = getattr(ev, "tool_results", None) or []
            st = _call_stream()
            if st is not None and not tool_results:
                try:
                    st.finish_attempt(
                        status="completed", validation="pending"
                    )
                except Exception:
                    # Attempt bookkeeping on the stream; the turn continues without it.
                    _log.warning("stream attempt not finished", exc_info=True)

        def _on_message_update(ev, cb):
            inner = getattr(ev, "assistant_message_event", None)
            inner_type = getattr(inner, "type", None)
            if inner_type == "text_delta":
                event = {
                    "type": "text",
                    "text": getattr(inner, "delta", "") or "",
                    "elapsed": _elapsed(),
                }
                output_attempt = getattr(inner, "output_attempt", None)
                if output_attempt is not None:
                    event["output_attempt"] = output_attempt
                _project(event)
                if cb:
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
                            from openprogram.host.cancellation import (
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
                    # These are completed tool receipts, not invalid
                    # generated text. Workflow verification still
                    # needs them after a response-only repair.
                    self.last_blocks.extend(dict(block) for block in _tool_index.values())
                    _tool_index.clear()
                    _hidden_tool_occurrences.clear()
                if forward_structured_event:
                    dumped = inner.model_dump(exclude_none=True)
                    _project(dumped)
                    if cb:
                        cb(dumped)
            elif inner_type == "retry" and getattr(inner, "restart", False):
                # The request is sent again after a transport failure and
                # what it streamed is void: the call stream starts a new
                # attempt, as it does for a retry made by the invocation.
                _thinking_buf["text"] = ""
                try:
                    from openprogram.runtime.agent.runtime.execution_stream.adapter import (
                        note_transport_retry,
                    )
                    note_transport_retry(self._exec_state(), reason="transport_retry")
                except Exception:
                    # Attempt bookkeeping on the stream; the re-send itself proceeds.
                    _log.debug("stream re-send not noted on call stream", exc_info=True)
            elif inner_type == "done":
                _project({"type": "done"})
                if cb and getattr(
                    inner.message, "structured_output_mode", None
                ):
                    cb({"type": "done"})
            elif inner_type in ("servertool_start", "servertool_end"):
                _forward_server_tool(inner, inner_type, cb, _project)
            elif inner_type == "text_end":
                from openprogram.runtime.agent.provider_search import (
                    text_end_citations,
                )

                citations = text_end_citations(inner)
                if citations and cb:
                    cb({
                        "type": "text_end",
                        "content_index": getattr(inner, "content_index", None),
                        "citations": citations,
                        "elapsed": _elapsed(),
                    })
            elif inner_type == "thinking_delta":
                delta = getattr(inner, "delta", "") or ""
                _thinking_buf["text"] += delta
                think_ev = {
                    "type": "thinking",
                    "text": delta,
                    "elapsed": _elapsed(),
                }
                _project(think_ev)
                if cb:
                    cb(think_ev)

        def _on_tool_execution_start(ev, cb):
            call_id = getattr(ev, "tool_call_id", "") or ""
            occurrence_id = getattr(ev, "occurrence_id", None) or call_id
            tool_name = getattr(ev, "tool_name", "?") or "?"
            raw_args = getattr(ev, "args", "") or ""
            input_str = str(raw_args)
            group_id = getattr(ev, "group_id", None) or ""
            expose = getattr(ev, "expose", None) or "full"
            # Hidden tools have no user-visible execution row. Do
            # this before creating the index or projecting the
            # event so the name/status cannot escape as an empty
            # ref_node_id placeholder.
            if expose == "hidden":
                _hidden_tool_occurrences.add(occurrence_id)
                return
            parent_node_id = getattr(self, "_active_llm_node_id", None) or ""
            ref_node_id = self._ensure_nested_tool_node(
                parent_node_id=parent_node_id,
                tool_call_id=call_id,
                occurrence_id=occurrence_id,
                tool_name=tool_name,
                arguments=raw_args,
                expose=expose,
            )
            _tool_index[occurrence_id] = {
                "type": "tool",
                "tool_call_id": call_id,
                "occurrence_id": occurrence_id,
                "tool": tool_name,
                "input": "" if expose == "hidden" else input_str,
                "expose": expose,
                "result": "",
                "is_error": False,
                "ref_node_id": ref_node_id,
                "group_id": group_id,
                "elapsed": _elapsed(),
            }
            tool_ev = {
                "type": "tool_use",
                "tool_call_id": call_id,
                "occurrence_id": occurrence_id,
                "tool": tool_name,
                "input": "" if expose == "hidden" else input_str,
                "expose": expose,
                "node_id": ref_node_id,
                "ref_node_id": ref_node_id,
                "group_id": group_id,
                "elapsed": _elapsed(),
            }
            _project(tool_ev)
            if cb:
                cb(tool_ev)

        def _on_tool_execution_end(ev, cb):
            result = getattr(ev, "result", "")
            try:
                result_str = (
                    result if isinstance(result, str) else str(result)
                )
            except Exception:
                result_str = ""
            call_id = getattr(ev, "tool_call_id", "") or ""
            occurrence_id = getattr(ev, "occurrence_id", None) or call_id
            if occurrence_id in _hidden_tool_occurrences:
                _hidden_tool_occurrences.discard(occurrence_id)
                return
            is_error = bool(getattr(ev, "is_error", False))
            block = _tool_index.get(occurrence_id)
            event_expose = (block or {}).get("expose") or getattr(ev, "expose", None) or "full"
            if event_expose == "hidden":
                return
            if block is not None:
                block["result"] = "" if event_expose == "hidden" else result_str
                block["is_error"] = is_error
                block["elapsed_end"] = _elapsed()
            parent_node_id = getattr(self, "_active_llm_node_id", None) or ""
            ref_node_id = (block or {}).get("ref_node_id", "")
            if not ref_node_id:
                ref_node_id = self._ensure_nested_tool_node(
                    parent_node_id=parent_node_id,
                    tool_call_id=call_id,
                    occurrence_id=occurrence_id,
                    tool_name=getattr(ev, "tool_name", "?") or "?",
                    arguments=None,
                    expose=event_expose,
                )
            from openprogram.models.providers.shared.tool_loads import durable_details
            self._finish_nested_tool_node(
                parent_node_id=parent_node_id,
                tool_call_id=call_id,
                occurrence_id=occurrence_id,
                node_id=ref_node_id,
                result="" if event_expose == "hidden" else result_str,
                is_error=is_error,
                outcome=getattr(ev, "outcome", None),
                details=durable_details(getattr(result, "details", None)),
            )
            group_id = (block or {}).get("group_id", "")
            result_ev = {
                "type": "tool_result",
                "tool_call_id": call_id,
                "occurrence_id": occurrence_id,
                "tool": getattr(ev, "tool_name", "?") or "?",
                "result": "" if event_expose == "hidden" else result_str,
                "expose": event_expose,
                "is_error": is_error,
                "outcome": getattr(ev, "outcome", None),
                "node_id": ref_node_id,
                "ref_node_id": ref_node_id,
                "group_id": group_id,
                "elapsed": _elapsed(),
            }
            _project(result_ev)
            if cb:
                cb(result_ev)

        def _forward(ev):
            cb = self.on_stream
            t = getattr(ev, "type", None)

            try:
                if t == "turn_end":
                    _on_turn_end(ev)
                if t == "message_update":
                    _on_message_update(ev, cb)
                elif t == "tool_execution_start":
                    _on_tool_execution_start(ev, cb)
                elif t == "tool_execution_end":
                    _on_tool_execution_end(ev, cb)
            except Exception:
                # Subscriber callback: a forwarding failure must not break the agent loop.
                _log.warning("agent event not forwarded", exc_info=True)

        # Subscribe even if on_stream is None so persistence accumulation
        # still runs (callers that reload history want thinking/tool blocks
        # even when they didn't watch the live stream).
        _unsub = session.agent.subscribe(_forward)
        return _unsub, capture


    def _bind_llm_call_frame(self):
        """Point _call_id at this call's llm node for the tool loop; returns the reset token."""
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
                from openprogram.runtime.context.scope import _call_id

                _frame_token = _call_id.set(_llm_id)
            except Exception:
                _frame_token = None
        return _frame_token


    def _freeze_stream_blocks(self, final, capture):
        """Freeze the streamed thinking, tool and citation blocks into last_blocks."""
        _thinking_buf = capture.thinking_buffer
        _tool_index = capture.tool_index
        # Freeze streaming blocks into `last_blocks` for persistence.
        if _thinking_buf["text"]:
            self.last_blocks.append({"type": "thinking", "text": _thinking_buf["text"]})
        for _blk in _tool_index.values():
            self.last_blocks.append(_blk)
        if final is not None:
            from openprogram.runtime.agent import provider_search as _ps

            for _content_block in final.content or []:
                if _ps.is_server_tool_block(_content_block):
                    # A search the stream did not announce still shows.
                    _card = _ps.server_tool_card(_content_block)
                    if _card["tool_call_id"] and _card["tool_call_id"] not in _tool_index:
                        self.last_blocks.append(_card)
                elif getattr(_content_block, "type", None) == "text":
                    _citations = _ps.text_citations(_content_block)
                    if _citations:
                        self.last_blocks.append({
                            "type": "text",
                            "text": getattr(_content_block, "text", "") or "",
                            "citations": _citations,
                        })
        self.last_agent_iteration_count = capture.iteration_count["value"]
    def _raise_for_session_error(self, final, model_call_budget, session_max_iterations):
        """Raise when the session produced no message or ended in an error."""
        if final is None:
            raise RuntimeError("Agent session produced no assistant message")
        if final.stop_reason == "error":
            message = (final.error_message
                or f"Agent session ended with stop_reason='error' but no "
                f"error_message (model={final.model!r})")
            inferred_unknown = (
                final.error_reason == "unknown"
                and final.error_retryable is False
                and final.error_retryable_inferred
                and not final.error_transport_exhausted
            )
            if not inferred_unknown and (final.error_retryable is not None or final.error_reason is not None):
                from openprogram.models.providers.utils.errors import ErrorReason, LLMError

                error = LLMError(
                    message=message,
                    reason=ErrorReason(final.error_reason or "unknown"),
                    retryable=bool(final.error_retryable),
                    retry_after_s=final.error_retry_after_s,
                    provider=final.provider,
                    model=final.model,
                    failure=final.error_cause,
                )
            else:
                error = RuntimeError(message)
            if (model_call_budget is not None and session_max_iterations is not None
                    and self.last_agent_iteration_count >= session_max_iterations):
                # An explicit Agent iteration cap is not a transport failure;
                # restarting the session would repeat already completed tools.
                error.retryable = False  # type: ignore[attr-defined]
            if getattr(final, "error_transport_exhausted", False):
                error.transport_exhausted = True  # type: ignore[attr-defined]
            if message == "ValueError: Protected request content exceeds the model input budget":
                error.retryable = False
            raise error
    def _session_result(self, final, structured_format):
        """Record the call's usage and return its text or validated structured output."""
        if final.usage is not None:
            # `final.usage.input` is already net of cache reads (see
            # shared.openai_responses — we subtract cached_tokens). Surface
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
            from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS

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
