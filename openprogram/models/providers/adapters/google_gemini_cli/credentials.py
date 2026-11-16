"""Gemini CLI (Google account) credential acquisition.

CredentialProvider-first, importing the Gemini CLI's
``~/.gemini/oauth_creds.json`` once on a miss.
"""
from __future__ import annotations

from openprogram.models.auth.credential_provider import CredentialProvider
from openprogram.models.auth.types import AuthConfigError, Credential

from . import auth_adapter


def require_sign_in(manager: CredentialProvider) -> None:
    """Make sure the provider has a Google sign-in (importing the Gemini
    CLI's OAuth file once when nothing is stored), without choosing one: a
    login held for a rate limit or a spent quota is still there, and the
    credential layer waits for it or says why at request time."""
    _import_when_empty(manager)
    kinds = {c.kind for c in manager.store.get(auth_adapter.PROVIDER_ID).credentials}
    if not kinds & {"oauth", "import"}:
        raise AuthConfigError(
            "gemini-subscription has no Google sign-in (Cloud Code Assist backend). Run "
            "`gemini auth login` to populate ~/.gemini/oauth_creds.json, then retry.",
            provider_id=auth_adapter.PROVIDER_ID,
        )


def ensure_credential(manager: CredentialProvider) -> Credential:
    """The Gemini CLI login a request would use, importing the CLI's OAuth
    file once when none is stored."""
    from openprogram.models.auth.selection import choose

    _import_when_empty(manager)
    return manager.prepare(choose(auth_adapter.PROVIDER_ID, record=False))


def _import_when_empty(manager: CredentialProvider) -> None:
    if not manager.store.get(auth_adapter.PROVIDER_ID).credentials:
        imported = auth_adapter.import_from_gemini_cli()
        if imported is None:
            raise AuthConfigError(
                f"{auth_adapter.gemini_cli_credentials_path()} not found or unusable. "
                "Run: gemini auth login",
                provider_id=auth_adapter.PROVIDER_ID,
            )
        manager.store.add_credential(imported)


def access_token_for(cred: Credential) -> str:
    """Pull the current access token out of whatever payload shape was returned.

    The credential can be either its own ``oauth`` kind (after
    CredentialProvider refreshed it) or an imported login (payload ``import``) that still
    points at the on-disk JSON. Both carry an access token; the store
    path just differs.
    """
    payload = cred.payload
    if payload.kind == "oauth":
        return payload.auth_value
    if payload.kind == "import":
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
    )
