# Mark Merged Atomicity Task Brief and Ledger

## Contract

- Design: `docs/reference/design/runtime/session/merged-head-atomicity.html`.
- Base: `0402ce3f`.
- Make `merged_heads` read-union-write atomic under the existing per-session index lock.
- Keep persistence outside that lock and preserve order, trimming, idempotence, and callers.
- Do not add a transaction abstraction or modify other SessionStore operations.

## Verification gate

```text
pytest -q tests/unit/test_mark_merged_atomicity.py tests/unit/test_archive_agent.py tests/unit/test_session_index_consistency.py
ruff check openprogram/store/session/session_store.py tests/unit/test_mark_merged_atomicity.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `0402ce3f` |
| Design | pending |
| RED | pending |
| GREEN | pending |
| Specification review | pending |
| Quality review | pending |
| Full gate | pending |
| Final implementation | pending |
