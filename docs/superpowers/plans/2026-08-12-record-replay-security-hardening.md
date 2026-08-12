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
| Design | `a499fc86` (`docs(providers): define record replay security hardening`) |
| RED | 3 failed, 1 passed: compound secret fields, existing managed directory mode, and external replay chmod behavior violated the contract |
| GREEN | 60 passed across record/replay, CLI, registry, and adversarial permission/symlink cases |
| Specification review | PASS at `6172411b`; managed/external provenance, redaction, strict replay, and compatibility verified |
| Quality review | PASS at `6172411b`; direct, dotdot, case-alias, nested-symlink, external-target, and replay no-mutation probes verified |
| Full gate | 60 passed; Ruff passed; docs 470 pages; 0 broken links; diff check passed |
| Final implementation | `fb9c159d`, `8d83784b`, `adec27c1`, `e0b70288`, `3567f427`, `cfa03f29`, `76173db5`, `600d0ed9`, `6172411b` |
