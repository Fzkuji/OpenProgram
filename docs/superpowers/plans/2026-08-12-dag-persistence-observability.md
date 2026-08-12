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
| Design | Pending commit |
| RED | Pending |
| GREEN | Pending |
| Specification review | Pending |
| Quality review | Pending |
| Full gate | Pending |
| Final implementation | Pending |
