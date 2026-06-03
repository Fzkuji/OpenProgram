# Error-taxonomy propagation — structured LLM errors up to the UI

Status: **planned** · Owner: providers/agent/webui · Created: 2026-06-04

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

1. Backend: classify at the agent boundary + widen the chat-turn error event
   with `reason/retryable/retry_after_s`. Independently verifiable by inspecting
   the WS error payload — induce each class (bad key → authentication, an
   oversized turn → context_length, a 429 → rate_limit).
2. Frontend: the error component reads the new fields and renders the
   categorized copy/affordance; falls back to `content` when absent.

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
