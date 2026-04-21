"""
Auth v2 — the runtime face of credential management.

This is what every API-calling code path reaches for. It wraps
:class:`AuthStore` + :mod:`.pool` with the two behaviours the raw layers
can't provide on their own:

  * **Refresh dedup.** Multiple concurrent calls near token expiry all
    hit one :meth:`acquire`; only the first actually performs a refresh,
    everyone else awaits its result. Implemented with per-pool
    in-flight :class:`asyncio.Future` plus the :class:`AuthStore` async
    lock (so cross-process mtime reloads also serialize against us).
    Pattern is the one Hermes lifted from Claude Code.

  * **Fallback chains.** If a pool is exhausted (every key cooling down,
    revoked, or needing reauth), the manager walks the configured
    fallback chain — ``[(provider_id, profile_id), ...]`` — trying the
    next one. This is the only place that knows about fallback; pools
    don't chain, runtimes don't reimplement it.

The manager is deliberately refresh-agnostic: it accepts a
``RefreshFn`` callable supplied by the provider plugin (or inferred
from :class:`Credential.kind`). That keeps the manager free of httpx /
pi-ai dependencies, so tests can exercise the full state machine with
a fake refresh that just increments a counter.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from . import pool as _pool
from .pool import PoolFailurePolicy
from .store import AuthStore, get_store
from .types import (
    AuthBillingBlockedError,
    AuthConfigError,
    AuthEvent,
    AuthEventType,
    AuthNeedsReauthError,
    AuthPoolExhaustedError,
    AuthRateLimitedError,
    AuthReadOnlyError,
    AuthRefreshError,
    AuthRotationConsumedError,
    Credential,
    CredentialPool,
    OAuthPayload,
)


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
    # Per-provider cooldown policy override (defaults kick in if None).
    failure_policy: Optional[PoolFailurePolicy] = None
    # Fallback chain: if the primary pool is exhausted, try these
    # (provider_id, profile_id) pairs in order. If they're exhausted
    # too, the manager raises :class:`AuthPoolExhaustedError`.
    fallback_chain: list[tuple[str, str]] = field(default_factory=list)


# Process-wide provider config registry. Populated at provider-plugin
# import time via :func:`register_provider_config`. One entry per
# ``provider_id`` — adding a second call for the same id replaces the
# first (last registration wins; useful for tests).
_provider_configs: dict[str, ProviderAuthConfig] = {}


def register_provider_config(cfg: ProviderAuthConfig) -> None:
    _provider_configs[cfg.provider_id] = cfg


def get_provider_config(provider_id: str) -> ProviderAuthConfig:
    cfg = _provider_configs.get(provider_id)
    if cfg is None:
        # A default config is usable for API-key-only providers — no
        # refresh, no fallback, default cooldowns. Providers with real
        # OAuth must register explicitly.
        cfg = ProviderAuthConfig(provider_id=provider_id)
    return cfg


# ---------------------------------------------------------------------------
# AuthManager — the orchestrator
# ---------------------------------------------------------------------------

class AuthManager:
    """Pool-aware credential acquirer with in-flight refresh dedup and
    fallback chains.

    Instantiate once per process (or use :func:`get_manager`). Hold a
    reference in your runtime; don't construct it per-call — the
    in-flight future bookkeeping depends on identity.
    """

    def __init__(self, store: Optional[AuthStore] = None) -> None:
        self._store = store or get_store()
        # (provider_id, profile_id, credential_id) → Future that resolves
        # to the refreshed Credential. Multiple coroutines awaiting the
        # same future get the same refreshed credential; exactly one
        # upstream refresh call happens per key.
        self._in_flight: dict[tuple[str, str, str], asyncio.Future[Credential]] = {}
        self._in_flight_lock = asyncio.Lock()

    @property
    def store(self) -> AuthStore:
        return self._store

    # -- acquire (async) -----------------------------------------------------

    def acquire_sync(self, provider_id: str, profile_id: str = "default") -> Credential:
        """Sync wrapper around :meth:`acquire` for callers in non-async
        contexts (legacy runtimes, CLI entry points, subprocess drivers).

        Rules:
          * if no event loop is running on the current thread, we spin up
            a private one for the call. Creating one loop per acquire is
            cheap (refresh involves at most one HTTPS POST) and keeps the
            code path self-contained.
          * if a loop IS running on this thread, we refuse — an async
            caller should use :meth:`acquire` directly rather than
            nesting loops. The error message names the caller to reduce
            "wait why doesn't this work" debugging.

        Cross-thread is fine either way: one event loop per thread is
        independent."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            raise RuntimeError(
                "AuthManager.acquire_sync() called from inside a running "
                "event loop — use `await acquire(...)` instead"
            )
        return asyncio.run(self.acquire(provider_id, profile_id))

    async def acquire(self, provider_id: str, profile_id: str = "default") -> Credential:
        """Return a usable credential for ``(provider, profile)``.

        Side effects:
          * may refresh an OAuth credential if its access token is
            expired or about to expire
          * updates pool's pick bookkeeping (``use_count`` etc.)
          * may rotate to a fallback chain entry if the pool is exhausted

        Does not persist every mutation — only refreshes are written
        back, to keep the hot path cheap. Cooldown updates happen via
        :meth:`report_failure`.
        """
        return await self._acquire_recursive(provider_id, profile_id, visited=set())

    async def _acquire_recursive(
        self,
        provider_id: str,
        profile_id: str,
        *,
        visited: set[tuple[str, str]],
    ) -> Credential:
        key = (provider_id, profile_id)
        if key in visited:
            # Fallback chain cycles back to a pool we already tried.
            # Preserve the outermost exception rather than looping forever.
            raise AuthPoolExhaustedError(
                f"fallback chain for {provider_id}/{profile_id} cycled",
                provider_id=provider_id, profile_id=profile_id,
            )
        visited.add(key)

        pool = self._store.find_pool(provider_id, profile_id)
        if pool is None:
            # No pool registered. If a fallback chain exists on the
            # provider config, try it. Otherwise it's a genuine config
            # error the caller must surface as "please log in".
            cfg = get_provider_config(provider_id)
            for nxt_prov, nxt_prof in cfg.fallback_chain:
                try:
                    return await self._acquire_recursive(
                        nxt_prov, nxt_prof, visited=visited,
                    )
                except AuthConfigError:
                    continue
                except AuthPoolExhaustedError:
                    continue
            raise AuthConfigError(
                f"no auth configured for {provider_id}/{profile_id}",
                provider_id=provider_id, profile_id=profile_id,
            )

        # Pick a healthy credential — may raise PoolExhausted.
        try:
            cred = _pool.pick(pool)
        except AuthPoolExhaustedError:
            # Try the pool-specific fallback chain first (set on the pool
            # itself), then provider-level, then surrender.
            for nxt_prov, nxt_prof in pool.fallback_chain:
                try:
                    return await self._acquire_recursive(
                        nxt_prov, nxt_prof, visited=visited,
                    )
                except (AuthConfigError, AuthPoolExhaustedError):
                    continue
            cfg = get_provider_config(provider_id)
            for nxt_prov, nxt_prof in cfg.fallback_chain:
                if (nxt_prov, nxt_prof) in visited:
                    continue
                try:
                    return await self._acquire_recursive(
                        nxt_prov, nxt_prof, visited=visited,
                    )
                except (AuthConfigError, AuthPoolExhaustedError):
                    continue
            self._store._emit(AuthEvent(                  # type: ignore[attr-defined]
                type=AuthEventType.POOL_EXHAUSTED,
                provider_id=provider_id,
                profile_id=profile_id,
            ))
            raise

        # Maybe refresh it. Only OAuth / device_code credentials need it;
        # static api_key and external-process creds are always "fresh".
        if cred.kind in ("oauth", "device_code"):
            cred = await self._maybe_refresh(cred, pool)
        return cred

    # -- refresh -------------------------------------------------------------

    async def _maybe_refresh(self, cred: Credential, pool: CredentialPool) -> Credential:
        if cred.read_only:
            # Read-only credentials (imported from external CLI) don't
            # get refreshed by us — the external tool owns that. If
            # they're past expiry, the external tool's stale state is
            # the user's problem; we surface it cleanly.
            if _oauth_stale(cred, get_provider_config(cred.provider_id).refresh_skew_seconds):
                raise AuthReadOnlyError(
                    f"read-only credential for {cred.provider_id}/{cred.profile_id} "
                    "is expired; re-run the external CLI's login",
                    provider_id=cred.provider_id,
                    profile_id=cred.profile_id,
                )
            return cred

        cfg = get_provider_config(cred.provider_id)
        if not _oauth_stale(cred, cfg.refresh_skew_seconds):
            return cred

        if cfg.refresh is None and cfg.async_refresh is None:
            # Expired and no refresh path registered → the user must
            # re-auth. Don't leave them thinking the API call succeeded.
            raise AuthNeedsReauthError(
                f"credential for {cred.provider_id}/{cred.profile_id} "
                "is expired and no refresh is configured",
                provider_id=cred.provider_id,
                profile_id=cred.profile_id,
            )

        key = (cred.provider_id, cred.profile_id, cred.credential_id)
        async with self._in_flight_lock:
            future = self._in_flight.get(key)
            if future is None:
                # We're the designated refresher.
                future = asyncio.get_event_loop().create_future()
                self._in_flight[key] = future
                owner = True
            else:
                owner = False

        if not owner:
            # Wait on the in-flight one; do not start a second request.
            return await future

        try:
            async with self._store.async_lock(cred.provider_id, cred.profile_id):
                # Under the lock, re-check disk — a cross-process refresh
                # may have already happened (mtime-watch will rehydrate
                # the pool). If our credential is no longer stale in the
                # fresh view, just use it.
                fresh_pool = self._store.find_pool(cred.provider_id, cred.profile_id)
                if fresh_pool is not None:
                    for fc in fresh_pool.credentials:
                        if fc.credential_id == cred.credential_id:
                            if not _oauth_stale(fc, cfg.refresh_skew_seconds):
                                future.set_result(fc)
                                return fc
                            cred = fc
                            break

                self._store._emit(AuthEvent(                     # type: ignore[attr-defined]
                    type=AuthEventType.REFRESH_STARTED,
                    provider_id=cred.provider_id,
                    profile_id=cred.profile_id,
                    credential_id=cred.credential_id,
                ))
                refreshed = await self._call_refresh(cred, cfg)
                # Persist: the pool object holds the same credential, so
                # mutate it in place and put_pool the whole thing.
                pool_to_save = self._store.find_pool(cred.provider_id, cred.profile_id) or pool
                for i, existing in enumerate(pool_to_save.credentials):
                    if existing.credential_id == cred.credential_id:
                        pool_to_save.credentials[i] = refreshed
                        break
                self._store.put_pool(pool_to_save)
                self._store._emit(AuthEvent(                     # type: ignore[attr-defined]
                    type=AuthEventType.REFRESH_SUCCEEDED,
                    provider_id=cred.provider_id,
                    profile_id=cred.profile_id,
                    credential_id=cred.credential_id,
                ))
                future.set_result(refreshed)
                return refreshed
        except AuthRotationConsumedError as e:
            # Reload from disk once; maybe a peer process already
            # succeeded. If the disk copy is usable now, we're fine.
            self._store._reload_if_disk_changed(          # type: ignore[attr-defined]
                (cred.provider_id, cred.profile_id),
            )
            fresh_pool = self._store.find_pool(cred.provider_id, cred.profile_id)
            if fresh_pool is not None:
                for fc in fresh_pool.credentials:
                    if fc.credential_id == cred.credential_id and not _oauth_stale(
                        fc, cfg.refresh_skew_seconds,
                    ):
                        future.set_result(fc)
                        return fc
            future.set_exception(e)
            raise
        except Exception as e:
            future.set_exception(e)
            self._store._emit(AuthEvent(                         # type: ignore[attr-defined]
                type=AuthEventType.REFRESH_FAILED,
                provider_id=cred.provider_id,
                profile_id=cred.profile_id,
                credential_id=cred.credential_id,
                detail={"error": str(e)},
            ))
            raise
        finally:
            async with self._in_flight_lock:
                self._in_flight.pop(key, None)

    async def _call_refresh(self, cred: Credential, cfg: ProviderAuthConfig) -> Credential:
        if cfg.async_refresh is not None:
            return await cfg.async_refresh(cred)
        if cfg.refresh is not None:
            # Run sync refresh in a thread to not block the event loop —
            # refresh is a network call, so this matters.
            return await asyncio.get_event_loop().run_in_executor(
                None, cfg.refresh, cred,
            )
        raise AuthRefreshError("no refresh callable registered",
                               provider_id=cred.provider_id,
                               profile_id=cred.profile_id)

    # -- failure reporting ---------------------------------------------------

    def report_failure(
        self,
        provider_id: str,
        profile_id: str,
        credential_id: str,
        reason: str,
        *,
        detail: str = "",
    ) -> None:
        """Record an API-call failure so the pool layer can cool the
        credential down. Idempotent-ish: calling twice just updates the
        last error; cooldown window is set from the first call.

        ``reason`` values map to :func:`pool.mark_failure` semantics.
        Callers (the HTTP wrapper around provider APIs) classify each
        error response before calling.
        """
        pool = self._store.find_pool(provider_id, profile_id)
        if pool is None:
            return
        cred = next(
            (c for c in pool.credentials if c.credential_id == credential_id),
            None,
        )
        if cred is None:
            return
        cfg = get_provider_config(provider_id)
        ev = _pool.mark_failure(cred, reason, policy=cfg.failure_policy, detail=detail)
        self._store.put_pool(pool)
        self._store._emit(ev)          # type: ignore[attr-defined]

    def report_success(self, provider_id: str, profile_id: str, credential_id: str) -> None:
        """Called on 2xx. Clears transient error state if its cooldown
        window has passed. Cheap; OK to call on every successful
        response."""
        pool = self._store.find_pool(provider_id, profile_id)
        if pool is None:
            return
        cred = next(
            (c for c in pool.credentials if c.credential_id == credential_id),
            None,
        )
        if cred is None:
            return
        _pool.mark_success(cred)
        # Intentionally no put_pool — the hot success path shouldn't
        # fsync every time. Next credential-modifying call (refresh,
        # cooldown, add, remove) will persist the state anyway.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _oauth_stale(cred: Credential, skew_seconds: int) -> bool:
    """Return True iff ``cred``'s access token is past (or close to)
    expiry. Applies to OAuth + device_code payloads; other kinds return
    False (they never expire)."""
    if not isinstance(cred.payload, OAuthPayload) and getattr(cred.payload, "expires_at_ms", None) is None:
        return False
    expires = getattr(cred.payload, "expires_at_ms", 0)
    if not expires:
        # 0 means "unknown"; play safe and refresh.
        return True
    return int(time.time() * 1000) + skew_seconds * 1000 >= expires


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

import threading  # noqa: E402  (module-level threading only used here)

_manager: Optional[AuthManager] = None
_manager_lock = threading.Lock()


def get_manager() -> AuthManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = AuthManager()
        return _manager


def set_manager_for_testing(manager: Optional[AuthManager]) -> None:
    global _manager
    with _manager_lock:
        _manager = manager


# Public re-exports for convenience — callers just import from
# ``openprogram.auth.manager`` and get everything they need.
__all__ = [
    "AuthManager", "get_manager", "set_manager_for_testing",
    "ProviderAuthConfig", "register_provider_config", "get_provider_config",
    "RefreshFn", "AsyncRefreshFn",
]
