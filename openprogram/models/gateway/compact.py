"""``POST /v1/responses/compact``: Codex's remote compaction.

After yetone/magpie ``internal/gateway/responses_compact.go`` (MIT License,
Copyright (c) yetone): a model whose provider speaks the Responses API with
an API key is asked on that provider's own ``/responses/compact``, with
OpenProgram's API key for it (``resolve_store_api_key_sync``: keys only, a
sign-in token never goes out); nothing the client sent beyond the body goes
on. Any other model is answered 400, on which Codex compacts on
``/responses`` itself. The request is checked as ``/responses`` checks one,
and the call is recorded in the usage ledger as the agent's.
"""
from __future__ import annotations

from typing import Any

from . import conversion as cv
from .conversion import GatewayError

_DEFAULT_BASE = "https://api.openai.com/v1"


def _refused(model_name: str, why: str) -> GatewayError:
    return GatewayError(400, "invalid_request",
                        f"/responses/compact is not supported for {model_name}: {why}; compact on /responses instead")


def _record(model, payload: Any, harness: str) -> None:
    from openprogram.models.providers.types import AssistantMessage, Usage
    from openprogram.models.usage import usage_scope
    from openprogram.models.usage.recorder import record_message
    usage = payload.get("usage") if isinstance(payload, dict) and isinstance(payload.get("usage"), dict) else {}
    cached = int(((usage.get("input_tokens_details") or {}).get("cached_tokens")) or 0)
    total_in = int(usage.get("input_tokens") or 0)
    counts = Usage(input=max(total_in - cached, 0), output=int(usage.get("output_tokens") or 0),
                   cache_read=cached, tokens_reported=bool(usage))
    message = AssistantMessage(content=[], api=model.api, provider=model.provider, model=model.id,
                               usage=counts, stop_reason="stop", timestamp=cv.now_ms())
    with usage_scope(call_kind="harness", call_label=harness):
        record_message(model, message)


async def compact(body: Any, *, harness: str = "openai-responses") -> tuple[int, Any]:
    """``(status, JSON body)`` of the provider's own answer."""
    from . import responses
    parsed = responses.parse_request(body)  # the same checks as /responses
    model = parsed.model
    name = cv.model_ref(model)
    if model.api != "openai-responses":
        raise _refused(name, "its provider does not speak the Responses API")
    from openprogram.models.auth.resolver import resolve_store_api_key_sync
    key = resolve_store_api_key_sync(model.provider)
    if not key:
        raise _refused(name, "OpenProgram holds no API key for its provider")
    import httpx

    from openprogram.host.security.safe_http import safe_async_client
    from openprogram.host.security.url_policy import normalize_origin
    base = (getattr(model, "base_url", None) or _DEFAULT_BASE).rstrip("/")
    sent = {**body, "model": model.id}
    async with safe_async_client("provider.configured_api", configured_origin=normalize_origin(base),
                                 timeout=httpx.Timeout(600.0, connect=30.0), overall_timeout=600.0) as client:
        try:
            reply = await client.post(f"{base}/responses/compact", json=sent,
                                      headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            raise GatewayError(502, "api_error", f"{model.provider} could not be reached: {exc}") from exc
    try:
        payload = reply.json()
    except ValueError:
        payload = {"error": {"message": reply.text or reply.reason_phrase, "type": "server_error",
                             "param": None, "code": "server_error"}}
    if reply.status_code in (401, 403):
        raise GatewayError(502, "api_error", f"OpenProgram's credential for {model.provider} was refused")
    if reply.status_code < 400:
        _record(model, payload, harness)
    return reply.status_code, payload
