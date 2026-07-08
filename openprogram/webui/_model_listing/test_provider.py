"""Connectivity probe — the one-shot "PING" behind the Settings →
Provider → Connectivity check button.

Credential validation now lives in ``credentials.validate_credential`` — a
model-independent auth probe (``GET /key`` / ``GET /models`` / ``x-api-key
/v1/models`` / ``?key=`` / AuthManager) with an inference ping only as layer 2
when a model is named. ``test_provider`` is a thin adapter that delegates to it
and reshapes the result into the legacy ``{ok, latency_ms, model?, note?,
error?}`` dict the React ``Connectivity`` component reads.

The one exception is **Codex / ChatGPT-subscription**, which has no auth-only
listing endpoint (the ChatGPT backend CF-blocks ``/chat/completions`` and 403s
on listing), so it keeps its dedicated streaming ``/codex/responses`` ping
below. See ``docs/design/providers/credential-validation-unification.md``.
"""
from __future__ import annotations

import time
from typing import Any


# Providers whose ``test`` flow needs the Codex Responses body shape (no
# auth-only endpoint exists for them). Currently just ``openai-codex`` —
# Gemini-subscription's CodeAssist endpoint uses a different shape again and
# isn't covered yet.
_CODEX_RESPONSES_PROVIDERS = frozenset({"openai-codex"})


def test_provider(
    provider_id: str,
    model: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """One-shot connectivity check. Delegates to the unified validator for
    every provider except Codex (dedicated streaming path below)."""
    if provider_id in _CODEX_RESPONSES_PROVIDERS:
        return _codex_ping(provider_id, model, timeout)

    from .credentials import validate_credential

    return validate_credential(
        provider_id, model=model, timeout=timeout, use_cache=False
    ).to_legacy()


def _codex_ping(
    provider_id: str, model: str | None, timeout: float
) -> dict[str, Any]:
    """Codex-only probe: a streaming ``/codex/responses`` PING. Cloudflare
    blanket-blocks ``/chat/completions`` from non-residential IPs and the
    backend rejects ``stream: false`` with a 400, so we open the stream, read
    the initial status code, and tear the connection down without consuming the
    SSE."""
    import httpx

    from .storage import _resolve_api_key, _resolve_base_url

    api_key = _resolve_api_key(provider_id)
    base = _resolve_base_url(provider_id)
    if not base:
        return {"ok": False, "error": "No base URL resolvable"}

    if not model:
        from openprogram.providers import get_models
        ms = get_models(provider_id)
        if not ms:
            return {"ok": False, "error": "No model available to test with"}
        model = ms[0].id

    url = base.rstrip("/") + "/codex/responses"
    body = {
        "model": model,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "PING"}]}],
        "instructions": "",
        "stream": True,
        "store": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "originator": "openprogram",
        "OpenAI-Beta": "responses=experimental",
    }
    # Codex credential lives in the OAuth pool, not env/config — ask AuthManager.
    if not api_key:
        try:
            from openprogram.auth.manager import get_manager
            cred = get_manager().acquire_sync(provider_id)
            api_key = getattr(cred.payload, "access_token", None) or None
            account_id = (getattr(cred.payload, "extra", None) or {}).get("account_id", "")
            if account_id:
                headers["chatgpt-account-id"] = account_id
        except Exception:
            return {
                "ok": False,
                "error": ("No usable Codex credential. "
                          "Run `openprogram providers login openai-codex`."),
            }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.time()
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", url, headers=headers, json=body) as r:
                latency_ms = int((time.time() - t0) * 1000)
                if r.status_code != 200:
                    err_body = b"".join(r.iter_bytes()).decode("utf-8", errors="replace")
                    return {
                        "ok": False,
                        "error": f"HTTP {r.status_code}: {err_body[:200]}",
                        "latency_ms": latency_ms,
                    }
        return {"ok": True, "latency_ms": latency_ms, "model": model}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Request failed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
