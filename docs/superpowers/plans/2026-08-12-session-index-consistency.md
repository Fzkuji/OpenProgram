# Session Index Consistency Task Brief and Ledger

## Contract

- Approved design: `docs/reference/design/runtime/session/index-consistency.html`.
- Base commit: `e831d0f0`.
- Production files: `openprogram/store/session/session_store.py`, `openprogram/store/session/session_node_writer.py`.
- Public tests: metadata-only updates preserve `updated_at` and ordering; append advances meta/index together; list rows are snapshots; an update during an older disk write remains dirty and reaches the next flush; concurrent update/list/flush leaves parseable, current JSON.
- Concurrency: registry dict, dirty state, and timer share one state lock; physical writes are ordered separately; no registry file I/O while holding the state lock.
- Compatibility: index schema, list filters/order, caller-supplied creation timestamps, and atomic file replacement stay unchanged.
- Exclusions: DAG node concurrency, `mark_merged` transaction redesign, project locations, Windows, credential stores, orphan files, auth work, and unrelated audit findings.

## Full gate manifest

```text
pytest -q tests/unit/test_session_index_consistency.py tests/unit/test_session_cache_lru.py tests/unit/test_session_branch_consistency.py tests/unit/test_archive_agent.py tests/unit/test_memory_written_marker.py tests/unit/test_list_agents.py
ruff check openprogram/store/session/session_store.py openprogram/store/session/session_node_writer.py tests/unit/test_session_index_consistency.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
git status --short
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `e831d0f0` |
| Design | `0a66a3cc` (`docs(session): define index consistency contract`) |
| RED | `tests/unit/test_session_index_consistency.py`: 3 failed; meta timestamp overwritten, returned row mutated registry, and an in-flight old save cleared newer dirty state |
| GREEN | `tests/unit/test_session_index_consistency.py`: 5 passed, including ordered concurrent direct saves |
| Affected verification | 103 passed; scoped Ruff passed; `git diff --check` passed |
| Specification review | Pending |
| Quality review | Pending |
| Full gate | Pending |
| Final implementation | Pending |
