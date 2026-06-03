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


# HTTP statuses where the request demonstrably *authenticated and routed*
# but the chosen model/endpoint is transiently unavailable — a rate limit
# (429) or a dead upstream (5xx). You cannot reach these without a valid
# credential (a bad key short-circuits at 401), so they prove the key
# works; only that one model is down right now.
_MODEL_DOWN_STATUSES = frozenset({429, 500, 502, 503, 504})


def _is_model_unavailable(status: int, body: str) -> bool:
    """True when a non-200 means "model/endpoint unavailable", not "bad key".

    The connectivity probe pings whatever model happens to be first in
    ``enabled_models``. If that model is a flaky free endpoint, the ping
    can 429/503 (or 404 with OpenRouter's data-policy "no endpoints"
    message) even though the api_key is perfectly valid. Treating those
    as a key failure brands the whole provider broken — the bug this
    guards against. Auth/credit failures (401/402/403) and genuine bad
    requests stay hard failures.
    """
    if status in _MODEL_DOWN_STATUSES:
        return True
    if status == 404:
        low = (body or "").lower()
        return "no endpoints" in low or "data policy" in low or "guardrail" in low
    return False


# An auth-only endpoint lets us answer "is this key valid?" without
# invoking any model. Every OpenAI-compatible base exposes ``GET /models``
# (auth-gated on OpenAI / DeepSeek / Groq / …), so that's the default.
# OpenRouter is the exception — its ``/models`` is *public* (returns 200
# for any key, even a bogus one), so the credential probe there has to
# hit ``/key``, which is auth-gated.
_CREDENTIAL_PROBE_PATHS = {
    "openrouter": "/key",
}
_DEFAULT_CREDENTIAL_PROBE_PATH = "/models"


def _credential_check(
    provider_id: str, base: str, api_key: str, timeout: float
) -> dict[str, Any] | None:
    """Validate the key against a model-independent auth endpoint.

    This is the right primitive for "is my key valid?" — it asks the
    provider directly and never depends on any particular model being up.

    Returns a success dict only on a clean ``200``; on anything else it
    returns ``None`` to mean "inconclusive — fall back to the inference
    ping". The ping uses the real chat path and the same Bearer scheme an
    actual request uses, so it can tell a genuinely bad key (401 there
    too) from a probe-path quirk (e.g. a provider that wants a different
    auth header on ``/models``). Short-circuiting only on 200 keeps this
    strictly additive: a valid key is confirmed without touching a model;
    everything else degrades to exactly the previous behaviour.
    """
    import httpx

    path = _CREDENTIAL_PROBE_PATHS.get(provider_id, _DEFAULT_CREDENTIAL_PROBE_PATH)
    url = base.rstrip("/") + path
    t0 = time.time()
    try:
        r = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    except httpx.RequestError:
        return None
    if r.status_code == 200:
        return {
            "ok": True,
            "latency_ms": int((time.time() - t0) * 1000),
            "via": f"GET {path}",
        }
    return None


def test_provider(
    provider_id: str,
    model: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Send a one-shot tiny PING to verify api_key + base_url work."""
    import httpx

    from .providers import _env_var_for
    from .storage import _read_providers_cfg, _resolve_api_key, _resolve_base_url

    api_key = _resolve_api_key(provider_id)
    env = _env_var_for(provider_id)
    if api_key is None and env:
        return {"ok": False, "error": f"No API key set ({env})"}

    base = _resolve_base_url(provider_id)
    if not base:
        return {"ok": False, "error": "No base URL resolvable"}

    # The default connectivity check answers "is this key valid?" — so do
    # it the right way: hit a model-independent auth endpoint instead of
    # gambling on whatever model happens to be enabled. Only when the
    # caller names a specific model ("can I reach *this* model?") do we
    # fall through to an inference ping. Codex/OAuth providers (no api_key
    # resolvable here) keep their dedicated ping path below.
    explicit_model = model is not None
    if not explicit_model and api_key and provider_id not in _CODEX_RESPONSES_PROVIDERS:
        cred = _credential_check(provider_id, base, api_key, timeout)
        if cred is not None:
            return cred

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
        if r.status_code == 200:
            return {"ok": True, "latency_ms": latency_ms, "model": model}
        detail = f"HTTP {r.status_code}: {r.text[:200]}"
        # A flaky test model (rate-limited / dead upstream / data-policy
        # blocked) still proves the key authenticated — report key-valid
        # with a note instead of failing the whole provider.
        if _is_model_unavailable(r.status_code, r.text):
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "model": model,
                "note": (
                    f"Key authenticated. Model {model} is unavailable right "
                    f"now ({detail})."
                ),
            }
        return {"ok": False, "error": detail, "latency_ms": latency_ms}
    except httpx.RequestError as e:
        return {"ok": False, "error": f"Request failed: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
