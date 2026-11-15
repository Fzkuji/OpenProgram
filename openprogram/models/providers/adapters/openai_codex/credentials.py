"""ChatGPT subscription credential acquisition for the Codex provider.

CredentialProvider-first: the store is consulted, and on a miss the Codex
CLI's ``~/.codex/auth.json`` is imported once. A ``cli_delegated``
credential is read afresh from that file on every call and refreshed there
before its token expires; an ``oauth`` credential adopts a newer login of
the same account found in that file.

A delegated login whose refresh token the token endpoint refuses is marked
``needs_reauth`` with that token's digest (:data:`SPENT_KEY`). While the
file still holds that token nothing is asked again; once it holds another
login (the person ran ``codex login``) the mark is cleared.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from openprogram.models.auth.credential_provider import CredentialProvider, get_provider_config
from openprogram.models.auth.types import (
    AuthConfigError,
    AuthError,
    AuthNeedsReauthError,
    AuthPoolExhaustedError,
    Credential,
    CredentialPool,
)

from . import auth_adapter

_log = logging.getLogger(__name__)

#: Credential metadata key: the digest of the refresh token that was refused.
SPENT_KEY = "spent_refresh_token"


# ---------------------------------------------------------------------------
# Credential acquisition — CredentialProvider-first, with one-shot import fallback
# ---------------------------------------------------------------------------

def ensure_credential(manager: CredentialProvider, account_id: str) -> Credential:
    """Resolve a Codex credential for the given profile.

    Try the store first. If no pool exists, attempt a one-shot import
    from the Codex CLI's ``auth.json``; register the imported credential
    and retry acquisition (which may also refresh if the JWT is close to
    expiry). If neither path yields a credential, raise — the caller
    should render a "please run `codex login --device-auth`" message.
    """
    revive_delegated(manager, account_id)
    try:
        return manager.acquire_sync(auth_adapter.PROVIDER_ID, account_id)
    except AuthConfigError:
        pass  # fall through to import path

    imported = auth_adapter.import_from_codex_file(account_id=account_id)
    if imported is None:
        raise AuthConfigError(
            f"{auth_adapter.codex_auth_path()} not found or unusable. "
            "Run: codex login --device-auth",
            provider_id=auth_adapter.PROVIDER_ID,
            account_id=account_id,
        )
    manager.store.put_pool(CredentialPool(
        provider_id=auth_adapter.PROVIDER_ID,
        account_id=account_id,
        credentials=[imported],
    ))
    return manager.acquire_sync(auth_adapter.PROVIDER_ID, account_id)


def oauth_view(cred: Credential, manager: CredentialProvider | None = None) -> Credential:
    """The OAuth credential this runtime calls with, valid before it is sent.

    A ``cli_delegated`` credential points at the Codex CLI's ``auth.json``,
    which holds the ChatGPT OAuth tokens; it is read afresh on every call,
    and when the token's own ``exp`` is near, the file's login is refreshed
    in place (under the file's refresh lock, as the Codex CLI itself does)
    before the request goes out. A development instance gets only this
    delegated credential, since OAuth logins are never copied between
    instances.

    An ``oauth`` credential of the same ChatGPT account as the file adopts a
    newer login found there (written by the Codex CLI, or by an instance
    that refreshed through the file), and saves it, so its next request does
    not carry a token whose refresh token another holder already spent."""
    if cred.kind == "oauth" and not cred.read_only:
        adopted = auth_adapter.adopt_newer_shared_login(cred)
        if adopted is None:
            return cred
        _save(manager, adopted, replacing=cred.payload.auth_value)
        return adopted
    if cred.kind != "cli_delegated":
        return cred
    store_path = (cred.payload.data or {}).get("store_path")
    path = Path(store_path) if store_path else None

    def view() -> Credential:
        viewed = auth_adapter.import_from_codex_file(account_id=cred.account_id, auth_path=path)
        if viewed is None:
            raise AuthConfigError(
                f"{store_path or auth_adapter.codex_auth_path()} holds no ChatGPT "
                "OAuth login. Run: codex login --device-auth",
                provider_id=auth_adapter.PROVIDER_ID,
                account_id=cred.account_id,
            )
        return viewed

    check_delegated(cred, manager)
    viewed = view()
    skew = get_provider_config(auth_adapter.PROVIDER_ID).refresh_skew_seconds
    if auth_adapter.token_expiring(viewed.payload.auth_value, skew):
        refresh_delegated(cred, viewed.payload.auth_value, manager)
        viewed = view()
    return viewed


def acquire(manager: CredentialProvider, account_id: Optional[str] = None) -> Credential:
    """The OAuth credential the next request is sent with: a delegated
    login that was signed in again is taken back first, then the pool
    picks, then :func:`oauth_view` makes the pick valid."""
    revive_delegated(manager, account_id)
    try:
        picked = manager.acquire_sync(auth_adapter.PROVIDER_ID, account_id)
    except AuthPoolExhaustedError:
        _raise_if_signed_out(manager, account_id)
        raise
    return oauth_view(picked, manager)


def _raise_if_signed_out(manager: CredentialProvider, account_id: Optional[str]) -> None:
    """The pool has nothing to pick. When that is because a login needs
    signing in again, say so instead of "no credential"."""
    try:
        pools = [p for p in manager.store.list_pools()
                 if p.provider_id == auth_adapter.PROVIDER_ID
                 and (account_id is None or p.account_id == account_id)]
    except Exception:
        return
    for pool in pools:
        for cred in pool.credentials:
            if cred.status != "needs_reauth":
                continue
            check_delegated(cred, manager)
            raise AuthNeedsReauthError(
                "The ChatGPT sign-in can no longer be refreshed: login expired. Sign in again.",
                provider_id=auth_adapter.PROVIDER_ID,
                account_id=cred.account_id,
            )


# ---------------------------------------------------------------------------
# A delegated login that can no longer be refreshed
# ---------------------------------------------------------------------------

def _delegated_path(cred: Credential) -> Optional[Path]:
    store_path = (cred.payload.data or {}).get("store_path")
    return Path(store_path) if store_path else None


def _spent(cred: Credential) -> str:
    return str((cred.metadata or {}).get(SPENT_KEY) or "")


def _set_sign_in(
    manager: CredentialProvider | None, cred: Credential, *, spent: str, detail: str = "",
) -> None:
    """Mark the stored delegated credential as needing sign-in (``spent``
    is the refused refresh token's digest), or clear that mark (``""``)."""
    from openprogram.models.auth import pool as _pool
    from openprogram.models.auth.credential_provider import get_credential_provider

    store = (manager or get_credential_provider()).store
    pool = store.find_pool(cred.provider_id, cred.account_id)
    stored = next(
        (c for c in (pool.credentials if pool else []) if c.credential_id == cred.credential_id),
        None,
    )
    event = None
    for target in ([stored] if stored is cred else [stored, cred]):
        if target is None:
            continue
        if spent:
            marked = _pool.mark_failure(target, "needs_reauth", detail=detail[:200])
            event = event or marked
            target.metadata[SPENT_KEY] = spent
        else:
            target.metadata.pop(SPENT_KEY, None)
            target.status = "valid"
            target.last_error = None
            target.updated_at_ms = int(time.time() * 1000)
    if pool is None or stored is None:
        return
    try:
        store.put_pool(pool)
        if event is not None:
            store._emit(event)  # type: ignore[attr-defined]
    except Exception:
        # The caller is about to raise or return the login's real state;
        # a store that cannot be written must not replace it.
        _log.warning("sign-in state of %s/%s not saved", cred.provider_id, cred.account_id,
                     exc_info=True)


def check_delegated(cred: Credential, manager: CredentialProvider | None = None) -> None:
    """A delegated login marked as needing sign-in: raise while its file
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
            account_id=cred.account_id,
        )
    _set_sign_in(manager, cred, spent="")
    _log.info("openai-codex: the Codex CLI's file holds a new sign-in; using it again")


def refresh_delegated(
    cred: Credential, rejected: str, manager: CredentialProvider | None = None,
) -> str:
    """Refresh the login a delegated credential points at and return its
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


def revive_delegated(manager: CredentialProvider, account_id: Optional[str] = None) -> None:
    """Take back the delegated logins that were signed in again since they
    were marked. The pool skips a marked credential, so this runs before it
    picks; it reads a file only for a marked credential."""
    try:
        pools = [p for p in manager.store.list_pools()
                 if p.provider_id == auth_adapter.PROVIDER_ID
                 and (account_id is None or p.account_id == account_id)]
    except Exception:
        return
    for pool in pools:
        for cred in pool.credentials:
            if cred.kind != "cli_delegated" or not _spent(cred):
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

    A delegated login is read from the Codex CLI's file: one signed in
    again is taken back, one about to expire is refreshed there, one whose
    refresh token is refused is marked as needing sign-in. A stored login
    adopts a newer one of its account from that file, else is refreshed
    when about to expire. A login already marked is not asked again.
    Never raises: each login's outcome is its stored status.
    """
    from openprogram.models.auth.credential_provider import get_credential_provider

    manager = manager or get_credential_provider()
    try:
        pools = [p for p in manager.store.list_pools() if p.provider_id == auth_adapter.PROVIDER_ID]
    except Exception:
        _log.warning("openai-codex logins could not be listed", exc_info=True)
        return
    for pool in pools:
        for cred in list(pool.credentials):
            try:
                if cred.kind == "cli_delegated":
                    oauth_view(cred, manager)
                elif cred.kind == "oauth" and not cred.read_only:
                    viewed = oauth_view(cred, manager)
                    if viewed.status != "needs_reauth":
                        manager.refresh_if_stale_sync(viewed)
            except AuthError as exc:
                _log.info("openai-codex login %s/%s: %s", cred.provider_id, cred.account_id, exc)
            except Exception as exc:
                # A network failure says nothing about the login; it keeps its status.
                _log.warning("openai-codex login %s/%s was not re-read: %s: %s",
                             cred.provider_id, cred.account_id, type(exc).__name__, exc)


def login_plan(manager: CredentialProvider | None = None) -> Optional[str]:
    """The ChatGPT plan the active login's tokens name, or ``None``. Reads
    the store and, for a delegated login, the Codex CLI's file; no request."""
    from openprogram.models.auth.account.account_selection import get_active_account
    from openprogram.models.auth.credential_provider import get_credential_provider

    manager = manager or get_credential_provider()
    try:
        pool = manager.store.find_pool(auth_adapter.PROVIDER_ID,
                                       get_active_account(auth_adapter.PROVIDER_ID))
    except Exception:
        return None
    if pool is None:
        return None
    ordered = sorted(pool.credentials,
                     key=lambda c: (c.credential_id != pool.active_credential_id,
                                    c.status == "needs_reauth"))
    for cred in ordered:
        if cred.kind == "cli_delegated":
            shared = auth_adapter.read_shared_login(_delegated_path(cred))
            plan = auth_adapter.plan_of(shared["id_token"], shared["access_token"]) if shared else None
        elif cred.kind == "oauth":
            plan = auth_adapter.plan_of(str(cred.payload.data.get("id_token") or ""),
                                        cred.payload.auth_value or "")
        else:
            plan = None
        if plan:
            return plan
    return None


def _save(manager: CredentialProvider | None, adopted: Credential, *, replacing: str) -> None:
    """Persist an adopted login over the stored copy it replaces.

    Only while the store still holds the token it was adopted over
    (``replacing``): a refresh that landed in between saved a newer login,
    which an older adoption must not overwrite."""
    from openprogram.models.auth.credential_provider import get_credential_provider

    store = (manager or get_credential_provider()).store
    pool = store.find_pool(adopted.provider_id, adopted.account_id)
    if pool is None:
        return
    for i, existing in enumerate(pool.credentials):
        if existing.credential_id == adopted.credential_id:
            if existing.payload.auth_value != replacing:
                return
            pool.credentials[i] = adopted
            store.put_pool(pool)
            return


def account_id_for(cred: Credential) -> str:
    """Return the chatgpt_account_id for the credential.

    Prefers metadata (cheap, survives refreshes since _codex_refresh
    preserves metadata). Falls back to decoding the JWT."""
    account_id = cred.metadata.get("account_id")
    if isinstance(account_id, str) and account_id.strip():
        return account_id.strip()
    payload = cred.payload
    assert payload.kind in ("oauth", "device_code")
    return auth_adapter.extract_account_id(payload.auth_value)
