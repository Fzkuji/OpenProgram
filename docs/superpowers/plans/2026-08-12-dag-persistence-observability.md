# DAG Persistence Observability Task Brief and Ledger

## Contract

- Design: `docs/reference/design/runtime/dag/persistence-observability.html`.
- Base: `4f8649d1`.
- Production: `openprogram/agentic_programming/function.py`.
- Preserve best-effort execution semantics; log entry/exit phase, node id, exception type, and traceback locally without logging args/output.
- Cover sync, async, and `traced` through the two shared persistence helpers.
- Exclude schema, SessionStore, retries, new events, Windows, credential stores, orphan files, and unrelated findings.

## Full gate

```text
pytest -q tests/agentic_programming/test_function_dag_persistence_observability.py tests/agentic_programming/test_function_dag_exit_node.py tests/agentic_programming/test_exec_breakdown.py tests/agentic_programming/test_ask_user_dag.py
ruff check openprogram/agentic_programming/function.py tests/agentic_programming/test_function_dag_persistence_observability.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
git status --short
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `4f8649d1` |
| Design | `a271dbb9` (`docs(dag): define persistence diagnostics contract`) |
| RED | 3 failed, 1 passed: entry, async exit, and traced exit persistence failures produced no diagnostics |
| GREEN | Final affected coverage: 38 passed, including diagnostic shape, hidden/no-store, BaseException cleanup, runtime aliases, and close-error priority |
| Specification review | PASS; shared helper contract and independent sync/async/traced probes passed |
| Quality review | PASS at `e9f4f7a6`; context cleanup, runtime aliases and active-abort priority findings repaired |
| Full gate | 38 passed; Ruff passed; docs 461 pages; 0 broken links; `git diff --check` passed |
| Final implementation | `74a0bbbc`, `ccb19f24`, `95772852`, `e9f4f7a6` |
