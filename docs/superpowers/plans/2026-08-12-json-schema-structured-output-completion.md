# JSON Schema Structured Output Completion Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete every user-reachable and provider-facing contract in `docs/reference/design/providers/json-schema-structured-output.html` so the feature-matrix `●` is supported by product-level evidence.

**Architecture:** Keep the existing `Runtime.exec -> _call_via_providers -> AgentSession -> agent_loop -> provider stream` path. Extend the current normalized `JsonSchemaOutput` contract with capability-based native/hidden-tool/prompt negotiation, then expose the same typed result and bounded retry lifecycle through Runtime, CLI, Web/API, record/replay, and the session-title consumer. Local validation against the caller's original schema remains authoritative in every mode.

**Tech Stack:** Python 3.12+, Pydantic v2, `jsonschema`, existing provider adapters, argparse, FastAPI/WebSocket, existing JSONL record/replay.

## Global Constraints

- Baseline is `main@b6460fdc`; Tasks already merged before this plan must be preserved and strengthened, not duplicated.
- The sole public Python entry remains `Runtime.exec(..., response_format=...)` / `Runtime.async_exec(...)`.
- Never add a second provider client or bypass the Agent loop, deadline, usage, DAG, or record/replay paths.
- `fallback="auto"` may select only a verified native contract or an equivalent strict hidden-tool contract; it must never silently select prompt fallback.
- `fallback="prompt"` is explicit and still requires strict local parsing and validation.
- Unknown capability is fail-closed. Provider/model names alone do not authorize a native payload.
- The caller schema is deep-copied, never weakened, and remains the final local validator input.
- Validation repair performs at most one additional provider call; cancellation, refusal, truncation, timeout, and transport errors are not validation repairs.
- Invalid candidate text, schema contents, and provider error bodies must not be copied into logs or public error messages.
- Existing ordinary text calls and v1 record/replay library compatibility remain unchanged unless the design explicitly rejects corrupt data.
- No new dependency is permitted; `jsonschema` must be a direct locked dependency.

### Task 1: Capability registry and hidden-tool negotiation

**Files:**
- Modify: `openprogram/providers/structured_output.py`
- Modify: `openprogram/providers/api_registry.py`
- Modify: `openprogram/providers/register.py`
- Modify: `openprogram/providers/types.py`
- Modify: `openprogram/agent/agent_loop.py`
- Test: `tests/providers/test_structured_output.py`
- Create: `tests/agent/test_structured_output_hidden_tool.py`

**Interfaces:**
- Produces: `StructuredOutputCapabilities(native, dialect, streaming, with_tools, schema_profile)` registered per API.
- Produces: `StructuredOutputPlan(mode, original_schema, provider_schema, submit_tool_name)` from `negotiate_structured_output(model, provider, options, tools)`.
- Consumes: existing `JsonSchemaOutput`, `AgentLoopConfig`, tool-choice and parallel-tool settings.

- [ ] **Step 1: Write failing negotiation tests**

```python
def test_auto_unknown_is_unsupported_but_verified_strict_tool_uses_hidden_tool():
    unknown = capabilities(native="unknown", strict_tool=False)
    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(model(), unknown, output(fallback="auto"), [])
    tool_capable = capabilities(native="unknown", strict_tool=True)
    assert negotiate_structured_output(
        model(), tool_capable, output(fallback="auto"), []
    ).mode == "tool"

@pytest.mark.parametrize("tool_choice,parallel", [("none", False), ("required", True)])
def test_hidden_tool_does_not_override_conflicting_caller_controls(tool_choice, parallel):
    with pytest.raises(StructuredOutputUnsupportedError):
        negotiate_structured_output(
            model(), capabilities(strict_tool=True), output(), [],
            tool_choice=tool_choice, parallel_tool_calls=parallel,
        )
```

- [ ] **Step 2: Run the negotiation tests and verify RED**

Run: `python -m pytest -q tests/providers/test_structured_output.py -k 'hidden_tool or conflicting or unknown'`

Expected: FAIL because the current negotiation returns only `native | prompt` and has no adapter capability registry.

- [ ] **Step 3: Implement the minimum capability and plan types**

```python
@dataclass(frozen=True)
class StructuredOutputCapabilities:
    native: Literal["supported", "unsupported", "unknown"] = "unknown"
    dialect: str | None = None
    streaming: bool = True
    with_tools: bool = False
    strict_tool: bool = False
    schema_profile: str = "none"

@dataclass(frozen=True)
class StructuredOutputPlan:
    mode: Literal["native", "tool", "prompt"]
    original_schema: dict[str, Any]
    provider_schema: dict[str, Any]
    submit_tool_name: str | None = None
```

Register these capabilities beside the existing API provider function, using the same registry lock and replacement semantics. Do not add a parallel global registry.

- [ ] **Step 4: Write failing hidden-tool loop tests**

```python
async def test_hidden_submit_tool_is_terminal_and_is_not_executed():
    result = await run_structured_loop(events=[submit_call({"answer": 4})])
    assert result.structured_output == {"answer": 4}
    assert result.structured_output_mode == "tool"
    assert executed_tools == []

async def test_submit_must_be_the_only_tool_call_in_its_message():
    with pytest.raises(StructuredOutputValidationError) as exc:
        await run_structured_loop(events=[message_with(normal_tool(), submit_call({"answer": 4}))])
    assert exc.value.code == "mixed_submission"
```

- [ ] **Step 5: Run hidden-tool tests and verify RED**

Run: `python -m pytest -q tests/agent/test_structured_output_hidden_tool.py`

Expected: FAIL because no internal submit tool is injected or recognized.

- [ ] **Step 6: Implement the hidden submit tool in the existing loop**

Inject `__openprogram_submit_json` only into the per-call tool list. It is not added to the global tool registry, never reaches approval or execution, must be the only tool call in its assistant message, and its arguments are validated against `original_schema`. Preserve ordinary tool execution before a later submit round.

- [ ] **Step 7: Run Task 1 tests and commit**

Run: `python -m pytest -q tests/providers/test_structured_output.py tests/agent/test_structured_output_hidden_tool.py tests/agent/test_loop_options.py`

Commit: `feat(structured-output): add capability negotiation and hidden tool fallback`

### Task 2: Complete verified native provider mappings

**Files:**
- Modify: `openprogram/providers/azure_openai_responses/azure_openai_responses.py`
- Modify: `openprogram/providers/google/google.py`
- Modify: `openprogram/providers/amazon_bedrock/amazon_bedrock.py`
- Modify: `openprogram/providers/openai_completions/openai_completions.py`
- Modify: `openprogram/providers/openai_responses/openai_responses.py`
- Modify: `openprogram/providers/anthropic/anthropic.py`
- Modify: `openprogram/providers/register.py`
- Modify: `openprogram/providers/sources/models_dev.py`
- Modify: `openprogram/providers/enabled_models.py`
- Modify: `openprogram/webui/_model_listing/listing.py`
- Test: `tests/providers/test_openai_structured_output.py`
- Create: `tests/providers/test_google_structured_output.py`
- Create: `tests/providers/test_bedrock_structured_output.py`
- Test: `tests/unit/test_models_dev_disk_cache.py`

**Interfaces:**
- Consumes: `StructuredOutputPlan.provider_schema` and the Task 1 API capability registry.
- Produces: exact provider request payloads; unsupported schemas return a bounded path/reason before network I/O.

- [ ] **Step 1: Add literal payload contract tests**

```python
def test_google_maps_schema_to_generation_config():
    body = build_google_body(response_format=normalized())
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA

def test_bedrock_serializes_schema_in_output_config():
    body = build_bedrock_body(response_format=normalized())
    raw = body["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
    assert json.loads(raw) == SCHEMA
```

Also add literal Azure Responses, Anthropic `effort + format`, and OpenAI community-endpoint fail-closed cases. Expected values must not be produced by the adapter helper under test.

Add a catalogue/listing test proving `structured_output` preserves all three states: `True`, `False`, and missing/`None`; a missing field must not be normalized to `False`.

- [ ] **Step 2: Run the new provider tests and verify RED**

Run: `python -m pytest -q tests/providers/test_openai_structured_output.py tests/providers/test_google_structured_output.py tests/providers/test_bedrock_structured_output.py`

- [ ] **Step 3: Implement only documented native dialects**

Map Google official GenerateContent and Bedrock Converse fields exactly as specified. Register native support only for adapters covered by these contract tests. Keep `openai-codex`, `gemini-subscription`, and unverified OpenAI-compatible base URLs at `unknown`, allowing only Task 1 hidden-tool fallback when strict-tool support is verified.

Carry the tri-state catalogue field through `Model`, enabled-model persistence, and the Web model-listing response without changing the default enabled/disabled selection behavior.

- [ ] **Step 4: Reject lossy schema transformations**

Use the existing `_schema` traversal to return the first unsupported constraint path. Do not delete constraints, force `additionalProperties`, rewrite unions, or use the weakened schema for final validation.

- [ ] **Step 5: Run provider suites and commit**

Run: `python -m pytest -q tests/providers/test_*structured_output.py tests/providers/test_openai.py tests/providers/test_anthropic.py tests/providers/test_gemini.py`

Commit: `feat(structured-output): complete verified native provider mappings`

### Task 3: Structured stream lifecycle and bounded repair

**Files:**
- Modify: `openprogram/providers/types.py`
- Modify: `openprogram/agent/types.py`
- Modify: `openprogram/agent/agent_loop.py`
- Modify: `openprogram/agent/internals/_event_parsing.py`
- Modify: `openprogram/agentic_programming/runtime.py`
- Create: `tests/agent/test_structured_output_streaming.py`
- Test: `tests/agentic_programming/test_runtime_structured_output.py`

**Interfaces:**
- Produces: `structured_output_retry` and `structured_output_end` events with `attempt`, bounded `issues`, `mode`, and validated `value`.
- Produces: one persisted successful assistant message; rejected candidates remain attempt-scoped and are not committed as final history.

- [ ] **Step 1: Write failing event and persistence tests**

```python
async def test_invalid_candidate_emits_retry_then_one_valid_terminal_result():
    events, history = await run_two_candidate_stream('{"answer":"bad"}', '{"answer":7}')
    assert [e.type for e in events if e.type.startswith("structured_")] == [
        "structured_output_retry", "structured_output_end",
    ]
    assert events[-2].value == {"answer": 7}
    assert [m for m in history if m.role == "assistant"][-1].structured_output == {"answer": 7}

async def test_cancellation_and_truncation_never_start_validation_repair():
    for terminal in (cancelled_message(), length_message()):
        assert await provider_call_count(terminal) == 1
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest -q tests/agent/test_structured_output_streaming.py`

- [ ] **Step 3: Move validation/retry to the Agent-loop terminal boundary**

The loop owns candidate events and persistence. Runtime consumes the validated `AssistantMessage.structured_output`; it must not perform a second provider retry. Callable-backed Runtime continues through the same AgentSession path. A failed second candidate raises the typed structured error without emitting `done`.

- [ ] **Step 4: Add sync/async parity tests**

```python
def test_exec_and_async_exec_return_the_same_typed_value(runtime_pair):
    assert runtime_pair.sync.exec("x", response_format=SCHEMA) == {"answer": 1}
    assert asyncio.run(runtime_pair.async_.async_exec("x", response_format=SCHEMA)) == {"answer": 1}
```

- [ ] **Step 5: Run Task 3 suites and commit**

Run: `python -m pytest -q tests/agent/test_structured_output_streaming.py tests/agentic_programming/test_runtime_structured_output.py tests/agent/test_loop_options.py`

Commit: `feat(structured-output): expose validated stream lifecycle`

### Task 4: CLI and Web/API user-reachable entrypoints

**Files:**
- Modify: `openprogram/cli.py`
- Modify: `openprogram/_cli_chat/turn.py`
- Modify: `openprogram/webui/ws_actions/chat.py`
- Modify: `openprogram/webui/_execute/chat.py`
- Modify: `openprogram/webui/server.py`
- Modify: `openprogram/webui/routes/functions.py`
- Modify: `cli/src/ws/client.ts`
- Modify: `cli/src/screens/repl/wsHandlers/handleChatResponse.ts`
- Create: `tests/providers/test_structured_output_cli.py`
- Create: `tests/unit/test_structured_output_web.py`

**Interfaces:**
- CLI consumes: `--json-schema PATH` on one-shot `--print`; `-` means stdin only when the prompt is not also read from stdin.
- Web/API consumes: `response_format` envelope in owner-authorized chat/function requests.
- Produces: JSON-only CLI stdout and typed Web result/error envelopes.

- [ ] **Step 1: Write failing CLI subprocess tests**

```python
def test_print_json_schema_writes_only_valid_json_to_stdout(cli_fixture):
    result = cli_fixture.run("--print", "answer", "--json-schema", "schema.json")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"answer": 3}
    assert result.stderr == ""

@pytest.mark.parametrize("case,code", [("bad_schema", 2), ("unsupported", 3), ("invalid_output", 4)])
def test_structured_cli_errors_have_stable_exit_codes_without_candidate_text(case, code, cli_fixture):
    result = cli_fixture.run_case(case)
    assert result.returncode == code
    assert "secret candidate" not in result.stderr
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `python -m pytest -q tests/providers/test_structured_output_cli.py`

- [ ] **Step 3: Implement the CLI boundary**

Parse and preflight the schema before Runtime construction. Keep normal `--print` unchanged. Serialize the already validated Python value once with `ensure_ascii=False`; diagnostic progress and typed errors go only to stderr.

- [ ] **Step 4: Write failing Web request/event tests**

```python
def test_chat_ws_accepts_response_format_and_returns_typed_result(ws_client):
    frames = ws_client.chat({"content": "x", "response_format": ENVELOPE})
    result = next(frame for frame in frames if frame["data"].get("type") == "result")
    assert result["data"]["structured_output"] == {"answer": 3}

def test_invalid_schema_is_rejected_before_ws_dispatch(ws_client, dispatch_spy):
    frame = ws_client.chat_one({"content": "x", "response_format": BAD_SCHEMA})
    assert frame["data"]["type"] == "error"
    assert frame["data"]["code"] == "invalid_schema"
    assert dispatch_spy.calls == []

def test_function_http_keeps_async_ack_and_delivers_typed_ws_result(client, ws_client):
    ack = client.post("/api/function/demo", json={"response_format": ENVELOPE})
    assert set(ack.json()) >= {"session_id", "msg_id"}
    result = ws_client.await_result(ack.json()["msg_id"])
    assert result["structured_output"] == {"answer": 3}
```

- [ ] **Step 5: Run Web tests and verify RED**

Run: `python -m pytest -q tests/unit/test_structured_output_web.py`

- [ ] **Step 6: Thread normalized schema and typed lifecycle through existing envelopes**

Do not add a Web schema editor and do not invent a synchronous `/api/chat` route. Add `response_format` to the existing WebSocket chat action and the existing `POST /api/function/{name}` body. The function route must continue returning only its immediate `{session_id,msg_id}` acknowledgement; typed success/error remains on the existing WS/event chain. Add backward-compatible optional TS fields; older clients may ignore them. Route retry/end only to the originating session/request. Public errors expose stable `code` and bounded issues, never candidate or schema text.

- [ ] **Step 7: Run Task 4 checks and commit**

Run: `python -m pytest -q tests/providers/test_structured_output_cli.py tests/unit/test_structured_output_web.py tests/unit/test_webui_chat_dispatcher.py`

Run: `npm --prefix cli test -- --runInBand`

Commit: `feat(structured-output): add CLI and Web typed entrypoints`

### Task 5: Record/replay v2 structured semantics

**Files:**
- Modify: `openprogram/providers/recording.py`
- Modify: `openprogram/providers/replay.py`
- Test: `tests/providers/test_record_replay.py`
- Test: `tests/providers/test_record_replay_registry.py`

**Interfaces:**
- Produces: format v2 rows containing the normalized structured option and every provider attempt in order.
- Consumes: Task 3 derived retry/end events after replay; provider raw-event vocabulary remains unchanged.

- [ ] **Step 1: Write failing format and offline replay tests**

```python
def test_structured_retry_records_two_calls_and_replays_same_typed_result(recording):
    live = run_structured(recording, replies=['{"answer":"bad"}', '{"answer":2}'])
    replay = replay_structured(recording.path)
    assert live == replay == {"answer": 2}
    assert recording.call_count == 2

def test_replay_rejects_schema_mismatch_before_any_credential_or_socket(recording, sentinels):
    with pytest.raises(ReplayMismatch) as exc:
        replay_structured(recording.path, schema=OTHER_SCHEMA)
    assert exc.value.path.endswith("response_format.schema")
    assert sentinels.credentials == sentinels.sockets == 0
```

- [ ] **Step 2: Run record/replay tests and verify RED**

Run: `python -m pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py -k structured`

- [ ] **Step 3: Implement v2 request normalization and strict replay comparison**

Write the normalized schema envelope without secrets. Compare exact normalized fields with a stable mismatch path. Replay executes local parse/validation and Task 3 retry decisions without credential resolution, vendor import, network, or fallback. Preserve documented v1 ordinary-call compatibility and reject v1 structured calls explicitly.

- [ ] **Step 4: Run all record/replay tests and commit**

Run: `python -m pytest -q tests/providers/test_record_replay.py tests/providers/test_record_replay_registry.py tests/providers/test_record_replay_cli.py`

Commit: `feat(recording): replay structured output deterministically`

### Task 6: Migrate session titles to the structured consumer

**Files:**
- Modify: `openprogram/agent/dispatcher/titles.py`
- Modify: `docs/reference/design/runtime/session/name.md`
- Test: `tests/unit/test_dispatcher_compaction_title.py`

**Interfaces:**
- Consumes: `Runtime.exec(..., response_format=TITLE_SCHEMA) -> {"title": str}`.
- Produces: Unicode-trimmed title capped at 80 code points; placeholder remains on typed failure.

- [ ] **Step 1: Write failing title tests**

```python
def test_title_uses_validated_object_without_text_cleanup(runtime_spy):
    runtime_spy.result = {"title": "  正式标题  "}
    assert generate_title(runtime_spy) == "正式标题"
    assert runtime_spy.response_format == TITLE_SCHEMA

def test_structured_title_failure_keeps_phase_one_placeholder(runtime_spy):
    runtime_spy.error = StructuredOutputValidationError("bad", code="validation_failed")
    assert generate_title(runtime_spy, placeholder="Existing") == "Existing"
```

- [ ] **Step 2: Run title tests and verify RED**

Run: `python -m pytest -q tests/unit/test_dispatcher_compaction_title.py`

- [ ] **Step 3: Replace the textual formatting protocol**

Remove `ONLY title text`, prefix stripping, quote stripping, and first-line parsing. Retain Unicode trim, the final 80-character storage guard, and best-effort placeholder behavior.

- [ ] **Step 4: Run title tests and commit**

Run: `python -m pytest -q tests/unit/test_dispatcher_compaction_title.py`

Commit: `refactor(session): generate titles with validated JSON schema`

### Task 7: Evidence, documentation, and feature-matrix completion gate

**Files:**
- Modify: `docs/reference/design/providers/json-schema-structured-output.html`
- Modify: `docs/reference/api/runtime.md`
- Modify: `docs/reference/api/runtime.zh.md`
- Modify: `docs/reference/design/feature-matrix.html`
- Modify: `docs/reference/design/runtime/session/name.md`

**Interfaces:**
- Consumes: commits and exact verification outputs from Tasks 1–6.
- Produces: evidence-separated HTML implementation record and a mechanically consistent matrix.

- [ ] **Step 1: Update the design document without rewriting the approved design**

Add an implementation record after the target design. For each contract, record `implemented | compatibility boundary | not implemented`, exact source/test symbols, commit hashes, and verification commands. Update the baseline and remove the stale “本文不表示功能已经实现” statement only after all required contracts pass.

- [ ] **Step 2: Correct public API wording**

Document actual `auto | none | prompt` behavior, supported native adapters, hidden-tool restrictions, CLI syntax, Web/API envelope, return types, errors, and record/replay format compatibility. Do not claim prompt fallback for `auto`.

- [ ] **Step 3: Apply the matrix completion gate**

Keep `●` only if Tasks 1–6 and their focused tests are complete. If any approved required contract remains absent, set this row to `◐` and recalculate score, gaps, category totals, SVG/chart labels, prose counts, and baseline together.

- [ ] **Step 4: Run final verification**

Run:

```bash
python -m pytest -q tests/providers/test_structured_output.py \
  tests/providers/test_openai_structured_output.py \
  tests/providers/test_google_structured_output.py \
  tests/providers/test_bedrock_structured_output.py \
  tests/agent/test_structured_output_hidden_tool.py \
  tests/agent/test_structured_output_streaming.py \
  tests/agentic_programming/test_runtime_structured_output.py \
  tests/providers/test_structured_output_cli.py \
  tests/unit/test_structured_output_web.py \
  tests/providers/test_record_replay.py \
  tests/providers/test_record_replay_registry.py \
  tests/providers/test_record_replay_cli.py \
  tests/unit/test_dispatcher_compaction_title.py
python -m pytest -q tests/unit tests/agent tests/providers tests/agentic_programming
python -m ruff check <all changed Python files>
python scripts/docs.py build
python scripts/check_doc_links.py
git diff --check
```

- [ ] **Step 5: Run fresh spec and quality reviews**

The spec reviewer checks every section of `json-schema-structured-output.html` against production evidence. The quality reviewer checks security boundaries, provider payload accuracy, retry/persistence duplication, public error leakage, and compatibility. Resolve every HIGH/MEDIUM finding with a new failing test and re-review until both return `PASS` with zero remaining findings.

- [ ] **Step 6: Commit the evidence update**

Commit: `docs: record complete JSON schema output evidence`
