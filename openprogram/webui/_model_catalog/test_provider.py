"""Connectivity probe — the one-shot "PING" behind the Settings →
Provider → Connectivity check button.

Two body shapes:

* **Codex / ChatGPT-subscription path.** Posts to
  ``/codex/responses`` on ``chatgpt.com/backend-api`` because
  Cloudflare blanket-blocks the ``/chat/completions`` path from
  non-residential IPs (the user sees ``CF-Mitigated: challenge``
  403s). Streaming is mandatory — the Codex backend rejects
  ``stream: false`` with a 400. We don't actually consume the SSE;
  ``httpx.Client.stream`` lets us read the initial status code and
  tear the connection down right away.

* **Everything else.** Plain OpenAI Chat Completions shape (``POST
  /chat/completions``, ``{model, messages: [...], max_tokens: 4}``).
  Covers DeepSeek, Groq, OpenRouter, Anthropic-via-Meridian, …
  literally every OpenAI-compatible provider.

Returns ``{"ok": True, "latency_ms": N, "model": ...}`` on success or
``{"ok": False, "error": "...", "latency_ms": N}`` on failure. The
React-side ``Connectivity`` component peels the ``HTTP <code>: <body>``
wrapper apart and shows an inline summary instead of the legacy
"✗ failed" tooltip-only render.
"""
from __future__ import annotations

import time
from typing import Any


# Providers whose ``test`` flow should use the Codex Responses body
# shape instead of the generic ``/chat/completions``. Currently just
# ``openai-codex`` — Gemini-subscription's CodeAssist endpoint uses a
# different shape again and isn't covered by either branch yet.
_CODEX_RESPONSES_PROVIDERS = frozenset({"openai-codex"})


def test_provider(
    provider_id: str,
    model: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Send a one-shot tiny PING to verify api_key + base_url work."""
    import httpx

    from .providers import _ENV_API_KEYS
    from .storage import _read_providers_cfg, _resolve_api_key, _resolve_base_url

    api_key = _resolve_api_key(provider_id)
    if api_key is None and _ENV_API_KEYS.get(provider_id):
        return {"ok": False, "error": f"No API key set ({_ENV_API_KEYS[provider_id]})"}

    base = _resolve_base_url(provider_id)
    if not base:
        return {"ok": False, "error": "No base URL resolvable"}

    if not model:
        # Pick the first enabled or first available model.
        cfg = _read_providers_cfg()
        enabled = (cfg.get(provider_id, {}).get("enabled_models") or [])
        if enabled:
            model = enabled[0]
        else:
            from openprogram.providers import get_models
            ms = get_models(provider_id)
            if not ms:
                return {"ok": False, "error": "No model available to test with"}
            model = ms[0].id

    use_codex_shape = provider_id in _CODEX_RESPONSES_PROVIDERS

    if use_codex_shape:
        url = base.rstrip("/") + "/codex/responses"
        body = {
            "model": model,
            "input": [{"role": "user",
                       "content": [{"type": "input_text", "text": "PING"}]}],
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
        # openai-codex has no api_key_env — its credential lives in the
        # OAuth pool. ``_resolve_api_key`` only checks env + config so
        # it returns None here; we have to ask AuthManager. Also
        # surface chatgpt-account-id when available so OpenAI's side
        # gets a clean account mapping.
        if not api_key:
            try:
                from openprogram.auth.manager import get_manager
                cred = get_manager().acquire_sync(provider_id)
                api_key = getattr(cred.payload, "access_token", None) or None
                account_id = (
                    getattr(cred.payload, "extra", None) or {}
                ).get("account_id", "")
                if account_id:
                    headers["chatgpt-account-id"] = account_id
            except Exception:
                return {
                    "ok": False,
                    "error": (
                        "No usable Codex credential. "
                        "Run `openprogram providers login openai-codex`."
                    ),
                }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        url = base.rstrip("/") + "/chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "PING"}],
            "max_tokens": 4,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.time()
    try:
        if use_codex_shape:
            # Codex responds with SSE — a bare ``httpx.post`` would
            # block reading the stream until close. Use
            # ``client.stream`` so we read headers, capture the status,
            # and tear the connection down immediately.
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", url, headers=headers, json=body) as r:
                    latency_ms = int((time.time() - t0) * 1000)
                    if r.status_code != 200:
                        err_body = b"".join(r.iter_bytes()).decode(
                            "utf-8", errors="replace"
                        )
                        return {
                            "ok": False,
                            "error": f"HTTP {r.status_code}: {err_body[:200]}",
                            "latency_ms": latency_ms,
                        }
            return {"ok": True, "latency_ms": latency_ms, "model": model}

        r = httpx.post(url, headers=headers, json=body, timeout=timeout)
        latency_ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"HTTP {r.status_code}: {r.text[:200]}",
                "latency_ms": latency_ms,
            }
        return {"ok": True, "latency_ms": latency_ms, "model": model}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Request failed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
