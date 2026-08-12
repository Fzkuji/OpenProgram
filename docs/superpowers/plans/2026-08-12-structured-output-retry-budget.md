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
pytest -q tests/agentic_programming/test_runtime_structured_output.py tests/agentic_programming/test_runtime_errors.py
ruff check openprogram/agentic_programming/runtime.py tests/agentic_programming/test_runtime_structured_output.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `a2ba5d49` |
| Design | pending |
| RED | pending |
| GREEN | pending |
| Specification review | pending |
| Quality review | pending |
| Full gate | pending |
| Final implementation | pending |
