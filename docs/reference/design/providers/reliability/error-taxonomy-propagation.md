# Error-taxonomy propagation — structured LLM errors up to the UI

Status: **agent boundary + model landed (079e0072)** · webui emit + frontend remain · Owner: providers/agent/webui · Created: 2026-06-04

Optimization-roadmap item: "propagate the structured LLMError taxonomy above the
provider layer". Builds on the existing taxonomy in
`openprogram/providers/utils/errors.py`.

## 1. Problem

A rich, opencode-style error taxonomy already exists at the **provider-stream**
layer: `providers/utils/errors.py` defines `ErrorReason`
(`transport / rate_limit / authentication / authorization / context_length /
content_policy / invalid_request / provider_internal / unknown`), an `LLMError`
carrying `reason` + `retry_after_s`, and `classify(exc) -> (reason, retryable)`.
`stream_retry` uses it to drive backoff.

But **above** the stream layer the structure is thrown away:
- the agent loop catches a failure and flattens it to `str(exc)`;
- the webui chat-turn error event is `{"type": "error", "content": "<string>"}`
  (`ws_actions/chat.py`);
- the WS connection handler logged a raw traceback (already fixed).

So the **UI cannot tell apart**:
- a **rate limit** (retryable — show "retrying in Ns", maybe auto-retry) from
- an **auth** failure (fatal — "check your API key / re-login") from
- a **context-length** overflow (fatal — "the conversation is too long; compact
  or start a new chat") from
- a transient **provider_internal** (retryable).

Every failure looks like an opaque red string. That's the single biggest gap in
error UX.

## 2. Goal

Thread `reason` / `retryable` / `retry_after_s` from the provider failure up to
the chat-turn error event, and render a **categorized, actionable** error in the
UI. Scope is the **main chat-turn streaming error** only — the operational error
strings (retry/compact-failure messages) stay plain.

## 3. Design

1. **Classify at the agent error boundary.** Where the agent turn catches a
   stream failure, if it's an `LLMError` use its `reason` / `retry_after_s`;
   otherwise run `errors.classify(exc)` to derive `(reason, retryable)`. Carry
   these on the error the agent surfaces (a small structured error object, not a
   bare string).
2. **Widen the chat-turn error event.** The webui error payload becomes
   `{"type": "error", "content": <human string>, "reason": <ErrorReason>,
   "retryable": <bool>, "retry_after_s": <float|null>}`. `content` stays for
   back-compat; the new fields are additive.
3. **Frontend renders by reason.** A categorized error chip maps reason →
   actionable copy + affordance:
   - `rate_limit` → "Rate limited — retrying in {retry_after_s}s" (and, if a
     retry policy exists, an auto-retry/▸ countdown).
   - `authentication`/`authorization` → "Your {provider} key was rejected —
     check it in Settings → Providers."
   - `context_length` → "This conversation is too long — compact it or start a
     new chat."
   - `content_policy` → "The provider blocked this request (content policy)."
   - `provider_internal`/`transport` → "Temporary provider/network error — try
     again." (retryable styling)
   - `invalid_request`/`unknown` → the raw `content` (fallback).

## 4. Migration

1a. **(done, 079e0072)** Classify at the agent error boundary —
   `errors.taxonomy_fields(exc)` + the new `AssistantMessage.error_reason /
   error_retryable / error_retry_after_s` fields. Unit-tested (LLMError
   passthrough, generic classified).
1b. **(landed across all three emit points; live render NOT yet captured)** A
   chat failure is caught at three layers, all now classified via
   `taxonomy_fields` and emitting `reason / retryable / retry_after_s`:
   - `agent.py` (the `Agent` class boundary, 079e0072) — used by the Agent run.
   - `_execute/__init__.py` outer except (5efc95ab) — the action-level error.
   - **`dispatcher.py` (5c17b848) — the REAL common path.** The webui chat turn
     runs via the dispatcher's `_run_loop_blocking`, whose failure is caught in
     the dispatcher's own except; the reason flows through `TurnResult`
     (`error_reason/error_retryable/error_retry_after_s`) into both the in-run
     dispatcher error event and the post-run `chat.py` broadcast.
   **Still unverified:** a live categorized render. Forcing a deterministic
   provider error is blocked by the frontend using its OWN selected model
   (codex) over the agent default, and codex being intermittent (sometimes 401,
   sometimes succeeds). The agent-model swap does not change what the frontend
   sends. Confirm with a real, repeatable provider failure on the selected
   model. Note: the persisted error node only carries the string (not the
   reason) — only the live broadcast does; rendering a categorized error on
   reload would need the DB node to carry the taxonomy too (future).
2. **(landed, compiles; live render NOT yet captured, e5f95445)** The assistant
   bubble (`assistant-bubble.tsx`) renders a categorized headline keyed off
   `errorReason` (rate_limit → retry hint, auth → check-key, context → compact,
   provider/transport/timeout → temporary) with the raw message below;
   `ChatResponseData` + `ChatMsg` carry the fields and `finalize()` captures
   them. Compiles clean; happy-path chat unregressed. **Still unverified:** a
   live categorized error render — forcing a deterministic provider failure was
   not achieved this session (codex kept succeeding; the model picker / raw WS /
   store-injection were each too fiddly). Confirm by hitting a real error
   (expired key → "auth"; an OpenRouter `:free` model that 503s → "provider"),
   or by temporarily setting `default_model` to a 503ing model.

Each step commits separately; the backend is useful on its own (API consumers,
logs, future channels) even before the frontend lands.

## 5. Verification

Induce each reason and confirm the WS payload's `reason/retryable` + the UI
render: a rejected key → `authentication`, fatal, "check your key"; a 429 →
`rate_limit`, retryable, retry hint; an oversized context → `context_length`,
fatal, "compact". `errors.classify` already has unit coverage of the mapping;
add a test that the agent boundary preserves an `LLMError`'s reason unchanged.

## 6. Non-goals

Not a rewrite of the ~991 `except Exception` sites — only the chat-turn LLM
error path is classified-and-surfaced. The blanket-except audit is separate.
Not an auto-retry policy change; this only *exposes* `retryable`/`retry_after_s`
so the UI (and any future policy) can act on it.
