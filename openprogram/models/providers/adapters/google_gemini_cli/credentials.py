"""Gemini CLI (Google account) credential acquisition.

CredentialProvider-first, importing the Gemini CLI's
``~/.gemini/oauth_creds.json`` once on a miss.
"""
from __future__ import annotations

from openprogram.models.auth.credential_provider import CredentialProvider
from openprogram.models.auth.types import (
    AuthConfigError,
    Credential,
    CredentialPool,
)

from . import auth_adapter


def ensure_credential(manager: CredentialProvider, account_id: str) -> Credential:
    """Resolve a Gemini CLI credential, importing the CLI's OAuth file on miss."""
    try:
        return manager.acquire_sync(auth_adapter.PROVIDER_ID, account_id)
    except AuthConfigError:
        pass

    imported = auth_adapter.import_from_gemini_cli(account_id=account_id)
    if imported is None:
        raise AuthConfigError(
            f"{auth_adapter.gemini_cli_credentials_path()} not found or unusable. "
            "Run: gemini auth login",
            provider_id=auth_adapter.PROVIDER_ID,
            account_id=account_id,
        )
    manager.store.put_pool(CredentialPool(
        provider_id=auth_adapter.PROVIDER_ID,
        account_id=account_id,
        credentials=[imported],
    ))
    return manager.acquire_sync(auth_adapter.PROVIDER_ID, account_id)


def access_token_for(cred: Credential) -> str:
    """Pull the current access token out of whatever payload shape was returned.

    The credential can be either its own ``oauth`` kind (after
    CredentialProvider refreshed it) or a ``cli_delegated`` kind that still
    points at the on-disk JSON. Both carry an access token; the store
    path just differs.
    """
    payload = cred.payload
    if payload.kind == "oauth":
        return payload.auth_value
    if payload.kind == "cli_delegated":
        import json
        from pathlib import Path
        store_path = payload.data.get("store_path")
        data = json.loads(Path(store_path).read_text(encoding="utf-8"))
        for key in payload.data.get("access_key_path", []):
            data = data[key]
        return str(data)
    raise AuthConfigError(
        f"gemini-subscription credential payload kind {payload.kind!r} "
        "has no access token path this runtime understands.",
        provider_id=auth_adapter.PROVIDER_ID,
        account_id=cred.account_id,
    )
