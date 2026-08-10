"""OpenProgram auth v2 — credential management.

Public surface, layered from inside out:

  * :mod:`.types`    — plain dataclasses + errors + events, zero deps
  * :mod:`.store`    — on-disk persistence, singleton, per-pool locks
  * :mod:`.credential_provider` — refresh, pool rotation, fallback chains
  * :mod:`.resolver` — flattens a credential to the bearer string a request sends
  * :mod:`.methods`  — interactive login flows
  * :mod:`.sources`  — external credential importers
  * :mod:`.accounts` — isolation boundary

Call sites should reach for ``credential_provider.acquire`` for API usage
and the ``methods`` login flows for interactive enrollment. The lower
layers are intentionally minimal so they can be exercised in tests
without mocking the network.

Two things named "account" and "profile" are kept apart deliberately:
an **account** here is a set of credentials for one provider identity
(``account_id``, ``~/.openprogram/profiles/<name>/``), while a
**profile** is the workspace scope in :mod:`openprogram.paths`
(``--profile``, ``~/.openprogram-<name>/``) covering config and
sessions. One workspace profile can hold many credential accounts.
"""
from .types import (
    AuthBillingBlockedError, AuthConfigError,
    AuthCorruptCredentialError, AuthError, AuthEvent, AuthEventListener,
    AuthEventType, AuthExpiredError, AuthCredentialProcessError,
    AuthNeedsReauthError,
    AuthPoolExhaustedError, AuthRateLimitedError, AuthReadOnlyError,
    AuthRefreshError, AuthRevokedError, AuthRotationConsumedError,
    Credential, CredentialData, CredentialKind, CredentialPayload,
    CredentialPool, CredentialSource, CredentialStatus,
    LoginMethod, LoginUi,
    PoolStrategy, Account, RemovalStep,
)
from .store import AuthStore, get_store, set_store_for_testing

__all__ = [
    # types
    "CredentialData",
    "CredentialPayload", "Credential", "CredentialKind", "CredentialStatus",
    "PoolStrategy", "CredentialPool", "Account",
    "AuthEventType", "AuthEvent", "AuthEventListener",
    "AuthError", "AuthConfigError", "AuthCorruptCredentialError",
    "AuthReadOnlyError", "AuthRefreshError", "AuthRotationConsumedError",
    "AuthExpiredError", "AuthRateLimitedError", "AuthBillingBlockedError",
    "AuthRevokedError", "AuthNeedsReauthError", "AuthPoolExhaustedError",
    "AuthCredentialProcessError",
    "RemovalStep", "CredentialSource", "LoginMethod", "LoginUi",
    # store
    "AuthStore", "get_store", "set_store_for_testing",
]
