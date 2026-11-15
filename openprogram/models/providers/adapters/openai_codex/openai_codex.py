"""
OpenAI Codex Responses API provider (ChatGPT backend).

A turn signed in with a ChatGPT account goes over the backend's WebSocket
first (``websocket_transport``: a conversation's socket is kept between
turns and a continuing turn is sent as a delta after
``previous_response_id``), and over HTTP SSE otherwise, its body
zstd-compressed (``request_compression``). Both are read under the same
budgets (``read_budget``) and feed the shared Responses processor.
Every request names its conversation in the ``session_id`` and
``conversation_id`` headers as well as ``prompt_cache_key``, which is what
lands a new connection on the replica holding the conversation's cache
(``CONVERSATION_HEADERS``).

Mirrors openai-codex-responses.ts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openprogram.models.providers.streaming.budget import provider_retry_attempts
from openprogram.models.providers.shared.openai_responses import (
    convert_responses_messages,
    carried_tool_names,
    convert_responses_tools,
    native_tool_search,
    process_responses_stream,
)
from openprogram.models.providers.shared.validate_modalities import validate_input_modalities
from openprogram.models.providers.shared.simple_options import build_base_options
from openprogram.models.providers.utils.event_stream import EventStream
from . import auth_adapter, websocket_transport
from .read_budget import ReadBudget, StreamIdleTimeout, StreamTotalTimeout
from .request_compression import encode_turn
from openprogram.models.providers.utils.http_client import (
    build_async_client,
    get_shared_async_client,
)
from openprogram.models.providers.utils.stream_retry import (
    PROVIDER_STREAM_MAX_ATTEMPTS,
    ProviderStreamError,
    is_retryable_status,
    read_retry_after,
    retry_stream,
)
import logging

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openprogram.models.providers.types import Context, Model, SimpleStreamOptions

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
    from openprogram.models.providers.utils.errors import classify_error, ErrorReason
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


def _pooled_codex_bearer() -> str:
    """The current bearer of the active pooled ChatGPT login, valid before
    it is sent: acquire_sync refreshes a stored login whose token is within
    the skew window; ``oauth_view`` does the same for a login delegated to
    the Codex CLI's file and adopts a newer login of the same account found
    there. ``""`` when there is none.

    A login that can no longer be refreshed raises
    :class:`AuthNeedsReauthError`: the reply says to sign in again instead
    of "No API key", and nothing is asked of the token endpoint again."""
    from openprogram.models.auth.types import AuthNeedsReauthError
    try:
        from openprogram.models.auth.credential_provider import get_credential_provider
        from .credentials import acquire
        return acquire(get_credential_provider()).payload.auth_value or ""
    except AuthNeedsReauthError:
        raise
    except Exception:
        # No provider config or no credentials: the caller raises the
        # actionable "No API key" error.
        _log.debug("no pooled openai-codex credential", exc_info=True)
        return ""


def _chatgpt_account(token: str) -> str:
    try:
        return auth_adapter.extract_account_id(token)
    except Exception:
        return ""


def _resolve_codex_bearer_token(opts_api_key: str | None) -> str:
    """Resolve the bearer token codex requests need to authorize.

      1. An ``api_key`` passed in opts. When it is a ChatGPT access token
         (a Runtime hands its login's token to every call it makes), the
         current token of that same account's pooled login replaces it, so
         a call made after a refresh does not send the token the refresh
         replaced.
      2. The provider's stored API key (``resolve_provider_key``).
      3. The pooled OAuth login (:func:`_pooled_codex_bearer`).

    Returns ``""`` when nothing yields a usable token, so the caller
    raises "No API key for provider".
    """
    if opts_api_key:
        account = _chatgpt_account(opts_api_key)
        if not account:
            return opts_api_key
        current = _pooled_codex_bearer()
        return current if current and _chatgpt_account(current) == account else opts_api_key

    from openprogram.models.providers.settings.env_api_keys import resolve_provider_key
    env_key = resolve_provider_key("openai-codex")
    if env_key:
        return env_key
    return _pooled_codex_bearer()


def _refresh_rejected_token(rejected: str) -> str:
    """The backend answered 401 to a pooled OAuth token before its recorded
    expiry (the session was revoked or rotated elsewhere). Refresh the login
    that token came from once, as the Codex CLI does on 401, and return the
    new bearer; ``""`` when the token is not ours or no fresher one can be
    had.

    A stored ``oauth`` login is refreshed through CredentialProvider (which
    adopts a newer login of the same account from the Codex CLI's file). A
    login delegated to that file is refreshed in the file itself; a refused
    refresh token marks it as needing sign-in."""
    from openprogram.models.auth.types import AuthNeedsReauthError
    try:
        from openprogram.models.auth.credential_provider import get_credential_provider
        from openprogram.models.auth.store import get_store
        manager = get_credential_provider()
        pools = [p for p in get_store().list_pools() if p.provider_id == "openai-codex"]
        for pool in pools:
            for cred in pool.credentials:
                if cred.kind == "oauth" and cred.payload.auth_value == rejected:
                    fresh = manager.force_refresh_sync(cred).payload.auth_value
                    return fresh if fresh and fresh != rejected else ""
        for pool in pools:
            for cred in pool.credentials:
                if cred.kind != "cli_delegated":
                    continue
                path = (cred.payload.data or {}).get("store_path")
                shared = auth_adapter.read_shared_login(Path(path) if path else None)
                if shared and shared["access_token"] == rejected:
                    from .credentials import refresh_delegated
                    fresh = refresh_delegated(cred, rejected, manager)
                    return fresh if fresh and fresh != rejected else ""
        # An older token of a login that has been refreshed since: answer
        # the login's current one.
        current = _pooled_codex_bearer()
        account = _chatgpt_account(rejected)
        if current and current != rejected and account and _chatgpt_account(current) == account:
            return current
    except AuthNeedsReauthError as exc:
        # The login is marked as needing sign-in; the caller surfaces the 401.
        _log.warning("openai-codex: %s", exc)
    except Exception:
        # The caller surfaces the original 401 when no fresher token exists.
        _log.warning("openai-codex token refresh after 401 failed", exc_info=True)
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
            import httpx  # noqa: F401 -- availability check before the request
        except ImportError:
            raise ImportError(
                "The HTTP client is missing; reinstall the complete OpenProgram release."
            )

        from openprogram.models.providers.types import AssistantMessage

        output = AssistantMessage.empty(model, api="openai-codex")

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
            # Reads (and may refresh) the stored login and the Codex CLI's
            # file under a cross-process lock: never on the event loop.
            api_key = await asyncio.to_thread(_resolve_codex_bearer_token, opts.get("api_key"))
            if not api_key:
                raise ValueError(f"No API key for provider: {model.provider}")
            base_url = getattr(model, "base_url", None) or _DEFAULT_CODEX_BASE_URL
            messages = convert_responses_messages(
                model, context, include_system_prompt=False,
                native_tool_search=native_tool_search(model, context),
            )
            request_body = _build_request_body(model, context, opts, messages)

            if opts.get("on_payload"):
                opts["on_payload"](request_body)
            from ...recording.capture import RESPONSES, capture_request
            capture_request(model, request_body, wire=RESPONSES)

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
            # on the Codex Runtime adapter's api_model.headers, but the dispatcher path
            # re-resolves the model via get_model() → a plain registry Model
            # with no headers, so it never reaches here. Backfill the identity
            # from the token when it's absent, so dispatch works regardless of
            # which Model object routed here.
            if "originator" not in headers:
                from .oauth import _get_account_id_from_jwt
                from .catalog import codex_client_version
                headers["originator"] = "codex_cli_rs"
                headers["version"] = codex_client_version()
                headers.setdefault("OpenAI-Beta", "responses=experimental")
                if "chatgpt-account-id" not in headers:
                    acct = _get_account_id_from_jwt(api_key) or ""
                    if acct:
                        headers["chatgpt-account-id"] = acct
            _route_to_conversation(headers, request_body.get("prompt_cache_key"))

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
            token_refreshed = False

            async def _consume(events: Any) -> None:
                """Feed one transport's events to the shared Responses processor."""
                nonlocal retry_route
                try:
                    try:
                        await process_responses_stream(
                            events, output, ev_stream, model, signal=_sig
                        )
                    finally:
                        # Close the parser here, in order, while the response
                        # is still open. Left to the loop's shutdown, it and
                        # httpx's nested line/byte generators are closed
                        # concurrently and each trips over the other
                        # ("aclose(): asynchronous generator is already running").
                        await _aclose_quietly(events)
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
                            _log.info(
                                "%s: empty %s reasoning stream; retrying at low effort",
                                model.api, effort,
                            )
                        elif effort in ("minimal", "low"):
                            request_body.pop("reasoning", None)
                            if request_body.get("include") == [
                                "reasoning.encrypted_content"
                            ]:
                                request_body.pop("include", None)
                            _log.info(
                                "%s: empty %s reasoning stream; retrying without reasoning",
                                model.api, effort,
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
                            _route_to_conversation(
                                headers, request_body["prompt_cache_key"], replace=True
                            )
                    raise

            def _observe_quota(response_headers: Any) -> None:
                """Keep the Codex windows (``x-codex-*``) or an API key's
                ``x-ratelimit-*`` headroom this answer carries."""
                from openprogram.models.usage.quota import observe_headers
                bearer = headers.get("Authorization", "").removeprefix("Bearer ")
                observe_headers(model.provider, bearer, response_headers)

            def _observe_frame(frame: dict) -> None:
                if frame.get("type") == "codex.rate_limits":
                    from openprogram.models.usage.quota import observe_codex_frame
                    bearer = headers.get("Authorization", "").removeprefix("Bearer ")
                    observe_codex_frame(model.provider, bearer, frame)

            async def _socket_turn() -> Any:
                """The turn begun on the backend's WebSocket, or ``None`` for HTTP."""
                if not _chatgpt_sign_in(base_url, headers):
                    return None
                budget = ReadBudget(_sig, label="Codex WebSocket")
                try:
                    turn = await websocket_transport.open_turn(
                        base_url=base_url,
                        headers=headers,
                        body=request_body,
                        receive_next=lambda connection: _receive_frame(connection, budget),
                        on_frame=_observe_frame,
                    )
                except websocket_transport.SocketUnavailable as exc:
                    _log.debug("%s: turn goes over HTTP (%s)", model.api, exc)
                    return None
                return turn.events(lambda connection: _receive_frame(connection, budget))

            async def _http_turn() -> None:
                nonlocal token_refreshed
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
                from openprogram.host.security.url_policy import (
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
                content, encoding_headers = encode_turn(
                    request_body, chatgpt_sign_in=_chatgpt_sign_in(base_url, headers)
                )
                resend = False
                try:
                    async with client.stream(
                        "POST",
                        f"{base_url.rstrip('/')}/codex/responses",
                        headers={**headers, **encoding_headers},
                        content=content,
                    ) as response:
                        # The plan windows ride on every answer, a 429 included.
                        _observe_quota(response.headers)
                        if response.status_code not in (200, 201):
                            error_text_bytes = await response.aread()
                            try:
                                err_text = error_text_bytes.decode()
                            except Exception:
                                err_text = repr(error_text_bytes)
                            if response.status_code == 401 and not token_refreshed:
                                # A token the backend no longer accepts: refresh
                                # its login once and send the same request again
                                # at once. This is not a transport retry, so it
                                # neither waits nor spends the retry budget.
                                token_refreshed = True
                                rejected = headers["Authorization"].removeprefix("Bearer ")
                                fresh = await asyncio.to_thread(_refresh_rejected_token, rejected)
                                if fresh:
                                    headers["Authorization"] = f"Bearer {fresh}"
                                    resend = True
                        if not resend and response.status_code not in (200, 201):
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

                        if not resend:
                            sse_events = _parse_sse_stream(response, signal=_sig)
                            await _consume(sse_events)
                finally:
                    if private_client:
                        await client.aclose()
                if resend:
                    await _http_turn()

            async def _attempt() -> None:
                nonlocal attempt_number
                attempt_number += 1
                del output.content[n0:]
                socket_events = await _socket_turn()
                if socket_events is not None:
                    await _consume(socket_events)
                else:
                    await _http_turn()

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
                on_retry=ev_stream.push,
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
                _log.info(
                    "%s: mid-stream break after %s block(s) — recovered partial (%s); "
                    "turn completes instead of erroring.",
                    model.api, len(output.content), type(exc).__name__,
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

    # Owned by the stream: a consumer that stops reading (a stopped turn)
    # cancels and awaits it through ``close_stream``, so the read in flight
    # and the socket it holds end with the turn, not when the loop is torn down.
    ev_stream.attach_producer(asyncio.ensure_future(_run()))
    return ev_stream


def stream_simple_openai_codex_responses(
    model: "Model",
    context: "Context",
    options: "SimpleStreamOptions | None" = None,
) -> EventStream:
    """Simple interface for OpenAI Codex Responses streaming."""
    explicit_key = getattr(options, "api_key", None) if options else None
    # A caller's key passes through as is: the stream swaps it for its
    # login's current token off the event loop, where a refresh may wait.
    api_key = explicit_key or _resolve_codex_bearer_token(None)
    if not api_key:
        raise ValueError(f"No API key for provider: {model.provider}")

    base = build_base_options(model, options, api_key)
    base_dict = base.model_dump() if hasattr(base, "model_dump") else dict(base)
    reasoning = getattr(options, "reasoning", None) if options else None
    if reasoning:
        from openprogram.models.providers.settings.thinking_spec import translate_reasoning
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
        # A deferred tool is loaded where it was searched for: its definition
        # rides in the input's tool_search_output, and the array, rendered
        # before the conversation, keeps the bytes the backend has cached.
        if native_tool_search(model, context):
            tool_list.extend(convert_responses_tools(
                tools, model.api, model.id, native_tool_search=True,
                carried=carried_tool_names(messages),
            ))
        else:
            tool_list.extend(convert_responses_tools(tools, model.api, model.id))
    # Built-in server-side web search. The Codex backend (chatgpt.com
    # /backend-api/codex/responses) natively supports the Responses API
    # ``{"type": "web_search"}`` tool — the model runs the search on the
    # server and the results are folded back into its output (the stream
    # emits web_search_call items, which the shared processor turns into
    # ServerToolUse blocks). Added even when the context has no function
    # tools. Without this, a codex run has NO way to reach
    # the internet, so prompts that say "search arXiv" just get refused
    # (a literature-search harness spins on dry retrieval). Opt-in via
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
    if getattr(model, "reasoning", False):
        # Codex asks for the encrypted reasoning on every request, whatever
        # the effort: the request is not stored, so without it the next
        # tool round has no reasoning to continue from.
        body["include"] = ["reasoning.encrypted_content"]

    if opts.get("text_verbosity"):
        body["text"] = {"verbosity": opts["text_verbosity"]}

    if "prompt_cache_key" not in body:
        # A call that names no conversation (a summary, a permission check,
        # a title made straight through the adapter) is keyed by its prefix,
        # with the same derivation a Runtime call uses.
        from openprogram.models.providers.shared.cache_key import prefix_cache_key
        body["prompt_cache_key"] = prefix_cache_key(
            getattr(model, "provider", None), getattr(model, "id", None), body.get("instructions"),
            [tool.get("name") or tool.get("type") for tool in body.get("tools") or []
             if isinstance(tool, dict)],
        )

    return body


__all__ = [
    "StreamIdleTimeout",
    "StreamTotalTimeout",
    "stream_openai_codex_responses",
    "stream_simple_openai_codex_responses",
]


#: Headers the ChatGPT backend routes a conversation by, as Codex's own
#: client sends them. ``prompt_cache_key`` in the body is not enough: a new
#: connection (a new WebSocket, a new HTTP client, a new process) carrying
#: only the body key lands on a replica without the conversation's cached
#: prefix, while one carrying these headers lands on the replica that has it.
CONVERSATION_HEADERS = ("session_id", "conversation_id")


def _route_to_conversation(
    headers: dict[str, str], cache_key: Any, *, replace: bool = False
) -> None:
    """Name the conversation in ``headers`` so its turns share one cache.

    A header the caller set is kept, unless ``replace`` (a retry moving the
    request to a new cache route on purpose).
    """
    if not cache_key:
        return
    present = {name.lower(): name for name in headers}
    for name in CONVERSATION_HEADERS:
        existing = present.get(name)
        if existing is not None and not replace:
            continue
        if existing is not None:
            del headers[existing]
        headers[name] = str(cache_key)


def _chatgpt_sign_in(base_url: str, headers: dict[str, str]) -> bool:
    """A turn to the ChatGPT backend itself, signed in with a ChatGPT account."""
    from urllib.parse import urlsplit

    return (
        urlsplit(base_url).hostname == "chatgpt.com"
        and any(name.lower() == "chatgpt-account-id" for name in headers)
    )


async def _receive_frame(connection: Any, budget: ReadBudget) -> Any:
    """One WebSocket frame, read under the reply's budgets; every frame is data."""
    frame = await budget.read(connection.recv)
    budget.saw_data()
    return frame


async def _aclose_quietly(iterator: Any) -> None:
    """Close an async iterator; closing is cleanup, its errors are not news."""
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        return
    try:
        await close()
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.debug("closing %r failed", iterator, exc_info=True)


async def _parse_sse_stream(response: Any, signal: Any = None):
    """Parse SSE events from an httpx streaming response.

    OpenAI's Codex Responses API emits keepalive frames (event: ping /
    blank lines) every few seconds during reasoning, so any line counts as
    activity but only parsed data events count as progress
    (:class:`ReadBudget`). A stream that ends before a terminal response
    event is a retryable break.
    """
    budget = ReadBudget(signal, label="SSE")
    saw_terminal = False
    debug_provider = os.environ.get("OPENPROGRAM_DEBUG_PROVIDER", "").strip().lower() \
        in ("1", "true", "yes")
    event_count = 0
    delta_chars = 0
    line_iter = response.aiter_lines().__aiter__()

    try:
        while True:
            try:
                line = await budget.read(line_iter.__anext__)
            except StopAsyncIteration:
                if debug_provider:
                    _log.info("sse eof events=%s delta_chars=%s terminal=%s",
                              event_count, delta_chars, saw_terminal)
                if not saw_terminal:
                    raise ProviderStreamError(
                        "Codex SSE ended before a terminal response event",
                        retryable=True,
                    )
                return
            if not line.startswith("data: "):
                continue  # "event: …" prefixes and keepalives are not data
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
                _log.info("sse event=%r status=%r", event_type, response_status)
            budget.saw_data()
            if event_type in ("response.completed", "response.incomplete"):
                saw_terminal = True
            yield evt
    finally:
        # The response's line generator (and the byte generators under it)
        # closes with the parser, in order, instead of at loop shutdown.
        await _aclose_quietly(line_iter)
