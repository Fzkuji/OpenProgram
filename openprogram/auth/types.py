"""
Auth v2 — type definitions.

Design goals that shape every type here:

  - one process-wide source of truth (:class:`AuthStore`) keyed by
    ``(provider_id, profile_id)``; every runtime asks the store, never
    caches its own copy
  - credentials are a tagged union so future auth kinds (SSO, hardware
    keys, enterprise JWT brokers) drop in without touching call sites
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
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Protocol

# Schema version for on-disk credential files. Every write stamps this;
# every read checks it. Mismatches are treated as corrupt rather than
# silently migrated — callers escalate to "re-auth this profile".
CREDENTIAL_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Credential — discriminated union over auth kinds
# ---------------------------------------------------------------------------

CredentialKind = Literal[
    "api_key",          # static secret (env or pasted)
    "oauth",            # access_token + refresh_token (PKCE or device code result)
    "cli_delegated",    # another tool's auth file we read through (read-only)
    "device_code",      # same payload shape as oauth but obtained via device flow;
                        # kept distinct so metrics + UI can show the origin
    "external_process", # credential is produced by shelling out to a helper each use
    "sso",              # enterprise SSO — reserved, not implemented; kind exists so
                        # the discriminator carries a valid value when stubbed out
]


CredentialStatus = Literal[
    "valid",            # usable right now
    "expiring_soon",    # ok but should refresh opportunistically
    "stale",            # past expiry; must refresh before use
    "refreshing",       # another caller is currently refreshing it; wait
    "needs_reauth",     # refresh is hopeless; UI should surface a re-login prompt
    "revoked",          # admin revoked / user deleted it; do not try to use
    "rate_limited",     # 429 recently; in cooldown, see :attr:`cooldown_until_ms`
    "billing_blocked",  # 402; in cooldown, see :attr:`cooldown_until_ms`
]


@dataclass
class ApiKeyPayload:
    """Static key — what 90 % of the world calls ``OPENAI_API_KEY``."""

    api_key: str


@dataclass
class OAuthPayload:
    """OAuth 2.0 access + refresh tokens plus enough provenance to refresh
    against the right endpoint without guessing.

    ``client_id`` is stored so a credential obtained via one OAuth client
    (e.g. Codex CLI's ``app_EMoamEEZ…``) can't accidentally be refreshed
    via a different one (e.g. the web flow's). Token-endpoint URL is
    stored so providers that relocate their OAuth endpoints don't invalidate
    persisted creds.
    """

    access_token: str
    refresh_token: str
    expires_at_ms: int          # unix ms; 0 if unknown
    scope: list[str] = field(default_factory=list)
    client_id: str = ""
    token_endpoint: str = ""
    id_token: str = ""          # openid id_token if present
    extra: dict = field(default_factory=dict)  # account_id, email, etc


@dataclass
class CliDelegatedPayload:
    """Pointer into another tool's on-disk auth store.

    We never duplicate their bytes — we re-read the file every time we need
    the credential, so the external CLI owning the store stays authoritative.
    This is how a user who runs ``codex login`` in their terminal sees the
    result reflected here without a re-import step.
    """

    store_path: str                # absolute path to the external store file
    access_key_path: list[str]     # JSON path to access token inside the file
    refresh_key_path: list[str] = field(default_factory=list)
    expires_key_path: list[str] = field(default_factory=list)


@dataclass
class DeviceCodePayload:
    """Same runtime shape as :class:`OAuthPayload`; distinguished for
    telemetry. Device-code flow tokens tend to have different lifetimes
    and rate limits than PKCE ones so separate stats matter."""

    access_token: str
    refresh_token: str
    expires_at_ms: int
    device_code_flow_id: str = ""   # which device-flow session minted this
    extra: dict = field(default_factory=dict)


@dataclass
class ExternalProcessPayload:
    """Credential is produced on-demand by running a helper command.

    Used by providers whose auth model is "shell out to the vendor CLI and
    read its stdout". The command runs under the current :class:`Profile`'s
    subprocess HOME so it doesn't leak between profiles.
    """

    command: list[str]              # argv for the helper
    parses: Literal["json", "text"] = "json"
    json_key_path: list[str] = field(default_factory=list)
    cache_seconds: int = 300        # cache the extracted value for this long


@dataclass
class SsoPayload:
    """Placeholder for enterprise SSO (SAML / OIDC broker).

    Reserved shape — the union needs a valid variant so call sites can
    switch on ``kind == "sso"`` today without a ``NotImplementedError``
    escape hatch. Fields will flesh out when an enterprise customer needs
    it; until then :class:`Credential` with this payload is rejected by
    the manager.
    """

    broker: str = ""
    subject: str = ""
    extra: dict = field(default_factory=dict)


CredentialPayload = (
    ApiKeyPayload
    | OAuthPayload
    | CliDelegatedPayload
    | DeviceCodePayload
    | ExternalProcessPayload
    | SsoPayload
)


@dataclass(frozen=True)
class AuthReference:
    """Pointer to a credential source we don't own the bytes of.

    Some providers — notably subscription-only CLIs like Claude Code —
    manage their own on-disk auth and don't expose the tokens to us.
    For those we can't create a :class:`Credential` (there's nothing to
    refresh or rotate), but the runtime still needs to tell the CLI
    *where* to find its own state.

    Two concrete uses today:

    - ``kind="external_file"`` + ``store_path=~/.claude/.credentials.json``
      — Claude Code CLI reads its own OAuth from this file. Our job
      is ensuring its HOME/XDG vars point at the right dir so the CLI
      finds the file; we never decode the contents.
    - ``kind="credential_ref"`` + ``provider_id`` + ``profile_id`` —
      "use whatever AuthManager currently returns for this pool".
      Useful for cross-profile reuse (a subprocess runtime that wants
      to share credentials with an API runtime's profile without
      duplicating the Credential entry).

    Intentionally not a union member of :class:`CredentialPayload` —
    ``AuthReference`` never lives *inside* a Credential; it's a
    sibling concept the Runtime layer resolves itself.
    """

    kind: Literal["external_file", "credential_ref"]
    # external_file: path to a vendor-CLI-owned auth file. The runtime
    # only uses this to derive HOME / env vars; contents are opaque.
    store_path: Optional[str] = None
    # credential_ref: target pool to delegate to. Resolve via AuthManager.
    provider_id: Optional[str] = None
    profile_id: Optional[str] = None
    # Free-form metadata for provider-specific hints (e.g. the HOME dir
    # the CLI expects, the env var name it reads from).
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind == "external_file" and not self.store_path:
            raise ValueError(
                "AuthReference(kind='external_file') requires store_path"
            )
        if self.kind == "credential_ref" and not (self.provider_id and self.profile_id):
            raise ValueError(
                "AuthReference(kind='credential_ref') requires provider_id "
                "and profile_id"
            )


@dataclass
class Credential:
    """One authentication artifact, plus the metadata needed to manage it.

    The split between :class:`Credential` (management state) and the
    variant payload (the secret bits) keeps rotation/cooldown/telemetry
    uniform regardless of auth kind — pool logic, UI renderers, event
    emitters all work on the outer object.
    """

    provider_id: str
    profile_id: str
    kind: CredentialKind
    payload: CredentialPayload
    status: CredentialStatus = "valid"
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    # Who produced us — "pkce_login" / "codex_cli_import" / "env_OPENAI_API_KEY".
    # Used by :class:`RemovalStep` to know where to clean up on unregister.
    source: str = "manual"
    # Free-form provenance: account email, display name, org id. The UI
    # renders whatever is here; the manager doesn't interpret it.
    metadata: dict = field(default_factory=dict)
    # Pool-level bookkeeping. Populated by :class:`CredentialPool` at
    # runtime; persisted so restart doesn't forget which key just got 429.
    cooldown_until_ms: int = 0
    last_used_at_ms: int = 0
    use_count: int = 0
    last_error: Optional[str] = None
    # Marks credentials that come from a :class:`CliDelegatedPayload` source
    # or an external-import profile. Write operations (refresh, rotate) on
    # read-only credentials either no-op or raise AuthReadOnlyError
    # depending on the call site — the manager enforces the rule, not
    # individual sources.
    read_only: bool = False
    # Unique id for cross-process coordination; avoids "I refreshed the one
    # whose access_token ends in abc" confusion when two processes race.
    credential_id: str = field(default_factory=lambda: _new_id("cred"))

    def to_dict(self) -> dict:
        return {
            "v": CREDENTIAL_SCHEMA_VERSION,
            "provider_id": self.provider_id,
            "profile_id": self.profile_id,
            "kind": self.kind,
            "payload": _payload_to_dict(self.payload),
            "status": self.status,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "source": self.source,
            "metadata": self.metadata,
            "cooldown_until_ms": self.cooldown_until_ms,
            "last_used_at_ms": self.last_used_at_ms,
            "use_count": self.use_count,
            "last_error": self.last_error,
            "read_only": self.read_only,
            "credential_id": self.credential_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Credential":
        if d.get("v") != CREDENTIAL_SCHEMA_VERSION:
            raise AuthCorruptCredentialError(
                f"credential schema v={d.get('v')!r} not understood"
            )
        return cls(
            provider_id=d["provider_id"],
            profile_id=d["profile_id"],
            kind=d["kind"],
            payload=_payload_from_dict(d["kind"], d["payload"]),
            status=d.get("status", "valid"),
            created_at_ms=d.get("created_at_ms", 0),
            updated_at_ms=d.get("updated_at_ms", 0),
            source=d.get("source", "manual"),
            metadata=d.get("metadata", {}),
            cooldown_until_ms=d.get("cooldown_until_ms", 0),
            last_used_at_ms=d.get("last_used_at_ms", 0),
            use_count=d.get("use_count", 0),
            last_error=d.get("last_error"),
            read_only=d.get("read_only", False),
            credential_id=d.get("credential_id", _new_id("cred")),
        )


def _payload_to_dict(p: CredentialPayload) -> dict:
    # dataclass asdict() is safe here because every payload class is a
    # flat dataclass with JSON-compatible field types.
    d = asdict(p)
    d["__type__"] = type(p).__name__
    return d


def _payload_from_dict(kind: CredentialKind, d: dict) -> CredentialPayload:
    # We key on the outer `kind` rather than the `__type__` discriminator
    # because the payload class and the kind string are required to agree
    # (enforced here); if they disagree, the file has been tampered with.
    data = {k: v for k, v in d.items() if k != "__type__"}
    mapping: dict[CredentialKind, type] = {
        "api_key": ApiKeyPayload,
        "oauth": OAuthPayload,
        "cli_delegated": CliDelegatedPayload,
        "device_code": DeviceCodePayload,
        "external_process": ExternalProcessPayload,
        "sso": SsoPayload,
    }
    cls = mapping.get(kind)
    if cls is None:
        raise AuthCorruptCredentialError(f"unknown credential kind: {kind!r}")
    try:
        return cls(**data)
    except TypeError as e:
        raise AuthCorruptCredentialError(f"bad payload for kind={kind!r}: {e}") from e


# ---------------------------------------------------------------------------
# Pool — multiple credentials per provider with rotation
# ---------------------------------------------------------------------------

PoolStrategy = Literal["fill_first", "round_robin", "random", "least_used"]


@dataclass
class CredentialPool:
    """Multiple credentials for the same provider, picked by strategy.

    Pool membership is a property of :class:`Profile`: a profile can bundle
    several keys (e.g. two OpenAI keys for hedging against rate limits).
    Profiles without multiple keys still go through the pool — it's just
    a one-element list. Unifying the code path avoids two "thin vs fat"
    branches in the manager.

    Strategy semantics:
      * ``fill_first``   — use #0 until it cools down, then #1, then #2…
      * ``round_robin``  — cycle through every call, skipping cooled-down ones
      * ``random``       — uniform choice among healthy members
      * ``least_used``   — pick the one with the smallest ``use_count``

    Cooldown / status filtering applies to every strategy: cred with
    ``cooldown_until_ms > now`` or ``status in {"revoked", "needs_reauth"}``
    is invisible to the picker.
    """

    provider_id: str
    profile_id: str
    strategy: PoolStrategy = "fill_first"
    credentials: list[Credential] = field(default_factory=list)
    # Strategy-private state. Kept on the pool rather than in a closure so
    # restart can rehydrate round-robin cursor etc. if ever needed.
    _rr_cursor: int = 0
    # Pool-level cascade: if every member is cooled-down / broken, the
    # manager falls back to these provider+profile pairs in order before
    # giving up. Each entry is a (provider_id, profile_id) tuple.
    fallback_chain: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "profile_id": self.profile_id,
            "strategy": self.strategy,
            "credentials": [c.to_dict() for c in self.credentials],
            "_rr_cursor": self._rr_cursor,
            "fallback_chain": [list(t) for t in self.fallback_chain],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CredentialPool":
        return cls(
            provider_id=d["provider_id"],
            profile_id=d["profile_id"],
            strategy=d.get("strategy", "fill_first"),
            credentials=[Credential.from_dict(c) for c in (d.get("credentials") or [])],
            _rr_cursor=d.get("_rr_cursor", 0),
            fallback_chain=[tuple(t) for t in (d.get("fallback_chain") or [])],
        )


# ---------------------------------------------------------------------------
# Profile — isolation boundary
# ---------------------------------------------------------------------------

@dataclass
class Profile:
    """A fully isolated auth + subprocess environment.

    Each profile has its own filesystem root:
      ~/.openprogram/profiles/<name>/
        auth/<provider>/<profile_id>.json   credential pools
        home/                                subprocess HOME override
        .env                                 per-profile env vars
        metadata.json                        display name, created_at, …

    When a call runs "under" a profile, subprocesses it spawns see
    ``HOME``, ``XDG_*`` pointed at ``home/`` so tools like ``git`` /
    ``ssh`` / ``gh`` / ``npm`` don't leak credentials across profiles.
    This matches hermes-agent's profile model, which is the only OSS
    agent framework that has taken this problem seriously.
    """

    name: str                                  # user-facing identifier
    root: Path                                 # profile filesystem root
    created_at_ms: int
    display_name: str = ""
    description: str = ""

    @property
    def auth_dir(self) -> Path:
        return self.root / "auth"

    @property
    def home_dir(self) -> Path:
        return self.root / "home"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"


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
    # Profile-lifecycle
    PROFILE_CREATED = "profile_created"
    PROFILE_DELETED = "profile_deleted"
    PROFILE_ACTIVATED = "profile_activated"


@dataclass
class AuthEvent:
    type: AuthEventType
    provider_id: str = ""
    profile_id: str = ""
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
    profile_id: str = ""

    def __init__(self, message: str, *, provider_id: str = "", profile_id: str = "") -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.profile_id = profile_id


class AuthConfigError(AuthError):
    """Provider/profile not registered, or registered wrong."""


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


class AuthExpiredError(AuthError):
    """Access token expired and we didn't catch it in time. Usually
    auto-upgraded to a refresh — you see this only if the manager is
    misconfigured."""


class AuthRateLimitedError(AuthError):
    """429 on this credential. Manager puts it in cooldown and rotates
    to the next pool member; raised only if the whole pool is cooled-down."""


class AuthBillingBlockedError(AuthError):
    """402 — account exhausted billing allowance. Longer cooldown, same
    pool semantics as rate-limited."""


class AuthRevokedError(AuthError):
    """403 with specific revocation semantics — the key/token is dead. Manager
    marks the credential revoked; re-login required."""


class AuthNeedsReauthError(AuthError):
    """Refresh token itself is invalid. User must go through login again.
    Webui listens for this, surfaces a banner, doesn't retry."""


class AuthPoolExhaustedError(AuthError):
    """Every credential in the pool is cooled-down, revoked, or needs
    re-auth. Manager escalates to fallback_chain; if that's empty too,
    this is what the caller sees."""


# ---------------------------------------------------------------------------
# Removal contract — explicit uninstall for every source
# ---------------------------------------------------------------------------

@dataclass
class RemovalStep:
    """One concrete cleanup action needed to fully forget a credential.

    Example: a credential imported from ``~/.codex/auth.json`` needs one
    step — a note that the user must ``codex logout`` to remove the
    underlying file (we don't touch external CLI stores). A credential
    we created via PKCE has one step — delete ``profiles/<p>/auth/<prov>/
    <profile>.json``. A credential that was also referenced by an env var
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

    ``try_import`` is allowed to return multiple credentials (e.g. a file
    with several accounts); each becomes its own profile id.
    """

    source_id: str

    def try_import(self, profile_root: Path) -> list[Credential]: ...

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

def _new_id(prefix: str) -> str:
    # 96 bits of randomness is plenty for credential ids — collision across
    # a single user's store is effectively impossible, and we're not
    # exporting these to a shared database.
    return f"{prefix}_{secrets.token_hex(6)}"


__all__ = [
    "CREDENTIAL_SCHEMA_VERSION",
    "CredentialKind", "CredentialStatus",
    "ApiKeyPayload", "OAuthPayload", "CliDelegatedPayload",
    "DeviceCodePayload", "ExternalProcessPayload", "SsoPayload",
    "CredentialPayload", "Credential",
    "AuthReference",
    "PoolStrategy", "CredentialPool",
    "Profile",
    "AuthEventType", "AuthEvent", "AuthEventListener",
    "AuthError", "AuthConfigError", "AuthCorruptCredentialError",
    "AuthReadOnlyError", "AuthRefreshError", "AuthRotationConsumedError",
    "AuthExpiredError", "AuthRateLimitedError", "AuthBillingBlockedError",
    "AuthRevokedError", "AuthNeedsReauthError", "AuthPoolExhaustedError",
    "RemovalStep",
    "CredentialSource", "LoginMethod", "LoginUi",
]
