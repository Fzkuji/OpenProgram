"""Agentic runtime: runtime."""
from __future__ import annotations










from typing import Optional


from .shared import (
    _ExecCallState,
    _current_exec_state,
    _default_max_retries,
)
from .history import HistoryOperations
from .questions import QuestionsOperations
from .execution import ExecutionOperations
from .providers import ProvidersOperations

class Runtime(HistoryOperations, QuestionsOperations, ExecutionOperations, ProvidersOperations):
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
        max_retries: Optional[int] = None,
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
            max_retries: Maximum number of provider calls per exec() before raising.
                         ``None`` (default) → read ``OPENPROGRAM_MAX_RETRIES``
                         env, fall back to 6. Set explicitly to override
                         env. Structured-output repair calls consume the
                         same budget as transport retries. 6 means at most
                         six provider calls, with exponential backoff +
                         ±25% jitter — wall-clock at worst ≈ 1.5 + 3 +
                         6 + 12 + 24 = 46s of sleeping before giving
                         up (tunable via ``OPENPROGRAM_RETRY_BACKOFF_BASE``).
                         Permanent errors (bad image, expired auth) are
                         not retried regardless of this value.
            api_key:     Optional API key. If omitted, resolved from the
                         provider's standard env var (OPENAI_API_KEY, etc).
            skills:      Skill discovery for the system prompt. Three shapes:
                         - None (default) or False → skills disabled
                         - True → the four external sources
                           (openprogram.skills.list_skills)
                         - list[str] → explicit directory list
                         When enabled, the <available_skills> block is
                         appended to system_prompt on every exec() call.
        """
        import uuid as _uuid

        self._closed = False  # Set early so __del__ is safe even if __init__ raises.
        # Per-exec scratch lives in ``_current_exec_state``. This idle bag is
        # only the post-exec fallback so ``rt.last_usage`` / ``rt.last_blocks``
        # still work after exec() returns.
        self._idle_exec_state = _ExecCallState()
        self._prompted_functions: set[str] = (
            set()
        )  # Functions whose docstrings have been sent
        # 提问通道（runtime.ask 的出口）。默认 None → 走事件层（前端卡片 + 总线）。
        # @agentic_function 跑的子进程里，process_runner 会换成 QueueTransport
        # （经 mp.Queue 把问题送回父进程）。对齐 logging：通道显式挂在对象上。
        self._question_transport = None

        if max_retries is None:
            max_retries = _default_max_retries()
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")

        self._call_fn = call
        # When ``call=fn`` is supplied, the user's function is wrapped into the
        # single provider/AgentSession path: ``api_model`` becomes a
        # CallableModel stand-in and ``_stream_fn`` the adapter that calls
        # ``fn``. This collapses the old "legacy call" branch — every exec now
        # flows through _call_via_providers, writing one llm DAG node and
        # honouring tool-loop attribution uniformly.
        self._stream_fn = None
        self.model = model
        self.max_retries = max_retries
        self.has_session = False  # Subclasses set True if they manage their own context
        self.on_stream = None  # Optional callback: fn(event_dict) for streaming events
        self.usage_is_cumulative = (
            False  # True if last_usage accumulates across calls (e.g. Codex CLI)
        )
        self.api_key = api_key
        # Skills config: resolved to a (possibly empty) list of dirs at
        # first use; actual SKILL.md loading is lazy and cached so we
        # don't rescan the filesystem every exec().
        self._skills_config = skills
        self._skills_cache_key: object = None
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

        # Authoritative provider identity of this runtime. Derived from the
        # ``provider:model_id`` form below; subscription runtime classes whose
        # provider differs from their model namespace (claude-code streams
        # ``anthropic:<id>`` models) overwrite it after construction. Readers
        # (thinking defaults, provider info) use this — never the class name.
        self.provider_id: Optional[str] = None

        # Resolve "provider:model_id" form against the pi-ai model registry.
        self.api_model = None
        if call is not None:
            # Wrap the user callable into the provider path: a stand-in model
            # + a stream_fn that calls ``fn``. The model is never used for a
            # real network call — the stream_fn intercepts it.
            from openprogram.providers.callable_model import (
                make_callable_model,
                make_callable_stream_fn,
            )

            self.api_model = make_callable_model(call)
            self._stream_fn = make_callable_stream_fn(call)
        elif isinstance(model, str) and ":" in model:
            provider, model_id = model.split(":", 1)
            from openprogram.providers import get_model

            resolved = get_model(provider, model_id)
            if resolved is None:
                raise ValueError(
                    f"Unknown model {provider!r}:{model_id!r}. "
                    f"Pass `call=`, subclass Runtime, or use a valid pi-ai model id."
                )
            self.api_model = resolved
            self.provider_id = provider


    def _exec_state(self) -> _ExecCallState:
        st = _current_exec_state.get()
        if st is not None:
            return st
        idle = getattr(self, "_idle_exec_state", None)
        if idle is None:
            idle = _ExecCallState()
            self._idle_exec_state = idle
        return idle


    def _publish_exec_state(self) -> None:
        st = _current_exec_state.get()
        idle = getattr(self, "_idle_exec_state", None)
        if idle is None:
            idle = _ExecCallState()
            self._idle_exec_state = idle
        if st is None or st is idle:
            return
        idle.last_usage = st.last_usage
        idle.last_blocks = list(st.last_blocks)
        idle.pending_breakdown = st.pending_breakdown
        idle.pending_tool_names = list(st.pending_tool_names)
        idle.pending_system_prompt = st.pending_system_prompt
        idle.last_agent_iteration_count = st.last_agent_iteration_count


    @property
    def last_usage(self):
        return self._exec_state().last_usage


    @last_usage.setter
    def last_usage(self, value) -> None:
        self._exec_state().last_usage = value


    @property
    def last_blocks(self):
        return self._exec_state().last_blocks


    @last_blocks.setter
    def last_blocks(self, value) -> None:
        self._exec_state().last_blocks = value if value is not None else []


    @property
    def last_agent_iteration_count(self):
        return self._exec_state().last_agent_iteration_count


    @last_agent_iteration_count.setter
    def last_agent_iteration_count(self, value) -> None:
        self._exec_state().last_agent_iteration_count = value


    @property
    def _active_llm_node_id(self):
        return self._exec_state().active_llm_node_id


    @_active_llm_node_id.setter
    def _active_llm_node_id(self, value) -> None:
        self._exec_state().active_llm_node_id = value


    @property
    def _pending_tool_names(self):
        return self._exec_state().pending_tool_names


    @_pending_tool_names.setter
    def _pending_tool_names(self, value) -> None:
        self._exec_state().pending_tool_names = value if value is not None else []


    @property
    def _pending_system_prompt(self):
        return self._exec_state().pending_system_prompt


    @_pending_system_prompt.setter
    def _pending_system_prompt(self, value) -> None:
        self._exec_state().pending_system_prompt = value or ""


    @property
    def _pending_breakdown(self):
        return self._exec_state().pending_breakdown


    @_pending_breakdown.setter
    def _pending_breakdown(self, value) -> None:
        self._exec_state().pending_breakdown = value


    def set_workdir(self, path: str) -> None:
        """Set the provider's working directory.

        For runtimes that spawn subprocesses (Codex CLI via --cd), this
        determines where shell/tool commands execute and where the LLM
        writes relative-path files. Default: no-op — runtimes that don't
        spawn subprocesses ignore this.
        """
        pass


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

