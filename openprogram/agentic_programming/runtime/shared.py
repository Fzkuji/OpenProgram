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
import logging
import os
import random
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable, Optional

from openprogram.providers.structured_output import StructuredOutputError

if TYPE_CHECKING:
    from openprogram.providers.utils.errors import (
        ErrorReason,
        LLMError,
        RetryInfo,
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
try:
    _RETRY_BACKOFF = float(os.environ.get("OPENPROGRAM_RETRY_BACKOFF_BASE", "1.5"))
except (TypeError, ValueError):
    _RETRY_BACKOFF = 1.5

_log = logging.getLogger(__name__)


@dataclass
class _ExecCallState:
    """Per-exec() scratch fields. Nested exec() must not share these."""

    active_llm_node_id: Optional[str] = None
    last_blocks: list = field(default_factory=list)
    last_usage: Any = None
    pending_tool_names: list = field(default_factory=list)
    pending_system_prompt: str = ""
    pending_breakdown: Any = None
    last_agent_iteration_count: int = 0


_current_exec_state: contextvars.ContextVar[Optional[_ExecCallState]] = (
    contextvars.ContextVar("_current_exec_state", default=None)
)


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
    base = _RETRY_BACKOFF * (2**attempt)
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
    """True if retrying ``exc`` is pointless (malformed request / bad auth).

    Honors a provider's explicit verdict first: a ``ProviderStreamError``
    (or any exception) that already set ``retryable=False`` has been judged
    non-retryable by the provider's own stream-retry layer — exec must NOT
    re-retry it with a fresh budget. Without this, exec only string-matched
    the message and so re-tried provider-declared-permanent failures (e.g.
    codex's empty ``{"type":"error"}`` event surfaced as
    "Error Code None: None", retryable=False) the full max_retries times,
    multiplying one transient backend hiccup into a long, doomed retry storm
    that still crashed the run.
    """
    if getattr(exc, "retryable", None) is False:
        return True
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
        LLMError,
        classify_error,
        had_image as _had_image,
    )

    # Try to pull HTTP status from the cause if the provider attached
    # it (HTTP providers stash it on the exc via ProviderStreamError).
    http_status = getattr(cause, "http_status", None) or getattr(
        cause, "status_code", None
    )
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
            cause,
            http_status=http_status,
            error_text=error_text,
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
        RetryInfo,
        classify_error,
        ErrorReason,
    )

    http_status = getattr(cause, "http_status", None) or getattr(
        cause, "status_code", None
    )
    reason, _ = classify_error(
        cause,
        http_status=http_status,
        error_text=getattr(cause, "error_text", "") or "",
    )
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

        print(
            f"[runtime] on_retry callback raised; ignoring: "
            f"{type(_sys.exc_info()[1]).__name__}",
            file=_sys.stderr,
        )


# Context var for the tools passed into the current exec() call.
# _call_via_providers reads it to feed AgentSession without changing
# the _call() signature subclasses override.
_current_tools: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "_current_tools",
    default=None,
)

# OpenClaw-style tool policy that overlays on top of the chosen tool
# list. Set by callers (dispatcher / channels / runtime.exec kwargs)
# to filter the resolved tools per-call without renaming them. Shape:
# ``{"toolset": "research", "source": "wechat", "allow": [...], "deny": [...]}``.
# Any subset of keys is valid; missing keys mean "no constraint".
_current_tool_policy: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_current_tool_policy",
    default=None,
)

# Agent-loop options for the current exec() call — tool_choice /
# parallel_tool_calls / max_iterations travel to _call_via_providers'
# AgentSession the same way the tools list does (the _call() signature
# subclasses override stays unchanged). Only non-default values are
# stored; missing keys mean "provider / loop default".
_current_loop_opts: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_current_loop_opts",
    default=None,
)

_current_response_format: contextvars.ContextVar[Optional[Any]] = (
    contextvars.ContextVar(
        "_current_response_format",
        default=None,
    )
)

_current_model_call_budget: contextvars.ContextVar[Optional[dict]] = (
    contextvars.ContextVar("_current_model_call_budget", default=None)
)

# Per-exec stream-fn override. exec(stream_fn=...) sets it so the dispatcher
# (and integration tests) can inject a fake / pre-built stream into the same
# _call_via_providers → AgentSession path real provider calls use. None →
# fall back to the runtime's own _stream_fn (CallableModel) or the provider.
_current_stream_fn: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "_current_stream_fn",
    default=None,
)

# Per-call overrides used by the shared AgentSession/provider path. They stay
# in ContextVars so llm() never mutates the ambient Runtime's session defaults.
_current_effort: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_effort", default=None,
)
_current_call_model: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_call_model", default=None,
)
_current_direct_content: contextvars.ContextVar[Optional[list[dict]]] = (
    contextvars.ContextVar("_current_direct_content", default=None)
)


def _exec_system_prompt(inline: str, tools: Optional[list]) -> str:
    """Assemble the function-body system prompt through the ONE assembler
    (dag/overview.md §7).

    The runtime has no AgentSpec, so it passes the active agent id (falling
    back to the default agent) plus its own ``self.system`` as the inline
    layer. Any failure degrades to the bare inline string — a function call
    must never die because a context component misbehaved.
    """
    try:
        from openprogram.context.components import build_system_prompt
        from openprogram.agent.internals._model_tools import load_agent_profile
        from openprogram.agent.management import manager as _A

        profile = dict(load_agent_profile(getattr(_A, "DEFAULT_AGENT_ID", "main")))
        if inline:
            profile["system_prompt"] = inline
        else:
            profile.pop("system_prompt", None)
        return build_system_prompt(profile, tools=tools)
    except Exception:
        return inline


def _situational_prefix(
    fn_name: str,
    fn_doc: str,
    call_path: str = "",
    position: str = "",
    output_contract: str = "",
) -> str:
    """L2 situation prompt prefixed to the inner model's turn — tells it
    which agentic function it is inside, what its job is, the call path that
    led here, where it sits, and where its output goes. Also the
    self-recursion guidance (the function's own tool stays visible; the model
    is steered away from re-entering it).

    Design: docs/design/context/composition.md §四'·situation. Wrapped
    in a <situation> tag (paired XML, like the other context sections).
    Optional fields render only when provided, so existing call sites that pass
    just (fn_name, fn_doc) stay backward-compatible.

    English to match the rest of the model-facing prompt.
    """
    lines = [f"You are running INSIDE the agentic function `{fn_name}`."]
    if fn_doc and fn_doc.strip():
        lines.append(f"Job: {fn_doc.strip()}")
    if call_path and call_path.strip():
        lines.append(f"Call path: {call_path.strip()}")
    if position and position.strip():
        lines.append(f"Position: {position.strip()}")
    if output_contract and output_contract.strip():
        lines.append(f"Your output: {output_contract.strip()}")
    lines.append(
        f"The tool list may include `{fn_name}` itself — do NOT call it "
        "(re-entering causes infinite recursion). Use lower-level tools "
        "(search / read-write files / run code) to do the work directly."
    )
    return "<situation>\n" + "\n".join(lines) + "\n</situation>"


def _compute_call_path(graph, frame_node_id: str, max_depth: int = 20) -> str:
    """Walk up the ``caller`` chain from ``frame_node_id`` to the root,
    collecting the names of agentic-function (code) nodes, and join them
    into ``"root → ... → current"``.

    Read-only over ``graph``. Never raises — any failure returns "" so the
    caller degrades to "fn_name only, no call path" (the prior behaviour).

    - Only nodes that have a ``name`` are collected (skips anonymous /
      meaningless intermediate nodes).
    - Cycle-guarded (``seen``) and depth-capped (``max_depth``); when the
      cap is hit the path is truncated with a leading "…".
    - Stops when a node's ``caller`` is empty or points outside the graph.
    """
    try:
        if not graph or frame_node_id not in graph.nodes:
            return ""
        names: list[str] = []
        seen: set[str] = set()
        cur: Optional[str] = frame_node_id
        truncated = False
        while cur and cur in graph.nodes:
            if cur in seen:
                break
            seen.add(cur)
            if len(seen) > max_depth:
                truncated = True
                break
            node = graph.nodes[cur]
            name = getattr(node, "name", "") or ""
            if name:
                names.append(name)
            cur = getattr(node, "caller", "") or None
        if not names:
            return ""
        names.reverse()  # root → ... → current
        if truncated:
            names.insert(0, "…")
        return " → ".join(names)
    except Exception:
        return ""




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
    # Detect a running loop, then run OUTSIDE the try/except. If we called
    # asyncio.run() inside the `except RuntimeError` and the coroutine later
    # raised, Python would chain the caught ``RuntimeError('no running event
    # loop')`` as that error's ``__context__`` — a misleading "During handling
    # of the above exception" traceback stacked over the real provider error.
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is None:
        return asyncio.run(_run_and_reap(coro))
    import concurrent.futures

    # Carry the caller's ContextVars (the published exec deadline, the
    # active tool policy, …) into the worker thread — a bare pool.submit
    # runs the callable in a fresh, empty context and would drop them, so
    # the inner stream-retry loop would never see the deadline.
    ctx = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(ctx.run, asyncio.run, _run_and_reap(coro)).result()


async def _reap_loop_clients() -> None:
    """Close shared httpx clients bound to the running loop (best-effort)."""
    try:
        from openprogram.providers.utils.http_client import (
            aclose_current_loop_clients,
        )

        await aclose_current_loop_clients()
    except Exception:
        pass


async def _run_and_reap(coro):
    """Await ``coro``, then close any shared httpx client this throwaway loop
    built, *before* ``asyncio.run`` tears the loop down.

    Every ``Runtime.exec`` provider call reaches here via ``asyncio.run`` on a
    fresh loop. Providers that keep-alive-cache their client
    (``get_shared_async_client``) key it by ``(name, loop_id)``; once this loop
    dies the entry is dead weight — unusable (httpx forbids cross-loop reuse)
    and never evicted, leaking one connection pool + its sockets per call.
    Reaping here, while still inside the loop, closes it cleanly and keeps the
    cache from growing without bound. Best-effort: never mask the real result.
    """
    try:
        return await coro
    finally:
        await _reap_loop_clients()


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

    def _load_media(block: dict, default_mime: str) -> tuple[str, str] | None:
        data = block.get("data")
        mime = block.get("mime_type")
        if not data:
            path = block.get("path")
            if not path:
                _log.warning("skipping media block without data or path")
                return None
            from openprogram.sandbox import validate_read_path

            violation = validate_read_path(path)
            if violation:
                _log.warning("skipping media block: %s", violation)
                return None
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
            parts.append(
                TextContent(
                    type="text",
                    text=block["text"],
                    cache_control=block.get("cache_control"),
                )
            )
        elif btype == "image":
            loaded = _load_media(block, _media_defaults["image"])
            if loaded is None:
                continue
            data, mime = loaded
            parts.append(
                ImageContent(
                    type="image",
                    data=data,
                    mime_type=mime,
                    cache_control=block.get("cache_control"),
                )
            )
        elif btype == "video":
            loaded = _load_media(block, _media_defaults["video"])
            if loaded is None:
                continue
            data, mime = loaded
            parts.append(VideoContent(type="video", data=data, mime_type=mime))
        elif btype == "audio":
            loaded = _load_media(block, _media_defaults["audio"])
            if loaded is None:
                continue
            data, mime = loaded
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


async def _invoke_adapted_executor(_exec, args: dict, signal):
    """Call an OpenProgram tool executor with the pi-agent (args, signal) pair.

    Retries the single-dict form only when the signature (or a bind-layer
    TypeError) says ``**args`` is the wrong calling convention — not when
    the function body itself raises TypeError.
    """
    kwargs = dict(args or {})
    extra = {}
    sig = None
    try:
        sig = inspect.signature(_exec)
        params = sig.parameters
    except (TypeError, ValueError):
        params = {}
    if "signal" in params:
        extra["signal"] = signal
        kwargs["signal"] = signal
    elif "cancel" in params:
        extra["cancel"] = signal
        kwargs["cancel"] = signal

    use_kwargs = True
    if sig is not None:
        try:
            sig.bind(**kwargs)
        except TypeError:
            use_kwargs = False

    async def _call(use_kw: bool):
        if inspect.iscoroutinefunction(_exec):
            if use_kw:
                return await _exec(**kwargs)
            return await _exec(args, **extra) if extra else await _exec(args)
        if use_kw:
            return await asyncio.to_thread(lambda: _exec(**kwargs))
        if extra:
            return await asyncio.to_thread(lambda: _exec(args, **extra))
        return await asyncio.to_thread(lambda: _exec(args))

    if sig is not None:
        return await _call(use_kwargs)
    try:
        return await _call(True)
    except TypeError as exc:
        msg = str(exc)
        if any(
            m in msg
            for m in (
                "unexpected keyword",
                "required positional argument",
                "positional arguments but",
            )
        ):
            return await _call(False)
        raise


def _adapt_tools(raw_tools: list) -> list:
    """Convert OpenProgram's tool entries into pi-agent ``AgentTool`` objects.

    Accepted input forms (per tool entry):
      - a native ``AgentTool``
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
        if isinstance(entry, AgentTool):
            adapted.append(entry)
            continue
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

        async def _run(
            tool_call_id: str, args: dict, signal, update_cb, _exec=captured_executor
        ) -> "AgentToolResult":
            result = await _invoke_adapted_executor(_exec, args, signal)

            if isinstance(result, str):
                text = result
            else:
                try:
                    text = json.dumps(result, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    text = str(result)
            return AgentToolResult(content=[TextContent(type="text", text=text)])

        adapted.append(
            AgentTool(
                name=spec["name"],
                description=spec.get("description", ""),
                parameters=spec.get("parameters")
                or {"type": "object", "properties": {}},
                label=spec.get("label", spec["name"]),
                execute=_run,
            )
        )
    return adapted
