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
| Design | `4df4ca98` (`docs(session): define merged head atomicity`) |
| RED | Concurrent merge lost one head; stale-writer-last and cached stale-rebuild probes reproduced disk/reload loss |
| GREEN | 36 passed; deterministic persistence interleavings preserve memory and fresh reload union |
| Specification review | PASS at `6a630607`; lock order, atomic union, snapshot timing, and lock-free state I/O verified |
| Quality review | PASS at `6a630607`; 30 rounds of 16 concurrent writers plus reader/deadlock/reload checks passed |
| Full gate | 36 passed; Ruff passed; docs 472 pages; 0 broken links; diff check passed |
| Final implementation | `1619f01c`, `74a49836`, `6a630607` |
