"""Unified provider-credential validation — one entry point, every surface.

Validating a *credential* must never invoke a *model*. Each provider KIND has a
model-independent auth probe (``GET /models``, ``GET /key`` for OpenRouter,
``x-api-key /v1/models`` for Anthropic, ``?key=`` for Google, or the
``CredentialProvider`` credential status for OAuth providers). A completion ping runs
ONLY as layer 2 — when a caller explicitly names a model to check that one
model's reachability.

``validate_credential`` returns a single structured ``CredentialResult`` with a
closed-enum ``status`` so every surface (save-key verify, the connectivity
button, the CLI/TUI status rows) renders the same distinctions: key rejected vs
key-fine-no-balance vs key-fine-that-model-is-down.

See ``docs/design/providers/auth/credential-validation-unification.md``.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional


# status taxonomy (doc §7)
VALID = "valid"
INVALID_CREDENTIAL = "invalid_credential"
VALID_NO_BALANCE = "valid_no_balance"
VALID_MODEL_UNAVAILABLE = "valid_model_unavailable"
MISSING = "missing"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

# ``ok`` / green Valid means usable *now* — only the exact persist-grade
# ``valid``. Probe-only statuses (no balance, model down) must never be ok.
_OK_STATUSES = frozenset({VALID})

# Layer-1 GET /models (and siblings) is auth-only: a 200 proves the key was
# accepted, not that the account can complete. These kinds have no cheap
# billing endpoint; only OpenRouter ``GET /key`` (or a layer-2 ping / live
# 200 chat) may treat a 200 as usable-proven.
_AUTH_ONLY_KINDS = frozenset({
    "openai_bearer", "anthropic_native", "anthropic_compat", "google_query",
})


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
        d["status"] = self.status
        if self.status == VALID_MODEL_UNAVAILABLE and self.detail:
            d["note"] = self.detail
        if not self.ok:
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


# provider KIND classification (doc §6)
_OAUTH_PROVIDERS = frozenset({
    "openai-codex", "gemini-subscription", "github-copilot",
    "xai-subscription",
    "opencode", "opencode-go",
})
# claude-code speaks the anthropic-messages wire but uses OAuth subscription
# tokens (stored in the CredentialProvider under the "anthropic" pool) instead of
# a raw x-api-key. Kind is "anthropic_native" so wire-invariant tests pass;
# validate_credential handles it via _oauth_check (see below).
_ANTHROPIC_OAUTH_PROVIDERS = frozenset({"claude-code"})
_CLOUD_PROVIDERS = frozenset({
    "amazon-bedrock", "google-vertex", "azure-openai-responses",
})


def _provider_api(provider_id: str) -> str | None:
    """The wire API a provider speaks (``anthropic-messages`` /
    ``openai-completions`` / …), used to pick the right auth probe.

    Delegates to the one derivation (``providers.default_api_for``):
    the provider's own static-model wire, else an override, else the
    ``…/anthropic`` community heuristic — so credential, fetch, and chat
    all classify a provider identically and can't disagree."""
    try:
        from openprogram.providers.metadata import default_api_for
        return default_api_for(provider_id)
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


# status-code interpretation (doc §7)
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


# auth-only HTTP probe
def _http_get(
    url: str, *, headers: dict | None = None, params: dict | None = None,
    timeout: float = 15.0, configured_url: str | None = None,
) -> tuple[int, str, int] | None:
    """``(status, body, latency_ms)`` or ``None`` on a transport error."""
    from openprogram.security import safe_http
    from openprogram.security.url_policy import OwnerURLException, normalize_origin

    t0 = time.time()
    try:
        client = (
            safe_http.configured_safe_client(
                "webui.model_listing.configured",
                configured_url,
                owner_exception=OwnerURLException(
                    consumer="webui.model_listing.configured",
                    origin=normalize_origin(configured_url),
                ),
            )
            if configured_url is not None
            else safe_http.safe_client("webui.model_listing.fixed")
        )
        with client:
            r = client.get(
                url, headers=headers or {}, params=params or {}, timeout=timeout
            )
    except Exception:
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
    via: str, balance_body: bool = False, require_json: bool = False,
) -> CredentialResult:
    if res is None:
        return _result(
            provider_id, UNKNOWN, kind=kind, via=via,
            detail=(f"Couldn't reach {provider_id} to verify (network/timeout). "
                    "Saved anyway; it'll be validated on first use."),
        )
    status, body, latency = res
    if status == 200:
        if require_json:
            try:
                import json
                json.loads(body)
            except (TypeError, ValueError):
                return _result(
                    provider_id, UNKNOWN, kind=kind, via=via,
                    http_status=200, latency_ms=latency,
                    detail="Endpoint returned non-JSON content.",
                )
        if balance_body and _openrouter_exhausted(body):
            return _result(provider_id, VALID_NO_BALANCE, kind=kind, via=via,
                           http_status=200, latency_ms=latency,
                           detail="Key works — credit limit is used up. Add funds in the OpenRouter dashboard.")
        # 200 on a non-billing endpoint is still a probe ``valid`` (auth
        # accepted). Callers must not persist that as usable ``valid`` unless
        # this is OpenRouter ``GET /key`` (``balance_body=True``, remaining > 0)
        # or a later layer-2 ping / live chat proves the key can complete.
        return _result(provider_id, VALID, kind=kind, via=via, http_status=200, latency_ms=latency)
    if status in (401, 403):
        return _result(provider_id, INVALID_CREDENTIAL, kind=kind, via=via,
                       http_status=status, latency_ms=latency,
                       detail=f"Key rejected (HTTP {status}). Re-check the key or re-login.")
    if _is_no_balance(status, body):
        return _result(provider_id, VALID_NO_BALANCE, kind=kind, via=via,
                       http_status=status, latency_ms=latency,
                       detail="Key works — account has no balance/credits. Add funds to use it.")
    # 404/400/5xx on the *auth* endpoint is ambiguous for key validity — don't
    # brand the key bad; report unknown so a save still succeeds.
    return _result(provider_id, UNKNOWN, kind=kind, via=via, http_status=status,
                   latency_ms=latency, detail=f"HTTP {status}.")


def _layer1_probe(provider_id: str, kind: str, api_key: str, base: str | None,
                  timeout: float) -> CredentialResult:
    """Model-independent auth probe — the canonical "is THIS KEY valid"."""
    if kind == "openrouter_key":
        res = _http_get(base.rstrip("/") + "/key",
                        headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout,
                        configured_url=base)
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
                        timeout=timeout, configured_url=base)
        return _interpret(provider_id, kind, res, via="GET /v1/models")
    if kind == "google_query":
        res = _http_get("https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": api_key, "pageSize": 1}, timeout=timeout)
        return _interpret(provider_id, kind, res, via="GET /v1beta/models")
    # openai_bearer (default)
    res = _http_get(base.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout,
                    configured_url=base)
    return _interpret(
        provider_id, kind, res, via="GET /models", require_json=True
    )


def _layer2_response(
    provider_id: str, kind: str, via: str, model: str,
    status: int, text: str, latency: int,
) -> CredentialResult:
    """Map a completion-ping HTTP result onto the probe taxonomy."""
    if status == 200:
        return _result(provider_id, VALID, kind=kind, via=via,
                       http_status=200, latency_ms=latency, model=model)
    if _is_model_unavailable(status, text):
        return _result(provider_id, VALID_MODEL_UNAVAILABLE, kind=kind,
                       via=via, http_status=status, latency_ms=latency,
                       model=model,
                       detail=(f"Key authenticated. Model {model} is unavailable right now "
                               f"(HTTP {status})."))
    if status in (401, 403):
        return _result(provider_id, INVALID_CREDENTIAL, kind=kind, via=via,
                       http_status=status, latency_ms=latency, model=model,
                       detail=f"HTTP {status}.")
    if _is_no_balance(status, text):
        return _result(provider_id, VALID_NO_BALANCE, kind=kind, via=via,
                       http_status=status, latency_ms=latency, model=model,
                       detail="Key works — account has no balance/credits.")
    return _result(provider_id, UNKNOWN, kind=kind, via=via, http_status=status,
                   latency_ms=latency, model=model, detail=f"HTTP {status}.")


def _layer2_post(
    url: str, *, headers: dict, json_body: dict, timeout: float,
    configured_url: str | None, params: dict | None = None,
) -> tuple[int, str, int] | None:
    from openprogram.security import safe_http
    from openprogram.security.url_policy import OwnerURLException, normalize_origin

    t0 = time.time()
    try:
        client = (
            safe_http.configured_safe_client(
                "webui.model_listing.configured",
                configured_url,
                owner_exception=OwnerURLException(
                    consumer="webui.model_listing.configured",
                    origin=normalize_origin(configured_url),
                ),
            )
            if configured_url is not None
            else safe_http.safe_client("webui.model_listing.fixed")
        )
        with client:
            r = client.post(
                url, headers=headers, json=json_body,
                params=params or {}, timeout=timeout,
            )
    except Exception:
        return None
    return (r.status_code, r.text, int((time.time() - t0) * 1000))


def _layer2_ping(provider_id: str, kind: str, api_key: str, base: str | None,
                 model: str, timeout: float) -> CredentialResult:
    """Inference ping — "can I reach THIS model right now?".

    OpenAI-shaped kinds POST ``/chat/completions``; Anthropic-shaped kinds
    POST ``/v1/messages``; Google POSTs ``:generateContent``. A kind that
    cannot be pinged safely is left ``unknown`` rather than painted Valid.
    """
    from openprogram.security.url_policy import normalize_origin

    if kind in ("openai_bearer", "openrouter_key"):
        if not base:
            return _result(provider_id, UNKNOWN, kind=kind, model=model,
                           detail="No base URL resolvable.")
        url = base.rstrip("/") + "/chat/completions"
        res = _layer2_post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json_body={
                "model": model,
                "messages": [{"role": "user", "content": "PING"}],
                "max_tokens": 4,
            },
            timeout=timeout,
            configured_url=base,
        )
        if res is None:
            return _result(
                provider_id, UNKNOWN, kind=kind, model=model,
                detail=f"Request failed for {normalize_origin(url)}",
            )
        status, text, latency = res
        return _layer2_response(
            provider_id, kind, "POST /chat/completions", model, status, text, latency,
        )

    if kind in ("anthropic_native", "anthropic_compat"):
        if kind == "anthropic_native":
            url = "https://api.anthropic.com/v1/messages"
            configured_url = None
        else:
            if not base:
                return _result(provider_id, UNKNOWN, kind=kind, model=model,
                               detail="No base URL resolvable.")
            url = base.rstrip("/") + "/v1/messages"
            configured_url = base
        res = _layer2_post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json_body={
                "model": model,
                "max_tokens": 4,
                "messages": [{"role": "user", "content": "PING"}],
            },
            timeout=timeout,
            configured_url=configured_url,
        )
        if res is None:
            return _result(
                provider_id, UNKNOWN, kind=kind, model=model,
                detail=f"Request failed for {normalize_origin(url)}",
            )
        status, text, latency = res
        return _layer2_response(
            provider_id, kind, "POST /v1/messages", model, status, text, latency,
        )

    if kind == "google_query":
        mid = model[7:] if model.startswith("models/") else model
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{mid}:generateContent"
        )
        res = _layer2_post(
            url,
            headers={"Content-Type": "application/json"},
            json_body={
                "contents": [{"parts": [{"text": "PING"}]}],
                "generationConfig": {"maxOutputTokens": 4},
            },
            timeout=timeout,
            configured_url=None,
            params={"key": api_key},
        )
        if res is None:
            return _result(
                provider_id, UNKNOWN, kind=kind, model=model,
                detail="Request failed for generativelanguage.googleapis.com",
            )
        status, text, latency = res
        return _layer2_response(
            provider_id, kind, "POST /v1beta/models:generateContent",
            model, status, text, latency,
        )

    return _result(
        provider_id, UNKNOWN, kind=kind, model=model,
        detail=f"No safe completion ping for {kind}; usability not confirmed.",
    )


def _oauth_token(cred) -> str:
    """Read the bearer from a Credential. Auth v2 stores it on ``payload.auth_value``;
    leftover migrated payloads may still expose ``access_token``."""
    payload = getattr(cred, "payload", None)
    if payload is None:
        return ""
    return (
        getattr(payload, "auth_value", None)
        or getattr(payload, "access_token", None)
        or ""
    )


def _xai_subscription_probe(provider_id: str, kind: str, token: str) -> CredentialResult:
    """Live auth check against the Grok CLI chat proxy (not api.x.ai)."""
    from openprogram.providers.xai_subscription.headers import (
        CLI_CHAT_PROXY_BASE_URL,
        grok_cli_headers,
    )
    url = CLI_CHAT_PROXY_BASE_URL.rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {token}",
        **grok_cli_headers("grok-4.5"),
    }
    res = _http_get(
        url,
        headers=headers,
        configured_url=CLI_CHAT_PROXY_BASE_URL,
    )
    return _interpret(
        provider_id, kind, res, via="GET /models", require_json=True,
    )


def _oauth_check(provider_id: str, kind: str) -> CredentialResult:
    """OAuth/subscription providers carry no api_key — read the CredentialProvider
    credential status instead of touching a model."""
    try:
        from openprogram.auth.credential_provider import get_credential_provider
        cred = get_credential_provider().acquire_sync(provider_id)
    except Exception:
        return _result(provider_id, UNKNOWN, kind=kind, via="CredentialProvider",
                       detail=(f"Not logged in or couldn't read login state — run "
                               f"`openprogram providers login {provider_id}`."))
    st = getattr(cred, "status", None)
    token = _oauth_token(cred)
    if st in ("needs_reauth", "revoked"):
        return _result(provider_id, INVALID_CREDENTIAL, kind=kind, via="CredentialProvider",
                       detail=f"Login expired — run `openprogram providers login {provider_id}`.")
    if st == "billing_blocked":
        return _result(provider_id, VALID_NO_BALANCE, kind=kind, via="CredentialProvider",
                       detail="Logged in, but billing is blocked.")
    logged_in = st in (
        "valid", "fresh", "expiring_soon", "stale", "refreshing", "rate_limited",
    ) or bool(token)
    if not logged_in:
        return _result(provider_id, UNKNOWN, kind=kind, via="CredentialProvider",
                       detail="Login state unknown.")
    if provider_id == "xai-subscription" and token:
        live = _xai_subscription_probe(provider_id, kind, token)
        # Transport failure: still treat the stored login as valid so Check
        # does not bounce back to "unknown" after a flaky proxy hop.
        if live.http_status is not None or live.status != UNKNOWN:
            return live
    return _result(provider_id, VALID, kind=kind, via="CredentialProvider",
                   detail=f"Logged in{f' ({st})' if st else ''}.")


# 60s cache (doc §8)
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


def probe_proves_usable(result: CredentialResult) -> bool:
    """True only when the probe itself proved the key can complete now.

    OpenRouter ``GET /key`` with remaining > 0, a layer-2 completion 200, a
    live OAuth token, or (implicitly) a later chat 200. Layer-1 ``GET /models``
    200 is auth-only and does **not** prove usable.
    """
    if getattr(result, "status", None) != VALID:
        return False
    kind = getattr(result, "kind", "") or ""
    via = getattr(result, "via", "") or ""
    if kind == "openrouter_key" and via.startswith("GET /key"):
        return True
    if via.startswith("POST "):
        return True
    if kind == "oauth" or via == "CredentialProvider":
        return True
    return False


# Persisted account statuses (shown in Settings). Probe-only values such as
# ``valid_no_balance`` are mapped here and must never be written as ``valid``.
_PERSIST_BILLING = "billing_blocked"
_PERSIST_REAUTH = "needs_reauth"


def persist_status_from_probe(
    probe_status: str,
    *,
    previous: str = "",
    usable_proven: bool = False,
) -> str | None:
    """Map a probe status onto the persisted enum, or ``None`` to leave previous.

    ``valid`` is returned only when ``usable_proven`` is true. Auth-only layer-1
    200 must not upgrade a credential (and must not clear ``billing_blocked``).
    """
    if probe_status == VALID:
        return VALID if usable_proven else None
    if probe_status == VALID_NO_BALANCE:
        return _PERSIST_BILLING
    if probe_status == INVALID_CREDENTIAL:
        return _PERSIST_REAUTH
    if probe_status == VALID_MODEL_UNAVAILABLE:
        return None
    if probe_status == MISSING:
        return MISSING
    if probe_status == UNKNOWN:
        return None
    if probe_status == "rate_limited":
        return "rate_limited"
    return None


def display_status_from_probe(
    probe_status: str,
    *,
    previous: str = "",
    usable_proven: bool = False,
) -> str:
    """Status the Settings chip / connectivity check should render."""
    persist = persist_status_from_probe(
        probe_status, previous=previous, usable_proven=usable_proven,
    )
    if persist:
        return persist
    if probe_status == VALID_MODEL_UNAVAILABLE:
        return VALID_MODEL_UNAVAILABLE
    if probe_status == VALID and not usable_proven:
        if previous == _PERSIST_BILLING:
            return _PERSIST_BILLING
        return UNKNOWN
    if probe_status == UNKNOWN:
        return previous or UNKNOWN
    return probe_status or UNKNOWN


def _first_id_from_models_json(body: str) -> str | None:
    """First model id from an OpenAI / Anthropic / Google list payload."""
    try:
        import json
        data = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    rows = data.get("data") or data.get("models") or []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, str) and row.strip():
            mid = row.strip()
        elif isinstance(row, dict):
            mid = row.get("id") or row.get("name") or ""
            if not isinstance(mid, str):
                continue
            mid = mid.strip()
        else:
            continue
        if mid:
            if mid.startswith("models/"):
                mid = mid[len("models/"):]
            return mid
    return None


def resolve_ping_model(provider_id: str, *, layer1_body: str | None = None) -> str | None:
    """Name a real model for an explicit layer-2 ping. Never invents an id.

    Preference: an enabled model for this provider, then the first id from a
    layer-1 ``/models`` body, then the provider catalog / browse list.
    """
    try:
        from openprogram.providers import get_models
        enabled = [m for m in get_models(provider_id) if getattr(m, "id", None)]
        if enabled:
            return enabled[0].id
    except Exception:
        pass
    if layer1_body:
        mid = _first_id_from_models_json(layer1_body)
        if mid:
            return mid
    try:
        from openprogram.webui._model_listing.listing import list_models_for_provider
        for row in list_models_for_provider(provider_id):
            mid = (row or {}).get("id")
            if mid:
                return mid
    except Exception:
        pass
    return None


def _maybe_layer2_usable(
    provider_id: str, kind: str, api_key: str, base: str | None,
    layer1: CredentialResult, timeout: float,
) -> CredentialResult:
    """If layer-1 is auth-only 200, run a cheap completion ping against a real model."""
    if layer1.status != VALID or probe_proves_usable(layer1):
        return layer1
    if kind not in _AUTH_ONLY_KINDS:
        return layer1
    model = resolve_ping_model(provider_id)
    if not model:
        return _result(
            provider_id, UNKNOWN, kind=kind, via=layer1.via,
            http_status=layer1.http_status, latency_ms=layer1.latency_ms,
            detail=("Key accepted, but no model is configured or listed to "
                    "confirm it is usable."),
        )
    return _layer2_ping(provider_id, kind, api_key, base, model, timeout)


# public API
def validate_credential(
    provider_id: str, *, api_key: str | None = None, model: str | None = None,
    timeout: float = 15.0, use_cache: bool = True, prove_usable: bool = False,
) -> CredentialResult:
    """Validate a provider credential without invoking a model (unless ``model``
    is given, which additionally checks that one model's reachability).

    ``prove_usable=True`` is for an explicit user Validate / Check: after an
    auth-only layer-1 200 it runs a cheap layer-2 ping so ``valid`` means
    usable now. On-mount and ``provider_auth_status`` must leave this False.
    """
    kind = _kind_for(provider_id)

    if kind == "oauth" or provider_id in _ANTHROPIC_OAUTH_PROVIDERS:
        return _oauth_check(provider_id, kind)
    if kind == "cloud":
        return _result(provider_id, NOT_APPLICABLE, kind=kind,
                       detail=("Cloud credential (SigV4 / ADC / deployment-keyed) — not "
                               "covered by the generic auth probe; verified at first use."))

    # Resolve the key if the caller didn't hand one in (save-verify passes it).
    if api_key is None:
        from openprogram.providers.env_api_keys import resolve_api_key_with_auth_store
        api_key = resolve_api_key_with_auth_store(provider_id)
    if not api_key:
        from openprogram.providers.metadata import env_var_for
        env = env_var_for(provider_id)
        return _result(provider_id, MISSING, kind=kind,
                       detail=f"No API key set ({env})." if env else "No credential configured.")

    # Cache only the model-independent (layer-1) result. A prove-usable ping
    # is never served from that cache — it would hide a dead quota.
    cache_key = (provider_id, model or "")
    if use_cache and model is None and not prove_usable:
        hit = _cache_get(cache_key)
        if hit is not None:
            return hit

    base = None
    if kind in ("openai_bearer", "openrouter_key", "anthropic_compat"):
        from openprogram.providers.storage import _resolve_base_url
        base = _resolve_base_url(provider_id)
        if not base:
            return _result(provider_id, UNKNOWN, kind=kind, detail="No base URL resolvable.")

    if model is not None:
        return _layer2_ping(provider_id, kind, api_key, base, model, timeout)

    result = _layer1_probe(provider_id, kind, api_key, base, timeout)
    if use_cache and not prove_usable:
        _cache_put(cache_key, result)
    if prove_usable:
        return _maybe_layer2_usable(provider_id, kind, api_key, base, result, timeout)
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
