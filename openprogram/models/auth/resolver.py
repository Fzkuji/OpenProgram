"""From a credential to what one request sends.

:func:`resolve_connection` turns a :class:`Credential` into the bearer
string, base URL and headers a request carries: an imported login is read
from the other tool's file, a helper command is run, an environment
variable's key is read from the environment. Model requests get their
credential through :func:`openprogram.models.providers.streaming.credential_failover.with_credential`;
the two ``resolve_*_sync`` helpers serve the callers outside a model request
(probes, tools, model listing), choosing the same way without counting a use.

A configured helper command that fails raises
:class:`AuthCredentialProcessError` instead of yielding nothing: the user
who set up the helper sees the helper's error, not another key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .types import AuthConfigError, AuthCredentialProcessError, AuthError, Credential


@dataclass
class ResolvedConnection:
    """What one request needs, translated from a Credential. ``base_url`` is
    ``None`` when unset so the catalogue's default applies."""

    kind: str
    auth_value: str
    base_url: Optional[str]
    headers: dict


def resolve_connection(cred: Credential) -> Optional[ResolvedConnection]:
    """The bearer value, base URL and headers ``cred`` sends, or ``None``
    when it has no usable value (Anthropic accepts API keys only)."""
    from openprogram.models.auth.provider_policy import UNSUPPORTED_PROVIDERS
    p = cred.payload
    payload_kind = getattr(p, "kind", "") or cred.kind
    if cred.provider_id in UNSUPPORTED_PROVIDERS or (
        cred.provider_id == "anthropic" and cred.kind != "api_key"
    ):
        return None
    auth_value = p.auth_value
    if cred.source == "env":
        auth_value = os.environ.get(cred.metadata.get("env_var") or "", "").strip()
    elif cred.kind == "import":
        auth_value = _read_delegated_token(p) or auth_value
    elif cred.kind == "credential_process" or payload_kind == "credential_process":
        from .methods.credential_process import token_for_payload
        auth_value = token_for_payload(p.data, scope=f"{cred.provider_id}/{cred.id}")
    elif payload_kind == "sso":
        raise AuthConfigError("enterprise SSO credentials are not implemented",
                              provider_id=cred.provider_id)
    if not auth_value or "sk-ant-oat" in auth_value:
        return None
    return ResolvedConnection(kind=cred.kind, auth_value=auth_value,
                              base_url=p.base_url or None, headers=dict(p.headers or {}))


def _token(provider_id: str, *, keys_only: bool) -> Optional[str]:
    from .credential_provider import get_credential_provider
    from .selection import choose, usable
    from .store import canonical_provider, get_store
    provider_id = canonical_provider(provider_id)
    pool = get_store().get(provider_id)
    if not pool.credentials:
        return None
    try:
        first = choose(provider_id, record=False)
    except (AuthConfigError, AuthError):
        return None
    order = [first] + [c for c in pool.credentials if c.id != first.id and usable(c)]
    for cred in order:
        if keys_only and cred.kind not in ("api_key", "credential_process"):
            continue
        try:
            conn = resolve_connection(get_credential_provider().prepare(cred))
        except AuthCredentialProcessError:
            raise
        except (AuthConfigError, AuthError):
            continue
        if conn is not None:
            return conn.auth_value
    return None


def has_key(provider_id: str) -> bool:
    """Whether ``provider_id`` has a key-shaped credential (an API key, an
    environment variable, a helper command) in its list, whatever its state
    now. For a check at setup; the key a request sends is chosen then."""
    from .store import canonical_provider, get_store
    return any(c.kind in ("api_key", "credential_process")
               for c in get_store().get(canonical_provider(provider_id)).credentials)


def resolve_api_key_sync(provider_id: str) -> Optional[str]:
    """The bearer value of the credential a request of ``provider_id`` would
    use now (any kind), or the cloud-credential sentinel, or ``None``."""
    token = _token(provider_id, keys_only=False)
    if token:
        return token
    from openprogram.models.auth.env_api_keys import resolve_provider_key
    return resolve_provider_key(provider_id) or None


def resolve_store_api_key_sync(provider_id: str) -> Optional[str]:
    """The key of the first usable key-shaped credential (an API key, an
    environment variable, a helper command), or ``None``. Sign-in tokens are
    left out: callers put this value in an ``x-api-key`` style header."""
    return _token(provider_id, keys_only=True)


def _read_delegated_token(payload) -> Optional[str]:
    """The access token in another tool's login file
    (``data["store_path"]``, at ``data["access_key_path"]``), or ``None``."""
    import json
    from pathlib import Path

    store_path = payload.data.get("store_path")
    if not store_path:
        return None
    try:
        data = json.loads(Path(store_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    node: object = data
    for key in payload.data.get("access_key_path", []):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) and node else None


__all__ = ["ResolvedConnection", "has_key", "resolve_api_key_sync", "resolve_connection",
           "resolve_store_api_key_sync"]
