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
        UNSUPPORTED_PROVIDERS, API_KEY_REQUIRED, require_api_key,
    )
    if provider in UNSUPPORTED_PROVIDERS:
        raise AuthConfigError(API_KEY_REQUIRED)
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

    Adapters produce either writable OAuth (Codex — we rotate) or
    delegated read-only (Anthropic/Gemini/Qwen — external CLI rotates).
    The distinction is invisible here; we just hand off."""
    if provider == "openai-codex":
        from openprogram.models.providers.adapters.openai_codex import auth_adapter
        cred = auth_adapter.import_from_codex_file()
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.codex_auth_path()} not found. "
                f"Run `codex login --device-auth` first, then re-run this command."
            )
        return cred
    if provider == "gemini-subscription":
        from openprogram.models.providers.adapters.google_gemini_cli import auth_adapter
        cred = auth_adapter.import_from_gemini_cli()
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.gemini_cli_credentials_path()} not found. "
                f"Run `gemini auth login` first."
            )
        return cred
    if provider == "xai-subscription":
        from openprogram.models.providers.adapters.xai_subscription import auth_adapter
        cred = auth_adapter.import_from_grok_cli()
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.grok_cli_auth_path()} not found. "
                f"Run `grok login` first, or Sign in on the Grok Subscription card."
            )
        return cred
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
