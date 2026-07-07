"""OpenProgram auth v2 — credential management.

Public surface, layered from inside out:

  * :mod:`.types`   — plain dataclasses + errors + events, zero deps
  * :mod:`.store`   — on-disk persistence, singleton, per-pool locks
  * :mod:`.manager` — refresh, pool rotation, fallback chains (v2 task 106)
  * :mod:`.methods` — interactive login flows (v2 task 107)
  * :mod:`.sources` — external credential importers (v2 task 108)
  * :mod:`.profiles` — isolation boundary (v2 task 109)

Call sites should reach for ``manager.acquire`` for API usage and
``manager.login`` for interactive enrollment. The lower layers are
intentionally minimal so they can be exercised in tests without
mocking the network.
"""
from .types import (
    AuthBillingBlockedError, AuthConfigError,
    AuthCorruptCredentialError, AuthError, AuthEvent, AuthEventListener,
    AuthEventType, AuthExpiredError, AuthNeedsReauthError,
    AuthPoolExhaustedError, AuthRateLimitedError, AuthReadOnlyError,
    AuthRefreshError, AuthRevokedError, AuthRotationConsumedError,
    Credential, CredentialData, CredentialKind, CredentialPayload,
    CredentialPool, CredentialSource, CredentialStatus,
    LoginMethod, LoginUi,
    PoolStrategy, Profile, RemovalStep,
)
from .store import AuthStore, get_store, set_store_for_testing

__all__ = [
    # types
    "CredentialData",
    "CredentialPayload", "Credential", "CredentialKind", "CredentialStatus",
    "PoolStrategy", "CredentialPool", "Profile",
    "AuthEventType", "AuthEvent", "AuthEventListener",
    "AuthError", "AuthConfigError", "AuthCorruptCredentialError",
    "AuthReadOnlyError", "AuthRefreshError", "AuthRotationConsumedError",
    "AuthExpiredError", "AuthRateLimitedError", "AuthBillingBlockedError",
    "AuthRevokedError", "AuthNeedsReauthError", "AuthPoolExhaustedError",
    "RemovalStep", "CredentialSource", "LoginMethod", "LoginUi",
    # store
    "AuthStore", "get_store", "set_store_for_testing",
]
