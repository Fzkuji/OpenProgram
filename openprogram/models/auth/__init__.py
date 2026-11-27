"""OpenProgram credentials.

  * :mod:`.types`    — dataclasses, errors, events; no dependencies
  * :mod:`.store`    — one ``credentials.json`` per provider, atomic writes
  * :mod:`.carry_over` — the one-time move from the per-account layout
  * :mod:`.selection` — which credential a request uses, and what a failure marks on it
  * :mod:`.credential_provider` — refresh of sign-ins, per-provider hooks
  * :mod:`.resolver` — a credential to the bearer value a request sends
  * :mod:`.login`, :mod:`.methods`, :mod:`.sources` — adding credentials
"""
from .types import (
    AuthConfigError, AuthCorruptCredentialError, AuthCredentialProcessError,
    AuthError, AuthEvent, AuthEventListener, AuthEventType, AuthNeedsReauthError,
    AuthPoolExhaustedError, AuthReadOnlyError, AuthRefreshError,
    AuthRotationConsumedError, Credential, CredentialData, CredentialKind,
    CredentialPayload, CredentialPool, CredentialSource, CredentialStatus,
    LoginMethod, LoginUi, RemovalStep, STRATEGIES, Strategy,
)
from .store import AuthStore, get_store, set_store_for_testing

__all__ = [
    "CredentialData", "CredentialPayload", "Credential", "CredentialKind",
    "CredentialStatus", "CredentialPool", "Strategy", "STRATEGIES",
    "AuthEventType", "AuthEvent", "AuthEventListener",
    "AuthError", "AuthConfigError", "AuthCorruptCredentialError",
    "AuthReadOnlyError", "AuthRefreshError", "AuthRotationConsumedError",
    "AuthNeedsReauthError", "AuthPoolExhaustedError", "AuthCredentialProcessError",
    "RemovalStep", "CredentialSource", "LoginMethod", "LoginUi",
    "AuthStore", "get_store", "set_store_for_testing",
]
