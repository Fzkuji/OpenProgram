"""
OpenAI Codex Responses API provider (ChatGPT backend).

Supports SSE transport with retry logic and session-based connection pooling.

Mirrors openai-codex-responses.ts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from typing import TYPE_CHECKING, Any

from openprogram.providers.models import supports_xhigh
from openprogram.providers.budget import provider_retry_attempts
from openprogram.providers._shared.openai_responses import (
    convert_responses_messages,
    convert_responses_tools,
    process_responses_stream,
)
from openprogram.providers._shared.validate_modalities import validate_input_modalities
from openprogram.providers._shared.simple_options import build_base_options, clamp_reasoning
from openprogram.providers.utils.event_stream import EventStream
from openprogram.providers.utils.http_client import (
    build_async_client,
    get_shared_async_client,
)
from openprogram.providers.utils.rate_limit import parse_rate_limit
from openprogram.providers.utils.stream_retry import (
    PROVIDER_STREAM_MAX_ATTEMPTS,
    ProviderStreamError,
    is_retryable_status,
    read_retry_after,
    retry_stream,
)

if TYPE_CHECKING:
    from openprogram.providers.types import Context, Model, SimpleStreamOptions

_DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api"

# Back-compat alias kept so external code that imports the old
# CodexStreamError name still works. New code should use
# ProviderStreamError from utils.stream_retry directly.
CodexStreamError = ProviderStreamError


def _recover_partial_enabled() -> bool:
    """Partial-response recovery toggle (default on)."""
    return os.environ.get("OPENPROGRAM_PARTIAL_RECOVERY", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def _is_permanent_failure(exc: Exception) -> bool:
    """True for failures that should hard-fail even when partial content
    already streamed: auth / authorization / invalid request / context
    overflow / content policy. Everything else (transport, rate-limit,
    provider-internal, timeout, unknown) is treated as a transient
    mid-stream break that's safe to salvage — the request was clearly
    accepted, since content came back."""
    from openprogram.providers.utils.errors import classify_error, ErrorReason
    reason, _ = classify_error(
        exc,
        http_status=getattr(exc, "http_status", None),
        error_text=getattr(exc, "error_text", "") or "",
    )
    return reason in (
        ErrorReason.AUTHENTICATION,
        ErrorReason.AUTHORIZATION,
        ErrorReason.INVALID_REQUEST,
        ErrorReason.CONTEXT_LENGTH,
        ErrorReason.CONTENT_POLICY,
    )


def _resolve_codex_bearer_token(opts_api_key: str | None) -> str:
    """Resolve the bearer token codex requests need to authorize.

    Codex (ChatGPT subscription) auth has three valid sources:

      1. Explicit ``api_key`` passed in opts — caller-supplied,
         always wins.
      2. The provider's env var (handled by ``resolve_provider_key``) —
         covers users on a bare API key with no OAuth flow.
      3. CredentialProvider's OAuth credential pool — covers users who ran
         ``codex login`` (or the OpenProgram OAuth wizard) so the
         pool has a ``CredentialData(kind="oauth").auth_value`` ready.

    Returns ``""`` when nothing yields a usable token, so the caller
    can raise the same "No API key for provider" error as before.
    Pre-fix: only sources 1 and 2 were checked, so OAuth users —
    despite a populated ``~/.openprogram/auth/openai-codex/default.json``
    — got the error the moment a stream started.
    """
    if opts_api_key:
        return opts_api_key

    from openprogram.providers.env_api_keys import resolve_provider_key
    env_key = resolve_provider_key("openai-codex")
    if env_key:
        return env_key

    # Try the OAuth pool. acquire_sync auto-refreshes if the token is
    # within the skew window, so we never serve a stale access_token.
    # Read the bearer via the canonical resolver (``auth_value``), NOT the
    # raw ``payload.access_token`` attribute: OAuth credentials carry the
    # live bearer in ``auth_value`` (``access_token`` is often None after a
    # refresh/re-login rewrites the payload), which silently broke codex —
    # and chatgpt-subscription, which routes through this same resolver.
    try:
        from openprogram.auth.credential_provider import get_credential_provider
        from openprogram.auth.resolver import resolve_connection
        cred = get_credential_provider().acquire_sync("openai-codex")
        conn = resolve_connection(cred)
        if conn and conn.auth_value:
            return conn.auth_value
    except Exception:
        # CredentialProvider raises when no provider config is registered or
        # no credentials exist. Both are recoverable — fall through to
        # the empty-string return so the caller's check fires the same
        # actionable error message.
        pass

    return ""


def stream_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: dict[str, Any] | None = None,
) -> EventStream:
    """Stream from OpenAI Codex (ChatGPT backend) Responses API."""
    opts = options or {}
    ev_stream: EventStream = EventStream()

    validate_input_modalities(model, context)

    async def _run() -> None:
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "The HTTP client is missing; reinstall the complete OpenProgram release."
            )

        from openprogram.providers.types import AssistantMessage, Usage

        output = AssistantMessage(
            content=[],
            api="openai-codex",
            provider=model.provider,
            model=model.id,
            usage=Usage(),
            stop_reason="stop",
            timestamp=int(time.time() * 1000),
        )

        def _has_substantive_content() -> bool:
            """Whether retrying would duplicate content visible to the caller.

            Responses streams announce an output item before sending any of
            its text, reasoning summary, or tool arguments.  A prematurely
            terminated stream can therefore leave an empty placeholder in
            ``output.content``.  Treating that placeholder as committed made
            Codex accept ``[DONE]`` after only ``response.output_item.added``
            as a successful empty reply, and also disabled the retry that
            could recover it.
            """
            for block in output.content:
                if not isinstance(block, dict):
                    return True
                block_type = block.get("type")
                if block_type == "text" and block.get("text"):
                    return True
                if block_type == "thinking" and block.get("thinking"):
                    return True
                if block_type == "toolCall" and (
                    block.get("partial_json") or "index" not in block
                ):
                    return True
            return False

        # --- Prep that's identical across retry attempts ----------
        try:
            api_key = _resolve_codex_bearer_token(opts.get("api_key"))
            if not api_key:
                raise ValueError(f"No API key for provider: {model.provider}")
            base_url = getattr(model, "base_url", None) or _DEFAULT_CODEX_BASE_URL
            messages = convert_responses_messages(model, context, include_system_prompt=False)
            request_body = _build_request_body(model, context, opts, messages)

            if opts.get("on_payload"):
                opts["on_payload"](request_body)

            # Per-request debug print — useful for tracing prompt cache
            # hits / payload growth, but it polluted the chat UI on every
            # turn ("[openai-codex req] key=... text_chars=... reasoning=
            # {...}" landing between the user's message and the model's
            # reply). Gate it behind an opt-in env var.
            if os.environ.get("OPENPROGRAM_DEBUG_PROVIDER", "").strip() in ("1", "true", "yes"):
                try:
                    import hashlib as _hashlib

                    _cache_key = request_body.get("prompt_cache_key", "")
                    _instr_len = len(request_body.get("instructions") or "")
                    _tool_names = sorted(t.get("name", "") for t in (request_body.get("tools") or []))
                    _reasoning = request_body.get("reasoning")
                    _tool_hash = _hashlib.sha256(
                        json.dumps(
                            request_body.get("tools") or [],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()[:12]
                    _input_items = request_body.get("input") or []
                    _input_parts = [
                        len(item.get("content") or [])
                        for item in _input_items
                        if isinstance(item, dict)
                    ]
                    _input_part_lengths = [
                        [
                            len(part.get("text") or "")
                            for part in (item.get("content") or [])
                            if isinstance(part, dict)
                        ]
                        for item in _input_items
                        if isinstance(item, dict)
                    ]
                    _input_text_len = sum(
                        len(c.get("text", ""))
                        for item in _input_items
                        if isinstance(item, dict)
                        for c in (item.get("content") or [])
                        if isinstance(c, dict) and isinstance(c.get("text"), str)
                    )
                    print(
                        f"[{model.api} req] key={_cache_key!r} items={len(_input_items)} "
                        f"parts={_input_parts} part_chars={_input_part_lengths} "
                        f"text_chars={_input_text_len} instr={_instr_len} "
                        f"tools={_tool_names} tool_hash={_tool_hash} "
                        f"choice={request_body.get('tool_choice')!r} "
                        f"parallel={request_body.get('parallel_tool_calls')!r} "
                        f"tier={request_body.get('service_tier')!r} "
                        f"reasoning={_reasoning}",
                        file=sys.stderr,
                        flush=True,
                    )
                except Exception:
                    pass

            headers: dict[str, str] = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                **(getattr(model, "headers", None) or {}),
                **(opts.get("headers") or {}),
            }
            # The Codex backend gates greylisted model ids (e.g. gpt-5.6-luna)
            # behind the real CLI identity: it serves them only to a recognised
            # ``originator: codex_cli_rs`` at/above a minimum ``version``. Older
            # ids don't check, so an identity-less request silently works for
            # gpt-5.5 but 404s ("Model not found") for luna. The identity lives
            # on OpenAICodexRuntime.api_model.headers, but the dispatcher path
            # re-resolves the model via get_model() → a plain registry Model
            # with no headers, so it never reaches here. Backfill the identity
            # from the token when it's absent, so dispatch works regardless of
            # which Model object routed here.
            if "originator" not in headers:
                from .oauth import _get_account_id_from_jwt
                from .runtime import _CODEX_CLIENT_VERSION
                headers["originator"] = "codex_cli_rs"
                headers["version"] = _CODEX_CLIENT_VERSION
                headers.setdefault("OpenAI-Beta", "responses=experimental")
                if "chatgpt-account-id" not in headers:
                    acct = _get_account_id_from_jwt(api_key) or ""
                    if acct:
                        headers["chatgpt-account-id"] = acct

            ev_stream.push({"type": "start", "partial": output})

            # --- Stream-level retry via shared helper -------------
            # Retry as long as the consumer hasn't seen any real
            # content yet. Once ``output.content`` is non-empty,
            # ``process_responses_stream`` has already pushed events
            # into ``ev_stream`` and the consumer is reading them —
            # we can't rewind that, so ``is_committed_fn`` flips to
            # True and ``retry_stream`` stops retrying.
            _sig = opts.get("signal")
            # Committed prefix at retry-loop entry. Attempts share this one
            # ``output`` object, so each try must first drop whatever blocks
            # a failed previous try half-streamed — otherwise a retried
            # stream APPENDS to the leftovers and the final message carries
            # duplicated content.
            n0 = len(output.content)
            retry_route = 0
            attempt_number = 0

            async def _attempt() -> None:
                nonlocal attempt_number, retry_route
                attempt_number += 1
                del output.content[n0:]
                # Decoupled timeouts: bound connect/write/pool, but let the
                # SSE idle/total parser govern the streaming body read (the
                # read value here is only a backstop above SSE_IDLE_TIMEOUT_S).
                # A single ``timeout=`` float would cap the read low and fire
                # before the idle budget whenever a proxy/VPN buffers the SSE
                # stream — the main source of spurious mid-stream timeouts.
                # The first attempt uses the hardened shared client (timeouts,
                # keepalive, force-IPv4 and proxy policy). A retry uses a fresh
                # private client: reusing the connection that just produced a
                # content-free EOF kept every replay in the same broken
                # transport window. Private clients close in ``finally``.
                from openprogram.security.url_policy import (
                    OwnerURLException,
                    normalize_origin,
                )

                configured_origin = normalize_origin(base_url)
                owner_exception = OwnerURLException(
                    consumer="provider.configured_api",
                    origin=configured_origin,
                )
                private_client = attempt_number > 1
                client = (
                    build_async_client(
                        consumer="provider.configured_api",
                        configured_origin=configured_origin,
                        owner_exception=owner_exception,
                    )
                    if private_client
                    else get_shared_async_client(
                        "openai-codex",
                        consumer="provider.configured_api",
                        configured_origin=configured_origin,
                        owner_exception=owner_exception,
                    )
                )
                try:
                    async with client.stream(
                        "POST",
                        f"{base_url.rstrip('/')}/codex/responses",
                        headers=headers,
                        content=json.dumps(request_body),
                    ) as response:
                        if response.status_code not in (200, 201):
                            error_text_bytes = await response.aread()
                            try:
                                err_text = error_text_bytes.decode()
                            except Exception:
                                err_text = repr(error_text_bytes)
                            raise ProviderStreamError(
                                f"HTTP {response.status_code}: {err_text}",
                                http_status=response.status_code,
                                retry_after_s=read_retry_after(response.headers),
                                error_text=err_text,
                                retryable=is_retryable_status(
                                    response.status_code, err_text
                                ) and not output.content,
                                provider=model.provider,
                            )

                        # Rate-limit telemetry (no-op if the backend omits the
                        # headers). Warn when a bucket is exhausted / nearly so.
                        rl = parse_rate_limit(response.headers)
                        if rl.present and (rl.is_throttled or rl.is_low):
                            print(
                                f"[{model.api} rate-limit] requests "
                                f"{rl.remaining_requests}/{rl.limit_requests} · tokens "
                                f"{rl.remaining_tokens}/{rl.limit_tokens}",
                                file=sys.stderr, flush=True,
                            )

                        sse_events = _parse_sse_stream(response, signal=_sig)
                        try:
                            await process_responses_stream(
                                sse_events, output, ev_stream, model, signal=_sig
                            )
                        except ProviderStreamError as exc:
                            # Once text, reasoning, or usable tool arguments have
                            # reached the caller, restarting the request cannot be
                            # transparent: it would either duplicate those events
                            # or require deleting content the UI already rendered.
                            # Stop the retry loop here and let the outer partial-
                            # response recovery finalize the blocks already
                            # received with ``stop_reason=length``.
                            if _has_substantive_content():
                                exc.retryable = False

                            # The subscription backend can terminate a Luna stream
                            # after announcing an empty reasoning item when a
                            # tool-heavy/long-instruction request uses medium or
                            # higher reasoning. Repeating the identical request
                            # just burns the entire retry budget. Preserve the
                            # requested effort for the first attempt, then make the
                            # already-scheduled transport retries useful by first
                            # dropping its reasoning effort to low and, if that
                            # still terminates empty, disabling reasoning. No
                            # text/tool output has committed at this point, so this
                            # cannot duplicate user-visible content.
                            reasoning = request_body.get("reasoning")
                            effort = (
                                reasoning.get("effort")
                                if isinstance(reasoning, dict)
                                else None
                            )
                            if (
                                exc.retryable
                                and "before a terminal response event" in str(exc)
                            ):
                                if effort not in (None, "minimal", "low"):
                                    reasoning["effort"] = "low"
                                    print(
                                        f"[{model.api}] empty {effort} reasoning stream; "
                                        "retrying at low effort",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                elif effort in ("minimal", "low"):
                                    request_body.pop("reasoning", None)
                                    if request_body.get("include") == [
                                        "reasoning.encrypted_content"
                                    ]:
                                        request_body.pop("include", None)
                                    print(
                                        f"[{model.api}] empty {effort} reasoning stream; "
                                        "retrying without reasoning",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                # Do not pin every replay to the exact cache route
                                # that just returned an empty stream. Rotate only
                                # after a safe, pre-content EOF so a healthy
                                # request keeps normal prefix-cache reuse.
                                cache_key = request_body.get("prompt_cache_key")
                                if cache_key:
                                    retry_route += 1
                                    digest = hashlib.sha256(
                                        f"{cache_key}:{retry_route}".encode("utf-8")
                                    ).hexdigest()[:24]
                                    request_body["prompt_cache_key"] = (
                                        f"op-retry-{digest}"
                                    )
                            raise
                finally:
                    if private_client:
                        await client.aclose()

                if output.stop_reason in ("aborted", "error"):
                    raise ProviderStreamError(
                        "stream ended with error stop_reason",
                        retryable=False,
                        provider=model.provider,
                    )

            await retry_stream(
                _attempt,
                is_committed_fn=_has_substantive_content,
                max_attempts=provider_retry_attempts(PROVIDER_STREAM_MAX_ATTEMPTS),
                label=model.api,
                provider=model.provider,
            )

            # Success — finalize.
            ev_stream.push({"type": "done", "reason": output.stop_reason, "message": output})
            ev_stream.end(output)
            return

        except Exception as exc:
            for b in output.content:
                if isinstance(b, dict):
                    b.pop("index", None)
            # User cancel — finalize as "aborted" (anthropic's cancel
            # semantics: a terminal aborted event, not an error/retry),
            # preserving whatever content already streamed.
            _sig_ = opts.get("signal")
            if _sig_ is not None and callable(getattr(_sig_, "is_set", None)) and _sig_.is_set():
                output.stop_reason = "aborted"
                output.error_message = str(exc)
                ev_stream.push({"type": "error", "reason": "aborted", "error": output})
                ev_stream.end(output)
                return
            # Partial-response recovery (hermes pattern). A transient
            # mid-stream break AFTER real content already streamed (common
            # over a flaky proxy/VPN) shouldn't discard the work: finalize
            # the partial turn with a non-error stop_reason so the turn
            # COMPLETES (partial output preserved + visible) instead of
            # erroring out. Only when (a) content streamed, (b) it wasn't a
            # user abort, and (c) the failure isn't a PERMANENT kind
            # (auth/invalid/context/policy). Toggle: OPENPROGRAM_PARTIAL_RECOVERY=0.
            if (
                _has_substantive_content()
                and output.stop_reason != "aborted"
                and _recover_partial_enabled()
                and not _is_permanent_failure(exc)
            ):
                output.stop_reason = "length"  # incomplete/truncated, NOT "error"
                output.error_message = None
                print(
                    f"[{model.api}] mid-stream break after {len(output.content)} "
                    f"block(s) — recovered partial ({type(exc).__name__}); turn "
                    f"completes instead of erroring.",
                    file=sys.stderr, flush=True,
                )
                ev_stream.push({"type": "done", "reason": output.stop_reason, "message": output})
                ev_stream.end(output)
                return
            output.stop_reason = "error"
            output.error_message = str(exc)
            # ev_stream.fail() makes the consumer's `async for` raise this
            # exception rather than see a normal end. (The old push-error +
            # end pattern looked "successful" to agent_loop, which then
            # auto-retried — a single idle timeout could busy-loop.)
            ev_stream.fail(exc)

    asyncio.ensure_future(_run())
    return ev_stream


def stream_simple_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for OpenAI Codex Responses streaming."""
    explicit_key = getattr(options, "api_key", None) if options else None
    api_key = _resolve_codex_bearer_token(explicit_key)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None
    if reasoning:
        from openprogram.providers.thinking_spec import translate_reasoning
        reasoning_effort = translate_reasoning(model.provider or "openai-codex", model.id, reasoning)
    else:
        reasoning_effort = None

    return stream_openai_codex_responses(model, context, {**base_dict, "reasoning_effort": reasoning_effort})


def _build_request_body(
    model: "Model",
    context: "Context",
    opts: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model.id,
        "input": messages,
        "stream": True,
        "store": False,
    }
    # Codex backend rejects requests without `instructions` (HTTP 400), so
    # fall back to a minimal default when no system prompt was supplied.
    body["instructions"] = context.system_prompt or "You are a helpful assistant."
    if opts.get("session_id"):
        body["prompt_cache_key"] = opts["session_id"]
    # Fast（priority）档：与 Responses API 同名参数，直接透传（用户裁决：
    # GPT 订阅入口开放 fast 按钮）。后端不认时按惯例忽略未知字段。
    if opts.get("service_tier"):
        body["service_tier"] = opts["service_tier"]
    # `max_output_tokens` and `temperature` are rejected by the Codex backend;
    # they're only valid on the public OpenAI Responses API.

    tool_list: list[dict[str, Any]] = []
    tools = getattr(context, "tools", None)
    if tools:
        tool_list.extend(convert_responses_tools(tools, model.api, model.id))
    # Built-in server-side web search. The Codex backend (chatgpt.com
    # /backend-api/codex/responses) natively supports the Responses API
    # ``{"type": "web_search"}`` tool — the model runs the search on the
    # server and the results are folded back into its output (the stream
    # emits response.web_search_call.* events, which our processor
    # ignores harmlessly). Without this, a codex run has NO way to reach
    # the internet, so prompts that say "search arXiv" just get refused
    # (the dry-retrieval spin we saw in research_harness). Opt-in via
    # opts["web_search"] so non-search calls don't pay for the tool.
    if opts.get("web_search"):
        tool_list.append({"type": "web_search"})

    if tool_list:
        body["tools"] = tool_list
        # Honor the caller's pick policy. The Codex backend speaks the
        # Responses API, which natively takes "auto" / "required" / "none"
        # and the forced-pick shape {"type": "function", "name": X} — pass
        # it through verbatim. Falling back to "auto" only when the caller
        # said nothing. Previously this was hardcoded to "auto", which
        # silently dropped tool_choice="required" (e.g. call_with_schema's
        # forced submit tool), letting the model reply with text instead of
        # calling the tool.
        tool_choice = opts.get("tool_choice")
        body["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        # Same for parallel calls: respect an explicit False, else default
        # to the Codex behaviour (parallel allowed).
        parallel = opts.get("parallel_tool_calls")
        body["parallel_tool_calls"] = False if parallel is False else True

    reasoning_effort = opts.get("reasoning_effort")
    reasoning_summary = opts.get("reasoning_summary")
    if getattr(model, "reasoning", False) and reasoning_effort:
        body["reasoning"] = {
            "effort": reasoning_effort,
            # Default to "auto" so the API streams a readable summary of the
            # reasoning trace. Without a summary field, Codex only returns
            # encrypted_content (opaque to the UI) and no thinking deltas ever
            # fire. Callers can override by passing reasoning_summary.
            "summary": reasoning_summary or "auto",
        }
        body["include"] = ["reasoning.encrypted_content"]

    if opts.get("text_verbosity"):
        body["text"] = {"verbosity": opts["text_verbosity"]}

    return body


# Streaming timeout budgets — single source of truth in
# ``providers.utils.timeouts`` (see docs/design/providers/reliability/llm-fault-tolerance.md).
# The two-budget SSE parser below enforces them:
#   * SSE_IDLE_TIMEOUT_S       — "no bytes at all" (any line resets) ≈ OpenClaw 30-min bodyTimeout
#   * SSE_DATA_STALL_TIMEOUT_S — "no real data" (our extra ping-flood guard)
#   * SSE_TOTAL_TIMEOUT_S      — runaway backstop
# Connection timeouts + TCP keepalive + force-IPv4 + proxy now live in the
# shared client builder (``get_shared_async_client``), not here.
from openprogram.providers.utils.timeouts import (  # noqa: E402
    STREAM_IDLE_TIMEOUT_S as SSE_IDLE_TIMEOUT_S,
    STREAM_DATA_STALL_TIMEOUT_S as SSE_DATA_STALL_TIMEOUT_S,
    STREAM_TOTAL_TIMEOUT_S as SSE_TOTAL_TIMEOUT_S,
)
# Caller's end-to-end deadline (published by runtime.exec). The SSE wait
# below clamps to it so a single stream read can't block past the budget.
from openprogram.providers.utils.deadline import remaining as _dl_remaining  # noqa: E402


class StreamIdleTimeout(Exception):
    """No real data event received for SSE_IDLE_TIMEOUT_S."""


class StreamTotalTimeout(Exception):
    """Single SSE stream exceeded SSE_TOTAL_TIMEOUT_S."""


async def _parse_sse_stream(response: Any, signal: Any = None):
    """Parse SSE events from an httpx streaming response.

    OpenAI's Codex Responses API emits keepalive frames (event: ping
    / blank lines) every few seconds during reasoning. httpx's read
    timeout treats *any* incoming bytes as activity and never trips
    on a stalled stream that's still echoing pings, so a session can
    hang forever waiting for content that never arrives.

    We track ``last_data_at`` independently — only "real" data events
    (i.e. parsed JSON payloads other than [DONE]) refresh it. If
    nothing of substance arrives for SSE_IDLE_TIMEOUT_S, we raise.
    A separate hard ceiling (SSE_TOTAL_TIMEOUT_S) backstops genuinely
    stuck requests that never even hit idle (e.g. ping-flooded).
    """
    import asyncio
    import time as _time
    deadline = _time.monotonic() + SSE_TOTAL_TIMEOUT_S
    _start = _time.monotonic()
    last_activity_at = _start  # ANY line (pings incl.) — OpenClaw bodyTimeout style
    last_data_at = _start      # real data events only — our progress guard
    saw_terminal = False
    debug_provider = os.environ.get("OPENPROGRAM_DEBUG_PROVIDER", "").strip().lower() \
        in ("1", "true", "yes")
    event_count = 0
    delta_chars = 0
    line_iter = response.aiter_lines().__aiter__()

    async def _read_next_line() -> str:
        """Read one SSE line while polling budgets without cancelling I/O.

        ``asyncio.wait_for(line_iter.__anext__(), 0.25)`` cancels the
        underlying httpx read every time the Stop-button poll interval
        expires. A normal model pause longer than 250 ms therefore looked
        exactly like a server EOF. Shield one persistent read task and only
        cancel it when a real timeout or caller cancellation exits the read.
        """
        read_task = asyncio.create_task(line_iter.__anext__())
        try:
            while True:
                # Caller cancel (Stop button): raising here unwinds through
                # ``async with client.stream(...)``, which closes the connection.
                if (
                    signal is not None
                    and callable(getattr(signal, "is_set", None))
                    and signal.is_set()
                ):
                    from openprogram.providers.utils.errors import StreamAborted

                    raise StreamAborted("stream cancelled by caller signal")
                now = _time.monotonic()
                if now >= deadline:
                    raise StreamTotalTimeout(
                        f"SSE total budget {SSE_TOTAL_TIMEOUT_S}s exceeded"
                    )
                # Two independent budgets, whichever trips first:
                #   * no bytes AT ALL for SSE_IDLE_TIMEOUT_S      → dead connection
                #   * no real data for SSE_DATA_STALL_TIMEOUT_S   → stuck / ping-flood
                idle_left = SSE_IDLE_TIMEOUT_S - (now - last_activity_at)
                stall_left = SSE_DATA_STALL_TIMEOUT_S - (now - last_data_at)
                if idle_left <= 0:
                    raise StreamIdleTimeout(
                        f"no SSE bytes for {SSE_IDLE_TIMEOUT_S}s"
                    )
                if stall_left <= 0:
                    raise StreamIdleTimeout(
                        f"no SSE data event for {SSE_DATA_STALL_TIMEOUT_S}s"
                    )
                wait = min(idle_left, stall_left, deadline - now)
                _rem = _dl_remaining()
                if _rem is not None:
                    if _rem <= 0:
                        raise StreamTotalTimeout(
                            "caller deadline (exec timeout_s) exceeded mid-stream"
                        )
                    wait = min(wait, _rem)
                if signal is not None:
                    # Poll the cancel signal at >=4 Hz without cancelling the
                    # in-flight network read between polls.
                    wait = min(wait, 0.25)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(read_task), timeout=wait
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            if not read_task.done():
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)

    while True:
        try:
            line = await _read_next_line()
        except StopAsyncIteration:
            if debug_provider:
                print(
                    f"[openai-codex sse] eof events={event_count} "
                    f"delta_chars={delta_chars} terminal={saw_terminal}",
                    file=sys.stderr,
                    flush=True,
                )
            if not saw_terminal:
                raise ProviderStreamError(
                    "Codex SSE ended before a terminal response event",
                    retryable=True,
                )
            return
        # Any received line is connection activity (mirrors OpenClaw
        # resetting bodyTimeout on any byte). Real data additionally
        # refreshes the progress guard below, where the event is parsed.
        last_activity_at = _time.monotonic()
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                if not saw_terminal:
                    raise ProviderStreamError(
                        "Codex SSE sent [DONE] before a terminal response event",
                        retryable=True,
                    )
                break
            try:
                evt = json.loads(data)
            except json.JSONDecodeError:
                continue
            event_count += 1
            event_type = evt.get("type")
            if isinstance(evt.get("delta"), str):
                delta_chars += len(evt["delta"])
            if debug_provider and not str(event_type).endswith(".delta"):
                response_payload = evt.get("response")
                response_status = (
                    response_payload.get("status")
                    if isinstance(response_payload, dict)
                    else None
                )
                print(
                    f"[openai-codex sse] event={event_type!r} "
                    f"status={response_status!r}",
                    file=sys.stderr,
                    flush=True,
                )
            # Only real, parsed data events refresh the idle timer —
            # keepalive pings never arrive here, so they can't stall
            # the abort path.
            last_data_at = _time.monotonic()
            if event_type in ("response.completed", "response.incomplete"):
                saw_terminal = True
            yield evt
        elif line.startswith("event: "):
            pass  # Event type prefix; not enough to count as data.
