# Record/Replay Reliability Task Brief and Ledger

## Contract

- Approved design: `docs/reference/design/providers/record-replay.html`.
- Base commit: `990bfe36e625579443e1cb01a1b3266c8dbd0e87`.
- Production files: `openprogram/providers/recording.py`, `openprogram/providers/replay.py`, `openprogram/providers/__init__.py`.
- Test files: `tests/providers/test_record_replay.py`, `tests/providers/test_record_replay_registry.py`.
- Public-entry RED cases:
  - provider exception, task cancellation, and consumer `aclose()` leave a parseable terminal call and close the source generator;
  - invalid startup record/replay configuration does not abort package import, does not resolve credentials, and cannot fall back to a live provider;
  - replay mismatch does not consume the recorded call, while a matched interrupted call consumes exactly once.
- Compatibility: old v1 `call_end` rows without `outcome` remain complete; existing constructors, exception attributes, normal recordings, and strict-offline behavior remain valid.
- Security/privacy: activation failure is fail-closed; no credential resolution or network fallback; terminal metadata contains only a fixed outcome enum and no exception text.
- Cancellation/concurrency: preserve `CancelledError` and `GeneratorExit`; always close the wrapped source; one terminal row per call.
- Exclusions: structured-output retry budgeting, O(N^2) indexing, additional redaction fields, arbitrary replay path policy, Windows work, Web UI, tool/MCP recording, and unrelated audit findings.

## Full gate manifest

```text
pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py tests/providers/test_record_replay_cli.py tests/unit/test_usage_stream_chokepoint.py
ruff check openprogram/providers/recording.py openprogram/providers/replay.py openprogram/providers/__init__.py tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py
python -m tools.docs_site.build
python -m tools.docs_site.checklinks
git diff --check
git status --short
```

## Ledger

| Evidence | Result |
|---|---|
| Base | `990bfe36e625579443e1cb01a1b3266c8dbd0e87` |
| Design revision | Pending commit |
| RED | Pending |
| GREEN | Pending |
| Affected verification | Pending |
| Specification review | Pending |
| Quality review | Pending |
| Full gate | Pending |
| Final implementation commit | Pending |

