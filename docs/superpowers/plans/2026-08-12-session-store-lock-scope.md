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
| GREEN | `5236feab`; 30 targeted tests passed and unrelated sessions complete during blocked rebuild/delete/location publish |
| Specification review | PASS at `5236feab`; state/I/O separation, per-session serialization, location publish order, compatibility, and invalid IDs verified |
| Quality review | PASS at `5236feab`; lock order, failure recovery, same-session interleavings, 8-session LRU stress, and invalid IDs verified |
| Full gate | 3739 passed, 13 skipped, 2 deselected, 1 xfailed; full Ruff passed; Web build and 22 checks passed; docs 526 pages and link check passed; diff check passed |
| Baseline collection repair | Reused existing `8220fd4f` as `62ed7216` to remove retired `test_framework` integration coverage |
| Baseline lint repair | Reused existing `a8c2448d` and `49b0b174` as `4f80c424`, `6554e839`; 88 affected tests passed, 1 skipped |
| Final implementation | `3dc3145c`, `5236feab` |
