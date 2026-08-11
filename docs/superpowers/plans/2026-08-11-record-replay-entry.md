# Provider Request Record/Replay Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure, deterministic CLI/config entry point that records provider traffic and replays it without credentials or network fallback.

**Architecture:** Keep the version-1 JSONL format and public provider classes, but move file coordination into a shared `RecordingSink`, install one registry-level provider transform, and select the transformed provider before credential resolution. Keep managed-recording lifecycle functions in `recording.py`; `cli.py` only declares arguments and delegates commands.

**Tech Stack:** Python 3.11+, argparse, JSONL, `os.open`/`os.write`/`fsync`, `_compat.flock`, pytest, Ruff.

## Global Constraints

- Start from `origin/main` commit `8f62da5a`; use only branch `codex/record-replay-entry-20260811` in `/private/tmp/openprogram-record-replay-entry-20260811`.
- Do not modify JSON Schema, MCP, or resource-governance files.
- Keep changes to `openprogram/cli.py` and `openprogram/config_schema.py` local to record/replay declarations and dispatch.
- `record_replay.mode` is `off`, `record`, or `replay`; changes apply on the next process start.
- Managed directories and lock files use mode `0700` and `0600`; POSIX permission verification is fail-closed.
- Redaction is unconditional and has no disabling configuration.
- Replay must parse the whole file before serving calls and must not resolve credentials, retain an original provider, or make network calls.
- Preserve version-1 constructors, properties, `call_count`, `RecordingFileError`, `ReplayMismatch`, and structurally valid legacy recordings.
- Use test-driven development for every production change and commit each task independently.

---

### Task 1: Strict recording format, redaction, and private paths

**Files:**
- Modify: `openprogram/paths.py`
- Modify: `openprogram/providers/recording.py`
- Modify: `openprogram/providers/replay.py`
- Modify: `tests/providers/test_record_replay.py`

**Interfaces:**
- Produces: `get_recordings_dir() -> Path`, `restrict_recording_file(path: Path) -> None`, `read_recording_file(path: str | Path) -> list[RecordedCall]`.
- Preserves: `RecordingProvider(provider, path)`, `ReplayProvider(path)`, `ReplayProvider.call_count`, `RecordingFileError`, and `ReplayMismatch`.

- [ ] **Step 1: Add failing parser, redaction, and permission tests**

```python
@pytest.mark.parametrize("mutation", ["event_after_end", "index_gap", "unknown_event"])
def test_strict_parser_rejects_invalid_structure(tmp_path, mutation):
    path = write_mutated_recording(tmp_path, mutation)
    with pytest.raises(RecordingFileError):
        ReplayProvider(path)

def test_redaction_covers_basic_auth_url_userinfo_and_query_secrets(tmp_path):
    path = record_one_request(tmp_path, "Basic dXNlcjpwYXNz https://u:p@host/?api_key=secret")
    assert "dXNlcjpwYXNz" not in path.read_text()
    assert "u:p" not in path.read_text()
    assert "secret" not in path.read_text()

def test_recording_file_and_directory_are_private(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path)
    recording = create_managed_recording("private")
    assert stat.S_IMODE(paths.get_recordings_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(recording.path.stat().st_mode) == 0o600
```

Construct exact JSONL fixtures for duplicate headers, orphan events, missing `call_end`, mismatched event counts, non-contiguous indexes, and unknown event classes. Assert `RecordingFileError` is raised during `ReplayProvider(path)`, before iteration.

- [ ] **Step 2: Run the new tests and verify the intended failures**

Run: `pytest -q tests/providers/test_record_replay.py`

Expected: FAIL because the current parser ignores `call_end`, unknown rows, index gaps, and permission requirements.

- [ ] **Step 3: Implement the minimum strict parser and path protection**

```python
def get_recordings_dir() -> Path:
    path = get_state_dir() / "recordings"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _restrict_to_owner(path, 0o700)
    return path

class RecordingFileError(RuntimeError):
    def __init__(self, path, reason, *, line_number=None, call_index=None):
        self.path = Path(path)
        self.reason = reason
        self.line_number = line_number
        self.call_index = call_index

def read_recording_file(path):
    rows = load_jsonl_rows(path)
    validate_header(rows[0])
    return assemble_and_validate_contiguous_calls(rows[1:])
```

Implement URL-userinfo, query-parameter, authorization-scheme, secret-field, and known-token-prefix replacement with the existing `<REDACTED>` placeholder. Add no redaction toggle and no entropy heuristic. Verify regular-file status, symlink safety, and exact POSIX `0600` before reading or appending.

- [ ] **Step 4: Run focused tests and compatibility tests**

Run: `pytest -q tests/providers/test_record_replay.py`

Expected: PASS, including existing multi-turn tool-call replay and mismatch tests.

- [ ] **Step 5: Commit Task 1**

```bash
git add openprogram/paths.py openprogram/providers/recording.py openprogram/providers/replay.py tests/providers/test_record_replay.py
git commit -m "feat(providers): harden recording file format"
```

### Task 2: Concurrent sink and registry-wide transform

**Files:**
- Modify: `openprogram/providers/recording.py`
- Modify: `openprogram/providers/api_registry.py`
- Modify: `openprogram/providers/__init__.py`
- Modify: `tests/providers/test_record_replay.py`
- Create: `tests/providers/test_record_replay_registry.py`

**Interfaces:**
- Consumes: strict format helpers and private-path checks from Task 1.
- Produces: `RecordingSink(path)`, `configure_provider_transform(transform)`, and `activate_record_replay_from_config()`.
- `RecordingProvider(provider, path_or_sink)` accepts a path or shared `RecordingSink` without changing existing path-based callers.

- [ ] **Step 1: Add failing concurrency and registry tests**

```python
def test_two_recording_providers_share_contiguous_call_indexes(tmp_path):
    sink = RecordingSink(tmp_path / "calls.jsonl")
    drain(RecordingProvider(provider_a, sink).stream("k", request_a))
    drain(RecordingProvider(provider_b, sink).stream("k", request_b))
    assert [call.call_index for call in read_recording_file(sink.path)] == [0, 1]

def test_transform_wraps_existing_and_future_registry_entries(monkeypatch):
    reset_test_registry(monkeypatch, {"a": provider_a})
    configure_provider_transform(wrap)
    register_api_provider("b", provider_b)
    assert get_api_provider("a").inner is provider_a
    assert get_api_provider("b").inner is provider_b
```

Use two provider instances and separate processes against one file; assert unique contiguous call indexes and a strict-reader success. Save and restore registry module dictionaries/globals in fixtures so the one-time transform does not leak across tests.

- [ ] **Step 2: Run the focused tests and verify failures**

Run: `pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py`

Expected: FAIL because call indexes are currently per wrapper and the registry has no transform hook.

- [ ] **Step 3: Implement `RecordingSink` and one-time transform**

```python
class RecordingSink:
    def begin_call(self, request: dict[str, object]) -> int:
        return self._append_request_with_next_index(request)
    def append_event(self, call_index: int, event_index: int, event: dict[str, object]) -> None:
        self._append_row({"type": "event", "call_index": call_index, "event_index": event_index, "event": event})
    def end_call(self, call_index: int, event_count: int) -> None:
        self._append_row({"type": "call_end", "call_index": call_index, "event_count": event_count}, fsync=True)

def configure_provider_transform(transform: Callable[[str, APIProvider], APIProvider]) -> None:
    install_once_and_rewrap_registered_providers(transform)
```

For each append, open the data file with `O_WRONLY|O_CREAT|O_APPEND` and mode `0600`, hold an exclusive sibling lock-file `flock`, loop until `os.write` consumes the complete encoded JSON line, and `fsync` on `call_end`. Allocate a call index while holding the lock by scanning request rows. Store original providers separately; transform all existing providers atomically, transform future registrations, no-op only when passed the identical callable object, and reject a different second transform.

- [ ] **Step 4: Implement startup activation without per-provider configuration**

```python
def activate_record_replay_from_config() -> None:
    setting = read_record_replay_setting()
    if setting.mode == "record":
        install_shared_recording_sink(setting.file)
    elif setting.mode == "replay":
        install_shared_replay_provider(setting.file)
```

Call activation once from `providers/__init__.py` after built-in providers register. Keep mode `off` behavior identical and avoid importing CLI modules.

- [ ] **Step 5: Run focused tests and commit Task 2**

Run: `pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py`

Expected: PASS.

```bash
git add openprogram/providers/recording.py openprogram/providers/api_registry.py openprogram/providers/__init__.py tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py
git commit -m "feat(providers): install concurrent record replay transform"
```

### Task 3: Strict offline stream selection

**Files:**
- Modify: `openprogram/providers/stream.py`
- Modify: `openprogram/providers/api_registry.py`
- Modify: `tests/unit/test_usage_stream_chokepoint.py`
- Modify: `tests/providers/test_record_replay_registry.py`

**Interfaces:**
- Consumes: transformed providers from Task 2.
- Produces: `APIProvider.requires_credentials: bool`, defaulting to `True`; `ReplayProvider.requires_credentials` returns `False`.

- [ ] **Step 1: Add failing credential-order and no-fallback tests**

```python
def test_replay_stream_skips_credential_resolution(monkeypatch):
    monkeypatch.setattr(stream_module, "resolve_api_key", fail_if_called)
    monkeypatch.setattr(stream_module, "get_api_provider", lambda api: replay)
    assert list(stream_simple(request)) == expected_events

def test_replay_provider_has_no_original_provider_or_network_fallback(tmp_path):
    replay = ReplayProvider(valid_recording(tmp_path))
    assert not hasattr(replay, "provider")
    with pytest.raises(ReplayMismatch):
        list(replay.stream("", mismatched_request))
```

Patch the credential resolver to raise if called in replay mode; invoke both `stream_simple` and `stream`. Assert malformed recordings fail during provider construction and mismatch/exhaustion never consult another provider.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `pytest -q tests/unit/test_usage_stream_chokepoint.py tests/providers/test_record_replay_registry.py`

Expected: FAIL because both stream entry points currently resolve credentials before selecting the provider.

- [ ] **Step 3: Select provider before credentials and expose capability**

```python
@property
def requires_credentials(self) -> bool:
    return True

provider = get_api_provider(api)
api_key = resolve_api_key(api) if provider.requires_credentials else ""
```

Apply the order in both stream entry points. Add `ReplayProvider.assert_consumed()` and keep the replay object free of any original-provider reference.

- [ ] **Step 4: Run stream and provider tests and commit Task 3**

Run: `pytest -q tests/unit/test_usage_stream_chokepoint.py tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py`

Expected: PASS.

```bash
git add openprogram/providers/stream.py openprogram/providers/api_registry.py openprogram/providers/replay.py tests/unit/test_usage_stream_chokepoint.py tests/providers/test_record_replay_registry.py
git commit -m "feat(providers): make replay strictly offline"
```

### Task 4: Config and recording-management CLI

**Files:**
- Modify: `openprogram/config_schema.py`
- Modify: `openprogram/cli.py`
- Modify: `openprogram/providers/recording.py`
- Create: `tests/providers/test_record_replay_cli.py`

**Interfaces:**
- Produces: `record_replay.mode`, `record_replay.file`, `create_managed_recording(name=None)`, `resolve_recording_selector(selector)`, `list_recordings()`, `show_recording(selector, include_content=False)`, `delete_recording(recording_id)`, and `prune_recordings(older_than_days, dry_run=False)`.
- CLI produces: `recordings status|record|replay|off|list|show|delete|prune` with exit codes `0` success, `1` runtime/validation failure, and `2` usage/confirmation failure.

- [ ] **Step 1: Add failing configuration and CLI tests**

```python
def test_record_command_precreates_private_header_then_updates_config(cli_env):
    assert main(["recordings", "record", "--name", "fixture"]) == 0
    config = setup._read_config()["record_replay"]
    assert config["mode"] == "record"
    assert managed_path(config["file"]).read_text().startswith('{"type":"header"')

def test_replay_command_prevalidates_before_updating_config(cli_env):
    invalid = cli_env.root / "invalid.jsonl"
    invalid.write_text("not-json\n")
    with pytest.raises(RecordingFileError):
        set_replay_mode(str(invalid))
    assert "record_replay" not in setup._read_config()

def test_delete_rejects_active_recording(cli_env):
    active = set_record_mode("active")
    with pytest.raises(RecordingManagementError):
        delete_recording(active.recording_id)
```

Drive `main(argv)` or subprocess parsing as appropriate. Patch state/config paths only, keep real filesystem modes, and verify no config mutation on every failure path.

- [ ] **Step 2: Run CLI tests and verify failure**

Run: `pytest -q tests/providers/test_record_replay_cli.py`

Expected: FAIL because the command group and config keys do not exist.

- [ ] **Step 3: Implement managed lifecycle and transactional mode changes**

```python
def create_managed_recording(name: str | None = None) -> RecordingInfo:
    return create_exclusive_private_header_file(validated_recording_id(name))
def set_record_mode(name: str | None = None) -> RecordingInfo:
    return transactionally_create_and_select_recording(name)
def set_replay_mode(selector: str) -> RecordingInfo:
    return validate_then_transactionally_select_replay(selector)
def set_record_replay_off() -> None:
    setup.update_config(clear_record_replay_selection)
```

Treat selectors containing a path separator or absolute syntax as explicit paths for replay/show only; otherwise resolve IDs under the managed root. Reject traversal and symlinks. `record` creates a header-only `0600` file before one `setup.update_config` call and removes only that newly created file if configuration fails. `replay` strictly parses before configuration update. Delete/prune only managed files, never delete the active selector, and skip a non-blocking locked recording during prune.

- [ ] **Step 4: Add the two config settings and thin argparse dispatch**

```python
SettingSpec("record_replay.mode", choices=("off", "record", "replay"), default="off", apply="next_start")
SettingSpec("record_replay.file", value_type="string", default=None, apply="next_start")
```

Add one `recordings` parser with the eight verbs and `set_defaults(_cmd_parser=recordings_parser)`; delegate runtime behavior to `recording.py`. JSON modes use `json.dumps`, destructive commands require `--yes` or an exact-ID interactive confirmation, and non-TTY refusal returns `2`.

- [ ] **Step 5: Run CLI/config tests and commit Task 4**

Run: `pytest -q tests/providers/test_record_replay_cli.py tests/unit/test_config_schema.py tests/unit/test_config_write_safety.py tests/unit/test_cli_subcommand_dispatch.py`

Expected: all feature/config tests PASS; if the isolated subprocess baseline still lacks `typing_extensions`, record that pre-existing environment failure separately and rerun all in-process command cases.

```bash
git add openprogram/config_schema.py openprogram/cli.py openprogram/providers/recording.py tests/providers/test_record_replay_cli.py
git commit -m "feat(cli): manage provider recordings"
```

### Task 5: Documentation status and final verification

**Files:**
- Modify: `docs/reference/design/providers/record-replay.html`

**Interfaces:**
- Consumes: completed behavior and test evidence from Tasks 1–4.
- Produces: an evidence-separated implementation/verification status section without changing the approved conceptual design.

- [ ] **Step 1: Update only the implementation status and evidence table**

Record exact production files, compatibility boundary, test files, and fresh verification commands. Mark only implemented behavior as complete; retain limitations such as provider-layer-only offline scope and scheduling-order nondeterminism.

- [ ] **Step 2: Run the complete relevant test matrix**

Run: `pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py tests/providers/test_record_replay_cli.py tests/providers/test_scripted_provider.py tests/unit/test_usage_stream_chokepoint.py tests/unit/test_config_schema.py tests/unit/test_config_write_safety.py tests/unit/test_cli_subcommand_dispatch.py`

Expected: PASS except any reproduced baseline-only missing-dependency subprocess case, which must be reported exactly and not attributed to this feature.

- [ ] **Step 3: Run lint, docs, and diff checks**

Run: `ruff check openprogram/paths.py openprogram/config_schema.py openprogram/cli.py openprogram/providers tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py tests/providers/test_record_replay_cli.py tests/unit/test_usage_stream_chokepoint.py`

Run the repository-provided HTML/docs check discovered from package scripts or CI configuration; then run `git diff --check 8f62da5a HEAD` and `git status --short`.

Expected: zero Ruff, docs, and whitespace errors; only intended files changed.

- [ ] **Step 4: Commit Task 5**

```bash
git add docs/reference/design/providers/record-replay.html
git commit -m "docs: record request replay implementation evidence"
```

- [ ] **Step 5: Review the branch without merging**

Run: `git log --oneline 8f62da5a..HEAD` and `git diff --stat 8f62da5a HEAD`.

Expected: one plan commit plus independently reviewable task commits, no JSON Schema/MCP/resource-governance file changes, and no merge into `main`.
