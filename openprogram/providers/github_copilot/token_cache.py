"""Short-lived Copilot API token cache.

Copilot's two-tier auth: the GitHub OAuth token (long-lived, managed by
AuthManager) vs the Copilot **api_token** (short-lived, ~30 min,
obtained by exchanging the GitHub OAuth token at
``api.github.com/copilot_internal/v2/token``). Request bearers against
the Copilot chat backend use the api_token, not the GitHub OAuth.

We don't persist api_tokens — they churn too fast to belong in the
credential store. This module caches them in-process keyed by the
GitHub OAuth token string; cache misses + near-expiry trigger a fresh
exchange. The cache is a plain dict, no locking — concurrent callers
may each kick off an exchange on a cold cache, which is harmless: the
GitHub endpoint is idempotent and returns the same api_token to both.

Parallel to what pi-ai / Hermes / OpenClaw do; the v2 endpoint + JSON
shape is stable across implementations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx


_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"
# Refresh a little before actual expiry so mid-request exchanges can't
# race the clock. 60s matches what every other layer uses.
_SKEW_SECONDS = 60


@dataclass(frozen=True)
class CopilotApiToken:
    """The short-lived bearer we pass to Copilot's chat endpoint."""

    token: str
    expires_at_epoch: int  # unix seconds

    def is_expired(self, *, skew_seconds: int = _SKEW_SECONDS) -> bool:
        return int(time.time()) + skew_seconds >= self.expires_at_epoch


# Cache keyed by the raw GitHub OAuth token string. Since we never
# expose this cache beyond this process and the key *is* the secret,
# there's no extra leakage risk vs holding the token in a Credential.
_cache: dict[str, CopilotApiToken] = {}


# Exchange hook — overridden in tests via ``set_exchange_fn`` to avoid
# HTTP. Production path is ``_http_exchange`` below.
ExchangeFn = Callable[[str, Optional[str]], CopilotApiToken]
_exchange_impl: Optional[ExchangeFn] = None


def _http_exchange(
    github_oauth_token: str,
    base_url: Optional[str] = None,
) -> CopilotApiToken:
    """Trade a GitHub OAuth token for a Copilot api_token via HTTP."""
    url = (base_url or "https://api.github.com").rstrip("/") + "/copilot_internal/v2/token"
    with httpx.Client(timeout=15.0) as client:
        resp = client.get(
            url,
            headers={
                "Authorization": f"token {github_oauth_token}",
                "Editor-Version": "openprogram/1.0",
                "Editor-Plugin-Version": "openprogram/1.0",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    token = data.get("token") or ""
    expires_at = int(data.get("expires_at") or 0)
    if not token or expires_at <= 0:
        raise RuntimeError(
            f"Copilot api_token exchange returned malformed response: "
            f"keys={sorted(data.keys())!r}"
        )
    return CopilotApiToken(token=token, expires_at_epoch=expires_at)


def set_exchange_fn(fn: Optional[ExchangeFn]) -> None:
    """Testing hook: swap the exchange implementation."""
    global _exchange_impl
    _exchange_impl = fn


def _exchange(github_oauth_token: str, base_url: Optional[str]) -> CopilotApiToken:
    impl = _exchange_impl or _http_exchange
    return impl(github_oauth_token, base_url)


def get_copilot_api_token(
    github_oauth_token: str,
    *,
    base_url: Optional[str] = None,
    force_refresh: bool = False,
) -> str:
    """Return a fresh Copilot api_token, exchanging if cache is cold / expiring.

    Args:
        github_oauth_token: The long-lived GitHub OAuth token — what
            :mod:`auth_adapter` persists in the credential store.
        base_url: Override the GitHub API host (for enterprise GHES).
            Defaults to api.github.com.
        force_refresh: Skip the cache and always exchange. Use for
            handling 401s from the Copilot backend.

    Returns:
        The ``token`` string to put in the ``Authorization`` header for
        Copilot chat completions.
    """
    if not github_oauth_token:
        raise ValueError("github_oauth_token must be non-empty")

    cached = _cache.get(github_oauth_token)
    if cached is not None and not cached.is_expired() and not force_refresh:
        return cached.token

    fresh = _exchange(github_oauth_token, base_url)
    _cache[github_oauth_token] = fresh
    return fresh.token


def invalidate(github_oauth_token: str) -> None:
    """Drop the cached api_token for this GitHub OAuth token.

    Call when the Copilot backend returns 401 on a token we thought was
    valid — the next get_copilot_api_token call will re-exchange.
    """
    _cache.pop(github_oauth_token, None)


def clear_all() -> None:
    """Test-only: nuke the whole cache."""
    _cache.clear()


__all__ = [
    "CopilotApiToken",
    "get_copilot_api_token",
    "invalidate",
    "clear_all",
    "set_exchange_fn",
]
