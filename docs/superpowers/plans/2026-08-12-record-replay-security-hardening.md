# Record Replay Security Hardening Task Brief and Ledger

## Contract

- Design: `docs/reference/design/providers/record-replay.html` §08 and test matrix.
- Base: `ccce0500`.
- Tighten pre-existing recording directories to POSIX 0700 before writing.
- Redact compound field names ending in token/key/secret/password while preserving non-secret counters.
- Managed replay may repair file permissions; explicit external replay must validate 0600 without changing mode.
- Preserve selector support, strict parser, recording lifecycle, concurrency, and replay matching.
- Keep the documented cross-process-safe O(n) call-index allocation; no sidecar counter without an atomic crash-recovery contract.
- Exclude Windows, credential stores, orphan files, and unrelated findings.

## Verification gate

```text
pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_cli.py tests/providers/test_record_replay_registry.py
ruff check openprogram/providers/recording.py openprogram/providers/replay.py tests/providers/test_record_replay.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `ccce0500` |
| Design | pending |
| RED | pending |
| GREEN | pending |
| Specification review | pending |
| Quality review | pending |
| Full gate | pending |
| Final implementation | pending |
