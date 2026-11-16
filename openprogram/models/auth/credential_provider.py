"""
Refreshing the sign-ins a request is about to use.

What every request path reaches for once :mod:`.selection` has chosen a
credential: :meth:`CredentialProvider.prepare` returns it ready to send — an
OAuth access token near its expiry is refreshed first (one refresh at a
time per credential, in this process and across processes through the
refresh lock beside the provider's file), and a provider that reads its
login where another tool keeps it (Codex) gets its ``view`` of it.

The refresh itself is provider code (``ProviderAuthConfig.refresh``),
registered by the provider adapters; this module stays free of HTTP so the
state machine is testable with a fake refresh.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .store import AuthStore, get_store
from .types import (
    AuthEvent,
    AuthEventType,
    AuthNeedsReauthError,
    AuthReadOnlyError,
    AuthRefreshError,
    AuthRotationConsumedError,
    Credential,
)
import logging

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Callable shapes supplied by provider plugins
# ---------------------------------------------------------------------------

# Refresh: take a credential whose access token is expired / expiring,
# return a new Credential (same credential_id, new access_token / refresh_token
# / expires_at_ms). The manager handles persisting the result.
#
# Sync signature kept — the HTTP call inside is usually just ~1 POST, and
# async callers wrap it via ``run_in_executor``. Making every refresh
# async would force every provider plugin to have an event loop handy
# even when the code path is entirely sync.
RefreshFn = Callable[[Credential], Credential]

# AsyncRefresh: same contract but for provider plugins that already
# produce awaitables. The manager picks whichever the plugin registers.
AsyncRefreshFn = Callable[[Credential], Awaitable[Credential]]


# ---------------------------------------------------------------------------
# Per-provider config handed to the manager at registration time
# ---------------------------------------------------------------------------

@dataclass
class ProviderAuthConfig:
    """Per-provider knobs. Populated by provider plugins at import time and
    looked up by ``(provider_id)`` at acquire-time."""

    provider_id: str
    # How close to expiry do we pre-emptively refresh. 60 s matches what
    # every other OSS framework uses — short enough to survive minor clock
    # skew, long enough to avoid refreshing on every call.
    refresh_skew_seconds: int = 60
    # Either sync or async; the manager prefers async if both are provided.
    refresh: Optional[RefreshFn] = None
    async_refresh: Optional[AsyncRefreshFn] = None
    # The credential as a request sends it, when the provider keeps its login
    # elsewhere and reads it there each time (Codex's ``auth.json``).
    view: Optional[Callable[[Credential], Credential]] = None
    # Read every stored login of this provider again, where it lives (its
    # own store, another CLI's file): refresh what is about to expire, mark
    # what can no longer be refreshed, clear the mark of one that was signed
    # in again. What the Providers page's Refresh runs before it reads the
    # quota. ``None`` for a provider whose keys never change by themselves.
    reread_login: Optional[Callable[[], None]] = None
    # The plan name the active sign-in carries ("plus", "pro"), read from
    # what is already stored; never a request. ``None`` when it has none.
    plan: Optional[Callable[[], Optional[str]]] = None


# Process-wide provider config registry. Populated by explicit adapter
# registration via :func:`register_provider_config`. One entry per
# ``provider_id`` — adding a second call for the same id replaces the
# first (last registration wins; useful for tests).
_provider_configs: dict[str, ProviderAuthConfig] = {}

# Have we lazily registered the built-in auth adapters? Set once so we
# only pay that cost on the first ``get_provider_config`` miss per process.
_PROVIDER_PLUGINS_LOADED = False
_provider_plugins_condition = threading.Condition(threading.RLock())
_provider_plugins_loading_thread: int | None = None
_provider_configs_staging: dict[str, ProviderAuthConfig] | None = None


def register_provider_config(cfg: ProviderAuthConfig) -> None:
    with _provider_plugins_condition:
        if _provider_plugins_loading_thread == threading.get_ident():
            assert _provider_configs_staging is not None
            _provider_configs_staging[cfg.provider_id] = cfg
        else:
            _provider_configs[cfg.provider_id] = cfg


def _load_provider_plugins() -> None:
    """Register provider authentication adapters once.

    Deferred until the first miss in :func:`get_provider_config` to
    keep ``openprogram.models.auth`` independently importable (providers
    imports auth, so eager-importing the reverse would form a cycle
    and the auth_adapter modules wouldn't see manager's symbols yet).

    Idempotent: safe to call multiple times; ``_PROVIDER_PLUGINS_LOADED``
    short-circuits on the second visit.
    """
    global _PROVIDER_PLUGINS_LOADED
    global _provider_configs_staging, _provider_plugins_loading_thread
    ident = threading.get_ident()
    with _provider_plugins_condition:
        while _provider_plugins_loading_thread is not None:
            if _provider_plugins_loading_thread == ident:
                return
            _provider_plugins_condition.wait()
        if _PROVIDER_PLUGINS_LOADED:
            return
        _provider_plugins_loading_thread = ident
        _provider_configs_staging = {}

    try:
        from openprogram.models.providers.catalog.register import register_auth_adapters

        register_auth_adapters()
    except BaseException:
        with _provider_plugins_condition:
            _PROVIDER_PLUGINS_LOADED = False
            _provider_configs_staging = None
            _provider_plugins_loading_thread = None
            _provider_plugins_condition.notify_all()
        raise

    with _provider_plugins_condition:
        assert _provider_configs_staging is not None
        _provider_configs.update(_provider_configs_staging)
        _PROVIDER_PLUGINS_LOADED = True
        _provider_configs_staging = None
        _provider_plugins_loading_thread = None
        _provider_plugins_condition.notify_all()


def _get_registered_provider_config(provider_id: str) -> ProviderAuthConfig | None:
    ident = threading.get_ident()
    with _provider_plugins_condition:
        while (
            _provider_plugins_loading_thread is not None
            and _provider_plugins_loading_thread != ident
        ):
            _provider_plugins_condition.wait()
        if _provider_plugins_loading_thread == ident:
            assert _provider_configs_staging is not None
            cfg = _provider_configs_staging.get(provider_id)
            if cfg is not None:
                return cfg
        return _provider_configs.get(provider_id)


def get_provider_config(provider_id: str) -> ProviderAuthConfig:
    cfg = _get_registered_provider_config(provider_id)
    if cfg is not None:
        return cfg
    # Miss — give provider plugins a chance to register before we
    # fall back to the default. ``openprogram providers doctor`` runs
    # entirely inside the auth package, so without this nudge it
    # never imports the providers package and forever reports
    # "no_refresh_registered" for OAuth credentials whose refresh
    # callbacks are sitting in ``providers/adapters/<x>/auth_adapter.py``.
    _load_provider_plugins()
    cfg = _get_registered_provider_config(provider_id)
    if cfg is not None:
        return cfg
    # A default config is usable for API-key-only providers — no
    # refresh, no fallback, default cooldowns. Providers with real
    # OAuth must register explicitly.
    return ProviderAuthConfig(provider_id=provider_id)


# ---------------------------------------------------------------------------
# CredentialProvider
# ---------------------------------------------------------------------------

class CredentialProvider:
    """Makes a chosen credential ready to send. One per process."""

    def __init__(self, store: Optional[AuthStore] = None) -> None:
        self._store = store
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @property
    def store(self) -> AuthStore:
        return self._store or get_store()

    def _lock(self, cred: Credential) -> threading.Lock:
        key = (cred.provider_id, cred.id)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    # -- what request paths call ----------------------------------------------

    def acquire_sync(self, provider_id: str) -> Credential:
        """The credential a request outside any conversation would use now
        (the first usable one by the provider's strategy), ready to send.
        Raises :class:`AuthConfigError` when the provider has none and
        :class:`AuthPoolExhaustedError` when none can be used now."""
        from .selection import choose

        return self.prepare(choose(provider_id, record=False))

    def needs_preparing(self, cred: Credential) -> bool:
        cfg = get_provider_config(cred.provider_id)
        return cfg.view is not None or (
            cred.kind in ("oauth", "device_code") and _oauth_stale(cred, cfg.refresh_skew_seconds))

    def prepare(self, cred: Credential) -> Credential:
        """``cred`` ready to send: refreshed when its token is near expiry,
        then the provider's ``view`` of it."""
        cfg = get_provider_config(cred.provider_id)
        if cred.kind in ("oauth", "device_code"):
            cred = self._maybe_refresh(cred, cfg)
        if cfg.view is not None:
            cred = cfg.view(cred)
        return cred

    def force_refresh_sync(self, cred: Credential) -> Credential:
        """Refresh ``cred`` now, before its recorded expiry, because the
        provider refused its access token. A peer process that already
        refreshed it wins; read-only credentials come back unchanged."""
        if cred.read_only:
            return cred
        return self._maybe_refresh(cred, get_provider_config(cred.provider_id), force=True)

    def refresh_if_stale_sync(self, cred: Credential) -> Credential:
        """Refresh ``cred`` when its access token is past or near expiry: a
        refusal of its refresh token marks it ``needs_login``."""
        if cred.read_only or cred.kind not in ("oauth", "device_code"):
            return cred
        return self._maybe_refresh(cred, get_provider_config(cred.provider_id))

    # -- refresh ---------------------------------------------------------------

    def _maybe_refresh(self, cred: Credential, cfg: ProviderAuthConfig, *, force: bool = False) -> Credential:
        if cred.read_only:
            if _oauth_stale(cred, cfg.refresh_skew_seconds):
                raise AuthReadOnlyError(
                    f"the {cred.provider_id} login read from another tool has expired; "
                    "sign in with that tool again",
                    provider_id=cred.provider_id,
                )
            return cred
        if not force and not _oauth_stale(cred, cfg.refresh_skew_seconds):
            return cred
        if cfg.refresh is None and cfg.async_refresh is None:
            raise AuthNeedsReauthError(
                f"the {cred.provider_id} sign-in has expired and cannot be refreshed",
                provider_id=cred.provider_id,
            )
        from . import refresh_lock

        with self._lock(cred), refresh_lock.held(self.store.pool_file(cred.provider_id)):
            # Another process may have spent the refresh token already; its
            # result is on disk.
            fresh = self.store.find(cred.provider_id, cred.id)
            if fresh is not None:
                renewed = (fresh.payload.auth_value != cred.payload.auth_value if force
                           else not _oauth_stale(fresh, cfg.refresh_skew_seconds))
                if renewed:
                    return fresh
                cred = fresh
            self.store.emit(AuthEvent(type=AuthEventType.REFRESH_STARTED,
                                       provider_id=cred.provider_id, credential_id=cred.id))
            try:
                refreshed = self._call_refresh(cred, cfg)
            except AuthRotationConsumedError:
                fresh = self.store.find(cred.provider_id, cred.id)
                if fresh is not None and not _oauth_stale(fresh, cfg.refresh_skew_seconds):
                    return fresh
                raise
            except Exception as e:
                if _is_permanent_refresh_failure(e):
                    from .selection import mark
                    mark(cred.provider_id, cred.id, "needs_login", message=str(e)[:200])
                self.store.emit(AuthEvent(type=AuthEventType.REFRESH_FAILED,
                                           provider_id=cred.provider_id, credential_id=cred.id,
                                           detail={"error": str(e)[:200]}))
                raise

            def _save(row: Credential) -> None:
                row.payload = refreshed.payload
                row.metadata = {**row.metadata, **(refreshed.metadata or {})}
                row.identity = {**row.identity, **(refreshed.identity or {})}
                if row.status == "needs_login":
                    row.status = "ready"
                row.updated_at_ms = int(time.time() * 1000)

            saved = self.store.update(cred.provider_id, cred.id, _save) or refreshed
            self.store.emit(AuthEvent(type=AuthEventType.REFRESH_SUCCEEDED,
                                       provider_id=cred.provider_id, credential_id=cred.id))
            return saved

    def _call_refresh(self, cred: Credential, cfg: ProviderAuthConfig) -> Credential:
        if cfg.refresh is not None:
            return cfg.refresh(cred)
        if cfg.async_refresh is not None:
            return _run_sync(lambda: cfg.async_refresh(cred), f"refresh-{cred.provider_id}")
        raise AuthRefreshError("no refresh callable registered", provider_id=cred.provider_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_sync(make_coro, thread_name: str):
    """Run a coroutine from sync code, on a private thread when this
    thread already runs an event loop (TUI, FastAPI, async tests)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is None:
        return asyncio.run(make_coro())

    import threading
    box: dict = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(make_coro())
        except BaseException as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=_runner, daemon=True, name=thread_name)
    t.start()
    t.join()
    if "err" in box:
        raise box["err"]
    return box["value"]


def _is_permanent_refresh_failure(exc: Exception) -> bool:
    """Does this refresh exception mean the refresh_token is DEAD (re-auth
    required), as opposed to a transient hiccup we should retry?

    Permanent: the token endpoint rejected the refresh_token itself —
    ``invalid_grant`` / 400 / 401 / 403 / "refresh token not found or
    invalid". Retrying these just re-fails and spams the logs, so we
    persist ``needs_reauth`` and stop trying.

    Transient (returns False — credential left untouched, retried next
    call): network errors, timeouts, 5xx, and our own
    :class:`AuthRotationConsumedError` (which the caller already reloads +
    retries). This honours the design rule "5xx / network errors do NOT
    touch the credential"."""
    if isinstance(exc, AuthNeedsReauthError):
        return True
    if isinstance(exc, AuthRotationConsumedError):
        return False
    text = str(exc).lower()
    # Transport-level failures say nothing about token validity.
    for transient in ("timeout", "timed out", "connection", "network",
                      "temporarily", "503", "502", "500", "504"):
        if transient in text:
            return False
    for permanent in ("invalid_grant", "invalid grant", "not found or invalid",
                      "400", "401", "403", "unauthorized", "invalid_token",
                      "revoked", "expired"):
        if permanent in text:
            return True
    return False


def _oauth_stale(cred: Credential, skew_seconds: int) -> bool:
    """Return True iff ``cred``'s access token is past (or close to)
    expiry. Applies to OAuth + device_code payloads; other kinds return
    False (they never expire)."""
    if cred.payload.kind not in ("oauth", "device_code") and cred.payload.data.get("expires_at_ms") is None:
        return False
    expires = cred.payload.data.get("expires_at_ms", 0)
    if not expires:
        # 0 means "unknown"; play safe and refresh.
        return True
    return int(time.time() * 1000) + skew_seconds * 1000 >= expires


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_manager: Optional[CredentialProvider] = None
_manager_lock = threading.Lock()


def get_credential_provider() -> CredentialProvider:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = CredentialProvider()
        return _manager


def set_credential_provider_for_testing(manager: Optional[CredentialProvider]) -> None:
    global _manager
    with _manager_lock:
        _manager = manager


__all__ = [
    "CredentialProvider", "get_credential_provider", "set_credential_provider_for_testing",
    "ProviderAuthConfig", "register_provider_config", "get_provider_config",
    "RefreshFn", "AsyncRefreshFn",
]
