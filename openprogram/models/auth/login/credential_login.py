"""Building and storing a credential once a login method has produced it.

The login driver (web, TUI and ``openprogram providers keys <p> add``) calls
these: an API key becomes a credential, and an existing vendor-CLI login is
imported.
"""
from __future__ import annotations

from ..types import AuthConfigError, Credential, CredentialData


def api_key_credential(provider: str, api_key: str, *, source: str = "cli") -> Credential:
    """An ``api_key`` credential for ``provider``; refuses empty or unsupported keys."""
    from openprogram.models.auth.provider_policy import (
        UNSUPPORTED_PROVIDERS, API_KEY_REQUIRED, WITHDRAWN_PROVIDERS, require_api_key,
    )
    if provider in UNSUPPORTED_PROVIDERS:
        raise AuthConfigError(API_KEY_REQUIRED)
    if provider in WITHDRAWN_PROVIDERS:
        raise AuthConfigError(f"{provider} is not offered: use the vendor's own program as an outside agent")
    key = (api_key or "").strip()
    if not key:
        raise AuthConfigError("empty API key — nothing to save")
    require_api_key(key)
    return Credential(
        provider_id=provider,
        kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=key),
        source=source,
    )


def import_from_cli(provider: str) -> Credential:
    """Delegate to the per-provider adapter's ``import_from_*`` helper.

    The import is delegated read-only: the external CLI rotates it."""
    if provider == "qwen":
        from openprogram.models.auth.sources.qwen_cli import QwenCliSource
        from ..store import get_store
        creds = QwenCliSource().try_import(get_store().root)
        if not creds:
            raise AuthConfigError(
                "~/.qwen/oauth_creds.json not found. Run `qwen login` first."
            )
        return creds[0]
    raise AuthConfigError(f"no import-from-CLI adapter for {provider!r}")


__all__ = ["api_key_credential", "import_from_cli"]
