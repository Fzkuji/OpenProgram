"""
Auth v2 — type definitions.

Design goals that shape every type here:

  - one process-wide source of truth (:class:`AuthStore`) holding one
    ordered credential list per provider; every runtime asks the store,
    never caches its own copy
  - API keys and sign-ins are rows of the same list, told apart by ``kind``
  - every state transition emits an :class:`AuthEvent` so telemetry,
    "your session expires in 5 min" UI, and audit logs can subscribe
    without the core knowing about them
  - errors are typed so the webui can distinguish "refresh hiccup, retry
    automatically" from "user really does need to re-auth" without
    string-matching traceback messages
  - every credential source declares :class:`RemovalStep` list so
    "forget this account" is actually complete — no surprise
    re-hydration from a forgotten env var or CLI store

Nothing here imports httpx, fastapi, or pi-ai. The type layer stays
framework-agnostic so tests can exercise the full state machine without
network.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Literal, Optional, Protocol

# Schema version of a provider's credential file. Every write stamps it and
# every read checks it; anything older is carried over once by
# :mod:`openprogram.models.auth.carry_over` before it is read.
CREDENTIAL_SCHEMA_VERSION = 4


# ---------------------------------------------------------------------------
# Credential — discriminated union over auth kinds
# ---------------------------------------------------------------------------

CredentialKind = Literal[
    "api_key",            # a static key, pasted or held by an environment variable
    "oauth",              # access + refresh token from a browser sign-in
    "device_code",        # same payload as oauth, obtained through a device code
    "import",             # another tool's login file, read where it lives
    "token",              # a pasted access token (a GitHub token for Copilot)
    "credential_process", # a helper command prints the key on each use
]


#: ready — usable; cooling — throttled until ``cooldown_until_ms``; quota —
#: its quota or balance is spent, until ``cooldown_until_ms``; invalid_key —
#: the provider refused the key; needs_login — the sign-in can no longer be
#: refreshed. The last two clear only when the key is replaced, the account
#: signed in again, or a check passes.
CredentialStatus = Literal["ready", "cooling", "quota", "invalid_key", "needs_login"]

#: in_order — the first usable one in the user's order; evenly — each in
#: turn; least_used — the one used least.
Strategy = Literal["in_order", "evenly", "least_used"]
STRATEGIES: tuple[str, ...] = ("in_order", "evenly", "least_used")

#: Where a credential was added: the settings page, the CLI, an import of
#: another tool's login, or an environment variable (never written to disk).
CredentialSourceName = Literal["web", "cli", "import", "env"]

#: Kinds that are signed in (and signed in again) rather than replaced.
LOGIN_KINDS = frozenset({"oauth", "device_code", "import", "token"})


@dataclass
class CredentialData:
    """One credential's connection info. Replaces the 6 payload classes.

    Common fields answer "what to send" uniformly; ``data`` holds whatever
    is specific to this ``kind`` (refresh tokens, external-file paths, ...).
    """
    kind: str
    auth_value: str = ""
    base_url: str = ""
    headers: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)


CredentialPayload = CredentialData  # kept as an alias so annotations still resolve


@dataclass
class Credential:
    """One key or sign-in of a provider, with what selection needs to know.

    A provider's credentials form one ordered list
    (:class:`CredentialPool`); the order is the order ``in_order`` tries
    them in. ``identity`` holds what a sign-in says about its account
    (``email``, ``plan``, ``org``, ``account_id``) and is how signing in
    again finds the row it renews. Secrets live only in ``payload`` and are
    never part of a listing.
    """

    provider_id: str
    kind: CredentialKind
    payload: CredentialData
    id: str = field(default_factory=lambda: _new_id("cred"))
    label: str = ""
    source: CredentialSourceName = "web"
    identity: dict = field(default_factory=dict)
    enabled: bool = True
    status: CredentialStatus = "ready"
    cooldown_until_ms: int = 0
    #: model id -> ms until which this credential is not used for that model.
    blocked_models: dict = field(default_factory=dict)
    #: ``{"code", "message", "at"}`` of the last failure, or ``None``.
    last_error: Optional[dict] = None
    last_used_at_ms: int = 0
    use_count: int = 0
    #: Requests since local midnight (``least_used`` picks by it). Like
    #: ``use_count`` it is read from the provider's ``usage.json``, never
    #: from ``credentials.json``.
    uses_today: int = field(default=0, compare=False)
    #: Consecutive throttles; doubles the next cooldown, reset by a success.
    strikes: int = 0
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    #: Held by another tool or an environment variable: never refreshed or rewritten here.
    read_only: bool = False
    #: Non-secret details a source keeps (the variable an env row reads, the
    #: file an import reads, the refresh token digest a refusal marked).
    metadata: dict = field(default_factory=dict)

    @property
    def key_tail(self) -> str:
        """The last four characters of a key, for telling keys apart."""
        if self.kind not in ("api_key", "token"):
            return ""
        value = self.payload.auth_value
        if self.source == "env":
            import os
            value = os.environ.get(self.metadata.get("env_var") or "", "")
        value = (value or "").strip()
        return value[-4:] if len(value) >= 8 else ""

    def display_label(self) -> str:
        """The name a person sees: the label, else the account's email, else
        ``$VAR`` for an environment key, ``Key ····tail`` for a key, and
        "<ChatGPT> sign-in" for a sign-in. The plan is not part of it; a
        listing shows it apart. (The web builds its own default name from
        the kind, in the reader's language.)"""
        if self.label:
            return self.label
        email = self.identity.get("email") or ""
        if email:
            return email
        if self.source == "env" and self.metadata.get("env_var"):
            return f"${self.metadata['env_var']}"
        if self.kind in ("api_key", "token"):
            tail = self.key_tail
            return f"Key ····{tail}" if tail else "Key"
        from openprogram.models.auth.login.login_method_registry import signin_name
        return f"{signin_name(self.provider_id)} sign-in"

    def to_dict(self) -> dict:
        payload = _payload_to_dict(self.payload)
        if self.source == "env":
            # An environment variable's key is read where it lives, never stored.
            payload["auth_value"] = ""
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "kind": self.kind,
            "label": self.label,
            "source": self.source,
            "identity": dict(self.identity),
            "enabled": self.enabled,
            "status": self.status,
            "cooldown_until_ms": self.cooldown_until_ms,
            "blocked_models": dict(self.blocked_models),
            "last_error": self.last_error,
            "last_used_at_ms": self.last_used_at_ms,
            "use_count": self.use_count,
            "strikes": self.strikes,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "read_only": self.read_only,
            "metadata": dict(self.metadata),
            "key_tail": self.key_tail if self.source != "env" else "",
            "payload": payload,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Credential":
        return cls(
            provider_id=d["provider_id"],
            kind=d["kind"],
            payload=_payload_from_dict(d["kind"], d.get("payload") or {}),
            # A row saved without an id gets one, the same on every read:
            # ``None`` would match every other id-less row when one is removed.
            id=str(d.get("id") or "") or _stable_id(d),
            label=d.get("label") or "",
            source=d.get("source") or "web",
            identity=dict(d.get("identity") or {}),
            enabled=bool(d.get("enabled", True)),
            status=d.get("status") or "ready",
            cooldown_until_ms=int(d.get("cooldown_until_ms") or 0),
            blocked_models={str(k): int(v) for k, v in (d.get("blocked_models") or {}).items()},
            last_error=d.get("last_error") if isinstance(d.get("last_error"), dict) else None,
            last_used_at_ms=int(d.get("last_used_at_ms") or 0),
            use_count=int(d.get("use_count") or 0),
            strikes=int(d.get("strikes") or 0),
            created_at_ms=int(d.get("created_at_ms") or 0),
            updated_at_ms=int(d.get("updated_at_ms") or 0),
            read_only=bool(d.get("read_only", False)),
            metadata=dict(d.get("metadata") or {}),
        )


def _payload_to_dict(p: CredentialData) -> dict:
    return {
        "kind": p.kind,
        "auth_value": p.auth_value,
        "base_url": p.base_url,
        "headers": dict(p.headers),
        "data": dict(p.data),
    }


def _payload_from_dict(kind: CredentialKind, d: dict) -> CredentialData:
    return CredentialData(
        kind=d.get("kind", kind),
        auth_value=d.get("auth_value", ""),
        base_url=d.get("base_url", ""),
        headers=dict(d.get("headers") or {}),
        data=dict(d.get("data") or {}),
    )


# ---------------------------------------------------------------------------
# A provider's credentials
# ---------------------------------------------------------------------------

@dataclass
class CredentialPool:
    """Every credential of one provider, in the order they are tried.

    Stored as ``<state>/auth/<provider>/credentials.json``. ``cursor`` is
    where ``evenly`` continues, kept in the file so a restart does not start
    the turn over.
    """

    provider_id: str
    strategy: Strategy = "in_order"
    credentials: list[Credential] = field(default_factory=list)
    cursor: int = 0

    def find(self, credential_id: str) -> Optional[Credential]:
        return next((c for c in self.credentials if c.id == credential_id), None)

    def to_dict(self) -> dict:
        return {
            "v": CREDENTIAL_SCHEMA_VERSION,
            "provider_id": self.provider_id,
            "strategy": self.strategy,
            "cursor": self.cursor,
            "credentials": [c.to_dict() for c in self.credentials],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CredentialPool":
        if d.get("v") != CREDENTIAL_SCHEMA_VERSION:
            raise AuthCorruptCredentialError(
                f"credential file schema v={d.get('v')!r} not understood",
                provider_id=str(d.get("provider_id") or ""),
            )
        strategy = d.get("strategy")
        rows: list[Credential] = []
        seen: set[str] = set()
        for index, raw in enumerate(d.get("credentials") or []):
            cred = Credential.from_dict(raw)
            if cred.id in seen:
                # Two rows under one id (a hand-copied file) stay two rows.
                cred.id = _stable_id(raw, salt=str(index))
            seen.add(cred.id)
            rows.append(cred)
        return cls(
            provider_id=d["provider_id"],
            strategy=strategy if strategy in STRATEGIES else "in_order",
            credentials=rows,
            cursor=int(d.get("cursor") or 0),
        )


# ---------------------------------------------------------------------------
# Events — everything state-changing goes through here
# ---------------------------------------------------------------------------

class AuthEventType(str, Enum):
    # Credential-lifecycle
    LOGIN_STARTED = "login_started"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    REFRESH_STARTED = "refresh_started"
    REFRESH_SUCCEEDED = "refresh_succeeded"
    REFRESH_FAILED = "refresh_failed"
    NEEDS_REAUTH = "needs_reauth"
    REVOKED = "revoked"
    IMPORTED_FROM_EXTERNAL = "imported_from_external"
    # Pool-lifecycle
    POOL_MEMBER_ADDED = "pool_member_added"
    POOL_MEMBER_REMOVED = "pool_member_removed"
    POOL_MEMBER_COOLDOWN = "pool_member_cooldown"
    POOL_ROTATED = "pool_rotated"
    POOL_EXHAUSTED = "pool_exhausted"
    # The list changed in any way a listing shows (order, strategy, a
    # credential's name, status, enabled flag): the web reloads it.
    CREDENTIALS_CHANGED = "credentials_changed"


@dataclass
class AuthEvent:
    type: AuthEventType
    provider_id: str = ""
    credential_id: str = ""
    detail: dict = field(default_factory=dict)
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))


AuthEventListener = Callable[[AuthEvent], None]


# ---------------------------------------------------------------------------
# Errors — the shape tree callers distinguish on
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Base for every auth-layer failure.

    Call sites catch :class:`AuthError` when they want "any auth problem";
    they catch subclasses when they want to differentiate "we'll retry for
    you" from "we can't, tell the user".
    """

    provider_id: str = ""

    def __init__(self, message: str, *, provider_id: str = "") -> None:
        super().__init__(message)
        self.provider_id = provider_id


class AuthConfigError(AuthError):
    """The provider has no credential, or it is set up wrong."""


class AuthCorruptCredentialError(AuthError):
    """On-disk credential file is corrupt / wrong schema / tampered."""


class AuthReadOnlyError(AuthError):
    """Tried to refresh / rotate / delete a read-only credential (one that
    lives in an external CLI store we don't own)."""


class AuthRefreshError(AuthError):
    """Transient refresh failure — network glitch, 500, timeout. Call the
    same endpoint again a moment later and it might work. The manager
    retries a few times internally before giving up and raising this."""


class AuthRotationConsumedError(AuthRefreshError):
    """Refresh token was already consumed by someone else (rotating
    refresh_token semantics). The manager reloads from disk and retries;
    if the on-disk copy is ALSO consumed, that's :class:`AuthNeedsReauthError`.
    """


class AuthNeedsReauthError(AuthError):
    """Refresh token itself is invalid. User must go through login again.
    Webui listens for this, surfaces a banner, doesn't retry."""


class AuthCredentialProcessError(AuthError):
    """A configured ``credential_process`` helper failed to produce a token.

    Deliberately NOT swallowed by the resolver's fall-through ladder: the
    user explicitly configured a helper command, so silently dropping to
    a different credential layer would hide a broken helper behind
    whatever key happened to be lying around. Surface it, name the
    command, let the user fix it.
    """


class AuthPoolExhaustedError(AuthError):
    """No credential of the provider can be used now."""


# ---------------------------------------------------------------------------
# Removal contract — explicit uninstall for every source
# ---------------------------------------------------------------------------

@dataclass
class RemovalStep:
    """One concrete cleanup action needed to fully forget a credential.

    Example: a credential imported from ``~/.codex/auth.json`` needs one
    step — a note that the user must ``codex logout`` to remove the
    underlying file (we don't touch external CLI stores). A credential
    we created via PKCE needs none beyond removing it from the provider's
    credential list. A credential that was also referenced by an env var
    hint has an informational step telling the user to unset that var.

    Steps with ``executable=True`` are run by the manager on removal;
    ``executable=False`` steps are surfaced to the user as instructions
    (we can't unset their shell env on their behalf). Without this
    contract, credentials would silently re-hydrate from forgotten sources
    — exactly the bug hermes-agent's ``credential_sources`` module-level
    docstring calls out.
    """

    description: str                    # "delete ~/.openprogram/auth/…/foo.json"
    executable: bool = True
    run: Optional[Callable[[], None]] = None
    # Free-form for the UI: "file", "env", "external_cli", "registry"
    kind: str = "file"
    target: str = ""                    # path / env var name / CLI name


# ---------------------------------------------------------------------------
# Source + login method protocols
# ---------------------------------------------------------------------------

class CredentialSource(Protocol):
    """Where credentials come from before the store knows about them.

    Implementations live in ``auth/sources/``:
      * ``env.py`` — ``OPENAI_API_KEY`` style env lookups
      * ``codex_cli.py`` — parses ``~/.codex/auth.json``
      * ``claude_code.py`` — parses Claude Code's oauth file
      * ``qwen_cli.py``, ``gh_cli.py`` — same pattern

    ``try_import`` may return several credentials (a file with several
    accounts); each becomes its own row of the provider's list.
    """

    source_id: str

    def try_import(self, state_root: Path) -> list[Credential]: ...

    def removal_steps(self, cred: Credential) -> list[RemovalStep]: ...


class LoginMethod(Protocol):
    """Interactive login flow. One per auth kind per provider.

    ``run`` is async because every real flow waits for a browser or the
    user typing — blocking the event loop would freeze the webui.
    """

    method_id: str                     # "pkce_oauth", "device_code", …
    provider_id: str

    async def run(self, ui: "LoginUi") -> Credential: ...


class LoginUi(Protocol):
    """Callbacks the login method uses to talk to the user.

    Abstraction matters because the same method runs under multiple UIs:
    the terminal wizard uses stdin/stdout, the webui uses a websocket
    dialog, CI pipelines wire it to whatever they have. The method code
    doesn't need to know which is which.
    """

    async def open_url(self, url: str) -> None: ...

    async def prompt(self, message: str, *, secret: bool = False) -> str: ...

    async def show_progress(self, message: str) -> None: ...

    async def show_code(self, user_code: str, verification_uri: str) -> None: ...


# ---------------------------------------------------------------------------
# Small internals
# ---------------------------------------------------------------------------

def _stable_id(d: dict, *, salt: str = "") -> str:
    """An id for a stored row that has none (or repeats another's): derived
    from the row, so it is the same on every read until the file is saved."""
    import hashlib
    import json as _json

    body = _json.dumps({k: v for k, v in d.items() if k != "id"}, sort_keys=True, default=str)
    return "cred_" + hashlib.sha256(f"{salt}:{body}".encode("utf-8")).hexdigest()[:12]


def _new_id(prefix: str) -> str:
    # 96 bits of randomness is plenty for credential ids — collision across
    # a single user's store is effectively impossible, and we're not
    # exporting these to a shared database.
    return f"{prefix}_{secrets.token_hex(6)}"


__all__ = [
    "CREDENTIAL_SCHEMA_VERSION", "LOGIN_KINDS", "STRATEGIES",
    "CredentialKind", "CredentialStatus", "CredentialSourceName", "Strategy",
    "CredentialData", "CredentialPayload", "Credential", "CredentialPool",
    "AuthEventType", "AuthEvent", "AuthEventListener",
    "AuthError", "AuthConfigError", "AuthCorruptCredentialError",
    "AuthReadOnlyError", "AuthRefreshError", "AuthRotationConsumedError",
    "AuthNeedsReauthError", "AuthPoolExhaustedError", "AuthCredentialProcessError",
    "RemovalStep", "CredentialSource", "LoginMethod", "LoginUi",
]
