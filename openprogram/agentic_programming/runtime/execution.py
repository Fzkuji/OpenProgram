"""Agentic runtime: execution."""
from __future__ import annotations

import asyncio



import json




import time


from typing import Any, Optional

from openprogram.providers.structured_output import StructuredOutputError

from .shared import (
    _ExecCallState,
    _build_llm_error,
    _current_call_model,
    _current_direct_content,
    _current_effort,
    _current_exec_state,
    _current_loop_opts,
    _current_model_call_budget,
    _current_response_format,
    _current_stream_fn,
    _current_tool_policy,
    _current_tools,
    _default_exec_timeout_s,
    _fire_on_retry,
    _is_permanent_error,
    _retry_sleep_seconds,
)

class ExecutionOperations:
    def exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
        tools: Optional[list] = None,
        toolset: Optional[str] = None,
        tools_source: Optional[str] = None,
        tools_allow: Optional[list[str]] = None,
        tools_deny: Optional[list[str]] = None,
        tool_choice: Any = "auto",
        parallel_tool_calls: Optional[bool] = None,
        max_iterations: int = 20,
        choices: Any = None,
        timeout_s: Optional[float] = None,
        on_retry: Optional["Callable[[RetryInfo], None]"] = None,
        web_search: bool = False,
        stream_fn: Any = None,
        effort: Optional[str] = None,
        execution_kind: str = "agent",
    ) -> Any:
        """
        Call the LLM. Appends a ModelCall node to the DAG.

        Args:
            content:          List of content blocks. Each block is a dict:
                              {"type": "text", "text": "..."}
                              {"type": "image", "path": "screenshot.png"}
                              {"type": "audio", "path": "recording.wav"}
                              {"type": "file", "path": "data.csv"}

            context:          Optional text prefix for the legacy ``_call``
                              path (``call=`` callable / subclass override).
                              Ignored on the default AgentSession path,
                              which builds history from the DAG.

            response_format:  JSON Schema or JsonSchemaOutput envelope. The
                              final reply is strictly parsed and validated.

            model:            Override the default model for this call.

            tools:            Optional list of tools the LLM may call. Each
                              entry may be an AgentTool, an @agentic_function, a
                              {"spec":..., "execute":...} dict, or an object
                              with .spec and .execute attributes. When set,
                              runs a tool loop until the model returns plain
                              text (or max_iterations is hit).

            tool_choice:      "auto" (default), "required", "none", or
                              {"type":"function","name":"X"} to force a
                              specific tool.

            parallel_tool_calls: explicitly allow or forbid multiple tool calls
                                 in one turn. ``None`` leaves the provider default.

            max_iterations:   safety cap on the tool loop (default 20).

            choices:          When set, constrains how the turn *finishes*.
                              The model runs the normal turn (reasoning,
                              tool calls — whatever ``tools`` allows), but
                              its final reply must pick one option from
                              ``choices``. The pick is then resolved: a
                              picked function is run and its return value
                              handed back, a picked value is returned
                              as-is. Same option forms as
                              ``decision.make`` — a dict ``{name: handler}``
                              or a list of callables / option tuples.

            timeout_s:        Wall-clock deadline for the entire exec()
                              call, **including all retry sleeps**.
                              When the elapsed time reaches the deadline,
                              raises ``LLMError(reason=TIMEOUT,
                              retryable=False)`` instead of starting
                              another attempt or sleeping further. The
                              currently-running ``_call`` is also bounded
                              when it's async (sync ``_call`` runs to
                              completion before the check, so very-long
                              synchronous calls can overshoot — set
                              ``max_retries=1`` if that matters).
                              ``None`` (default): no wall-clock cap,
                              behaviour is the same as before this knob
                              existed. Separate from ``max_retries`` —
                              ``max_retries`` is a count cap, this is a
                              time cap; they compose.

            on_retry:         Optional ``Callable[[RetryInfo], None]``.
                              Fired immediately before each backoff
                              sleep — i.e. once per *failed* attempt
                              that has a retry queued behind it. Not
                              fired for the terminal failure that
                              exhausts the budget (that raises
                              ``LLMError`` instead). Use this to emit
                              structured retry logs, drive a circuit
                              breaker, or accumulate per-provider
                              failure metrics without subclassing
                              Runtime. Exceptions inside the callback
                              are swallowed so a broken hook never
                              prevents the retry loop from making
                              progress.

            effort:           Per-call reasoning/thinking effort. It is
                              passed to AgentSession for this call only and
                              does not mutate ``Runtime.thinking_level``.

            execution_kind:   DAG observability label. ``llm`` is set by the
                              public one-request primitive; tool-loop-capable
                              Runtime calls default to ``agent``.

        Returns:
            ``str`` for ordinary calls; a Python JSON value for structured
            output calls. When ``choices`` is set, returns the resolved
            decision instead.
        """
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

        structured_format = None
        if response_format is not None:
            from openprogram.providers.structured_output import (
                normalize_response_format,
            )

            structured_format = normalize_response_format(response_format)
        # Cancel check — lets long-running loops inside one function also abort.
        from openprogram.agentic_programming.function import _run_pre_invocation_hooks

        _run_pre_invocation_hooks()

        # Handle plain string input
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        # --- Choice-constrained finish ---
        # When `choices` is set, the model runs a normal turn but must end
        # with a pick from the menu. Append the menu + finish instruction
        # to the prompt now; the reply is resolved against it below.
        _decision_menu = _decision_values = None
        if choices is not None:
            from openprogram.agentic_programming.decision import (
                DECISION_FINISH_INSTRUCTION,
                _normalize_options,
                render_options,
            )

            _decision_menu, _decision_values = _normalize_options(choices)
            content = list(content) + [
                {
                    "type": "text",
                    "text": DECISION_FINISH_INSTRUCTION
                    + render_options(_decision_menu),
                }
            ]

        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Build call input ---
        # Single path: _call → _call_via_providers builds its own message
        # history from the DAG (via _render_history_messages) and prepends
        # the system prompt + skills block there. ``content`` is the current
        # turn; pass it through as-is. (The old legacy-call branch that
        # text-merged system_text + context here is gone — Runtime(call=fn)
        # now flows through the same provider path via a CallableModel.)
        call_input = content

        # --- Call the LLM (with retry) ---
        # ``tools=[]`` means "no tools" and must be distinguished from
        # "not specified" (None), which falls back to the default toolset.
        tools_token = _current_tools.set(tools) if tools is not None else None
        stream_fn_token = (
            _current_stream_fn.set(stream_fn) if stream_fn is not None else None
        )
        effort_token = _current_effort.set(effort) if effort else None
        call_model_token = _current_call_model.set(use_model)
        direct_content_token = (
            _current_direct_content.set(content)
            if execution_kind == "llm" and self._call_fn is not None
            else None
        )
        _policy_kwargs = {
            "toolset": toolset,
            "source": tools_source,
            "allow": tools_allow,
            "deny": tools_deny,
        }
        _policy_kwargs = {k: v for k, v in _policy_kwargs.items() if v is not None}
        policy_token = (
            _current_tool_policy.set(
                {**(_current_tool_policy.get(None) or {}), **_policy_kwargs}
            )
            if _policy_kwargs
            else None
        )
        # Loop options — only explicit values travel. In particular, structured
        # output negotiation must distinguish an explicit True (which conflicts
        # with the single hidden submission tool) from the provider default.
        _loop_opts = {}
        if tool_choice is not None and tool_choice != "auto":
            _loop_opts["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            _loop_opts["parallel_tool_calls"] = parallel_tool_calls
        if max_iterations is not None:
            _loop_opts["max_iterations"] = max_iterations
        if web_search:
            _loop_opts["web_search"] = True
        loop_opts_token = _current_loop_opts.set(_loop_opts) if _loop_opts else None
        response_format_token = None
        model_call_budget_token = None
        reply = None
        _exec_start = time.monotonic()
        if not (timeout_s and timeout_s > 0):
            timeout_s = _default_exec_timeout_s()
        _deadline = _exec_start + timeout_s if (timeout_s and timeout_s > 0) else None
        # Publish the deadline so the provider's INNER stream-retry loop and
        # the SSE parser honour the SAME wall-clock budget — otherwise the
        # nested loops multiply (max_attempts × max_retries) with nobody
        # capping the total. See docs/design/providers/reliability/error-and-timeout-mechanism.html.
        from openprogram.providers.utils.errors import ExecInterrupt
        from openprogram.providers.utils import deadline as _dl

        if structured_format is not None:
            response_format_token = _current_response_format.set(structured_format)
            model_call_budget_token = _current_model_call_budget.set(
                {
                    "limit": self.max_retries,
                    "remaining": self.max_retries,
                    "validation_repairs_used": 0,
                    "deadline": _deadline,
                }
            )
        _deadline_token = _dl.set_deadline(_deadline)
        _exec_token = _current_exec_state.set(_ExecCallState())
        _llm_node_id = None
        _llm_closed = False
        attempts_used = 0
        try:
            # One exec == one llm node. Open it now (status=running); the
            # tool loop inside _call_via_providers repoints _call_id to this
            # node (after the prompt is built) so the model's tool calls
            # attribute here. Closed on success/failure below.
            _llm_node_id = self._open_model_call_node(
                model=use_model,
                execution_kind=execution_kind,
                content_text=content_text,
            )
            self._active_llm_node_id = _llm_node_id
            self.last_agent_iteration_count = 0
            errors: list[str] = []
            while attempts_used < self.max_retries:
                # Pre-attempt deadline check: previous sleep or _call
                # may have already crossed the line, in which case we
                # don't even start another attempt.
                if _deadline is not None and time.monotonic() >= _deadline:
                    from openprogram.providers.utils.errors import ErrorReason as _ER

                    cause = TimeoutError(
                        f"exec() timed out after {timeout_s}s "
                        f"({attempts_used} attempt(s))"
                    )
                    raise _build_llm_error(
                        cause=cause,
                        attempts=max(1, attempts_used),
                        elapsed_s=time.monotonic() - _exec_start,
                        content=content,
                        model=use_model,
                        provider=getattr(self, "provider", None),
                        history=errors,
                        permanent=True,
                        override_reason=_ER.TIMEOUT,
                    ) from cause

                from openprogram.agentic_programming.function import (
                    CancelledError as _CE,
                    check_cancelled,
                )

                try:
                    check_cancelled()
                except _CE:
                    raise ExecInterrupt("cancelled") from None

                try:
                    attempts_used += 1
                    raw_reply = self._call(
                        call_input, model=use_model, response_format=response_format
                    )
                    reply = raw_reply
                    dag_reply = (
                        json.dumps(
                            raw_reply,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if structured_format is not None
                        else raw_reply
                    )
                    agent_iteration_count = (
                        0 if execution_kind == "llm"
                        else int(getattr(self, "last_agent_iteration_count", 0) or 0)
                    )
                    provider_request_count = max(
                        attempts_used,
                        attempts_used - 1
                        + int(getattr(self, "last_agent_iteration_count", 0) or 0),
                    )
                    self._close_model_call_node(
                        _llm_node_id,
                        reply=dag_reply,
                        execution_kind=execution_kind,
                        provider_request_count=provider_request_count,
                        agent_iteration_count=agent_iteration_count,
                    )
                    _llm_closed = True
                    break
                except ExecInterrupt:
                    raise  # caller hard-stop — bypass the retry layer
                except (TypeError, NotImplementedError):
                    raise  # Programming errors — don't retry
                except ImportError:
                    # A subsystem this path needs isn't installed (e.g. an
                    # embedded install without the agent/tool extra). Retrying
                    # can't make a module appear, and the default budget would
                    # otherwise spend ~50s of backoff before reporting it.
                    raise
                except StructuredOutputError:
                    raise
                except Exception as e:
                    model_call_budget = _current_model_call_budget.get(None)
                    if model_call_budget is not None:
                        attempts_used = max(
                            attempts_used,
                            self.max_retries - model_call_budget["remaining"],
                        )
                    errors.append(f"Attempt {attempts_used}: {type(e).__name__}: {e}")
                    permanent = _is_permanent_error(e)
                    # The provider already exhausted its OWN transport-retry
                    # budget on this error — don't let exec re-retry it with a
                    # fresh max_retries budget (that's the 3×6 multiplication).
                    transport_done = bool(getattr(e, "transport_exhausted", False))
                    elapsed = time.monotonic() - _exec_start
                    # If the _call itself ran us past the deadline (e.g. the
                    # inner loop gave up exactly at the budget), surface it as
                    # TIMEOUT rather than the incidental transport cause.
                    timed_out = _deadline is not None and time.monotonic() >= _deadline
                    if (
                        permanent
                        or transport_done
                        or timed_out
                        or attempts_used >= self.max_retries
                    ):
                        from openprogram.providers.utils.errors import (
                            ErrorReason as _ER,
                        )

                        raise _build_llm_error(
                            cause=e,
                            attempts=attempts_used,
                            elapsed_s=elapsed,
                            content=content,
                            model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=permanent or timed_out,
                            override_reason=_ER.TIMEOUT if timed_out else None,
                        ) from e
                    # Honor server-supplied Retry-After when the
                    # underlying provider attached it to the exception.
                    retry_after_s = getattr(e, "retry_after_s", None)
                    sleep_s = _retry_sleep_seconds(attempts_used - 1, retry_after_s)

                    # Would sleeping cross the deadline? If yes, give
                    # up now as TIMEOUT — don't waste wall-clock on a
                    # backoff we'll never get to consume.
                    if (
                        _deadline is not None
                        and (time.monotonic() + sleep_s) >= _deadline
                    ):
                        from openprogram.providers.utils.errors import (
                            ErrorReason as _ER,
                        )

                        raise _build_llm_error(
                            cause=e,
                            attempts=attempts_used,
                            elapsed_s=elapsed,
                            content=content,
                            model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=True,
                            override_reason=_ER.TIMEOUT,
                        ) from e

                    _fire_on_retry(
                        on_retry,
                        cause=e,
                        attempt=attempts_used,
                        max_attempts=self.max_retries,
                        sleep_s=sleep_s,
                        elapsed_s=elapsed,
                        retry_after_s=retry_after_s,
                    )
                    time.sleep(sleep_s)
        finally:
            if not _llm_closed:
                import sys as _sys

                _exc = _sys.exc_info()[1]
                _st = (
                    "cancelled"
                    if (
                        isinstance(_exc, ExecInterrupt)
                        and "cancel" in str(_exc).lower()
                    )
                    else "error"
                )
                self._close_model_call_node(
                    _llm_node_id,
                    reply=reply if reply is not None else "",
                    status=_st,
                    execution_kind=execution_kind,
                    provider_request_count=attempts_used,
                    agent_iteration_count=(
                        0 if execution_kind == "llm"
                        else int(getattr(self, "last_agent_iteration_count", 0) or 0)
                    ),
                    error=_exc if _st == "error" else None,
                )
            self._active_llm_node_id = None
            self._publish_exec_state()
            try:
                _current_exec_state.reset(_exec_token)
            except (ValueError, LookupError):
                pass
            _dl.reset_deadline(_deadline_token)
            if tools_token is not None:
                _current_tools.reset(tools_token)
            if stream_fn_token is not None:
                _current_stream_fn.reset(stream_fn_token)
            if effort_token is not None:
                _current_effort.reset(effort_token)
            _current_call_model.reset(call_model_token)
            if direct_content_token is not None:
                _current_direct_content.reset(direct_content_token)
            if policy_token is not None:
                _current_tool_policy.reset(policy_token)
            if loop_opts_token is not None:
                _current_loop_opts.reset(loop_opts_token)
            if response_format_token is not None:
                _current_response_format.reset(response_format_token)
            if model_call_budget_token is not None:
                _current_model_call_budget.reset(model_call_budget_token)

        # No choices — the raw reply text is the result.
        if choices is None:
            return reply

        # Choice-constrained finish — resolve the reply against the menu.
        # parse_args' own re-pick path issues fresh choice-free exec()
        # calls, so the tool/policy tokens above are already reset.
        #
        # Forward the caller's timeout_s / on_retry into resolve_decision
        # so re-pick exec() calls inside parse_args stay inside the
        # caller's wall-clock budget and fire the observability hook.
        # ``timeout_s`` here is the *remaining* budget — the initial
        # choice-bearing exec already debited some of it (potentially
        # all of it, if retries ran long). Negative remainder = deadline
        # already hit; surface as TIMEOUT instead of starting parse_args
        # with a useless near-zero budget.
        remaining_timeout: Optional[float] = None
        if timeout_s is not None:
            remaining_timeout = timeout_s - (time.monotonic() - _exec_start)
            if remaining_timeout <= 0:
                from openprogram.providers.utils.errors import ErrorReason as _ER

                cause = TimeoutError(
                    f"exec() exhausted timeout_s={timeout_s}s before choice "
                    "resolution could start"
                )
                raise _build_llm_error(
                    cause=cause,
                    attempts=self.max_retries,
                    elapsed_s=time.monotonic() - _exec_start,
                    content=content,
                    model=use_model,
                    provider=getattr(self, "provider", None),
                    history=[],
                    permanent=True,
                    override_reason=_ER.TIMEOUT,
                ) from cause

        from openprogram.agentic_programming.decision import resolve_decision

        return resolve_decision(
            reply,
            _decision_menu,
            _decision_values,
            self,
            max_retries=0 if execution_kind == "llm" else 1,
            timeout_s=remaining_timeout,
            on_retry=on_retry,
        )


    async def async_exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
        timeout_s: Optional[float] = None,
        on_retry: Optional["Callable[[RetryInfo], None]"] = None,
    ) -> Any:
        """Async version of :meth:`exec`. Same ``timeout_s`` /
        ``on_retry`` semantics; ``await``-friendly throughout.

        Async retries use ``asyncio.sleep`` so the loop yields to the
        event loop and an external cancellation (``asyncio.CancelledError``)
        actually wakes the runtime up — sync ``exec()`` blocks in
        ``time.sleep`` for the same path. ``timeout_s`` here is
        independent of any ``asyncio.wait_for`` wrapper the caller
        might add: this one converts to a structured
        ``LLMError(reason=TIMEOUT)``, ``wait_for`` raises
        ``TimeoutError``.
        """
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

        structured_format = None
        if response_format is not None:
            from openprogram.providers.structured_output import (
                normalize_response_format,
            )

            structured_format = normalize_response_format(response_format)
        response_format_token = None
        model_call_budget_token = None

        # Cancel check — lets long-running loops inside one function also abort.
        from openprogram.agentic_programming.function import _run_pre_invocation_hooks

        _run_pre_invocation_hooks()

        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Build call input ---
        # Single path (see sync exec): _call_via_providers handles system
        # prompt + history; ``content`` is the current turn, passed as-is.
        call_input = content

        # --- Call the LLM (with retry) ---
        errors: list[str] = []
        reply = None
        _exec_start = time.monotonic()
        if not (timeout_s and timeout_s > 0):
            timeout_s = _default_exec_timeout_s()
        _deadline = _exec_start + timeout_s if (timeout_s and timeout_s > 0) else None
        # Same end-to-end deadline publish as exec() — the inner stream-retry
        # loop and SSE parser read it. See error-and-timeout-mechanism.html.
        from openprogram.providers.utils.errors import ExecInterrupt
        from openprogram.providers.utils import deadline as _dl

        if structured_format is not None:
            response_format_token = _current_response_format.set(structured_format)
            model_call_budget_token = _current_model_call_budget.set(
                {
                    "limit": self.max_retries,
                    "remaining": self.max_retries,
                    "validation_repairs_used": 0,
                    "deadline": _deadline,
                }
            )
        _deadline_token = _dl.set_deadline(_deadline)
        _exec_token = _current_exec_state.set(_ExecCallState())
        _llm_node_id = None
        _llm_closed = False
        attempts_used = 0
        try:
            # One exec == one llm node (see exec() for the rationale).
            _llm_node_id = self._open_model_call_node(
                model=use_model,
                content_text=content_text,
            )
            self._active_llm_node_id = _llm_node_id
            while attempts_used < self.max_retries:
                # Pre-attempt deadline check (see exec() for the rationale).
                if _deadline is not None and time.monotonic() >= _deadline:
                    from openprogram.providers.utils.errors import ErrorReason as _ER

                    cause = TimeoutError(
                        f"async_exec() timed out after {timeout_s}s "
                        f"({attempts_used} attempt(s))"
                    )
                    raise _build_llm_error(
                        cause=cause,
                        attempts=max(1, attempts_used),
                        elapsed_s=time.monotonic() - _exec_start,
                        content=content,
                        model=use_model,
                        provider=getattr(self, "provider", None),
                        history=errors,
                        permanent=True,
                        override_reason=_ER.TIMEOUT,
                    ) from cause

                from openprogram.agentic_programming.function import (
                    CancelledError as _CE,
                    check_cancelled,
                )

                try:
                    check_cancelled()
                except _CE:
                    raise ExecInterrupt("cancelled") from None

                try:
                    attempts_used += 1
                    raw_reply = await self._async_call(
                        call_input, model=use_model, response_format=response_format
                    )
                    reply = raw_reply
                    dag_reply = (
                        json.dumps(
                            raw_reply,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if structured_format is not None
                        else raw_reply
                    )
                    self._close_model_call_node(_llm_node_id, reply=dag_reply)
                    _llm_closed = True
                    return reply
                except ExecInterrupt:
                    raise  # caller hard-stop — bypass the retry layer
                except (TypeError, NotImplementedError):
                    raise
                except StructuredOutputError:
                    raise
                except Exception as e:
                    model_call_budget = _current_model_call_budget.get(None)
                    if model_call_budget is not None:
                        attempts_used = max(
                            attempts_used,
                            self.max_retries - model_call_budget["remaining"],
                        )
                    errors.append(f"Attempt {attempts_used}: {type(e).__name__}: {e}")
                    permanent = _is_permanent_error(e)
                    # Don't re-retry a transport error the provider already
                    # exhausted its own budget on (the 3×6 multiplication).
                    transport_done = bool(getattr(e, "transport_exhausted", False))
                    elapsed = time.monotonic() - _exec_start
                    timed_out = _deadline is not None and time.monotonic() >= _deadline
                    if (
                        permanent
                        or transport_done
                        or timed_out
                        or attempts_used >= self.max_retries
                    ):
                        from openprogram.providers.utils.errors import (
                            ErrorReason as _ER,
                        )

                        raise _build_llm_error(
                            cause=e,
                            attempts=attempts_used,
                            elapsed_s=elapsed,
                            content=content,
                            model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=permanent or timed_out,
                            override_reason=_ER.TIMEOUT if timed_out else None,
                        ) from e
                    retry_after_s = getattr(e, "retry_after_s", None)
                    sleep_s = _retry_sleep_seconds(attempts_used - 1, retry_after_s)

                    if (
                        _deadline is not None
                        and (time.monotonic() + sleep_s) >= _deadline
                    ):
                        from openprogram.providers.utils.errors import (
                            ErrorReason as _ER,
                        )

                        raise _build_llm_error(
                            cause=e,
                            attempts=attempts_used,
                            elapsed_s=elapsed,
                            content=content,
                            model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=True,
                            override_reason=_ER.TIMEOUT,
                        ) from e

                    _fire_on_retry(
                        on_retry,
                        cause=e,
                        attempt=attempts_used,
                        max_attempts=self.max_retries,
                        sleep_s=sleep_s,
                        elapsed_s=elapsed,
                        retry_after_s=retry_after_s,
                    )
                    await asyncio.sleep(sleep_s)
        finally:
            if not _llm_closed:
                import sys as _sys

                _exc = _sys.exc_info()[1]
                _st = (
                    "cancelled"
                    if (
                        isinstance(_exc, ExecInterrupt)
                        and "cancel" in str(_exc).lower()
                    )
                    else "error"
                )
                self._close_model_call_node(
                    _llm_node_id,
                    reply=reply if reply is not None else "",
                    status=_st,
                    error=_exc if _st == "error" else None,
                )
            self._active_llm_node_id = None
            self._publish_exec_state()
            try:
                _current_exec_state.reset(_exec_token)
            except (ValueError, LookupError):
                pass
            _dl.reset_deadline(_deadline_token)
            if response_format_token is not None:
                _current_response_format.reset(response_format_token)
            if model_call_budget_token is not None:
                _current_model_call_budget.reset(model_call_budget_token)

