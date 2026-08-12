# Provider Metadata Diagnostics Task Brief and Ledger

## Contract

- Design: `docs/reference/design/providers/metadata-load-diagnostics.html`.
- Base: `be56d0dd`.
- Add local warnings for unreadable, malformed, or wrong-shaped `provider.json` and `models_dev.json` files.
- Preserve empty fallback behavior and keep missing files silent.
- Do not log file content, exception messages, HTTP bodies, or credentials.
- Reuse one shared `provider.json` parser for endpoint lookup and shipped-provider enumeration.
- Exclude the orphan `_provider_meta.py`, Windows, credential stores, and unrelated provider/auth work.

## Verification gate

```text
pytest -q tests/providers/test_provider_metadata_diagnostics.py tests/providers/test_models_dev_cache.py tests/unit/test_models_dev_disk_cache.py tests/providers/test_registry_from_config.py
ruff check openprogram/providers/metadata.py openprogram/providers/sources/models_dev.py tests/providers/test_provider_metadata_diagnostics.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `be56d0dd` |
| Design | `b5deec5e` (`docs(providers): define metadata load diagnostics`) |
| RED | 4 failed, 2 passed: malformed and wrong-shaped provider/cache metadata lacked warnings |
| GREEN | 36 passed, including invalid UTF-8, delete race, exception-message redaction, and unresolved cache path |
| Specification review | PASS at `c73f2de6`; all metadata fallback and log-shape contracts verified |
| Quality review | PASS at `c73f2de6`; formatted-log probes found no content or exception-message disclosure |
| Full gate | 36 passed; Ruff passed; docs 468 pages; 0 broken links; diff check passed |
| Final implementation | `758fead4`, `c73f2de6` |
