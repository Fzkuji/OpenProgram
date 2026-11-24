"""
The credential store: one ordered credential list per provider.

Each provider's list is one file, ``<state>/auth/<provider>/credentials.json``
(:class:`~openprogram.models.auth.types.CredentialPool`), written whole and
atomically through the private-file seam (``credentials/io.py``, which
publishes with ``filesystem.replace_file``). The store keeps the parsed
lists in memory, re-reads a file another process changed (byte revision),
serialises writes per process and per file, and fans an
:class:`AuthEvent` out to subscribers on every change.

An API key is stored once, for the company that issued it
(:mod:`.company_keys`, ``<state>/auth/_keys/<company>.json``), and is a row
of every way of the company that takes a key. A key held in an environment
variable the provider reads (``login_method_registry.env_vars``) is a row
too, marked ``source="env"`` and read-only: it is read from the environment
when it is used and never written. Both keep their place in the order and
their state in the way's file as a row without a secret, so they can be
moved like any other, and a way a key does not work on marks only its own row.

Before the first read the store carries the old layout (one file per
account plus ``_active`` / ``_rotation`` / ``_account_priority`` side files)
over once (:mod:`.carry_over`), and repairs once what an earlier carry-over
wrote (:func:`.carry_over.repair`).

Reading a provider that has no folder takes no lock and creates nothing:
only a write creates the folder (0700) and its lock files, so listing the
whole catalogue leaves ``auth/`` as it was.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .types import (
    AuthCorruptCredentialError,
    AuthEvent,
    AuthEventListener,
    AuthEventType,
    Credential,
    CredentialData,
    CredentialPool,
    _new_id,
)
import logging

_log = logging.getLogger(__name__)

from .company_keys import COMPANY_MARK, KEYS_DIR

FILE_NAME = "credentials.json"
USAGE_FILE_NAME = "usage.json"

#: What a :meth:`AuthStore.mutate` change returns to save nothing.
UNCHANGED = object()

# How long a write waits for another process's lock on the same file.
FILE_LOCK_TIMEOUT_SECONDS = 15.0


def default_root() -> Path:
    """The active profile's state dir. Credentials belong to a profile like
    everything else, so ``--profile dev`` never reads or refreshes the
    default profile's logins."""
    from openprogram.state.paths import get_state_dir

    return get_state_dir()


def _clear_runtime_credential_caches() -> None:
    """Drop process-local secrets a removed credential could still feed
    (a ``credential_process`` token cached for its TTL)."""
    from openprogram.models.auth.methods.credential_process import clear_token_cache

    clear_token_cache()


def canonical_provider(provider_id: str) -> str:
    """The provider id a list is stored under (aliases resolved)."""
    try:
        from openprogram.models.auth.account.aliases import resolve

        return resolve(provider_id)
    except Exception:
        return provider_id


def shared_rows(provider_id: str, stored: list[Credential],
                keys: list[dict] = ()) -> list[Credential]:
    """``stored`` with the company's keys and the provider's set environment
    variables as rows.

    A company key (``company_keys.py``) is a row of every way that takes a
    key: a stored row of it keeps this way's place and state and gets its
    secret from ``keys``; a key without one is added at the end; a row whose
    key is gone is left out. A stored env row whose variable is unset is left
    out; a set variable without a stored row is added at the end. A variable
    that holds the same key as a stored row or a company key is left out too:
    that row is the key, and it keeps working when the variable is not set
    (one row per key, decided here at read time)."""
    import hmac

    from openprogram.models.auth.login.login_method_registry import env_vars

    from .company_keys import COMPANY_MARK

    by_id = {k["id"]: k for k in keys}
    names = [n for n in env_vars(provider_id) if "OAUTH" not in n.upper()]
    kept = [(c.payload.auth_value or "").strip().encode() for c in stored
            if c.source != "env" and not c.metadata.get(COMPANY_MARK)
            and c.kind == "api_key" and (c.payload.auth_value or "").strip()]
    kept += [(k.get("value") or "").strip().encode() for k in keys]

    def _held_by_a_stored_row(name: str) -> bool:
        value = (os.environ.get(name) or "").strip().encode()
        return any(hmac.compare_digest(value, k) for k in kept)

    present = {n for n in names if (os.environ.get(n) or "").strip() and not _held_by_a_stored_row(n)}
    out: list[Credential] = []
    seen: set[str] = set()
    placed: set[str] = set()
    for cred in stored:
        if cred.source == "env":
            name = cred.metadata.get("env_var") or ""
            if name not in present or name in seen:
                continue
            seen.add(name)
        elif cred.metadata.get(COMPANY_MARK):
            entry = by_id.get(cred.id)
            if entry is None or cred.id in placed:
                continue
            placed.add(cred.id)
            cred = _with_key(cred, entry)
        out.append(cred)
    for entry in keys:
        if entry["id"] not in placed:
            placed.add(entry["id"])
            out.append(company_credential(provider_id, entry))
    for name in names:
        if name in present and name not in seen:
            seen.add(name)
            out.append(env_credential(provider_id, name))
    return out


def _with_key(row: Credential, entry: dict) -> Credential:
    """A way's row of a company key, with the key's secret and name."""
    row = Credential.from_dict(row.to_dict())
    row.kind = "api_key"
    row.payload.auth_value = entry.get("value") or ""
    row.label = entry.get("label") or ""
    row.source = entry.get("source") or "web"
    return row


def company_credential(provider_id: str, entry: dict) -> Credential:
    """A way's row of a company key it has no stored place for yet."""
    from .company_keys import COMPANY_MARK, company_for

    return Credential(
        provider_id=provider_id,
        kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=entry.get("value") or ""),
        id=entry["id"],
        label=entry.get("label") or "",
        source=entry.get("source") or "web",
        metadata={COMPANY_MARK: company_for(provider_id)},
        created_at_ms=int(entry.get("created_at_ms") or 0),
        updated_at_ms=int(entry.get("updated_at_ms") or 0),
    )


def env_credential(provider_id: str, name: str) -> Credential:
    """The row of an environment variable's key (its value is read at use)."""
    return Credential(
        provider_id=provider_id,
        kind="api_key",
        payload=CredentialData(kind="api_key"),
        id=f"env:{name}",
        source="env",
        read_only=True,
        metadata={"env_var": name},
        created_at_ms=0,
        updated_at_ms=0,
    )


class AuthStore:
    """In-memory + on-disk credential lists, one per provider."""

    def __init__(self, root: Optional[Path] = None, *, carry_over: bool = True) -> None:
        """``carry_over=False`` opens another instance's store only to read
        it, leaving its old layout for that instance to carry over."""
        self._root = root or default_root()
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if carry_over:
            try:
                from .carry_over import carry_over as _carry_over, repair

                _carry_over(self.base_dir(), state_root=self._root)
                repair(self.base_dir(), state_root=self._root)
            except Exception:
                # The old files stay as they were; the next start tries again.
                _log.warning("credential carry-over failed under %s", self._root, exc_info=True)
        self._pools: dict[str, CredentialPool] = {}
        self._revisions: dict[str, str] = {}
        self._usage_revisions: dict[str, str] = {}
        self._sync_lock = threading.RLock()
        self._async_locks: dict[str, asyncio.Lock] = {}
        self._listeners: list[AuthEventListener] = []
        if carry_over:
            try:
                from .key_move import move_keys_to_companies

                move_keys_to_companies(self)
            except Exception:
                _log.warning("moving keys to their companies failed under %s", self._root, exc_info=True)

    # -- configuration --------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    def base_dir(self) -> Path:
        return self._root / "auth"

    def pool_file(self, provider_id: str) -> Path:
        """The file that holds ``provider_id``'s list; the refresh lock
        (:mod:`openprogram.models.auth.refresh_lock`) sits beside it."""
        return self.base_dir() / canonical_provider(provider_id) / FILE_NAME

    # -- listeners ------------------------------------------------------------

    def subscribe(self, listener: AuthEventListener):
        with self._sync_lock:
            self._listeners.append(listener)

        def _unsub():
            with self._sync_lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return _unsub

    def emit(self, event: AuthEvent) -> None:
        for lst in list(self._listeners):
            try:
                lst(event)
            except Exception:
                # Listeners are advisory; the change already happened.
                _log.warning("auth event listener failed on %s for %s",
                             event.type, event.provider_id, exc_info=True)

    def changed(self, provider_id: str, **detail) -> None:
        """Tell subscribers that ``provider_id``'s list changed."""
        self.emit(AuthEvent(type=AuthEventType.CREDENTIALS_CHANGED,
                            provider_id=canonical_provider(provider_id), detail=detail))

    # -- reads ----------------------------------------------------------------

    def stored(self, provider_id: str) -> CredentialPool:
        """The list as stored (no environment rows); empty when there is none."""
        provider_id = canonical_provider(provider_id)
        with self._sync_lock:
            self._reload_if_disk_changed(provider_id)
            pool = self._pools.get(provider_id)
            if pool is None:
                pool = self._load(provider_id) or CredentialPool(provider_id=provider_id)
                self._overlay_usage(pool)
                self._pools[provider_id] = pool
            return pool

    def get(self, provider_id: str) -> CredentialPool:
        """The list a request chooses from: stored rows and environment rows,
        in order. A snapshot: change the list through :meth:`mutate`."""
        stored = self.stored(provider_id)
        rows = shared_rows(stored.provider_id, stored.credentials, self._keys_of(stored.provider_id))
        if [c.id for c in rows] == [c.id for c in stored.credentials] \
                and not any(c.metadata.get(COMPANY_MARK) for c in rows):
            return stored
        view = CredentialPool(provider_id=stored.provider_id, strategy=stored.strategy,
                              credentials=rows, cursor=stored.cursor)
        return view

    def find(self, provider_id: str, credential_id: str) -> Optional[Credential]:
        return self.get(provider_id).find(credential_id)

    def has_credentials(self, provider_id: str) -> bool:
        return bool(self.get(provider_id).credentials)

    def providers(self) -> list[str]:
        """Every provider with a stored list or a set environment key."""
        out: set[str] = set()
        base = self.base_dir()
        if base.exists():
            for d in base.iterdir():
                if d.is_dir() and (d / FILE_NAME).exists():
                    out.add(d.name)
        from openprogram.models.auth.login.login_method_registry import env_vars, listed_providers

        for provider in listed_providers():
            if provider not in out and any((os.environ.get(n) or "").strip()
                                           for n in env_vars(provider) if "OAUTH" not in n.upper()):
                out.add(provider)
        from .company_keys import key_ways
        folder = base / KEYS_DIR
        if folder.is_dir():
            for f in folder.glob("*.json"):
                if self.company_keys(f.stem):
                    out.update(key_ways(f.stem))
        return sorted(out)

    def list_pools(self) -> list[CredentialPool]:
        """Every provider's non-empty stored list (environment rows included)."""
        pools = [self.get(p) for p in self.providers()]
        return [p for p in pools if p.credentials]

    # -- writes ---------------------------------------------------------------

    def mutate(self, provider_id: str, change, *, forget: frozenset[str] = frozenset()):
        """Apply ``change(pool)`` to the list as it is on disk now and save it.

        The file's lock is held from the read to the write, so a change made
        by another process in between (the CLI adding a key, another worker
        saving a refreshed token) is never overwritten: ``change`` always
        sees the current file. ``pool`` includes the environment rows; they
        keep only their place and state. ``change`` returns what
        :meth:`mutate` returns; returning :data:`UNCHANGED` writes nothing
        (and :meth:`mutate` returns ``None``). ``forget`` names environment
        rows to drop from the file, which are otherwise kept while unlisted."""
        provider_id = canonical_provider(provider_id)
        path = self.pool_file(provider_id)
        from openprogram.models.auth.credentials import _private_file_lock

        self._private_folder(path)
        with self._sync_lock, _private_file_lock(path, root=self._root,
                                                 timeout=FILE_LOCK_TIMEOUT_SECONDS):
            fresh = self._load(provider_id) or CredentialPool(provider_id=provider_id)
            self._overlay_usage(fresh)
            keys = self._keys_of(provider_id)
            view = CredentialPool(provider_id=provider_id, strategy=fresh.strategy,
                                  credentials=shared_rows(provider_id, fresh.credentials, keys),
                                  cursor=fresh.cursor)
            result = change(view)
            if result is UNCHANGED:
                self._pools[provider_id] = fresh
                return None
            self._save_key_edits(provider_id, keys, view)
            merged = _with_stored_rows(fresh, view, forget)
            merged.provider_id = provider_id
            self._persist(merged)
            saved = CredentialPool.from_dict(merged.to_dict())
            self._overlay_usage(saved)
            self._pools[provider_id] = saved
            return result

    def add_credential(self, cred: Credential) -> Credential:
        """Add ``cred`` at the end, or renew the row it repeats.

        The same API key pasted again keeps its one row. A sign-in whose identity (``account_id`` or ``email``) matches a
        stored row of the same provider replaces that row's secret and keeps
        its place, name and id: signing in again never adds a second row.
        Returns the row as stored."""
        cred.provider_id = canonical_provider(cred.provider_id)
        if _is_company_key(cred):
            return self._add_company_key_row(cred)

        def _add(pool: CredentialPool) -> Credential:
            same = _same_account(pool, cred)
            if same is None:
                if pool.find(cred.id) is not None:
                    cred.id = _new_id("cred")
                pool.credentials.append(cred)
                return cred
            same.payload = cred.payload
            same.kind = cred.kind
            same.identity = {**same.identity, **cred.identity}
            same.metadata = {**same.metadata, **cred.metadata}
            same.read_only = cred.read_only
            same.status = "ready"
            same.cooldown_until_ms = 0
            same.blocked_models = {}
            same.last_error = None
            same.strikes = 0
            same.updated_at_ms = int(time.time() * 1000)
            return same

        kept = self.mutate(cred.provider_id, _add)
        self._drop_old_generations(cred.provider_id)
        self.emit(AuthEvent(type=AuthEventType.POOL_MEMBER_ADDED, provider_id=cred.provider_id,
                            credential_id=kept.id, detail={"source": kept.source, "kind": kept.kind}))
        self.changed(cred.provider_id)
        return kept

    def remove_credential(self, provider_id: str, credential_id: str) -> bool:
        """Remove one row. The file stays (it keeps the strategy); the
        replaced generation (``.bak``) goes, so the secret leaves the disk."""

        row = self.find(provider_id, credential_id)
        if row is not None and row.metadata.get(COMPANY_MARK):
            return self.remove_company_key(row.metadata[COMPANY_MARK], credential_id)

        def _remove(pool: CredentialPool):
            kept = [c for c in pool.credentials if c.id != credential_id]
            if len(kept) == len(pool.credentials):
                return UNCHANGED
            pool.credentials = kept
            return True

        if not self.mutate(provider_id, _remove):
            return False
        self._drop_old_generations(provider_id)
        _clear_runtime_credential_caches()
        self.emit(AuthEvent(type=AuthEventType.POOL_MEMBER_REMOVED,
                            provider_id=canonical_provider(provider_id), credential_id=credential_id))
        self.changed(provider_id)
        return True

    # -- company keys ---------------------------------------------------------

    def keys_file(self, company: str) -> Path:
        from .company_keys import keys_path
        return keys_path(self.base_dir(), company)

    def company_keys(self, company: str) -> list[dict]:
        """``company``'s keys, secrets included (``company_keys.py``)."""
        path = self.keys_file(company)
        if not path.exists():
            return []
        from openprogram.models.auth.credentials import _private_file_lock, _read_private_bytes
        from .company_keys import parse

        with _private_file_lock(path, root=self._root, timeout=FILE_LOCK_TIMEOUT_SECONDS):
            raw = _read_private_bytes(path, root=self._root)
        return parse(raw, path)

    def _keys_of(self, provider_id: str) -> list[dict]:
        from .company_keys import company_for, takes_company_keys
        if not takes_company_keys(provider_id):
            return []
        return self.company_keys(company_for(provider_id))

    def update_company_keys(self, company: str, change) -> object:
        """Apply ``change(keys)`` to ``company``'s keys as they are on disk
        now and save them; ``change`` returns what this returns, or
        :data:`UNCHANGED` to write nothing."""
        path = self.keys_file(company)
        from openprogram.models.auth.credentials import _private_atomic_update
        from .company_keys import dump, parse

        box: dict = {}

        def _update(raw):
            keys = parse(raw, path)
            box["result"] = change(keys)
            return raw if box["result"] is UNCHANGED and raw is not None else dump(keys)

        self._private_folder(path)
        with self._sync_lock:
            _private_atomic_update(path, _update, root=self._root, lock_timeout=FILE_LOCK_TIMEOUT_SECONDS)
        result = box.get("result")
        return None if result is UNCHANGED else result

    def add_company_key(self, company: str, value: str, *, label: str = "", source: str = "web",
                        key_id: str = "", first: bool = False) -> str:
        """Store ``value`` for ``company`` (the same key again keeps its one
        entry); returns its id. ``first`` puts it before the company's
        other keys."""
        from .company_keys import new_entry, same_value

        found: dict = {}

        def _add(keys: list[dict]):
            same = next((k for k in keys if same_value(k, value)), None)
            if same is not None:
                found["id"] = same["id"]
                moved = first and keys[0] is not same
                if moved:
                    keys.remove(same)
                    keys.insert(0, same)
                if label and not same.get("label"):
                    same["label"] = label
                    return same["id"]
                return same["id"] if moved else UNCHANGED
            taken = {k["id"] for k in keys}
            kid = key_id if key_id and key_id not in taken else _new_id("cred")
            keys.insert(0 if first else len(keys), new_entry(kid, value, label=label, source=source))
            return kid

        kid = self.update_company_keys(company, _add) or found["id"]
        self._drop_key_generations(company)
        self._company_changed(company, kid, AuthEventType.POOL_MEMBER_ADDED)
        return kid

    def remove_company_key(self, company: str, key_id: str) -> bool:
        """Remove one of ``company``'s keys: it leaves every way at once."""

        def _remove(keys: list[dict]):
            kept = [k for k in keys if k["id"] != key_id]
            if len(kept) == len(keys):
                return UNCHANGED
            keys[:] = kept
            return True

        if not self.keys_file(company).exists() or not self.update_company_keys(company, _remove):
            return False
        self._drop_key_generations(company)
        from .company_keys import key_ways
        for way in key_ways(company):
            # A way's older copies may predate the company file and hold the key.
            if self.pool_file(way).parent.is_dir():
                self._drop_old_generations(way)
        _clear_runtime_credential_caches()
        self._company_changed(company, key_id, AuthEventType.POOL_MEMBER_REMOVED)
        return True

    def _company_changed(self, company: str, key_id: str, kind: AuthEventType) -> None:
        from .company_keys import key_ways
        for way in key_ways(company) or [company]:
            self._pools.pop(canonical_provider(way), None)
            self.emit(AuthEvent(type=kind, provider_id=canonical_provider(way), credential_id=key_id,
                                detail={"kind": "api_key", "company": company}))
            self.changed(way)

    def _drop_key_generations(self, company: str) -> None:
        """A removed or replaced key must not stay in the keys file's older copies."""
        for old in _generations(self.keys_file(company))[1:]:
            old.unlink(missing_ok=True)

    def _add_company_key_row(self, cred: Credential) -> Credential:
        """``add_credential`` for an API key: stored for the company, its row
        on this way renewed (a key added again starts over here)."""
        from .company_keys import company_for
        company = company_for(cred.provider_id)
        kid = self.add_company_key(company, cred.payload.auth_value, label=cred.label,
                                   source=cred.source, key_id=cred.id)

        def _renew(pool: CredentialPool):
            row = pool.find(kid)
            if row is None:
                return UNCHANGED
            row.status = "ready"
            row.cooldown_until_ms = 0
            row.blocked_models = {}
            row.last_error = None
            row.strikes = 0
            row.enabled = True
            return True

        self.mutate(cred.provider_id, _renew)
        return self.find(cred.provider_id, kid) or cred

    def _save_key_edits(self, provider_id: str, keys: list[dict], view: CredentialPool) -> None:
        """A company key renamed or replaced on a way is renamed or replaced
        for the company (the name and the secret are the key's, not the way's)."""
        by_id = {k["id"]: k for k in keys}
        edits = {}
        for row in view.credentials:
            entry = by_id.get(row.id) if row.metadata.get(COMPANY_MARK) else None
            if entry is None:
                continue
            value = row.payload.auth_value or ""
            if (row.label or "") != (entry.get("label") or "") or (value and value != entry.get("value")):
                edits[row.id] = (row.label or "", value or entry.get("value"))
        if not edits:
            return
        company = next(r.metadata[COMPANY_MARK] for r in view.credentials if r.id in edits)

        def _edit(entries: list[dict]):
            now = int(time.time() * 1000)
            for e in entries:
                if e["id"] in edits:
                    e["label"], e["value"] = edits[e["id"]]
                    e["updated_at_ms"] = now
            return True

        self.update_company_keys(company, _edit)
        self._drop_key_generations(company)
        from .company_keys import key_ways
        for way in key_ways(company):
            if canonical_provider(way) != provider_id:
                self._pools.pop(canonical_provider(way), None)
                self.changed(way)

    def delete_provider(self, provider_id: str) -> None:
        """Remove every stored credential of ``provider_id``, with every copy
        of them (the last good generation, set-aside unreadable copies), the
        usage counts, the lock files and the folder when that leaves it empty.
        Its company's keys are among its credentials, so they go too, from
        every way of the company."""
        provider_id = canonical_provider(provider_id)
        from .company_keys import company_for
        company = company_for(provider_id)
        for entry in self._keys_of(provider_id):
            self.remove_company_key(company, entry["id"])
        path = self.pool_file(provider_id)
        from openprogram.models.auth.credentials import _private_file_lock, _private_unlink

        with self._sync_lock:
            with _private_file_lock(path, root=self._root, timeout=FILE_LOCK_TIMEOUT_SECONDS):
                for leftover in _generations(path) + [self.usage_file(provider_id)] \
                        + _generations(self.usage_file(provider_id))[1:]:
                    _private_unlink(leftover, root=self._root, lock_timeout=FILE_LOCK_TIMEOUT_SECONDS)
                self._pools.pop(provider_id, None)
                self._revisions.pop(provider_id, None)
            folder = path.parent
            # Old per-account files a carry-over could not take yet must not
            # bring these keys back on a later run.
            from .carry_over import _read_old
            for old in list(folder.glob("*.json")) if folder.is_dir() else []:
                if _read_old(old) is not None:
                    for leftover in _generations(old):
                        leftover.unlink(missing_ok=True)
            for lock in folder.glob("*.lock"):
                lock.unlink(missing_ok=True)
            try:
                folder.rmdir()
            except OSError:
                pass  # something else lives there; it stays
        _clear_runtime_credential_caches()
        self.emit(AuthEvent(type=AuthEventType.POOL_MEMBER_REMOVED, provider_id=provider_id,
                            detail={"reason": "provider_cleared"}))
        self.changed(provider_id)

    def update(self, provider_id: str, credential_id: str, change) -> Optional[Credential]:
        """Apply ``change(cred)`` to one row of the current file and save it
        (:meth:`mutate`). An environment row that has no stored place yet
        gets one. ``None`` when there is no such row."""

        def _one(pool: CredentialPool):
            cred = pool.find(credential_id)
            if cred is None:
                return UNCHANGED
            change(cred)
            return cred

        return self.mutate(provider_id, _one)

    def drop_old_generations(self, provider_id: str) -> None:
        """Make the current file the only copy on disk: after a key was
        removed or replaced its secret must not stay in ``credentials.json.bak``
        or a set-aside ``.bad-*`` copy."""
        self._drop_old_generations(provider_id)

    def _drop_old_generations(self, provider_id: str) -> None:
        path = self.pool_file(provider_id)
        from openprogram.models.auth.credentials import _private_file_lock

        with self._sync_lock, _private_file_lock(path, root=self._root,
                                                 timeout=FILE_LOCK_TIMEOUT_SECONDS):
            for old in _generations(path)[1:]:
                old.unlink(missing_ok=True)

    # -- use counts -----------------------------------------------------------

    def usage_file(self, provider_id: str) -> Path:
        """``<provider>/usage.json``: each credential's use count, last use
        and calls today, and where ``evenly`` continues. Kept apart from
        ``credentials.json`` so a request never rewrites the secrets file."""
        return self.base_dir() / canonical_provider(provider_id) / USAGE_FILE_NAME

    def record_use(self, provider_id: str, credential_id: str, now_ms: int,
                   *, cursor: Optional[int] = None) -> None:
        """Count one request on ``credential_id`` (and move ``evenly``'s turn)."""
        path = self.usage_file(provider_id)
        day = _day(now_ms)

        def _count(raw: Optional[bytes]) -> bytes:
            doc = _usage_doc(path, raw)
            row = doc["uses"].setdefault(credential_id, {})
            row["use_count"] = int(row.get("use_count") or 0) + 1
            row["last_used_at_ms"] = now_ms
            row["today"] = (int(row.get("today") or 0) if row.get("day") == day else 0) + 1
            row["day"] = day
            if cursor is not None:
                doc["cursor"] = cursor
            return json.dumps(doc, indent=2).encode("utf-8")

        self._write_usage(provider_id, path, _count)

    def set_cursor(self, provider_id: str, cursor: int) -> None:
        path = self.usage_file(provider_id)

        def _set(raw: Optional[bytes]) -> bytes:
            doc = _usage_doc(path, raw)
            doc["cursor"] = cursor
            return json.dumps(doc, indent=2).encode("utf-8")

        self._write_usage(provider_id, path, _set)

    def _write_usage(self, provider_id: str, path: Path, updater) -> None:
        from openprogram.models.auth.credentials import _private_atomic_update

        self._private_folder(path)
        with self._sync_lock:
            _private_atomic_update(path, updater, root=self._root,
                                   lock_timeout=FILE_LOCK_TIMEOUT_SECONDS)
            self._pools.pop(canonical_provider(provider_id), None)

    def _read_usage(self, provider_id: str) -> tuple[dict, str]:
        path = self.usage_file(provider_id)
        from openprogram.models.auth.credentials import (
            _private_file_lock, _read_private_bytes, _revision,
        )

        if not path.parent.is_dir():
            # Nothing stored: no lock, so no folder is made by a read.
            return _usage_doc(path, None), _revision(None)
        with _private_file_lock(path, root=self._root, timeout=FILE_LOCK_TIMEOUT_SECONDS):
            raw = _read_private_bytes(path, root=self._root)
        return _usage_doc(path, raw), _revision(raw)

    def _overlay_usage(self, pool: CredentialPool) -> None:
        doc, revision = self._read_usage(pool.provider_id)
        self._usage_revisions[pool.provider_id] = revision
        _apply_usage(pool, doc)

    # -- locks ----------------------------------------------------------------

    def async_lock(self, provider_id: str) -> asyncio.Lock:
        """Per-provider asyncio lock refresh code holds while it spends a refresh token."""
        key = canonical_provider(provider_id)
        with self._sync_lock:
            lock = self._async_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._async_locks[key] = lock
            return lock

    # -- persistence internals -----------------------------------------------

    def _private_folder(self, path: Path) -> None:
        """``auth/`` and the provider's folder owner-only before a write."""
        from openprogram.models.auth.credentials import tighten_private_directory

        tighten_private_directory(self.base_dir())
        tighten_private_directory(path.parent)

    def _persist(self, pool: CredentialPool) -> None:
        path = self.pool_file(pool.provider_id)
        data = json.dumps(pool.to_dict(), indent=2, ensure_ascii=False)
        from openprogram.models.auth.credentials import _private_atomic_write

        result = _private_atomic_write(
            path,
            lambda handle: handle.write(data.encode("utf-8")),
            root=self._root,
            lock_timeout=FILE_LOCK_TIMEOUT_SECONDS,
        )
        self._revisions[pool.provider_id] = result.revision

    def _load(self, provider_id: str) -> Optional[CredentialPool]:
        path = self.pool_file(provider_id)
        from openprogram.models.auth.credentials import (
            _private_file_lock,
            _read_private_bytes,
            _revision,
        )

        raw_bytes = None
        if path.parent.is_dir():
            # A provider with no folder has nothing stored: no lock, no folder.
            try:
                with _private_file_lock(path, root=self._root, timeout=FILE_LOCK_TIMEOUT_SECONDS):
                    raw_bytes = _read_private_bytes(path, root=self._root)
            except FileNotFoundError:
                raw_bytes = None
        self._revisions[provider_id] = _revision(raw_bytes)
        if raw_bytes is None:
            return None
        try:
            d = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            # Unreadable is unknown, not empty: read the last good copy.
            from openprogram.state.last_good import load_json

            d = load_json(path, raw_bytes)
            if not isinstance(d, dict):
                raise AuthCorruptCredentialError(
                    f"{path} is not valid JSON: {e}", provider_id=provider_id,
                ) from e
        return CredentialPool.from_dict(d)

    def _reload_if_disk_changed(self, provider_id: str) -> None:
        from openprogram.models.auth.credentials import _revision, private_file_revision

        if not self.pool_file(provider_id).parent.is_dir():
            current = usage = _revision(None)
        else:
            current = private_file_revision(
                self.pool_file(provider_id), root=self._root,
                lock_timeout=FILE_LOCK_TIMEOUT_SECONDS,
            )
            usage = private_file_revision(
                self.usage_file(provider_id), root=self._root,
                lock_timeout=FILE_LOCK_TIMEOUT_SECONDS,
            )
        if self._revisions.get(provider_id) == current \
                and self._usage_revisions.get(provider_id) == usage:
            return
        self._pools.pop(provider_id, None)


def _is_company_key(cred: Credential) -> bool:
    """A pasted API key for an address the way decides is its company's."""
    from .company_keys import takes_company_keys
    return (cred.kind == "api_key" and cred.source != "env" and not cred.read_only
            and bool((cred.payload.auth_value or "").strip())
            and not cred.payload.base_url and not cred.payload.headers
            and not cred.metadata.get(COMPANY_MARK) and takes_company_keys(cred.provider_id))


def _same_account(pool: CredentialPool, cred: Credential) -> Optional[Credential]:
    """The stored row ``cred`` renews, if any: the same API key, or a
    sign-in of the same account."""
    if cred.source == "env":
        return None
    if cred.kind == "api_key":
        value = cred.payload.auth_value
        return next((row for row in pool.credentials if row.kind == "api_key" and row.source != "env"
                     and value and row.payload.auth_value == value), None)
    keys = [(k, cred.identity.get(k)) for k in ("account_id", "email") if cred.identity.get(k)]
    if not keys:
        return None
    for row in pool.credentials:
        if row.kind == "api_key" or row.source == "env":
            continue
        if any(row.identity.get(k) == v for k, v in keys):
            return row
    return None


def _with_stored_rows(stored: CredentialPool, view: CredentialPool,
                      forget: frozenset[str] = frozenset()) -> CredentialPool:
    """``view`` saved over ``stored``: every row ``view`` shows in its order,
    then stored env rows whose variable is unset now (kept for when it is set
    again), except the ones in ``forget``."""
    shown = {c.id for c in view.credentials}
    hidden = [c for c in stored.credentials if c.id not in shown and c.source == "env" and c.id not in forget]
    return CredentialPool(provider_id=view.provider_id, strategy=view.strategy,
                          credentials=list(view.credentials) + hidden, cursor=view.cursor)


def _generations(path: Path) -> list[Path]:
    """``path`` and every older copy of it on disk (``.bak``, ``.bad-*``)."""
    from openprogram.state.last_good import UNREADABLE_SUFFIX, backup_path

    return [path, backup_path(path), *sorted(path.parent.glob(path.name + UNREADABLE_SUFFIX + "*"))]


def _day(now_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now_ms / 1000))


def _usage_doc(path: Path, raw: Optional[bytes]) -> dict:
    """The usage file's document (``{"cursor", "uses": {id: {...}}}``);
    an unreadable one is read through its last good copy, else empty."""
    doc = None
    if raw is not None:
        from openprogram.state.last_good import load_json

        doc = load_json(path, raw)
    if not isinstance(doc, dict):
        doc = {}
    uses = doc.get("uses") if isinstance(doc.get("uses"), dict) else {}
    return {"cursor": int(doc.get("cursor") or 0), "uses": dict(uses)}


def _apply_usage(pool: CredentialPool, doc: dict) -> None:
    """Put the usage file's counts on ``pool``'s rows (a row it does not
    name keeps what the credentials file says)."""
    today = _day(int(time.time() * 1000))
    if doc.get("cursor") or doc.get("uses"):
        pool.cursor = int(doc.get("cursor") or 0)
    for cred in pool.credentials:
        row = doc["uses"].get(cred.id)
        if not isinstance(row, dict):
            continue
        cred.use_count = int(row.get("use_count") or 0)
        cred.last_used_at_ms = int(row.get("last_used_at_ms") or 0)
        cred.uses_today = int(row.get("today") or 0) if row.get("day") == today else 0


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
    "AuthStore",
    "FILE_LOCK_TIMEOUT_SECONDS",
    "FILE_NAME",
    "UNCHANGED",
    "USAGE_FILE_NAME",
    "canonical_provider",
    "company_credential",
    "default_root",
    "env_credential",
    "shared_rows",
    "get_store",
    "set_store_for_testing",
]
