"""
runtime — LLM call interface with automatic Context integration.

Runtime is a class that wraps an LLM provider. You instantiate it once
with your provider config, then call rt.exec() inside @agentic_functions.

exec() automatically:
    1. Reads the Context tree (via render_context) to build execution context
    2. Prepends context to your content as a text block
    3. Calls _call() (override this for your provider)
    4. Records the reply to the Context tree

Usage:
    from openprogram import Runtime, agentic_function

    rt = Runtime(call=my_llm_func)
    # or: subclass Runtime and override _call()

    @agentic_function
    def observe(task):
        '''Look at the screen and describe what you see.'''
        return rt.exec(content=[
            {"type": "text", "text": "Find the login button."},
            {"type": "image", "path": "screenshot.png"},
        ])
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import os
from typing import Any, Optional

from openprogram.agentic_programming.context import Context, _current_ctx
from openprogram.agentic_programming.events import _emit_event

# Context var for the currently-running exec node. Runtime.exec() and
# async_exec() set it around the provider call; _call_via_providers() reads
# it to get the node (and its render_messages() history) without changing
# the _call() signature subclasses override.
_current_exec_ctx: contextvars.ContextVar[Optional[Context]] = contextvars.ContextVar(
    "_current_exec_ctx", default=None,
)

# Context var for the tools passed into the current exec() call. Set alongside
# _current_exec_ctx so _call_via_providers can feed them to AgentSession.
_current_tools: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "_current_tools", default=None,
)

# OpenClaw-style tool policy that overlays on top of the chosen tool
# list. Set by callers (dispatcher / channels / runtime.exec kwargs)
# to filter the resolved tools per-call without renaming them. Shape:
# ``{"toolset": "research", "source": "wechat", "allow": [...], "deny": [...]}``.
# Any subset of keys is valid; missing keys mean "no constraint".
_current_tool_policy: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_current_tool_policy", default=None,
)


class Runtime:
    """
    LLM runtime. Wraps a provider and handles Context integration.

    Two ways to use:

    1. Pass a call function:
        rt = Runtime(call=my_func, model="gpt-4o")

    2. Subclass and override _call():
        class MyRuntime(Runtime):
            def _call(self, content, response_format=None):
                # your API logic here
                return reply_text
    """

    def __init__(
        self,
        call: Optional[callable] = None,
        model: str = "default",
        max_retries: int = 2,
        api_key: Optional[str] = None,
        skills: "bool | list[str] | None" = None,
    ):
        """
        Args:
            call:        LLM provider function.
                         Signature: fn(content: list[dict], model: str, response_format: dict) -> str
                         If None, the default pi-ai backend is used (when `model`
                         is "provider:model_id"). Subclasses may override _call().
            model:       Default model. Two forms:
                         - "provider:model_id" (e.g. "anthropic:claude-sonnet-4.5")
                           → resolved via openprogram.providers; _call() goes
                           through complete() by default.
                         - Any other string → legacy path (subclass overrides
                           _call, or pass a `call` function).
            max_retries: Maximum number of exec() attempts before raising.
                         Default 2 (try once, retry once on failure).
            api_key:     Optional API key. If omitted, resolved from the
                         provider's standard env var (OPENAI_API_KEY, etc).
            skills:      Skill discovery for the system prompt. Three shapes:
                         - None (default) or False → skills disabled
                         - True → probe default_skill_dirs() (user + repo)
                         - list[str] → explicit directory list
                         When enabled, the <available_skills> block is
                         appended to system_prompt on every exec() call.
        """
        import uuid as _uuid
        self._closed = False  # Set early so __del__ is safe even if __init__ raises.
        self._prompted_functions: set[str] = set()  # Functions whose docstrings have been sent

        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        self._call_fn = call
        self.model = model
        self.max_retries = max_retries
        self.has_session = False  # Subclasses set True if they manage their own context
        self.on_stream = None  # Optional callback: fn(event_dict) for streaming events
        self.last_usage = None  # Last call's token usage: {input_tokens, output_tokens, ...}
        self.usage_is_cumulative = False  # True if last_usage accumulates across calls (e.g. Codex CLI)
        self.api_key = api_key
        # Skills config: resolved to a (possibly empty) list of dirs at
        # first use; actual SKILL.md loading is lazy and cached so we
        # don't rescan the filesystem every exec().
        self._skills_config = skills
        self._skills_cache_key: tuple[str, ...] | None = None
        self._skills_prompt_block: str = ""
        # Unified reasoning knob, matches pi-ai's ThinkingLevel:
        #   "off" | "low" | "medium" | "high" | "xhigh"
        # API runtimes pass this straight through to AgentSession → provider
        # SimpleStreamOptions.reasoning. CLI subclasses override however their
        # backend expects (flags, env vars, etc).
        self.thinking_level: str = "off"
        # Stable id across successive exec() calls — provider uses it as
        # prompt_cache_key (Codex) so repeat prefixes hit the cache.
        self.session_id = f"op-{_uuid.uuid4().hex[:16]}"

        # Resolve "provider:model_id" form against the pi-ai model registry.
        self.api_model = None
        if call is None and isinstance(model, str) and ":" in model:
            provider, model_id = model.split(":", 1)
            from openprogram.providers import get_model
            resolved = get_model(provider, model_id)
            if resolved is None:
                raise ValueError(
                    f"Unknown model {provider!r}:{model_id!r}. "
                    f"Pass `call=`, subclass Runtime, or use a valid pi-ai model id."
                )
            self.api_model = resolved

    # --- Skills ---

    def _resolved_skill_dirs(self) -> list[str]:
        """Turn the constructor's ``skills`` argument into a concrete dir list.

        None / False → []. True → default dirs. list → as-is.
        """
        cfg = self._skills_config
        if not cfg:
            return []
        if cfg is True:
            from openprogram.agentic_programming.skills import default_skill_dirs
            return default_skill_dirs()
        if isinstance(cfg, (list, tuple)):
            return [str(d) for d in cfg]
        return []

    def _skills_block(self) -> str:
        """Return the ``<available_skills>`` XML block for this runtime.

        Cached per dir tuple so repeat exec() calls don't rescan unless the
        configured dirs change. Empty string when skills are disabled or no
        SKILL.md files were found — callers can unconditionally concatenate.
        """
        dirs = tuple(self._resolved_skill_dirs())
        if self._skills_cache_key == dirs:
            return self._skills_prompt_block
        if not dirs:
            self._skills_cache_key = dirs
            self._skills_prompt_block = ""
            return ""
        from openprogram.agentic_programming.skills import (
            format_skills_for_prompt, load_skills,
        )
        self._skills_prompt_block = format_skills_for_prompt(load_skills(dirs))
        self._skills_cache_key = dirs
        return self._skills_prompt_block

    # --- Path dispatch ---

    def _uses_legacy_call(self) -> bool:
        """True if this runtime sends responses through the text-prompt
        pathway of ``_call()`` rather than the AgentSession + render_messages
        pathway.

        Legacy providers (OpenAICodexRuntime, ...) and
        user-supplied ``call=`` functions expect a text-merged
        ``full_content`` list. The default Runtime (``model="provider:id"``)
        builds messages directly from the execution tree and ignores
        ``full_content``.
        """
        if self._call_fn is not None:
            return True
        return type(self)._call is not Runtime._call

    # --- Working directory ---

    def set_workdir(self, path: str) -> None:
        """Set the provider's working directory.

        For runtimes that spawn subprocesses (Codex CLI via --cd), this
        determines where shell/tool commands execute and where the LLM
        writes relative-path files. Default: no-op — runtimes that don't
        spawn subprocesses ignore this.
        """
        pass

    # --- Lifecycle ---

    def close(self):
        """Close this runtime: release resources, kill processes, end session.

        After close(), exec() will raise RuntimeError.
        Subclasses should override this to clean up provider-specific resources
        (kill CLI processes, clear session IDs, etc.) and call super().close().
        """
        self.has_session = False
        self._prompted_functions.clear()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        # Defensive: subclasses that raise mid-__init__ never reach
        # Runtime.__init__, so `_closed` may be missing on the
        # partially-built object the GC eventually reaps. Treat
        # missing as already closed.
        if not getattr(self, "_closed", True):
            self.close()

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
        parallel_tool_calls: bool = True,
        max_iterations: int = 20,
    ) -> str:
        """
        Call the LLM. Creates an exec node in the Context tree.

        Args:
            content:          List of content blocks. Each block is a dict:
                              {"type": "text", "text": "..."}
                              {"type": "image", "path": "screenshot.png"}
                              {"type": "audio", "path": "recording.wav"}
                              {"type": "file", "path": "data.csv"}

            context:          Override auto-generated context string.
                              If None: exec node calls render_context() on itself.

            response_format:  Expected output format (JSON schema).
                              Passed to _call() for provider-native handling.

            model:            Override the default model for this call.

            tools:            Optional list of tools the LLM may call. Each
                              entry may be an @agentic_function, a
                              {"spec":..., "execute":...} dict, or an object
                              with .spec and .execute attributes. When set,
                              runs a tool loop until the model returns plain
                              text (or max_iterations is hit).

            tool_choice:      "auto" (default), "required", "none", or
                              {"type":"function","name":"X"} to force a
                              specific tool.

            parallel_tool_calls: allow the model to emit multiple tool calls
                                 in one turn (default True).

            max_iterations:   safety cap on the tool loop (default 20).

        Returns:
            str — the LLM's reply text.
        """
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

        # Cancel check — lets long-running loops inside one function also abort.
        from openprogram.agentic_programming.function import _run_pre_invocation_hooks
        _run_pre_invocation_hooks()

        # Handle plain string input
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        import time as _time
        parent_ctx = _current_ctx.get(None)
        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Create exec child node ---
        exec_ctx = None
        if parent_ctx is not None:
            exec_ctx = Context(
                name="_exec",
                node_type="exec",
                params={"_content": content_text, "_content_blocks": list(content)},
                parent=parent_ctx,
                start_time=_time.time(),
                expose="io",
            )
            parent_ctx.children.append(exec_ctx)
            _emit_event("node_created", exec_ctx)

        # --- Build call input (shape depends on the dispatch path) ---
        # AgentSession path: _call_via_providers reads exec_ctx directly and
        # renders its own message history, so we just pass `content` through.
        # Legacy path (_call_fn or subclass-overridden _call): build the
        # text-merged full_content list those implementations expect.
        if self._uses_legacy_call():
            if context is None and exec_ctx is not None:
                if self.has_session:
                    if parent_ctx.prompt and parent_ctx.name not in self._prompted_functions:
                        context = parent_ctx.prompt
                        self._prompted_functions.add(parent_ctx.name)
                else:
                    kwargs = dict(parent_ctx.render_range) if parent_ctx.render_range else {}
                    kwargs["prompted_functions"] = self._prompted_functions
                    context = exec_ctx.render_context(**kwargs)

            call_input = _merge_content(context, content, exec_ctx)
            system_text = _find_system_prompt(parent_ctx)
            skills_block = self._skills_block()
            if skills_block:
                system_text = (system_text + skills_block) if system_text else skills_block.lstrip("\n")
            if system_text:
                call_input.insert(0, {"type": "text", "text": system_text, "role": "system"})

            if os.environ.get("AGENTIC_DUMP_INPUT"):
                _dump_llm_input(call_input, exec_ctx, parent_ctx, self)
        else:
            call_input = content

        # --- Call the LLM (with retry) ---
        # Publish exec_ctx + tools so _call_via_providers can reach them via
        # _current_exec_ctx / _current_tools and build the AgentSession.
        exec_ctx_token = _current_exec_ctx.set(exec_ctx) if exec_ctx is not None else None
        tools_token = _current_tools.set(tools) if tools else None
        # Snapshot any policy kwargs into the contextvar so
        # _call_via_providers picks them up without growing its
        # signature. Only set when at least one is provided; otherwise
        # leave the outer-scope policy alone (e.g. dispatcher set one
        # earlier from session config).
        _policy_kwargs = {
            "toolset": toolset,
            "source":  tools_source,
            "allow":   tools_allow,
            "deny":    tools_deny,
        }
        _policy_kwargs = {k: v for k, v in _policy_kwargs.items() if v is not None}
        policy_token = (
            _current_tool_policy.set({**(_current_tool_policy.get(None) or {}), **_policy_kwargs})
            if _policy_kwargs else None
        )
        try:
            attempts = exec_ctx.attempts if exec_ctx is not None else []
            for attempt in range(self.max_retries):
                try:
                    reply = self._call(call_input, model=use_model, response_format=response_format)
                    attempts.append({"attempt": attempt + 1, "reply": reply, "error": None})
                    if exec_ctx is not None:
                        exec_ctx.raw_reply = reply
                        exec_ctx.output = reply
                        exec_ctx.status = "success"
                        exec_ctx.end_time = _time.time()
                        _emit_event("node_completed", exec_ctx)
                        # Backward compat: parent function also gets latest reply
                        parent_ctx.raw_reply = reply
                    return reply
                except (TypeError, NotImplementedError):
                    raise  # Programming errors — don't retry
                except Exception as e:
                    attempts.append({"attempt": attempt + 1, "reply": None, "error": f"{type(e).__name__}: {e}"})
                    if attempt == self.max_retries - 1:
                        if exec_ctx is not None:
                            exec_ctx.error = str(e)
                            exec_ctx.status = "error"
                            exec_ctx.end_time = _time.time()
                            _emit_event("node_completed", exec_ctx)
                        error_report = "\n".join(f"Attempt {a['attempt']}: {a['error']}" for a in attempts)
                        raise RuntimeError(
                            f"exec() failed after {self.max_retries} attempts in {parent_ctx.name if parent_ctx else 'unknown'}():\n{error_report}"
                        ) from e
        finally:
            if exec_ctx_token is not None:
                _current_exec_ctx.reset(exec_ctx_token)
            if tools_token is not None:
                _current_tools.reset(tools_token)
            if policy_token is not None:
                _current_tool_policy.reset(policy_token)

    async def async_exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> str:
        """Async version of exec(). Creates exec node, calls _async_call()."""
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

        # Cancel check — lets long-running loops inside one function also abort.
        from openprogram.agentic_programming.function import _run_pre_invocation_hooks
        _run_pre_invocation_hooks()

        if isinstance(content, str):
            content = [{"type": "text", "text": content}]

        import time as _time
        parent_ctx = _current_ctx.get(None)
        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Create exec child node ---
        exec_ctx = None
        if parent_ctx is not None:
            exec_ctx = Context(
                name="_exec",
                node_type="exec",
                params={"_content": content_text, "_content_blocks": list(content)},
                parent=parent_ctx,
                start_time=_time.time(),
                expose="io",
            )
            parent_ctx.children.append(exec_ctx)
            _emit_event("node_created", exec_ctx)

        # --- Build call input (legacy text-merge only if needed) ---
        if self._uses_legacy_call():
            if context is None and exec_ctx is not None:
                if self.has_session:
                    if parent_ctx.prompt and parent_ctx.name not in self._prompted_functions:
                        context = parent_ctx.prompt
                        self._prompted_functions.add(parent_ctx.name)
                else:
                    kwargs = dict(parent_ctx.render_range) if parent_ctx.render_range else {}
                    kwargs["prompted_functions"] = self._prompted_functions
                    context = exec_ctx.render_context(**kwargs)

            call_input = _merge_content(context, content, exec_ctx)
            system_text = _find_system_prompt(parent_ctx)
            skills_block = self._skills_block()
            if skills_block:
                system_text = (system_text + skills_block) if system_text else skills_block.lstrip("\n")
            if system_text:
                call_input.insert(0, {"type": "text", "text": system_text, "role": "system"})
        else:
            call_input = content

        # --- Call the LLM (with retry) ---
        exec_ctx_token = _current_exec_ctx.set(exec_ctx) if exec_ctx is not None else None
        try:
            attempts = exec_ctx.attempts if exec_ctx is not None else []
            for attempt in range(self.max_retries):
                try:
                    reply = await self._async_call(call_input, model=use_model, response_format=response_format)
                    attempts.append({"attempt": attempt + 1, "reply": reply, "error": None})
                    if exec_ctx is not None:
                        exec_ctx.raw_reply = reply
                        exec_ctx.output = reply
                        exec_ctx.status = "success"
                        exec_ctx.end_time = _time.time()
                        _emit_event("node_completed", exec_ctx)
                        parent_ctx.raw_reply = reply
                    return reply
                except (TypeError, NotImplementedError):
                    raise
                except Exception as e:
                    attempts.append({"attempt": attempt + 1, "reply": None, "error": f"{type(e).__name__}: {e}"})
                    if attempt == self.max_retries - 1:
                        if exec_ctx is not None:
                            exec_ctx.error = str(e)
                            exec_ctx.status = "error"
                            exec_ctx.end_time = _time.time()
                            _emit_event("node_completed", exec_ctx)
                        error_report = "\n".join(f"Attempt {a['attempt']}: {a['error']}" for a in attempts)
                        raise RuntimeError(
                            f"async_exec() failed after {self.max_retries} attempts in {parent_ctx.name if parent_ctx else 'unknown'}():\n{error_report}"
                        ) from e
        finally:
            if exec_ctx_token is not None:
                _current_exec_ctx.reset(exec_ctx_token)

    def _call(self, content: list[dict], model: str = "default", response_format: dict = None) -> str:
        """
        Call the LLM. Override this in subclasses.

        Args:
            content:          List of content blocks (text, image, audio, file).
            model:            Model name.
            response_format:  Output format constraint (JSON schema).

        Returns:
            str — the LLM's reply text.
        """
        if self._call_fn is not None:
            if inspect.iscoroutinefunction(self._call_fn):
                raise TypeError(
                    "exec() received an async call function. "
                    "Use async_exec() for async providers, or pass a sync function."
                )
            result = self._call_fn(content, model=model, response_format=response_format)
            if asyncio.iscoroutine(result):
                raise TypeError(
                    "call function returned a coroutine. "
                    "Use async_exec() for async providers, or pass a sync function."
                )
            return result
        if self.api_model is not None:
            return self._call_via_providers(content, response_format=response_format)
        raise NotImplementedError(
            "No LLM provider configured. Either pass `call=your_function` to Runtime(), "
            "use model='provider:model_id' form, or subclass Runtime and override _call()."
        )

    # ---- Default backend: openprogram.providers (pi-ai) ---------------------

    def _call_via_providers(
        self,
        content: list[dict],
        response_format: dict = None,
    ) -> str:
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

        exec_ctx = _current_exec_ctx.get(None)
        raw_tools = _current_tools.get(None)
        policy = _current_tool_policy.get(None) or {}
        # Default-on semantics: `tools=` unset (None) → resolve the
        # caller's preset (or DEFAULT_TOOLS) and run the OpenClaw-style
        # filter chain. Matches Hermes' "all toolsets, then filter"
        # behaviour so every provider exposes a non-trivial tool list
        # by default, not just the old claude-code CLI path.
        if raw_tools is None:
            from openprogram.tools import (
                agent_tools as _resolve_agent_tools,
            )
            preset = policy.get("toolset") if policy else None
            tools_for_session = _resolve_agent_tools(
                toolset=preset,
                source=policy.get("source") if policy else None,
                allow=policy.get("allow") if policy else None,
                deny=policy.get("deny") if policy else None,
            )
            agent_tools = tools_for_session or None
        elif raw_tools:
            from openprogram.tools import apply_tool_policy as _apply_policy
            adapted = _adapt_tools(raw_tools) or []
            adapted = _apply_policy(
                adapted,
                source=policy.get("source") if policy else None,
                allow=policy.get("allow") if policy else None,
                deny=policy.get("deny") if policy else None,
            )
            agent_tools = adapted or None
        else:
            # Explicit `tools=[]` — caller wanted no tools, honour it.
            agent_tools = None

        if exec_ctx is not None:
            messages = exec_ctx.render_messages()
            system_prompt = _find_system_prompt(exec_ctx.parent) or ""
            history = messages[:-1]
            current = messages[-1]
        else:
            ctx, sp = _build_pi_context(content)
            system_prompt = sp or ""
            history = []
            current = ctx.messages[0]

        skills_block = self._skills_block()
        if skills_block:
            system_prompt = (system_prompt + skills_block) if system_prompt else skills_block.lstrip("\n")

        session = AgentSession(
            model=self.api_model,
            tools=agent_tools,
            system_prompt=system_prompt,
            api_key=self.api_key,
            session_id=self.session_id,
            thinking_level=self.thinking_level,
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
                    if t == "message_update":
                        inner = getattr(ev, "assistant_message_event", None)
                        inner_type = getattr(inner, "type", None)
                        if inner_type == "text_delta":
                            if cb:
                                cb({"type": "text", "text": getattr(inner, "delta", "") or "", "elapsed": _elapsed()})
                        elif inner_type == "thinking_delta":
                            delta = getattr(inner, "delta", "") or ""
                            _thinking_buf["text"] += delta
                            if cb:
                                cb({"type": "thinking", "text": delta, "elapsed": _elapsed()})
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
                            cb({
                                "type": "tool_use",
                                "tool_call_id": call_id,
                                "tool": tool_name,
                                "input": input_str,
                                "elapsed": _elapsed(),
                            })
                    elif t == "tool_execution_end":
                        result = getattr(ev, "result", "")
                        try:
                            result_str = result if isinstance(result, str) else str(result)
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
                            cb({
                                "type": "tool_result",
                                "tool_call_id": call_id,
                                "tool": getattr(ev, "tool_name", "?") or "?",
                                "result": result_str,
                                "is_error": is_error,
                                "elapsed": _elapsed(),
                            })
                except Exception:
                    pass

            _unsub = session.agent.subscribe(_forward)

        try:
            session.replace_messages(history)
            pre_run_len = len(session.messages)
            final = _run_async(session.run(current))
            new_messages = session.messages[pre_run_len:]
            if exec_ctx is not None:
                _record_session_trace(exec_ctx, new_messages)
        finally:
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

        if final is None:
            raise RuntimeError("Agent session produced no assistant message")
        if final.stop_reason == "error":
            raise RuntimeError(final.error_message or "Agent session failed")

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
            }
        return _assistant_text(final)

    def list_models(self) -> list[str]:
        """Return available models for this runtime. Override in subclasses."""
        return [self.model] if self.model and self.model != "default" else []

    async def _async_call(self, content: list[dict], model: str = "default", response_format: dict = None) -> str:
        """Async version of _call(). Override for async providers."""
        if self._call_fn is not None:
            result = self._call_fn(content, model=model, response_format=response_format)
            if asyncio.iscoroutine(result):
                return await result
            # Sync function passed to async_exec — just return it
            return result
        raise NotImplementedError(
            "No async LLM provider configured. Either pass an async `call` to Runtime(), "
            "or subclass Runtime and override _async_call()."
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _find_system_prompt(ctx: Optional["Context"]) -> str:
    """Walk up the Context tree to find the nearest @agentic_function(system=...).

    Returns the first non-empty `system` field encountered, or "" if none.
    Closer ancestors override farther ones (innermost wins).
    """
    node = ctx
    while node is not None:
        if getattr(node, "system", ""):
            return node.system
        node = node.parent
    return ""


def _run_async(coro):
    """
    Run a coroutine from sync code. Safe to call from any context:
    - No running event loop → asyncio.run
    - Running event loop (Jupyter, FastAPI, pytest-asyncio) → run in a worker
      thread so we don't clash with the live loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _guess_mime(path: str) -> str:
    """Minimal mime guess for image blocks."""
    low = path.lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        return "image/jpeg"
    if low.endswith(".gif"):
        return "image/gif"
    if low.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _build_pi_context(content: list[dict]):
    """
    Convert OpenProgram's ``content: list[dict]`` into a pi-ai Context
    (one UserMessage with text/image blocks) plus an optional system prompt
    (drawn from any block with ``role == "system"``).
    """
    import base64
    import time as _time
    from openprogram.providers import (
        Context,
        UserMessage,
        TextContent,
        ImageContent,
    )

    system_text = None
    parts = []

    for block in content:
        btype = block.get("type", "text")

        if block.get("role") == "system" and btype == "text":
            if system_text is None:
                system_text = block["text"]
            else:
                system_text += "\n\n" + block["text"]
            continue

        if btype == "text":
            parts.append(TextContent(type="text", text=block["text"]))
        elif btype == "image":
            data = block.get("data")
            mime = block.get("mime_type")
            if not data:
                path = block["path"]
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                mime = mime or _guess_mime(path)
            parts.append(ImageContent(type="image", data=data, mime_type=mime or "image/png"))
        # audio / file / other blocks: skipped until upstream providers accept them

    if not parts:
        parts.append(TextContent(type="text", text=""))

    user_msg = UserMessage(content=parts, timestamp=int(_time.time() * 1000))
    return Context(messages=[user_msg]), system_text


def _assistant_text(message) -> str:
    """Extract the concatenated text from an AssistantMessage.

    Blocks may be pydantic content objects *or* raw dicts — providers streaming
    incremental output often append dicts to ``content`` directly.
    """
    out = []
    for block in message.content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                out.append(block.get("text", ""))
        elif getattr(block, "type", None) == "text":
            out.append(block.text)
    return "".join(out)


def _adapt_tools(raw_tools: list) -> list:
    """Convert OpenProgram's tool entries into pi-agent ``AgentTool`` objects.

    Accepted input forms (per tool entry):
      - ``{"spec": {...}, "execute": callable}``
      - object with ``.spec`` and ``.execute``
      - a plain spec dict (``{"name": ..., "parameters": ...}``) — **requires**
        an accompanying executor, else we refuse

    The resulting ``AgentTool.execute`` adapts OpenProgram's sync/async
    ``executor(**args) -> str | dict`` signature to the pi-agent contract
    ``async (tool_call_id, args, signal, update_cb) -> AgentToolResult``.
    """
    from openprogram.agent import AgentTool
    from openprogram.agent.types import AgentToolResult
    from openprogram.providers.types import TextContent

    adapted: list = []
    for entry in raw_tools:
        if isinstance(entry, dict) and "spec" in entry and "execute" in entry:
            spec, executor = entry["spec"], entry["execute"]
        elif hasattr(entry, "spec") and hasattr(entry, "execute"):
            spec, executor = entry.spec, entry.execute
        elif isinstance(entry, dict) and "name" in entry:
            raise ValueError(
                f"Tool {entry.get('name')!r} has no executor. "
                "Pass {'spec':..., 'execute':...} or an object with .spec/.execute."
            )
        else:
            raise TypeError(f"Cannot adapt tool entry: {entry!r}")

        captured_executor = executor

        async def _run(tool_call_id: str, args: dict, signal, update_cb,
                       _exec=captured_executor) -> "AgentToolResult":
            if inspect.iscoroutinefunction(_exec):
                try:
                    result = await _exec(**args)
                except TypeError:
                    result = await _exec(args)
            else:
                try:
                    result = await asyncio.to_thread(lambda: _exec(**args))
                except TypeError:
                    result = await asyncio.to_thread(lambda: _exec(args))

            if isinstance(result, str):
                text = result
            else:
                try:
                    text = json.dumps(result, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    text = str(result)
            return AgentToolResult(content=[TextContent(type="text", text=text)])

        adapted.append(AgentTool(
            name=spec["name"],
            description=spec.get("description", ""),
            parameters=spec.get("parameters") or {"type": "object", "properties": {}},
            label=spec.get("label", spec["name"]),
            execute=_run,
        ))
    return adapted


def _record_session_trace(exec_ctx: "Context", new_messages: list) -> None:
    """Attach the agent-loop trace from a completed AgentSession run onto
    ``exec_ctx`` as a tree of ``assistant_round`` and ``tool_call`` children.

    Structure:
      exec
      ├── assistant_round (one per LLM response during the loop)
      │     params: _thinking, _text, _stop_reason, _round_index
      │     children:
      │     ├── tool_call (node_type="tool_call")
      │     │     params: tool args + _tool_call_id
      │     │     output/status from the matching ToolResultMessage
      │     └── ...
      └── ...

    This preserves the precise "round N: LLM said X + called T1/T2 → results"
    sequence so ``render_messages`` can reconstruct a faithful tool-loop
    transcript under ``expose="full"``.
    """
    from openprogram.providers.types import (
        AssistantMessage,
        TextContent,
        ThinkingContent,
        ToolCall,
        ToolResultMessage,
    )
    import time as _time

    results_by_id: dict[str, ToolResultMessage] = {}
    for msg in new_messages:
        if isinstance(msg, ToolResultMessage):
            results_by_id[msg.tool_call_id] = msg

    round_index = 0
    for msg in new_messages:
        if not isinstance(msg, AssistantMessage):
            continue

        thinking_parts: list[str] = []
        text_parts: list[str] = []
        tool_call_blocks: list[ToolCall] = []
        for block in msg.content:
            if isinstance(block, ToolCall):
                tool_call_blocks.append(block)
            elif isinstance(block, ThinkingContent):
                thinking_parts.append(block.thinking)
            elif isinstance(block, TextContent):
                text_parts.append(block.text)

        round_ts = (msg.timestamp / 1000.0) if msg.timestamp else _time.time()
        round_ctx = Context(
            name=f"round_{round_index}",
            node_type="assistant_round",
            params={
                "_thinking": "\n".join(thinking_parts),
                "_text": "\n".join(text_parts),
                "_stop_reason": msg.stop_reason,
                "_round_index": round_index,
            },
            parent=exec_ctx,
            start_time=round_ts,
            end_time=round_ts,
            status="success",
            expose="io",
        )
        exec_ctx.children.append(round_ctx)
        _emit_event("node_created", round_ctx)

        for tc_block in tool_call_blocks:
            result = results_by_id.get(tc_block.id)
            result_text = ""
            if result is not None:
                parts = []
                for rb in result.content:
                    if hasattr(rb, "text"):
                        parts.append(rb.text)
                result_text = "".join(parts)

            tc_ctx = Context(
                name=tc_block.name,
                node_type="tool_call",
                params={**tc_block.arguments, "_tool_call_id": tc_block.id},
                parent=round_ctx,
                start_time=round_ts,
                end_time=(result.timestamp / 1000.0) if (result and result.timestamp) else round_ts,
                output=result_text,
                status="error" if (result and result.is_error) else "success",
                expose="io",
            )
            round_ctx.children.append(tc_ctx)
            _emit_event("node_created", tc_ctx)
            _emit_event("node_completed", tc_ctx)

        _emit_event("node_completed", round_ctx)
        round_index += 1


def _dump_llm_input(
    call_input: list[dict],
    exec_ctx: Optional["Context"],
    parent_ctx: Optional["Context"],
    runtime: "Runtime",
) -> None:
    """Write the text-merged LLM input to AGENTIC_DUMP_DIR for debugging."""
    dump_dir = os.environ.get(
        "AGENTIC_DUMP_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp"),
    )
    os.makedirs(dump_dir, exist_ok=True)
    call_path = (
        exec_ctx._call_path() if exec_ctx
        else (parent_ctx._call_path() if parent_ctx else "unknown")
    )
    seq = getattr(runtime, "_dump_seq", 0)
    runtime._dump_seq = seq + 1
    dump_path = os.path.join(dump_dir, f"{seq:03d}_{call_path}.txt")
    with open(dump_path, "w") as fh:
        for block in call_input:
            if block.get("type") == "text":
                fh.write(block["text"])
            else:
                fh.write(json.dumps(block, ensure_ascii=False, default=str))
            fh.write("\n\n")
    print(f"[DUMP] {call_path} -> {dump_path}")


def _merge_content(
    context: Optional[str],
    content: list[dict],
    ctx: Optional["Context"],
) -> list[dict]:
    """Merge text content blocks into the context string.

    Text blocks are indented and placed under a "→ Current Task:" marker.
    Non-text blocks (images, audio, files) stay as separate content blocks.
    """
    full_content = []
    if context and ctx is not None:
        # Calculate indent relative to outermost ancestor
        base = ctx._depth()
        node = ctx.parent
        while node and node.name:
            base = node._depth()
            node = node.parent
        exec_indent = "    " * (ctx._depth() - base + 1)

        text_parts = []
        for block in content:
            if block.get("type") == "text":
                indented = "\n".join(
                    exec_indent + line if line.strip() else ""
                    for line in block["text"].splitlines()
                )
                text_parts.append(indented)
            else:
                full_content.append(block)

        if text_parts:
            merged = context + "\n" + exec_indent + "→ Current Task:\n" + "\n".join(text_parts)
        else:
            merged = context
        full_content.insert(0, {"type": "text", "text": merged})
    elif context:
        full_content.append({"type": "text", "text": context})
        full_content.extend(content)
    else:
        full_content.extend(content)
    return full_content
