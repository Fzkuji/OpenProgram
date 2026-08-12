# Session Index Consistency Task Brief and Ledger

## Contract

- Approved design: `docs/reference/design/runtime/session/index-consistency.html`.
- Base commit: `e831d0f0`.
- Production file: `openprogram/store/session/session_store.py`.
- Public tests: metadata-only updates preserve `updated_at` and ordering; append advances meta/index together; list rows are snapshots; an update during an older disk write remains dirty and reaches the next flush; concurrent update/list/flush leaves parseable, current JSON.
- Concurrency: registry dict, dirty state, and timer share one state lock; physical writes are ordered separately; no registry file I/O while holding the state lock.
- Compatibility: index schema, list filters/order, caller-supplied creation timestamps, and atomic file replacement stay unchanged.
- Exclusions: DAG node concurrency, `mark_merged` transaction redesign, project locations, Windows, credential stores, orphan files, auth work, and unrelated audit findings.

## Full gate manifest

```text
pytest -q tests/unit/test_session_index_consistency.py tests/unit/test_session_store.py tests/unit/test_session_cache_lru.py tests/unit/test_session_branch_consistency.py tests/unit/test_session_index_registry.py
ruff check openprogram/store/session/session_store.py tests/unit/test_session_index_consistency.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
git status --short
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `e831d0f0` |
| Design | Pending commit |
| RED | Pending |
| GREEN | Pending |
| Affected verification | Pending |
| Specification review | Pending |
| Quality review | Pending |
| Full gate | Pending |
| Final implementation | Pending |
