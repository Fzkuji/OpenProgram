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
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from openprogram.providers.utils.errors import (
        ErrorReason, LLMError, RetryInfo,
    )

# Backoff base (seconds) between exec() retry attempts. Retries sleep
# _RETRY_BACKOFF * 2**attempt before the next try, with ±25% jitter
# so multiple concurrent retries don't fire in lock-step against the
# same upstream and re-trigger whatever connection-pool / rate-limit
# threshold caused the original failure.
#
# Default 1.5s; ``OPENPROGRAM_RETRY_BACKOFF_BASE`` env overrides so
# deployments with non-default network characteristics (proxied,
# offline-capable, very-low-latency local models) can re-tune without
# touching code.
_RETRY_BACKOFF = float(os.environ.get("OPENPROGRAM_RETRY_BACKOFF_BASE", "1.5"))


def _default_max_retries() -> int:
    """Process-wide default for ``Runtime(max_retries=...)``.

    Reads ``OPENPROGRAM_MAX_RETRIES`` lazily on every Runtime
    construction so tests / scripts can flip the env after this
    module is imported and still see the new value. Falls back to
    6 — the legacy hard-coded default (try once + retry five times,
    total ≈46s of sleeping at the default backoff base).
    """
    try:
        v = int(os.environ.get("OPENPROGRAM_MAX_RETRIES", "6"))
    except ValueError:
        v = 6
    return max(1, v)


def _default_exec_timeout_s() -> Optional[float]:
    """Process-wide fallback wall-clock budget for ``exec()`` /
    ``async_exec()`` when the caller passes ``timeout_s=None``.

    Default ``0`` ⇒ ``None`` ⇒ the historical unbounded behaviour, so
    existing callers are unaffected. Set ``OPENPROGRAM_EXEC_TIMEOUT_S`` to
    a positive number to arm a deadline on EVERY exec from one place — the
    cheapest way to stop an un-armed caller (a benchmark that forgot to
    pass ``timeout_s``, a worker turn) from running an unbounded nested
    retry storm. A deliberately non-arming default: a too-tight blanket
    timeout would false-positive on legitimately long reasoning turns, so
    the value is left to the deployment rather than hard-coded here.
    """
    try:
        v = float(os.environ.get("OPENPROGRAM_EXEC_TIMEOUT_S", "0") or 0)
    except ValueError:
        return None
    return v if v > 0 else None


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
    override_reason: "Optional[ErrorReason]" = None,
) -> "LLMError":  # type: ignore[name-defined]
    """Construct the structured exception ``exec()`` raises when it
    gives up.

    Collects everything a caller needs to decide "retry the whole
    turn", "reauthenticate", "trim prompt", or "circuit-break this
    provider":

      * ``reason`` — classified by :func:`classify_error`, or forced
        via ``override_reason`` (e.g. TIMEOUT for deadline hits where
        the underlying cause was incidental, not the real reason
        we're giving up).
      * ``retryable`` — honest about whether the underlying kind was
        transient; ``False`` for permanent failures (auth / invalid
        request / context overflow / timeout). Note: ``retryable=True``
        means "the kind was transient but we exhausted our budget",
        not "you should retry this immediately"
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
    # it (HTTP providers stash it on the exc via ProviderStreamError).
    http_status = getattr(cause, "http_status", None) or getattr(cause, "status_code", None)
    retry_after_s = getattr(cause, "retry_after_s", None)
    error_text = getattr(cause, "error_text", "") or ""

    if override_reason is not None:
        reason = override_reason
        # An overridden reason (currently only TIMEOUT) is always
        # non-retryable in this attempt budget: even if the underlying
        # transport error was transient, *we* gave up because of a
        # deadline, not because the kind was permanent.
        retryable = False
    else:
        reason, kind_retryable = classify_error(
            cause, http_status=http_status, error_text=error_text,
        )
        # Even if the underlying kind was retryable, when we gave up
        # because the budget was exhausted, retryable stays True
        # (caller may decide to retry the whole turn with a fresh
        # budget). When the failure was permanent, force
        # retryable=False regardless of what classify_error said.
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


def _fire_on_retry(
    on_retry: "Optional[Callable[[RetryInfo], None]]",
    *,
    cause: BaseException,
    attempt: int,
    max_attempts: int,
    sleep_s: float,
    elapsed_s: float,
    retry_after_s: Optional[float],
) -> None:
    """Invoke an ``on_retry`` callback safely.

    Exceptions inside the callback are swallowed — a broken hook
    must never prevent the retry loop from making progress. The
    callback receives a fully-populated :class:`RetryInfo`,
    classified the same way as the final :class:`LLMError` would
    be, so consumers can route on ``info.reason`` without
    re-classifying.
    """
    if on_retry is None:
        return
    from openprogram.providers.utils.errors import (
        RetryInfo, classify_error, ErrorReason,
    )
    http_status = getattr(cause, "http_status", None) or getattr(cause, "status_code", None)
    reason, _ = classify_error(cause, http_status=http_status,
                               error_text=getattr(cause, "error_text", "") or "")
    info = RetryInfo(
        attempt=attempt,
        max_attempts=max_attempts,
        reason=reason,
        sleep_s=sleep_s,
        elapsed_s=elapsed_s,
        retry_after_s=retry_after_s,
        last_error_type=type(cause).__name__,
        last_error_msg=str(cause),
    )
    try:
        on_retry(info)
    except Exception:
        # Don't break the retry loop on a buggy hook. Print once for
        # the operator; future identical hook failures stay silent.
        import sys as _sys
        print(f"[runtime] on_retry callback raised; ignoring: "
              f"{type(_sys.exc_info()[1]).__name__}", file=_sys.stderr)

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

# Agent-loop options for the current exec() call — tool_choice /
# parallel_tool_calls / max_iterations travel to _call_via_providers'
# AgentSession the same way the tools list does (the _call() signature
# subclasses override stays unchanged). Only non-default values are
# stored; missing keys mean "provider / loop default".
_current_loop_opts: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_current_loop_opts", default=None,
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
            max_retries: Maximum number of exec() attempts before raising.
                         ``None`` (default) → read ``OPENPROGRAM_MAX_RETRIES``
                         env, fall back to 6. Set explicitly to override
                         env. 6 means try once + retry five times on
                         transient failure, with exponential backoff +
                         ±25% jitter — wall-clock at worst ≈ 1.5 + 3 +
                         6 + 12 + 24 = 46s of sleeping before giving
                         up (tunable via ``OPENPROGRAM_RETRY_BACKOFF_BASE``).
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
        # 提问通道（runtime.ask 的出口）。默认 None → 走事件层（前端卡片 + 总线）。
        # @agentic_function 跑的子进程里，process_runner 会换成 QueueTransport
        # （经 mp.Queue 把问题送回父进程）。对齐 logging：通道显式挂在对象上。
        self._question_transport = None

        if max_retries is None:
            max_retries = _default_max_retries()
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
          1. Load the DAG state from the store.
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

            # Synthesize the current turn from ``content`` blocks via
            # the same helper the no-store fallback uses, so image /
            # video / audio blocks survive the DAG render path. The old
            # version concatenated text parts only — any screenshot the
            # caller attached to this turn (gui_agent's verify / plan /
            # locate sub-calls all do this) was silently dropped, so
            # the LLM ended up reasoning over the OCR/component text
            # alone and missed the current frame.
            ctx, _sp = _build_pi_context(content or [])
            return history + [ctx.messages[0]]
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

    # --- Asking the user (user-input-requests.md Phase 1) ---

    def _ui_session_id(self) -> str:
        """前端路由用的 webui session（dispatcher 在执行上下文里设的
        ContextVar），不是 Runtime 自己的 op-xxx id。无 webui 时为空串。"""
        try:
            from openprogram.webui._pause_stop import get_current_session_id
            return get_current_session_id() or ""
        except Exception:
            return ""

    def can_ask(self) -> bool:
        """当前是否有人能回答（有前端会话连着）。headless 跑时为 False，
        作者可据此分支（user-input-requests.md API）。"""
        return bool(self._ui_session_id())

    def set_question_transport(self, transport) -> None:
        """换掉这个 runtime 的提问通道（QuestionTransport）。子进程入口用它
        装上 QueueTransport，把 runtime.ask 的问题经 mp.Queue 送回父进程。
        传 None 恢复默认（事件层）。"""
        self._question_transport = transport

    def _ask_raw(self, *, kind, prompt, options=None, multi=False,
                 allow_custom=True, detail="", timeout=300.0):
        from openprogram.agent.questions import ask_blocking, emit_question_asked

        transport = getattr(self, "_question_transport", None)  # None → 默认事件层通道

        def _on_asked(q):
            # 经本 runtime 的提问通道把问题送出去：worker 进程默认走事件层
            # （前端卡片 + 总线）；@agentic_function 跑的子进程被 process_runner
            # 换成 QueueTransport（经 mp.Queue 送回父进程 registry）。
            emit_question_asked({
                "id": q.id, "session_id": q.session_id, "kind": q.kind,
                "prompt": q.prompt, "options": q.options, "multi": q.multi,
                "allow_custom": q.allow_custom, "detail": q.detail,
                "expires_at": q.expires_at,
            }, transport)

        return ask_blocking(
            session_id=self._ui_session_id(), kind=kind, prompt=prompt,
            options=options, multi=multi, allow_custom=allow_custom,
            detail=detail, timeout=timeout, on_asked=_on_asked,
        )

    def ask(self, prompt: str, *, options=None, multi: bool = False,
            allow_custom: bool = True, timeout: float = 300.0, default=None):
        """问用户一个问题，阻塞到有答案。三态：答了→返回答案
        （multi=True 返回 list[str]）；用户拒绝→抛 UserDeclined；超时→有
        default 返回 default，否则抛 AskTimeout。"""
        from openprogram.agent.questions import UserDeclined, AskTimeout
        outcome, value = self._ask_raw(
            kind="ask", prompt=prompt, options=options, multi=multi,
            allow_custom=allow_custom, timeout=timeout,
        )
        if outcome == "answered":
            return value
        if outcome == "declined":
            raise UserDeclined(prompt)
        # timeout
        if default is not None:
            return default
        raise AskTimeout(prompt)

    def confirm(self, prompt: str, *, detail: str = "",
                timeout: float = 300.0, default: bool = False) -> bool:
        """问一个是/否，返回 bool。拒绝=False；超时返回 default（不抛）。"""
        outcome, value = self._ask_raw(
            kind="confirm", prompt=prompt, detail=detail,
            options=["确认", "取消"], allow_custom=False, timeout=timeout,
        )
        if outcome == "answered":
            if isinstance(value, str):
                return value.strip() in ("确认", "yes", "y", "true", "ok", "是")
            return bool(value)
        if outcome == "declined":
            return False
        return default  # timeout

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
        timeout_s: Optional[float] = None,
        on_retry: Optional["Callable[[RetryInfo], None]"] = None,
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
        # Loop options — only non-default values travel ("auto" / True
        # are the provider defaults, sending them adds nothing).
        _loop_opts = {}
        if tool_choice is not None and tool_choice != "auto":
            _loop_opts["tool_choice"] = tool_choice
        if parallel_tool_calls is False:
            _loop_opts["parallel_tool_calls"] = False
        if max_iterations is not None:
            _loop_opts["max_iterations"] = max_iterations
        loop_opts_token = (
            _current_loop_opts.set(_loop_opts) if _loop_opts else None
        )
        reply = None
        _exec_start = time.monotonic()
        if not (timeout_s and timeout_s > 0):
            timeout_s = _default_exec_timeout_s()
        _deadline = _exec_start + timeout_s if (timeout_s and timeout_s > 0) else None
        # Publish the deadline so the provider's INNER stream-retry loop and
        # the SSE parser honour the SAME wall-clock budget — otherwise the
        # nested loops multiply (max_attempts × max_retries) with nobody
        # capping the total. See docs/design/providers/error-and-timeout-mechanism.html.
        from openprogram.providers.utils.errors import ExecInterrupt
        from openprogram.providers.utils import deadline as _dl
        _deadline_token = _dl.set_deadline(_deadline)
        try:
            errors: list[str] = []
            for attempt in range(self.max_retries):
                # Pre-attempt deadline check: previous sleep or _call
                # may have already crossed the line, in which case we
                # don't even start another attempt.
                if _deadline is not None and time.monotonic() >= _deadline:
                    from openprogram.providers.utils.errors import ErrorReason as _ER
                    cause = TimeoutError(
                        f"exec() timed out after {timeout_s}s "
                        f"({attempt} attempt(s))"
                    )
                    raise _build_llm_error(
                        cause=cause, attempts=max(1, attempt),
                        elapsed_s=time.monotonic() - _exec_start,
                        content=content, model=use_model,
                        provider=getattr(self, "provider", None),
                        history=errors,
                        permanent=True,
                        override_reason=_ER.TIMEOUT,
                    ) from cause

                try:
                    reply = self._call(call_input, model=use_model, response_format=response_format)
                    self._append_model_call_node(
                        reply=reply,
                        model=use_model,
                        content_text=content_text,
                    )
                    break
                except ExecInterrupt:
                    raise  # caller hard-stop — bypass the retry layer
                except (TypeError, NotImplementedError):
                    raise  # Programming errors — don't retry
                except Exception as e:
                    errors.append(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
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
                    if permanent or transport_done or timed_out or attempt == self.max_retries - 1:
                        from openprogram.providers.utils.errors import ErrorReason as _ER
                        raise _build_llm_error(
                            cause=e, attempts=attempt + 1,
                            elapsed_s=elapsed,
                            content=content, model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=permanent or timed_out,
                            override_reason=_ER.TIMEOUT if timed_out else None,
                        ) from e
                    # Honor server-supplied Retry-After when the
                    # underlying provider attached it to the exception.
                    retry_after_s = getattr(e, "retry_after_s", None)
                    sleep_s = _retry_sleep_seconds(attempt, retry_after_s)

                    # Would sleeping cross the deadline? If yes, give
                    # up now as TIMEOUT — don't waste wall-clock on a
                    # backoff we'll never get to consume.
                    if _deadline is not None and (time.monotonic() + sleep_s) >= _deadline:
                        from openprogram.providers.utils.errors import ErrorReason as _ER
                        raise _build_llm_error(
                            cause=e, attempts=attempt + 1,
                            elapsed_s=elapsed,
                            content=content, model=use_model,
                            provider=getattr(self, "provider", None),
                            history=errors,
                            permanent=True,
                            override_reason=_ER.TIMEOUT,
                        ) from e

                    _fire_on_retry(
                        on_retry, cause=e, attempt=attempt + 1,
                        max_attempts=self.max_retries, sleep_s=sleep_s,
                        elapsed_s=elapsed, retry_after_s=retry_after_s,
                    )
                    time.sleep(sleep_s)
        finally:
            _dl.reset_deadline(_deadline_token)
            if tools_token is not None:
                _current_tools.reset(tools_token)
            if policy_token is not None:
                _current_tool_policy.reset(policy_token)
            if loop_opts_token is not None:
                _current_loop_opts.reset(loop_opts_token)

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
                    cause=cause, attempts=self.max_retries,
                    elapsed_s=time.monotonic() - _exec_start,
                    content=content, model=use_model,
                    provider=getattr(self, "provider", None),
                    history=[],
                    permanent=True,
                    override_reason=_ER.TIMEOUT,
                ) from cause

        from openprogram.agentic_programming.decision import resolve_decision
        return resolve_decision(
            reply, _decision_menu, _decision_values, self,
            timeout_s=remaining_timeout, on_retry=on_retry,
        )

    async def async_exec(
        self,
        content: list[dict],
        context: Optional[str] = None,
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
        timeout_s: Optional[float] = None,
        on_retry: Optional["Callable[[RetryInfo], None]"] = None,
    ) -> str:
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
        if not (timeout_s and timeout_s > 0):
            timeout_s = _default_exec_timeout_s()
        _deadline = _exec_start + timeout_s if (timeout_s and timeout_s > 0) else None
        # Same end-to-end deadline publish as exec() — the inner stream-retry
        # loop and SSE parser read it. See error-and-timeout-mechanism.html.
        from openprogram.providers.utils.errors import ExecInterrupt
        from openprogram.providers.utils import deadline as _dl
        _deadline_token = _dl.set_deadline(_deadline)
        try:
          for attempt in range(self.max_retries):
            # Pre-attempt deadline check (see exec() for the rationale).
            if _deadline is not None and time.monotonic() >= _deadline:
                from openprogram.providers.utils.errors import ErrorReason as _ER
                cause = TimeoutError(
                    f"async_exec() timed out after {timeout_s}s "
                    f"({attempt} attempt(s))"
                )
                raise _build_llm_error(
                    cause=cause, attempts=max(1, attempt),
                    elapsed_s=time.monotonic() - _exec_start,
                    content=content, model=use_model,
                    provider=getattr(self, "provider", None),
                    history=errors,
                    permanent=True,
                    override_reason=_ER.TIMEOUT,
                ) from cause

            try:
                reply = await self._async_call(call_input, model=use_model, response_format=response_format)
                self._append_model_call_node(
                    reply=reply,
                    model=use_model,
                    content_text=content_text,
                )
                return reply
            except ExecInterrupt:
                raise  # caller hard-stop — bypass the retry layer
            except (TypeError, NotImplementedError):
                raise
            except Exception as e:
                errors.append(f"Attempt {attempt + 1}: {type(e).__name__}: {e}")
                permanent = _is_permanent_error(e)
                # Don't re-retry a transport error the provider already
                # exhausted its own budget on (the 3×6 multiplication).
                transport_done = bool(getattr(e, "transport_exhausted", False))
                elapsed = time.monotonic() - _exec_start
                timed_out = _deadline is not None and time.monotonic() >= _deadline
                if permanent or transport_done or timed_out or attempt == self.max_retries - 1:
                    from openprogram.providers.utils.errors import ErrorReason as _ER
                    raise _build_llm_error(
                        cause=e, attempts=attempt + 1,
                        elapsed_s=elapsed,
                        content=content, model=use_model,
                        provider=getattr(self, "provider", None),
                        history=errors,
                        permanent=permanent or timed_out,
                        override_reason=_ER.TIMEOUT if timed_out else None,
                    ) from e
                retry_after_s = getattr(e, "retry_after_s", None)
                sleep_s = _retry_sleep_seconds(attempt, retry_after_s)

                if _deadline is not None and (time.monotonic() + sleep_s) >= _deadline:
                    from openprogram.providers.utils.errors import ErrorReason as _ER
                    raise _build_llm_error(
                        cause=e, attempts=attempt + 1,
                        elapsed_s=elapsed,
                        content=content, model=use_model,
                        provider=getattr(self, "provider", None),
                        history=errors,
                        permanent=True,
                        override_reason=_ER.TIMEOUT,
                    ) from e

                _fire_on_retry(
                    on_retry, cause=e, attempt=attempt + 1,
                    max_attempts=self.max_retries, sleep_s=sleep_s,
                    elapsed_s=elapsed, retry_after_s=retry_after_s,
                )
                await asyncio.sleep(sleep_s)
        finally:
            _dl.reset_deadline(_deadline_token)

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

        loop_opts = _current_loop_opts.get(None) or {}
        session = AgentSession(
            model=self.api_model,
            tools=agent_tools,
            system_prompt=system_prompt,
            api_key=self.api_key,
            session_id=self.session_id,
            thinking_level=self.thinking_level,
            tool_choice=loop_opts.get("tool_choice"),
            parallel_tool_calls=loop_opts.get("parallel_tool_calls"),
            max_iterations=loop_opts.get("max_iterations"),
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
    # Carry the caller's ContextVars (the published exec deadline, the
    # active tool policy, …) into the worker thread — a bare pool.submit
    # runs the callable in a fresh, empty context and would drop them, so
    # the inner stream-retry loop would never see the deadline.
    ctx = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(ctx.run, asyncio.run, coro).result()


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
            parts.append(TextContent(type="text", text=block["text"],
                                     cache_control=block.get("cache_control")))
        elif btype == "image":
            data, mime = _load_media(block, _media_defaults["image"])
            parts.append(ImageContent(type="image", data=data, mime_type=mime,
                                       cache_control=block.get("cache_control")))
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


