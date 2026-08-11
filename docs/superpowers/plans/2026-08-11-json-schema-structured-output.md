# JSON Schema Structured Output Implementation Plan

> **For implementation:** Execute each task with a failing test first. Keep the existing
> `Runtime.exec -> AgentSession -> agent_loop -> stream_simple` path; do not add a second
> provider client.

**Goal:** Make `Runtime.exec(..., response_format=...)` carry a normalized JSON Schema to
the provider path and return a strictly parsed, locally validated Python JSON value.

**Architecture:** Normalize and validate the caller schema at the Runtime trust boundary,
carry the immutable option through existing agent/provider option types, map it only in
adapters with a verified native contract, and validate the final assistant text locally.
The first implementation batch covers the shared path and OpenAI-compatible native
payloads. Other adapters remain explicitly unsupported until their contract tests exist.

**Dependencies:** Existing Pydantic models and the already locked `jsonschema` package.

## Task 1: Structured-output value object and strict validator

**Files:**
- Create: `openprogram/providers/structured_output.py`
- Modify: `openprogram/providers/types.py`
- Test: `tests/providers/test_structured_output.py`

1. Add failing tests for bare-schema/envelope normalization, invalid schema rejection,
   rejection of fenced/trailing/non-finite JSON, deterministic bounded issues, and valid
   typed values.
2. Run `pytest -q tests/providers/test_structured_output.py` and confirm failure.
3. Add `JsonSchemaOutput`, structured error classes, `normalize_response_format`, and
   `parse_and_validate_json` using `jsonschema.validators.validator_for`.
4. Add `response_format` to `StreamOptions` and structured result metadata to
   `AssistantMessage`.
5. Re-run the focused test and commit.

## Task 2: Preserve response_format through Runtime and AgentSession

**Files:**
- Modify: `openprogram/agent/agent.py`
- Modify: `openprogram/agent/session.py`
- Modify: `openprogram/agent/agent_loop.py`
- Modify: `openprogram/providers/callable_model.py`
- Modify: `openprogram/agentic_programming/runtime.py`
- Test: `tests/agent/test_loop_options.py`
- Test: `tests/agentic_programming/test_runtime_structured_output.py`

1. Add failing tests proving the stream function receives the normalized option and a
   callable-backed Runtime returns a Python value after strict local validation.
2. Thread `response_format` through `AgentOptions`, `AgentSession`, `AgentLoopConfig`, and
   `_stream_assistant_response`'s `SimpleStreamOptions` rebuild.
3. Make the callable adapter read the per-call option rather than capture a stale value.
4. Normalize before the network boundary, validate the final assistant text, retain its
   canonical JSON text for history, and return the parsed value from Runtime.
5. Re-run focused Runtime/Agent tests and commit.

## Task 3: OpenAI native payload mapping

**Files:**
- Modify: `openprogram/providers/openai_completions/openai_completions.py`
- Modify: `openprogram/providers/openai_responses/openai_responses.py`
- Test: existing request-builder tests under `tests/providers/`

1. Locate the existing request-builder tests and add failures for Chat Completions
   `response_format.json_schema` and Responses `text.format` payloads.
2. Map the normalized option without mutating the caller schema.
3. Reject provider-private input shapes at normalization rather than passing them through.
4. Re-run provider tests and commit.

## Task 4: Bounded semantic repair

**Files:**
- Modify: `openprogram/agent/agent_loop.py`
- Modify: `openprogram/providers/structured_output.py`
- Test: `tests/agent/test_structured_output_loop.py`

1. Add a two-response stream fake: the first candidate fails validation and the second
   succeeds; assert exactly two provider calls and only the valid final message persists.
2. At the no-tool terminal boundary, validate; on a validation failure and remaining
   budget, remove the invalid candidate and append a bounded repair user message to the
   temporary loop context.
3. Do not retry refusal, incomplete, cancellation, or transport failures as validation
   failures.
4. Re-run loop tests and commit.

## Task 5: Verification

1. Run focused provider, agent, and runtime suites.
2. Run `ruff check` on changed Python files and `git diff --check`.
3. Run `pytest -q tests/unit tests/agent tests/providers tests/agentic_programming`.
4. Record explicitly which provider adapters remain unsupported; do not advertise them as
   implemented.
