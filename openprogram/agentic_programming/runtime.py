"""
runtime — LLM call interface with automatic DAG integration.

Runtime is a class that wraps an LLM provider. You instantiate it once
with your provider config, then call rt.exec() inside @agentic_functions.

exec() automatically:
    1. Builds the prompt's message history from the DAG (the
       ``_store`` GraphStore the dispatcher installed for this turn)
    2. Calls _call() (override this for your provider)
    3. Appends a ModelCall node recording the reply into the DAG

Usage:
    from openprogram import agentic_function
    from openprogram.agentic_programming.runtime import Runtime

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
import random
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from openprogram.providers.utils.errors import LLMError

# Backoff base (seconds) between exec() retry attempts. Retries sleep
# _RETRY_BACKOFF * 2**attempt before the next try, with ±25% jitter
# so multiple concurrent retries don't fire in lock-step against the
# same upstream and re-trigger whatever connection-pool / rate-limit
# threshold caused the original failure.
_RETRY_BACKOFF = 1.5


def _retry_sleep_seconds(attempt: int, retry_after_s: Optional[float] = None) -> float:
    """Exponential backoff + jitter, honoring a server-supplied
    ``Retry-After`` hint as a lower bound.

    Without a hint (default): ``base * 2^attempt`` scaled by
    ``[0.75, 1.25]`` (symmetric jitter) so a burst of retries spreads
    out instead of slamming the upstream simultaneously. Attempt 0
    sleeps ~1.5s, attempt 1 ~3s, ..., attempt 5 ~48s.

    With a hint (server returned ``Retry-After``): the delay is the
    larger of the exponential base and ``retry_after_s``, then scaled
    by ``[1.0, 1.25]`` — positive-only jitter so we never wake up
    before the server-specified deadline. Honoring the lower bound
    matters during rate-limit storms: ±25% symmetric jitter would
    let a quarter of retries fire too early, defeating the server's
    backpressure and triggering 429 again.

    Mirrors OpenClaw's ``computeBackoffDelay`` (references/openclaw/
    src/infra/retry.ts) which uses the same "positive-only when
    Retry-After present" rule.
    """
    base = _RETRY_BACKOFF * (2 ** attempt)
    if retry_after_s and retry_after_s > 0:
        floor = max(base, retry_after_s)
        return floor * random.uniform(1.0, 1.25)
    return base * random.uniform(0.75, 1.25)

# Substrings marking a *permanent* provider error. Retrying these only
# burns attempts and wall-clock time — the request is malformed or the
# credentials are bad, so the next identical attempt fails identically.
_PERMANENT_ERROR_MARKERS = (
    "not a valid image",
    "invalid image",
    "image data is not",
    "login expired",
    "login failed",
    "re-auth",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
)


def _is_permanent_error(exc: Exception) -> bool:
    """True if retrying ``exc`` is pointless (malformed request / bad auth)."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in msg for marker in _PERMANENT_ERROR_MARKERS)


def _build_llm_error(
    *,
    cause: BaseException,
    attempts: int,
    elapsed_s: float,
    content: Any,
    model: Optional[str],
    provider: Optional[str],
    history: list[str],
    permanent: bool,
) -> "LLMError":  # type: ignore[name-defined]
    """Construct the structured exception ``exec()`` raises when it
    gives up.

    Collects everything the dispatcher / GUI agent needs to decide
    "retry the whole turn", "reauthenticate", "trim prompt", or
    "circuit-break this provider":

      * ``reason`` — classified by :func:`classify_error`
      * ``retryable`` — honest about whether the underlying kind was
        transient; ``False`` for permanent failures (auth / invalid
        request / context overflow). Note: even retryable=True means
        "the kind was transient but we exhausted our budget", not
        "you should retry this immediately"
      * ``attempts`` / ``elapsed_s`` / ``had_image`` — observability
      * ``cause`` — original exception, preserved for traceback
        via ``raise ... from cause``

    The ``history`` list (per-attempt error strings) is folded into
    the message so the LLMError's text is greppable like the old
    RuntimeError. Localised to this helper so the two retry loops
    stay tidy.
    """
    from openprogram.providers.utils.errors import (
        LLMError, classify_error, had_image as _had_image,
    )

    # Try to pull HTTP status from the cause if the provider attached
    # it (openai-codex / anthropic providers can stash it on the exc).
    http_status = getattr(cause, "http_status", None) or getattr(cause, "status_code", None)
    retry_after_s = getattr(cause, "retry_after_s", None)
    error_text = getattr(cause, "error_text", "") or ""

    reason, kind_retryable = classify_error(
        cause, http_status=http_status, error_text=error_text,
    )
    # Even if the underlying kind was retryable, when we gave up
    # because the budget was exhausted, retryable stays True (caller
    # may decide to retry the whole turn with a fresh budget). When
    # the failure was permanent, force retryable=False regardless of
    # what classify_error said.
    retryable = kind_retryable and not permanent

    label = "permanently" if permanent else f"after {attempts} attempt(s)"
    detail = "\n".join(history) if history else f"{type(cause).__name__}: {cause}"
    message = f"exec() failed {label}:\n{detail}"

    return LLMError(
        message=message,
        reason=reason,
        retryable=retryable,
        http_status=http_status,
        retry_after_s=retry_after_s,
        attempts=attempts,
        elapsed_s=elapsed_s,
        had_image=_had_image(content),
        provider=provider,
        model=model,
        last_error_type=type(cause).__name__,
        cause=cause,
    )

# Context var for the tools passed into the current exec() call.
# _call_via_providers reads it to feed AgentSession without changing
# the _call() signature subclasses override.
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
        max_retries: int = 6,
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
                         Default 6 (try once, retry five times on transient
                         failure, with exponential backoff + ±25% jitter).
                         Total wall-clock at worst ≈ 1.5 + 3 + 6 + 12 + 24
                         = 46s of sleeping before giving up — enough for
                         provider gateway connection-pool / rate-limit
                         spikes during tool-heavy turns to recover.
                         Permanent errors (bad image, expired auth) are
                         not retried regardless of this value.
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

    def _render_history_messages(self, content) -> Optional[list]:
        """Build the provider message list for an in-progress exec()
        from the DAG.

        Source of truth: the ``_store`` ContextVar set by the dispatcher
        at turn entry (``openprogram.context.storage._store``). When no
        store is installed (standalone scripts, tests without the
        dispatcher), returns ``None`` so the caller falls back to the
        tree-Context render path.

        Algorithm:
          1. Load the DAG snapshot from the store.
          2. Read the enclosing ``@agentic_function`` call id from
             ``_call_id`` ContextVar; pull its node from the graph to
             get seq + render_range.
          3. Compute reads → render pi-ai messages.
          4. Append a fresh UserMessage built from ``content``.
        """
        from openprogram.store import _store

        store = _store.get()
        if store is None:
            return None

        try:
            from openprogram.context.nodes import compute_reads
            from openprogram.context.render import render_dag_messages
            from openprogram.providers.types import UserMessage, TextContent
            from openprogram.agentic_programming.function import _call_id

            graph = store.load()
            frame_node_id = _call_id.get()

            frame_entry_seq = -1
            render_range = None
            if frame_node_id and frame_node_id in graph.nodes:
                frame_node = graph.nodes[frame_node_id]
                frame_entry_seq = frame_node.seq
                render_range = (frame_node.metadata or {}).get(
                    "render_range"
                )

            head_seq = max(
                (n.seq for n in graph.nodes.values()), default=-1,
            )
            read_ids = compute_reads(
                graph,
                head_seq=head_seq,
                frame_entry_seq=frame_entry_seq,
                render_range=render_range,
            )
            history = render_dag_messages(graph, read_ids)

            # Synthesize the current turn from ``content`` blocks. Most
            # callers pass a single ``{"type":"text","text":"..."}``
            # block; we concatenate all text blocks and skip non-text
            # ones for now (multimodal is a future extension).
            text_parts: list[str] = []
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            current_text = "\n".join(p for p in text_parts if p)
            import time as _time
            current_msg = UserMessage(
                role="user",
                content=[TextContent(type="text", text=current_text)],
                timestamp=int(_time.time() * 1000),
            )
            return history + [current_msg]
        except Exception:
            # If anything goes wrong building DAG messages, fall back
            # to the legacy render_messages path. Never break exec().
            return None

    def _append_model_call_node(
        self,
        *,
        reply: str,
        model: str,
        system_prompt: Optional[str] = None,
        content_text: str = "",
    ) -> None:
        """Append an llm-role Call after a successful provider call.

        Writes to the GraphStore the dispatcher installed in ``_store``;
        ``called_by`` comes from the enclosing ``@agentic_function``
        invocation via ``_call_id``. No-op when no store is installed
        (standalone scripts).

        ``reads`` is intentionally left empty for now — wiring the
        exact read-id set the prompt consumed is a future refinement.
        """
        try:
            from openprogram.store import _store
            from openprogram.context.nodes import Call, ROLE_LLM
            from openprogram.agentic_programming.function import _call_id

            store = _store.get()
            if store is None:
                return

            node = Call(
                role=ROLE_LLM,
                name=model or self.model or "",
                input=({"system": system_prompt} if system_prompt else None),
                output=reply,
                reads=[],
                called_by=_call_id.get() or "",
                metadata=(
                    {"prompt_text": content_text[:8000]}
                    if content_text else {}
                ),
            )
            store.append(node)
        except Exception:
            # DAG bookkeeping failure must not break the LLM call.
            pass

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
        choices: Any = None,
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

        Returns:
            ``str`` — the LLM's reply text. When ``choices`` is set,
            returns the resolved decision instead (a function option's
            return value, or a value option's value).
        """
        if self._closed:
            raise RuntimeError("Runtime is closed. Create a new runtime instance.")

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
            content = list(content) + [{
                "type": "text",
                "text": DECISION_FINISH_INSTRUCTION + render_options(_decision_menu),
            }]

        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Build call input ---
        # AgentSession path: _call_via_providers builds its own message
        # history from the DAG (via _render_history_messages). Pass
        # ``content`` through as-is.
        # Legacy path (_call_fn or subclass-overridden _call): prepend
        # the caller-supplied ``context`` string + any system prompt
        # the runtime is carrying. The caller is responsible for
        # composing history themselves; we don't auto-walk a tree.
        if self._uses_legacy_call():
            call_input = list(content)
            if context:
                call_input.insert(0, {"type": "text", "text": context})
            system_text = getattr(self, "system", "") or ""
            skills_block = self._skills_block()
            if skills_block:
                system_text = (system_text + skills_block) if system_text else skills_block.lstrip("\n")
            if system_text:
                call_input.insert(0, {"type": "text", "text": system_text, "role": "system"})
        else:
            call_input = content

        # --- Call the LLM (with retry) ---
        tools_token = _current_tools.set(tools) if tools else None
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
        reply = None
        _exec_start = time.monotonic()
        try:
            errors: list[str] = []
            for attempt in range(self.max_retries):
                try:
                    reply = self._call(call_input, model=use_model, response_format=response_format)
                    self._append_model_call_node(
                        reply=reply,
                        model=use_model,
                        content_text=content_text,
                    )
                    break
                except (TypeError, NotImplementedError):
                    raise  # Programming errors — don't retry
                except Exception as e:
                    errors.append(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                    permanent = _is_permanent_error(e)
                    if permanent or attempt == self.max_retries - 1:
                        raise _build_llm_error(
                            cause=e, attempts=attempt + 1,
                            elapsed_s=time.monotonic() - _exec_start,
                            content=content, model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=permanent,
                        ) from e
                    # Honor server-supplied Retry-After when the
                    # underlying provider attached it to the exception.
                    retry_after_s = getattr(e, "retry_after_s", None)
                    time.sleep(_retry_sleep_seconds(attempt, retry_after_s))
        finally:
            if tools_token is not None:
                _current_tools.reset(tools_token)
            if policy_token is not None:
                _current_tool_policy.reset(policy_token)

        # No choices — the raw reply text is the result.
        if choices is None:
            return reply

        # Choice-constrained finish — resolve the reply against the menu.
        # parse_args' own re-pick path issues fresh choice-free exec()
        # calls, so the tool/policy tokens above are already reset.
        from openprogram.agentic_programming.decision import resolve_decision
        return resolve_decision(reply, _decision_menu, _decision_values, self)

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

        use_model = model or self.model
        content_text = "\n".join(b["text"] for b in content if b.get("type") == "text")

        # --- Build call input (legacy text-merge only if needed) ---
        if self._uses_legacy_call():
            call_input = list(content)
            if context:
                call_input.insert(0, {"type": "text", "text": context})
            system_text = getattr(self, "system", "") or ""
            skills_block = self._skills_block()
            if skills_block:
                system_text = (system_text + skills_block) if system_text else skills_block.lstrip("\n")
            if system_text:
                call_input.insert(0, {"type": "text", "text": system_text, "role": "system"})
        else:
            call_input = content

        # --- Call the LLM (with retry) ---
        errors: list[str] = []
        _exec_start = time.monotonic()
        for attempt in range(self.max_retries):
            try:
                reply = await self._async_call(call_input, model=use_model, response_format=response_format)
                self._append_model_call_node(
                    reply=reply,
                    model=use_model,
                    content_text=content_text,
                )
                return reply
            except (TypeError, NotImplementedError):
                raise
            except Exception as e:
                errors.append(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                permanent = _is_permanent_error(e)
                if permanent or attempt == self.max_retries - 1:
                    raise _build_llm_error(
                        cause=e, attempts=attempt + 1,
                        elapsed_s=time.monotonic() - _exec_start,
                        content=content, model=use_model,
                        provider=getattr(self, "provider", None),
                        history=errors,
                        permanent=permanent,
                    ) from e
                retry_after_s = getattr(e, "retry_after_s", None)
                await asyncio.sleep(_retry_sleep_seconds(attempt, retry_after_s))

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

        raw_tools = _current_tools.get(None)
        policy = _current_tool_policy.get(None) or {}
        # Tools are OPT-IN. A bare `runtime.exec(content=...)` with no
        # `tools=` and no `toolset=` is a pure reasoning call — the model
        # gets NO tools. This matches the Agentic Programming paradigm:
        # the LLM reasons when asked; it only *acts* (runs tools) when
        # the function explicitly hands it tools. To get tools, pass
        # `tools=[...]` or `toolset="default"` (or a named preset).
        if raw_tools is None:
            preset = policy.get("toolset") if policy else None
            if preset is None:
                # Nothing requested — reasoning-only call, no tools.
                agent_tools = None
            else:
                from openprogram.functions import (
                    agent_tools as _resolve_agent_tools,
                )
                tools_for_session = _resolve_agent_tools(
                    toolset=preset,
                    source=policy.get("source") if policy else None,
                    allow=policy.get("allow") if policy else None,
                    deny=policy.get("deny") if policy else None,
                )
                agent_tools = tools_for_session or None
        elif raw_tools:
            from openprogram.functions import apply_tool_policy as _apply_policy
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

        # Prompt-composition: prefer DAG-derived history when a store
        # is installed; fall back to wrapping ``content`` as a single
        # UserMessage for standalone runs.
        dag_messages = self._render_history_messages(content)
        if dag_messages is not None:
            history = dag_messages[:-1]
            current = dag_messages[-1]
        else:
            ctx, _sp_unused = _build_pi_context(content)
            history = []
            current = ctx.messages[0]
        system_prompt = getattr(self, "system", "") or ""

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
            final = _run_async(session.run(current))
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
            raise RuntimeError(
                final.error_message
                or f"Agent session ended with stop_reason='error' but no "
                f"error_message (model={final.model!r})"
            )

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
    from openprogram.providers.types import VideoContent, AudioContent

    system_text = None
    parts = []

    _media_defaults = {
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/mp3",
    }

    def _load_media(block: dict, default_mime: str) -> tuple[str, str]:
        data = block.get("data")
        mime = block.get("mime_type")
        if not data:
            path = block["path"]
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            mime = mime or _guess_mime(path) or default_mime
        return data, (mime or default_mime)

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
            data, mime = _load_media(block, _media_defaults["image"])
            parts.append(ImageContent(type="image", data=data, mime_type=mime))
        elif btype == "video":
            data, mime = _load_media(block, _media_defaults["video"])
            parts.append(VideoContent(type="video", data=data, mime_type=mime))
        elif btype == "audio":
            data, mime = _load_media(block, _media_defaults["audio"])
            parts.append(AudioContent(type="audio", data=data, mime_type=mime))
        # other unknown block types are skipped silently

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


