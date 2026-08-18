# Structured Output Retry Budget Task Brief and Ledger

## Contract

- Design: `docs/reference/design/providers/json-schema-structured-output.html`.
- Base: `a2ba5d49`.
- Production: `openprogram/agentic_programming/runtime.py`.
- Count the initial provider call, semantic repair calls, and transport retries against one `max_retries` budget in both `exec` and `async_exec`.
- Keep `max_validation_retries` as the independent cap on semantic repairs; emit a retry event only when a repair call will start.
- Preserve provider-internal transport retry, deadline, error classification, cancellation, and ordinary unstructured output behavior.
- Exclude Windows, OS credential stores, orphan files, and unrelated audit findings.

## Verification gate

```text
pytest -q tests/agentic_programming/test_runtime_structured_output.py tests/unit/test_permanent_error_retryable.py tests/unit/test_error_taxonomy.py tests/unit/test_exec_timeout_code.py
ruff check openprogram/agentic_programming/runtime.py tests/agentic_programming/test_runtime_structured_output.py
python -m scripts.docs_site.build
python -m scripts.docs_site.checklinks
git diff --check
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `a2ba5d49` |
| Design | `7e6236ad` (`docs(runtime): define shared structured retry budget`) |
| RED | 2 failed, 1 passed: sync and async `max_retries=1` both issued an extra semantic repair call |
| GREEN | Exact manifest 28 passed; full agentic-programming regression 142 passed |
| Specification review | PASS at `8c68f3a1`; shared budgets, deadline, cancellation, cleanup, and sync/async parity verified |
| Quality review | PASS at `8c68f3a1`; adversarial mixed validation/transport/deadline/cancellation sequences verified |
| Full gate | 142 agentic-programming tests passed; Ruff passed; docs 466 pages; 0 broken links; diff check passed |
| Final implementation | `cf2dedae`, `38045c75`, `0927721f`, `f1adf56b`, `8c68f3a1` |
