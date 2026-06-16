"""Unified provider-credential validation — one entry point, every surface.

Validating a *credential* must never invoke a *model*. Each provider KIND has a
model-independent auth probe (``GET /models``, ``GET /key`` for OpenRouter,
``x-api-key /v1/models`` for Anthropic, ``?key=`` for Google, or the
``AuthManager`` credential status for OAuth providers). A completion ping runs
ONLY as layer 2 — when a caller explicitly names a model to check that one
model's reachability.

``validate_credential`` returns a single structured ``CredentialResult`` with a
closed-enum ``status`` so every surface (save-key verify, the connectivity
button, the CLI/TUI status rows) renders the same distinctions: key rejected vs
key-fine-no-balance vs key-fine-that-model-is-down.

See ``docs/design/providers/credential-validation-unification.md``.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional


# ── status taxonomy (doc §7) ──────────────────────────────────────────────────
VALID = "valid"
INVALID_CREDENTIAL = "invalid_credential"
VALID_NO_BALANCE = "valid_no_balance"
VALID_MODEL_UNAVAILABLE = "valid_model_unavailable"
MISSING = "missing"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

# "the key is good" — everything the green dot covers.
_OK_STATUSES = frozenset({VALID, VALID_NO_BALANCE, VALID_MODEL_UNAVAILABLE})


@dataclass
class CredentialResult:
    provider_id: str
    status: str
    ok: bool
    kind: str
    via: Optional[str] = None
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    model: Optional[str] = None
    detail: Optional[str] = None
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def to_legacy(self) -> dict:
        """The shape the React ``Connectivity`` component + ``verify_key``
        already read: ``{ok, latency_ms, model?, via?, note?, error?}``."""
        d: dict[str, Any] = {"ok": self.ok, "latency_ms": self.latency_ms}
        if self.via:
            d["via"] = self.via
        if self.model:
            d["model"] = self.model
        if self.ok:
            if self.status == VALID_MODEL_UNAVAILABLE and self.detail:
                d["note"] = self.detail
        else:
            d["error"] = self.detail or (f"HTTP {self.http_status}" if self.http_status else "failed")
        return d


def _result(
    provider_id: str, status: str, *, kind: str, via: str | None = None,
    http_status: int | None = None, latency_ms: int | None = None,
    model: str | None = None, detail: str | None = None, cached: bool = False,
) -> CredentialResult:
    return CredentialResult(
        provider_id=provider_id, status=status, ok=status in _OK_STATUSES,
        kind=kind, via=via, http_status=http_status, latency_ms=latency_ms,
        model=model, detail=detail, cached=cached,
    )


# ── provider KIND classification (doc §6) ─────────────────────────────────────
_OAUTH_PROVIDERS = frozenset({
    "openai-codex", "gemini-subscription", "github-copilot",
    "opencode", "opencode-go",
})
# claude-code speaks the anthropic-messages wire but uses OAuth subscription
# tokens (stored in the AuthManager under the "anthropic" pool) instead of
# a raw x-api-key. Kind is "anthropic_native" so wire-invariant tests pass;
# validate_credential handles it via _oauth_check (see below).
_ANTHROPIC_OAUTH_PROVIDERS = frozenset({"claude-code"})
_CLOUD_PROVIDERS = frozenset({
    "amazon-bedrock", "google-vertex", "azure-openai-responses",
})


def _provider_api(provider_id: str) -> str | None:
    """The wire API a provider speaks (``anthropic-messages`` /
    ``openai-completions`` / …), used to pick the right auth probe.

    Delegates to the one derivation (``providers._default_api_for``):
    the provider's own static-model wire, else an override, else the
    ``…/anthropic`` community heuristic — so credential, fetch, and chat
    all classify a provider identically and can't disagree."""
    try:
        from .providers import _default_api_for
        return _default_api_for(provider_id)
    except Exception:
        return None


def _kind_for(provider_id: str) -> str:
    if provider_id == "openrouter":
        return "openrouter_key"
    if provider_id == "anthropic":
        return "anthropic_native"
    if provider_id in _ANTHROPIC_OAUTH_PROVIDERS:
        # Speaks the anthropic-messages wire; uses OAuth tokens, not x-api-key.
        # validate_credential routes these through _oauth_check.
        return "anthropic_native"
    if provider_id == "google":
        return "google_query"
    if provider_id in _OAUTH_PROVIDERS:
        return "oauth"
    if provider_id in _CLOUD_PROVIDERS:
        return "cloud"
    # Third-party providers that speak the Anthropic Messages wire format
    # (e.g. minimax-cn at api.minimaxi.com/anthropic) need an Anthropic-
    # style probe against THEIR OWN base_url — x-api-key + GET /v1/models,
    # not the OpenAI-shaped GET /models + POST /chat/completions, which
    # 404s on those hosts and would brand a perfectly good key as invalid.
    if _provider_api(provider_id) == "anthropic-messages":
        return "anthropic_compat"
    return "openai_bearer"


# ── status-code interpretation (doc §7) ───────────────────────────────────────
# Statuses where the request authenticated + routed but the chosen model is
# transiently unavailable — only reachable past auth, so the key is proven good.
_MODEL_DOWN_STATUSES = frozenset({429, 500, 502, 503, 504})


def _is_model_unavailable(status: int, body: str) -> bool:
    if status in _MODEL_DOWN_STATUSES:
        return True
    if status == 404:
        low = (body or "").lower()
        return "no endpoints" in low or "data policy" in low or "guardrail" in low
    return False


def _is_no_balance(status: int, body: str) -> bool:
    if status == 402:
        return True
    low = (body or "").lower()
    return (
        "insufficient_quota" in low
        or "insufficient balance" in low
        or "exceeded your current quota" in low
    )


def _short(body: str | None, n: int = 200) -> str:
    return (body or "")[:n]


# ── auth-only HTTP probe ──────────────────────────────────────────────────────
def _http_get(
    url: str, *, headers: dict | None = None, params: dict | None = None,
    timeout: float = 15.0,
) -> tuple[int, str, int] | None:
    """``(status, body, latency_ms)`` or ``None`` on a transport error."""
    import httpx

    t0 = time.time()
    try:
        r = httpx.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
    except httpx.RequestError:
        return None
    return (r.status_code, r.text, int((time.time() - t0) * 1000))


def _openrouter_exhausted(body: str) -> bool:
    """OpenRouter ``/key`` reports ``data.limit_remaining`` — ``0`` (not null)
    means the key's credit limit is used up."""
    try:
        import json
        d = json.loads(body).get("data", {})
        rem = d.get("limit_remaining")
        return rem is not None and float(rem) <= 0
    except Exception:
        return False


def _interpret(
    provider_id: str, kind: str, res: tuple[int, str, int] | None, *,
    via: str, balance_body: bool = False,
) -> CredentialResult:
    if res is None:
        return _result(
            provider_id, UNKNOWN, kind=kind, via=via,
            detail=(f"Couldn't reach {provider_id} to verify (network/timeout). "
                    "Saved anyway; it'll be validated on first use."),
        )
    status, body, latency = res
    if status == 200:
        if balance_body and _openrouter_exhausted(body):
            return _result(provider_id, VALID_NO_BALANCE, kind=kind, via=via,
                           http_status=200, latency_ms=latency,
                           detail="Key works — credit limit is used up. Add funds in the OpenRouter dashboard.")
        return _result(provider_id, VALID, kind=kind, via=via, http_status=200, latency_ms=latency)
    if status in (401, 403):
        return _result(provider_id, INVALID_CREDENTIAL, kind=kind, via=via,
                       http_status=status, latency_ms=latency,
                       detail=f"Key rejected (HTTP {status}). Re-check the key or re-login. {_short(body)}".strip())
    if _is_no_balance(status, body):
        return _result(provider_id, VALID_NO_BALANCE, kind=kind, via=via,
                       http_status=status, latency_ms=latency,
                       detail="Key works — account has no balance/credits. Add funds to use it.")
    # 404/400/5xx on the *auth* endpoint is ambiguous for key validity — don't
    # brand the key bad; report unknown so a save still succeeds.
    return _result(provider_id, UNKNOWN, kind=kind, via=via, http_status=status,
                   latency_ms=latency, detail=f"HTTP {status}: {_short(body)}")


def _layer1_probe(provider_id: str, kind: str, api_key: str, base: str | None,
                  timeout: float) -> CredentialResult:
    """Model-independent auth probe — the canonical "is THIS KEY valid"."""
    if kind == "openrouter_key":
        res = _http_get(base.rstrip("/") + "/key",
                        headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        return _interpret(provider_id, kind, res, via="GET /key", balance_body=True)
    if kind == "anthropic_native":
        res = _http_get("https://api.anthropic.com/v1/models",
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                        timeout=timeout)
        return _interpret(provider_id, kind, res, via="GET /v1/models")
    if kind == "anthropic_compat":
        # Same Anthropic-style probe, but against the provider's own host
        # (base already resolved by validate_credential). MiniMax & friends
        # expose Anthropic's GET /v1/models, so this proves the key without
        # an inference call.
        res = _http_get(base.rstrip("/") + "/v1/models",
                        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                        timeout=timeout)
        return _interpret(provider_id, kind, res, via="GET /v1/models")
    if kind == "google_query":
        res = _http_get("https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": api_key, "pageSize": 1}, timeout=timeout)
        return _interpret(provider_id, kind, res, via="GET /v1beta/models")
    # openai_bearer (default)
    res = _http_get(base.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    return _interpret(provider_id, kind, res, via="GET /models")


def _layer2_ping(provider_id: str, kind: str, api_key: str, base: str | None,
                 model: str, timeout: float) -> CredentialResult:
    """Inference ping — "can I reach THIS model right now?". Only OpenAI-shaped
    chat completions are pingable here; other KINDs fall back to the auth probe
    with the model echoed."""
    if kind not in ("openai_bearer", "openrouter_key"):
        r = _layer1_probe(provider_id, kind, api_key, base, timeout)
        return dataclasses.replace(r, model=model)

    import httpx
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": [{"role": "user", "content": "PING"}], "max_tokens": 4}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    t0 = time.time()
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.RequestError as e:
        return _result(provider_id, UNKNOWN, kind=kind, model=model, detail=f"Request failed: {e}")
    latency = int((time.time() - t0) * 1000)
    status, text = r.status_code, r.text
    if status == 200:
        return _result(provider_id, VALID, kind=kind, via="POST /chat/completions",
                       http_status=200, latency_ms=latency, model=model)
    if _is_model_unavailable(status, text):
        return _result(provider_id, VALID_MODEL_UNAVAILABLE, kind=kind,
                       via="POST /chat/completions", http_status=status, latency_ms=latency,
                       model=model,
                       detail=(f"Key authenticated. Model {model} is unavailable right now "
                               f"(HTTP {status}: {_short(text)})."))
    if status in (401, 403):
        return _result(provider_id, INVALID_CREDENTIAL, kind=kind, http_status=status,
                       latency_ms=latency, model=model, detail=f"HTTP {status}: {_short(text)}")
    if _is_no_balance(status, text):
        return _result(provider_id, VALID_NO_BALANCE, kind=kind, http_status=status,
                       latency_ms=latency, model=model,
                       detail="Key works — account has no balance/credits.")
    return _result(provider_id, INVALID_CREDENTIAL, kind=kind, http_status=status,
                   latency_ms=latency, model=model, detail=f"HTTP {status}: {_short(text)}")


def _oauth_check(provider_id: str, kind: str) -> CredentialResult:
    """OAuth/subscription providers carry no api_key — read the AuthManager
    credential status instead of touching a model."""
    try:
        from openprogram.auth.manager import get_manager
        cred = get_manager().acquire_sync(provider_id)
    except Exception:
        return _result(provider_id, UNKNOWN, kind=kind, via="AuthManager",
                       detail=(f"Not logged in or couldn't read login state — run "
                               f"`openprogram providers login {provider_id}`."))
    st = getattr(cred, "status", None)
    token = getattr(getattr(cred, "payload", None), "access_token", None)
    if st == "needs_reauth":
        return _result(provider_id, INVALID_CREDENTIAL, kind=kind, via="AuthManager",
                       detail=f"Login expired — run `openprogram providers login {provider_id}`.")
    if st in ("fresh", "expiring_soon", "stale", "refreshing") or token:
        return _result(provider_id, VALID, kind=kind, via="AuthManager",
                       detail=f"Logged in{f' ({st})' if st else ''}.")
    return _result(provider_id, UNKNOWN, kind=kind, via="AuthManager", detail="Login state unknown.")


# ── 60s cache (doc §8) ────────────────────────────────────────────────────────
_CACHE_TTL_S = 60.0
_cache: dict[tuple, tuple[float, CredentialResult]] = {}


def _cache_get(key: tuple) -> CredentialResult | None:
    ent = _cache.get(key)
    if ent is None:
        return None
    ts, res = ent
    if time.time() - ts > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return dataclasses.replace(res, cached=True)


def _cache_put(key: tuple, res: CredentialResult) -> None:
    _cache[key] = (time.time(), res)


# ── public API ────────────────────────────────────────────────────────────────
def validate_credential(
    provider_id: str, *, api_key: str | None = None, model: str | None = None,
    timeout: float = 15.0, use_cache: bool = True,
) -> CredentialResult:
    """Validate a provider credential without invoking a model (unless ``model``
    is given, which additionally checks that one model's reachability)."""
    kind = _kind_for(provider_id)

    if kind == "oauth" or provider_id in _ANTHROPIC_OAUTH_PROVIDERS:
        return _oauth_check(provider_id, kind)
    if kind == "cloud":
        return _result(provider_id, NOT_APPLICABLE, kind=kind,
                       detail=("Cloud credential (SigV4 / ADC / deployment-keyed) — not "
                               "covered by the generic auth probe; verified at first use."))

    # Resolve the key if the caller didn't hand one in (save-verify passes it).
    if api_key is None:
        from .storage import _resolve_api_key
        api_key = _resolve_api_key(provider_id)
    if not api_key:
        from .providers import _env_var_for
        env = _env_var_for(provider_id)
        return _result(provider_id, MISSING, kind=kind,
                       detail=f"No API key set ({env})." if env else "No credential configured.")

    # Cache only the model-independent (layer-1) result.
    cache_key = (provider_id, model or "")
    if use_cache and model is None:
        hit = _cache_get(cache_key)
        if hit is not None:
            return hit

    base = None
    if kind in ("openai_bearer", "openrouter_key", "anthropic_compat"):
        from .storage import _resolve_base_url
        base = _resolve_base_url(provider_id)
        if not base:
            return _result(provider_id, UNKNOWN, kind=kind, detail="No base URL resolvable.")

    if model is not None:
        return _layer2_ping(provider_id, kind, api_key, base, model, timeout)

    result = _layer1_probe(provider_id, kind, api_key, base, timeout)
    if use_cache:
        _cache_put(cache_key, result)
    return result


def provider_id_for_env_var(env_var: str) -> str | None:
    """Reverse of the env-var → provider mapping, for the save-key verify path
    which only knows the env var name. Re-exports the canonical reverse map in
    ``providers.env_api_keys`` (one source of truth for provider ↔ env-var)."""
    from openprogram.providers.env_api_keys import provider_id_for_env_var as _canon
    return _canon(env_var)


def provider_auth_status(
    provider_ids: list[str] | None = None, refresh: bool = False,
) -> dict[str, dict]:
    """Batch credential status (mirrors OpenClaw ``models.authStatus``). Live +
    60s-cached per provider; ``refresh=True`` bypasses the cache. Pass explicit
    ``provider_ids`` to avoid probing the full registry."""
    if provider_ids is None:
        try:
            from openprogram.providers.registry import check_providers
            provider_ids = list(check_providers().keys())
        except Exception:
            provider_ids = []
    out: dict[str, dict] = {}
    for pid in provider_ids:
        out[pid] = validate_credential(pid, use_cache=not refresh).to_dict()
    return out


async def provider_auth_status_async(
    provider_ids: list[str] | None = None, refresh: bool = False,
) -> dict[str, dict]:
    """Async, concurrent variant of :func:`provider_auth_status`.

    Each per-provider probe (a synchronous network call) runs in a worker
    thread via ``asyncio.to_thread`` and they are awaited together with
    ``asyncio.gather`` — so the event loop is never blocked on a sequential
    chain of probes, and the batch's wall-clock is the slowest single probe
    instead of their sum. ``validate_credential`` itself stays synchronous for
    its many sync callers (the save-key verify path, the single-provider
    routes); only the batch is parallelised. The 60s cache still applies.
    """
    import asyncio

    if provider_ids is None:
        try:
            from openprogram.providers.registry import check_providers
            provider_ids = list(check_providers().keys())
        except Exception:
            provider_ids = []

    async def _one(pid: str) -> tuple[str, dict]:
        res = await asyncio.to_thread(validate_credential, pid, use_cache=not refresh)
        return pid, res.to_dict()

    pairs = await asyncio.gather(*[_one(p) for p in provider_ids])
    return dict(pairs)
