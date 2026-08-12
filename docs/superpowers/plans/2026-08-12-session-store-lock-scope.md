# SessionStore Lock Scope Task Brief and Ledger

## Contract

- Design: `docs/reference/design/runtime/session/store-lock-scope.html`.
- Base: `9f01e27a`.
- Keep the store-wide lock limited to in-memory cache and location-map operations.
- Serialize load/rebuild/delete/invalidate per session; sessions with different IDs must not wait for one another's filesystem I/O.
- Publish `locations.json` snapshots in order without holding the store-wide state lock during filesystem I/O.
- Preserve the existing index persistence lock order, file formats, LRU behavior, stale detection, deletion behavior, and public API.
- Exclude Windows and credential-store work.

## Verification gate

```text
pytest -q tests/unit/test_session_store_lock_scope.py tests/unit/test_mark_merged_atomicity.py tests/unit/test_session_cache_lru.py tests/unit/test_session_index_consistency.py
ruff check openprogram/store/session/session_store.py tests/unit/test_session_store_lock_scope.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `9f01e27a` |
| Design | `a12dee40` (`docs(session): define store lock scope`) |
| RED | 3 failed: blocked rebuild, recursive delete, and locations publish each blocked a cached read of another session |
| GREEN | pending |
| Specification review | pending |
| Quality review | pending |
| Full gate | pending |
| Final implementation | pending |
