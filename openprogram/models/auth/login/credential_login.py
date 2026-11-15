"""Building and storing a credential once a login method has produced it.

The login driver (web, TUI and ``openprogram providers login``) calls these:
an API key becomes a credential, an existing vendor-CLI login is imported,
and a fresh OAuth login retires the pool's superseded OAuth siblings.
"""
from __future__ import annotations

from ..account.accounts import get_account_manager
from ..store import AuthStore
from ..types import AuthConfigError, Credential, CredentialData


def api_key_credential(provider: str, account: str, api_key: str) -> Credential:
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
        account_id=account,
        kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=key),
        source="cli_paste",
        metadata={},
    )


def import_from_cli(provider: str, account: str) -> Credential:
    """Delegate to the per-provider adapter's ``import_from_*`` helper.

    Adapters produce either writable OAuth (Codex — we rotate) or
    delegated read-only (Anthropic/Gemini/Qwen — external CLI rotates).
    The distinction is invisible here; we just hand off."""
    if provider == "openai-codex":
        from openprogram.models.providers.adapters.openai_codex import auth_adapter
        cred = auth_adapter.import_from_codex_file(account_id=account)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.codex_auth_path()} not found. "
                f"Run `codex login --device-auth` first, then re-run this command."
            )
        return cred
    if provider == "gemini-subscription":
        from openprogram.models.providers.adapters.google_gemini_cli import auth_adapter
        cred = auth_adapter.import_from_gemini_cli(account_id=account)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.gemini_cli_credentials_path()} not found. "
                f"Run `gemini auth login` first."
            )
        return cred
    if provider == "xai-subscription":
        from openprogram.models.providers.adapters.xai_subscription import auth_adapter
        cred = auth_adapter.import_from_grok_cli(account_id=account)
        if cred is None:
            raise AuthConfigError(
                f"{auth_adapter.grok_cli_auth_path()} not found. "
                f"Run `grok login` first, or Sign in on the Grok Subscription card."
            )
        return cred
    if provider == "qwen":
        from openprogram.models.auth.sources.qwen_cli import QwenCliSource
        src = QwenCliSource(account_id=account)
        creds = src.try_import(get_account_manager().get_account(account).root)
        if not creds:
            raise AuthConfigError(
                "~/.qwen/oauth_creds.json not found. Run `qwen login` first."
            )
        return creds[0]
    raise AuthConfigError(f"no import-from-CLI adapter for {provider!r}")


def prune_superseded_oauth(store: AuthStore, new_cred: Credential) -> list[str]:
    """Retire sibling OAuth credentials after a fresh interactive login.

    OAuth refresh-token rotation means at most one OAuth credential in a
    given pool can be valid at a time — once the user runs ``providers
    login`` and lands a fresh access+refresh pair, every older OAuth row
    in the same pool is dead bytes. Without pruning them, ``providers
    status`` (which iterates pool members in insertion order) keeps
    reporting the stale row as "expired and no refresh is configured",
    making the user think the relogin didn't take. Observed in practice
    against ``openai-codex`` after a successful PKCE flow.

    Only ``kind == "oauth"`` siblings are touched. Non-OAuth members
    (``api_key``, ``cli_delegated`` pointing at a sibling tool's auth
    file) are left alone — they're independent artifacts the user
    might still want.

    Returns the list of removed credential ids so the caller can mention
    them in the success message.
    """
    if new_cred.kind != "oauth":
        return []
    pool = store.find_pool(new_cred.provider_id, new_cred.account_id)
    if pool is None:
        return []
    removed: list[str] = []
    for sibling in list(pool.credentials):
        if sibling.credential_id == new_cred.credential_id:
            continue
        if sibling.kind != "oauth":
            continue
        store.remove_credential(
            sibling.provider_id, sibling.account_id, sibling.credential_id,
        )
        removed.append(sibling.credential_id)
    return removed


__all__ = ["api_key_credential", "import_from_cli", "prune_superseded_oauth"]
