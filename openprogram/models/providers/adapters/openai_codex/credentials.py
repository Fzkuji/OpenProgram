"""ChatGPT sign-ins of the Codex provider, as a request sends them.

A credential of kind ``import`` points at the Codex CLI's ``~/.codex/auth.json``
and is read afresh from that file on every use, refreshed there before its
token expires. An ``oauth`` credential adopts a newer login of the same
account found in that file. :func:`oauth_view` is the provider's ``view``
(``ProviderAuthConfig.view``): the credential layer applies it to the
credential it chose, so every request goes out with a valid token.

An imported login whose refresh token the token endpoint refuses is marked
``needs_login`` with that token's digest (:data:`SPENT_KEY`). While the file
still holds that token nothing is asked again; once it holds another login
(the person ran ``codex login``) the mark is cleared.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from openprogram.models.auth.credential_provider import CredentialProvider, get_provider_config
from openprogram.models.auth.types import AuthConfigError, AuthError, AuthNeedsReauthError, Credential

from . import auth_adapter

_log = logging.getLogger(__name__)

#: Credential metadata key: the digest of the refresh token that was refused.
SPENT_KEY = "spent_refresh_token"


def _imported(cred: Credential) -> bool:
    return cred.kind == "import"


def require_sign_in(manager: CredentialProvider | None = None) -> None:
    """Make sure the provider has a ChatGPT sign-in (importing the Codex
    CLI's ``auth.json`` once when nothing is stored), without choosing one:
    a login that is only cooling down or out of quota for now is still
    there, and choosing, waiting and saying why one cannot be used are the
    credential layer's at request time (``models/auth/selection.py``).
    Raises :class:`AuthConfigError` naming the command to run when there is
    no sign-in at all."""
    from openprogram.models.auth.credential_provider import get_credential_provider

    manager = manager or get_credential_provider()
    _import_when_empty(manager)
    kinds = {c.kind for c in manager.store.get(auth_adapter.PROVIDER_ID).credentials}
    if not kinds & {"oauth", "import"}:
        # The ChatGPT Responses backend needs a ChatGPT sign-in (its token
        # carries the account id); a bare OpenAI key belongs to ``openai``.
        raise AuthConfigError(
            "openai-codex has no ChatGPT sign-in. Run `openprogram providers keys openai-codex add`, "
            "or `codex login` (not the API-key option). With only an OpenAI key, use the "
            "`openai` provider.",
            provider_id=auth_adapter.PROVIDER_ID,
        )


def _import_when_empty(manager: CredentialProvider) -> None:
    if not manager.store.get(auth_adapter.PROVIDER_ID).credentials:
        imported = auth_adapter.import_from_codex_file()
        if imported is None:
            raise AuthConfigError(
                f"{auth_adapter.codex_auth_path()} not found or unusable. "
                "Run: codex login --device-auth",
                provider_id=auth_adapter.PROVIDER_ID,
            )
        manager.store.add_credential(imported)


def ensure_credential(manager: CredentialProvider | None = None) -> Credential:
    """The login a request would use first, ready to send. With none stored,
    the Codex CLI's ``auth.json`` is imported once; with no usable file
    either, raises :class:`AuthConfigError` naming the command to run."""
    from openprogram.models.auth.credential_provider import get_credential_provider
    from openprogram.models.auth.selection import choose

    manager = manager or get_credential_provider()
    _import_when_empty(manager)
    revive_delegated(manager)
    return manager.prepare(choose(auth_adapter.PROVIDER_ID, record=False))


def oauth_view(cred: Credential, manager: CredentialProvider | None = None) -> Credential:
    """The credential as this provider sends it, valid before it is sent.

    An imported login is read from the Codex CLI's file on every call, and
    refreshed in place (under the file's refresh lock, as the Codex CLI
    does) when its token's own ``exp`` is near. An ``oauth`` credential of
    the same ChatGPT account as the file adopts a newer login found there and
    saves it, so its next request does not carry a token whose refresh token
    another holder already spent."""
    if cred.kind == "oauth" and not cred.read_only:
        adopted = auth_adapter.adopt_newer_shared_login(cred)
        if adopted is None:
            return cred
        _save(manager, adopted, replacing=cred.payload.auth_value)
        return adopted
    if not _imported(cred):
        return cred
    path = _delegated_path(cred)

    def view() -> Credential:
        viewed = auth_adapter.import_from_codex_file(auth_path=path)
        if viewed is None:
            raise AuthConfigError(
                f"{path or auth_adapter.codex_auth_path()} holds no ChatGPT "
                "OAuth login. Run: codex login --device-auth",
                provider_id=auth_adapter.PROVIDER_ID,
            )
        # The stored row (its id, name, state) with the file's tokens.
        import dataclasses
        return dataclasses.replace(cred, payload=dataclasses.replace(
            cred.payload, auth_value=viewed.payload.auth_value,
            data={**cred.payload.data, **viewed.payload.data}))

    check_delegated(cred, manager)
    viewed = view()
    skew = get_provider_config(auth_adapter.PROVIDER_ID).refresh_skew_seconds
    if auth_adapter.token_expiring(viewed.payload.auth_value, skew):
        refresh_delegated(cred, viewed.payload.auth_value, manager)
        viewed = view()
    return viewed


# ---------------------------------------------------------------------------
# An imported login that can no longer be refreshed
# ---------------------------------------------------------------------------

def _delegated_path(cred: Credential) -> Optional[Path]:
    store_path = (cred.payload.data or {}).get("store_path")
    return Path(store_path) if store_path else None


def _spent(cred: Credential) -> str:
    return str((cred.metadata or {}).get(SPENT_KEY) or "")


def _store(manager: CredentialProvider | None):
    from openprogram.models.auth.credential_provider import get_credential_provider
    return (manager or get_credential_provider()).store


def _set_sign_in(manager: CredentialProvider | None, cred: Credential, *, spent: str,
                 detail: str = "") -> None:
    """Mark the stored imported login as needing sign-in (``spent`` is the
    refused refresh token's digest), or clear that mark (``""``)."""
    from openprogram.models.auth.selection import clear, mark

    def _note(row: Credential) -> None:
        if spent:
            row.metadata[SPENT_KEY] = spent
        else:
            row.metadata.pop(SPENT_KEY, None)

    try:
        _store(manager).update(cred.provider_id, cred.id, _note)
        if spent:
            mark(cred.provider_id, cred.id, "needs_login", message=detail[:200])
        else:
            clear(cred.provider_id, cred.id)
    except Exception:
        # The caller is about to raise or return the login's real state;
        # a store that cannot be written must not replace it.
        _log.warning("sign-in state of %s/%s not saved", cred.provider_id, cred.id, exc_info=True)
    if spent:
        cred.metadata[SPENT_KEY] = spent
    else:
        cred.metadata.pop(SPENT_KEY, None)


def check_delegated(cred: Credential, manager: CredentialProvider | None = None) -> None:
    """An imported login marked as needing sign-in: raise while its file
    still holds the refused refresh token (asking again is refused again),
    and clear the mark once the file holds another login. Reads the file
    only for a marked credential; never a request."""
    spent = _spent(cred)
    if not spent:
        return
    path = _delegated_path(cred)
    shared = auth_adapter.read_shared_login(path)
    if shared is None:
        return  # the file's own state ("holds no login") is the caller's error
    if auth_adapter.refresh_token_digest(shared["refresh_token"]) == spent:
        raise AuthNeedsReauthError(
            f"The ChatGPT sign-in in {path or auth_adapter.codex_auth_path()} can no longer "
            "be refreshed: login expired. Sign in again, or run: codex login",
            provider_id=auth_adapter.PROVIDER_ID,
        )
    _set_sign_in(manager, cred, spent="")
    _log.info("openai-codex: the Codex CLI's file holds a new sign-in; using it again")


def refresh_delegated(cred: Credential, rejected: str, manager: CredentialProvider | None = None) -> str:
    """Refresh the login an imported credential points at and return its
    access token. A refused refresh token marks the credential as needing
    sign-in and raises :class:`AuthNeedsReauthError`; it is not asked again
    until the file holds another login."""
    check_delegated(cred, manager)
    try:
        return auth_adapter.refresh_shared_login(_delegated_path(cred), rejected)
    except auth_adapter.SharedLoginSpent as exc:
        _set_sign_in(manager, cred, spent=exc.spent,
                     detail=f"the refresh token was refused ({exc.__cause__})")
        raise


def revive_delegated(manager: CredentialProvider | None = None) -> None:
    """Take back the imported logins that were signed in again since they
    were marked; reads a file only for a marked credential."""
    for cred in list(_store(manager).get(auth_adapter.PROVIDER_ID).credentials):
        if not _imported(cred) or not _spent(cred):
            continue
        try:
            check_delegated(cred, manager)
        except AuthNeedsReauthError:
            continue


# ---------------------------------------------------------------------------
# The Providers page: read the login again, and the plan it names
# ---------------------------------------------------------------------------

def reread_login(manager: CredentialProvider | None = None) -> None:
    """Read every stored Codex login again, where it lives.

    An imported login is read from the Codex CLI's file: one signed in again
    is taken back, one about to expire is refreshed there, one whose refresh
    token is refused is marked as needing sign-in. A stored login adopts a
    newer one of its account from that file, else is refreshed when about to
    expire. Never raises: each login's outcome is its stored status.
    """
    from openprogram.models.auth.credential_provider import get_credential_provider

    manager = manager or get_credential_provider()
    for cred in list(manager.store.get(auth_adapter.PROVIDER_ID).credentials):
        try:
            if _imported(cred):
                oauth_view(cred, manager)
            elif cred.kind == "oauth" and not cred.read_only:
                viewed = oauth_view(cred, manager)
                if viewed.status != "needs_login":
                    manager.refresh_if_stale_sync(viewed)
        except AuthError as exc:
            _log.info("openai-codex login %s: %s", cred.id, exc)
        except Exception as exc:
            # A network failure says nothing about the login; it keeps its status.
            _log.warning("openai-codex login %s was not re-read: %s: %s",
                         cred.id, type(exc).__name__, exc)


def plan_of_credential(cred: Credential) -> Optional[str]:
    """The ChatGPT plan a login's tokens name, or ``None``; no request."""
    if _imported(cred):
        shared = auth_adapter.read_shared_login(_delegated_path(cred))
        return auth_adapter.plan_of(shared["id_token"], shared["access_token"]) if shared else None
    if cred.kind == "oauth":
        return auth_adapter.plan_of(str(cred.payload.data.get("id_token") or ""),
                                    cred.payload.auth_value or "")
    return None


def login_plan(manager: CredentialProvider | None = None) -> Optional[str]:
    """The ChatGPT plan of the login a request would use first, or ``None``."""
    try:
        creds = _store(manager).get(auth_adapter.PROVIDER_ID).credentials
    except Exception:
        return None
    ordered = sorted(creds, key=lambda c: c.status != "ready")
    for cred in ordered:
        plan = plan_of_credential(cred)
        if plan:
            return plan
    return None


def _save(manager: CredentialProvider | None, adopted: Credential, *, replacing: str) -> None:
    """Persist an adopted login over the stored copy it replaces, only while
    the store still holds the token it was adopted over: a refresh that
    landed in between saved a newer login an older adoption must not overwrite."""

    def _adopt(row: Credential) -> None:
        if row.payload.auth_value == replacing:
            row.payload = adopted.payload
            row.status = "ready"
            row.last_error = None

    _store(manager).update(adopted.provider_id, adopted.id, _adopt)


def account_id_for(cred: Credential) -> str:
    """The chatgpt_account_id of a login: from its identity, else its JWT."""
    for value in (cred.identity.get("account_id"), cred.metadata.get("account_id")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return auth_adapter.extract_account_id(cred.payload.auth_value)
