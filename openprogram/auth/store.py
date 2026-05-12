"""
Auth v2 — persistent credential store.

Responsibilities:

  * hold the canonical in-memory map of :class:`CredentialPool` objects
    keyed by ``(provider_id, profile_id)``
  * persist each pool to its own JSON file at
    ``<profile_root>/auth/<provider_id>/<profile_id>.json`` with 0600
    permissions
  * serialize concurrent mutations per pool (one ``asyncio.Lock`` per
    ``(provider, profile)`` key plus a short-lived file lock on write)
  * detect cross-process edits via mtime + size tracking, reload the
    in-memory copy when an outside writer has bumped the file, so
    two OpenProgram processes (or OpenProgram + an external CLI that
    happens to write our format) stay coherent
  * fan out :class:`AuthEvent`s to subscribers on every state change

Design references:

  * hermes-agent ``hermes_cli/auth.py`` — fcntl/msvcrt advisory locks,
    ``AUTH_STORE_VERSION``, ``AUTH_LOCK_TIMEOUT_SECONDS`` tuning
  * Claude Code's ``invalidateOAuthCacheIfDiskChanged`` — the mtime
    re-read pattern
  * hermes-agent ``tools/mcp_oauth_manager.py`` — in-flight refresh
    dedup (implemented in :mod:`auth.manager`, not here)

Nothing here imports httpx or talks to LLM providers. Refresh itself
happens in :mod:`auth.manager`; this module just stores the result.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from .types import (
    AuthConfigError,
    AuthCorruptCredentialError,
    AuthEvent,
    AuthEventListener,
    AuthEventType,
    Credential,
    CredentialPool,
    Profile,
)

# Default root. Overridable per-process via ``AuthStore(root=...)`` and
# per-profile via :class:`Profile.root`.
DEFAULT_ROOT = Path.home() / ".openprogram"

# How long we'll wait for an OS-level advisory file lock before giving up
# and raising. Tuned the same as hermes-agent (15 s) — long enough to
# survive a slow fsync on a network drive, short enough that a genuinely
# stuck process doesn't hang the whole app forever.
FILE_LOCK_TIMEOUT_SECONDS = 15.0

# How often (seconds) we re-stat a pool file looking for cross-process
# modifications. Zero means "check on every read" (expensive on a hot
# path; never do this). One hundred ms is imperceptible to the user and
# handles the common case of two processes updating near-simultaneously.
MTIME_POLL_INTERVAL = 0.1


# ---------------------------------------------------------------------------
# OS-level file locking — cross-platform wrapper
# ---------------------------------------------------------------------------

try:
    import fcntl  # type: ignore[import]

    @contextmanager
    def _flock(path: Path, timeout: float = FILE_LOCK_TIMEOUT_SECONDS):
        """Advisory exclusive lock on ``path``. POSIX implementation.

        The lock lives on a sibling ``.lock`` file rather than the
        credential file itself: locking the data file would mean opening
        it for write just to get the lock, which is annoying on a path
        the caller might not intend to write.
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.time() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() >= deadline:
                        raise TimeoutError(f"could not acquire {lock_path} within {timeout}s")
                    time.sleep(0.05)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

except ImportError:  # pragma: no cover — Windows path
    import msvcrt  # type: ignore[import]

    @contextmanager
    def _flock(path: Path, timeout: float = FILE_LOCK_TIMEOUT_SECONDS):
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.time() + timeout
        locked = False
        try:
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.time() >= deadline:
                        raise TimeoutError(f"could not acquire {lock_path} within {timeout}s")
                    time.sleep(0.05)
            yield
        finally:
            if locked:
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            os.close(fd)


# ---------------------------------------------------------------------------
# AuthStore
# ---------------------------------------------------------------------------

class AuthStore:
    """Authoritative in-memory + on-disk credential pool store.

    API surface is small on purpose — the manager layer adds refresh
    logic, rotation, events-with-side-effects on top of this. Keeping
    raw CRUD here means tests of higher layers can mock the manager
    without reimplementing persistence.
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        profile: Optional[Profile] = None,
    ) -> None:
        self._root = root or DEFAULT_ROOT
        self._profile = profile
        self._pools: dict[tuple[str, str], CredentialPool] = {}
        # Per-pool asyncio lock for in-process serialization. sync paths
        # use the thread RLock; async paths use the asyncio lock.
        self._sync_lock = threading.RLock()
        self._async_locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Cross-process coherence: remember (mtime, size) we last observed
        # for each file so reads can notice out-of-process writes.
        self._fstat: dict[tuple[str, str], tuple[float, int]] = {}
        self._listeners: list[AuthEventListener] = []

    # -- configuration --------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    def base_dir(self) -> Path:
        if self._profile is not None:
            return self._profile.auth_dir
        return self._root / "auth"

    def _pool_path(self, provider_id: str, profile_id: str) -> Path:
        """Resolve the on-disk path for ``(provider, profile)``.

        Returns the canonical path first; if that file doesn't exist,
        falls back to any directory whose alias resolves to this
        provider (so legacy login dirs like ``openai-codex/`` keep
        working when the canonical id is ``chatgpt-subscription``).
        """
        canonical = self.base_dir() / provider_id / f"{profile_id}.json"
        if canonical.exists():
            return canonical
        # Reverse-alias scan: any short name that resolves to this
        # canonical id is also a valid on-disk directory.
        try:
            from openprogram.auth.aliases import known_aliases
            for alias, target in known_aliases().items():
                if target == provider_id and alias != provider_id:
                    alt = self.base_dir() / alias / f"{profile_id}.json"
                    if alt.exists():
                        return alt
        except Exception:
            pass
        return canonical

    # -- listeners ------------------------------------------------------------

    def subscribe(self, listener: AuthEventListener):
        with self._sync_lock:
            self._listeners.append(listener)

        def _unsub():
            with self._sync_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)
        return _unsub

    def _emit(self, event: AuthEvent) -> None:
        # Copy the list so a listener that unsubscribes mid-iteration
        # doesn't corrupt iteration order.
        listeners = list(self._listeners)
        for lst in listeners:
            try:
                lst(event)
            except Exception:
                # An auth event listener cannot be trusted to not throw —
                # for instance a webui ws broadcast may race a reconnect.
                # Swallowing is correct: state changes already happened,
                # subscribers are advisory.
                pass

    # -- reads ----------------------------------------------------------------

    def get_pool(self, provider_id: str, profile_id: str) -> CredentialPool:
        """Return the pool for ``(provider, profile)``, loading from disk
        if necessary. Raises :class:`AuthConfigError` if not found."""
        with self._sync_lock:
            key = (provider_id, profile_id)
            self._reload_if_disk_changed(key)
            pool = self._pools.get(key)
            if pool is None:
                pool = self._load_from_disk(key)
                if pool is None:
                    raise AuthConfigError(
                        f"no credentials for {provider_id}/{profile_id}",
                        provider_id=provider_id,
                        profile_id=profile_id,
                    )
                self._pools[key] = pool
            return pool

    def find_pool(self, provider_id: str, profile_id: str) -> Optional[CredentialPool]:
        """Like :meth:`get_pool` but returns ``None`` instead of raising."""
        try:
            return self.get_pool(provider_id, profile_id)
        except AuthConfigError:
            return None

    def list_pools(self) -> list[CredentialPool]:
        """Enumerate every pool known to this store (in-memory or on-disk).

        Useful for the settings UI that wants to show "all your accounts".
        """
        with self._sync_lock:
            found: dict[tuple[str, str], CredentialPool] = dict(self._pools)
            base = self.base_dir()
            if base.exists():
                for prov_dir in base.iterdir():
                    if not prov_dir.is_dir():
                        continue
                    for pool_file in prov_dir.glob("*.json"):
                        key = (prov_dir.name, pool_file.stem)
                        if key in found:
                            continue
                        loaded = self._load_from_disk(key)
                        if loaded is not None:
                            self._pools[key] = loaded
                            found[key] = loaded
            return list(found.values())

    # -- writes ---------------------------------------------------------------

    def put_pool(self, pool: CredentialPool) -> None:
        """Persist ``pool`` and emit appropriate events.

        Intended use: call by higher-level helpers (``add_credential``,
        ``remove_credential``, manager's refresh). Not for callers that
        just want to poke a field on a credential — they should mutate
        the credential and then call :meth:`put_pool` with the same pool
        object, because we atomically re-write the whole file.
        """
        key = (pool.provider_id, pool.profile_id)
        with self._sync_lock:
            self._pools[key] = pool
            self._persist(pool)

    def add_credential(self, cred: Credential) -> CredentialPool:
        """Append ``cred`` to the pool at ``(cred.provider_id, cred.profile_id)``,
        creating the pool if absent. Returns the updated pool."""
        key = (cred.provider_id, cred.profile_id)
        with self._sync_lock:
            pool = self._pools.get(key) or self._load_from_disk(key) or CredentialPool(
                provider_id=cred.provider_id, profile_id=cred.profile_id,
            )
            pool.credentials.append(cred)
            self._pools[key] = pool
            self._persist(pool)
            self._emit(AuthEvent(
                type=AuthEventType.POOL_MEMBER_ADDED,
                provider_id=cred.provider_id,
                profile_id=cred.profile_id,
                credential_id=cred.credential_id,
                detail={"source": cred.source, "kind": cred.kind},
            ))
            return pool

    def remove_credential(self, provider_id: str, profile_id: str, credential_id: str) -> None:
        """Remove a single credential from a pool. If the pool becomes
        empty the pool file is *not* deleted — the profile may want to
        add new credentials back. Use :meth:`delete_pool` to nuke it."""
        key = (provider_id, profile_id)
        with self._sync_lock:
            pool = self._pools.get(key) or self._load_from_disk(key)
            if pool is None:
                return
            before = len(pool.credentials)
            pool.credentials = [c for c in pool.credentials if c.credential_id != credential_id]
            if len(pool.credentials) == before:
                return
            self._pools[key] = pool
            self._persist(pool)
            self._emit(AuthEvent(
                type=AuthEventType.POOL_MEMBER_REMOVED,
                provider_id=provider_id,
                profile_id=profile_id,
                credential_id=credential_id,
            ))

    def delete_pool(self, provider_id: str, profile_id: str) -> None:
        """Delete a pool entirely (in-memory and on-disk)."""
        key = (provider_id, profile_id)
        path = self._pool_path(provider_id, profile_id)
        with self._sync_lock:
            self._pools.pop(key, None)
            self._fstat.pop(key, None)
            if path.exists():
                with _flock(path):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
            self._emit(AuthEvent(
                type=AuthEventType.POOL_MEMBER_REMOVED,
                provider_id=provider_id,
                profile_id=profile_id,
                detail={"reason": "pool_deleted"},
            ))

    # -- locks (public — used by manager) ------------------------------------

    def async_lock(self, provider_id: str, profile_id: str) -> asyncio.Lock:
        """Per-pool asyncio lock. Refresh code grabs this to serialize
        refresh_token rotation within the process."""
        key = (provider_id, profile_id)
        with self._sync_lock:
            lock = self._async_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._async_locks[key] = lock
            return lock

    # -- persistence internals -----------------------------------------------

    def _persist(self, pool: CredentialPool) -> None:
        path = self._pool_path(pool.provider_id, pool.profile_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = json.dumps(pool.to_dict(), indent=2, ensure_ascii=False)
        with _flock(path):
            # Write → chmod → fsync → rename so a crash mid-write leaves
            # the previous good file untouched, and permissions are set
            # before the file becomes visible at its final name.
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                os.write(fd, data.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
            # Record observed fstat so we don't treat our own write as a
            # cross-process change on the next read.
            st = path.stat()
            self._fstat[(pool.provider_id, pool.profile_id)] = (st.st_mtime, st.st_size)

    def _load_from_disk(self, key: tuple[str, str]) -> Optional[CredentialPool]:
        path = self._pool_path(*key)
        if not path.exists():
            return None
        try:
            with _flock(path):
                raw = path.read_text(encoding="utf-8")
                st = path.stat()
        except FileNotFoundError:
            return None
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AuthCorruptCredentialError(
                f"{path} is not valid JSON: {e}",
                provider_id=key[0], profile_id=key[1],
            ) from e
        pool = CredentialPool.from_dict(d)
        self._fstat[key] = (st.st_mtime, st.st_size)
        return pool

    def _reload_if_disk_changed(self, key: tuple[str, str]) -> None:
        """If the on-disk file has a different (mtime, size) than we last
        saw, reload. This is the "Claude Code mtime-watch" pattern.

        Safe to call cheaply on every read because `os.stat` is a few
        microseconds and we only actually re-parse when something
        changed. Zero network, zero allocation in the common path.
        """
        path = self._pool_path(*key)
        if not path.exists():
            # Race: someone deleted the file since our last load. Drop
            # the cached copy so the next access goes through get_pool's
            # "no credentials" path rather than silently returning stale
            # data.
            self._pools.pop(key, None)
            self._fstat.pop(key, None)
            return
        try:
            st = path.stat()
        except FileNotFoundError:
            self._pools.pop(key, None)
            self._fstat.pop(key, None)
            return
        prev = self._fstat.get(key)
        if prev is None or prev != (st.st_mtime, st.st_size):
            loaded = self._load_from_disk(key)
            if loaded is not None:
                self._pools[key] = loaded


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_store: Optional[AuthStore] = None
_store_lock = threading.Lock()


def get_store() -> AuthStore:
    """Return the process-wide :class:`AuthStore`. First call creates it."""
    global _store
    with _store_lock:
        if _store is None:
            _store = AuthStore()
        return _store


def set_store_for_testing(store: Optional[AuthStore]) -> None:
    """Override / clear the singleton; used only by tests."""
    global _store
    with _store_lock:
        _store = store


__all__ = [
    "AuthStore", "get_store", "set_store_for_testing",
    "DEFAULT_ROOT", "FILE_LOCK_TIMEOUT_SECONDS", "MTIME_POLL_INTERVAL",
]
