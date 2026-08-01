# Error-taxonomy propagation — structured LLM errors up to the UI

`reason`, `retryable`, and `retry_after_s` travel from the provider failure all
the way to the chat-turn error event, so the UI renders a categorized,
actionable error instead of an opaque string. This builds on the taxonomy in
`openprogram/providers/utils/errors.py`.

## 1. Why the structure must survive the trip

The taxonomy exists at the **provider-stream** layer: `providers/utils/errors.py`
defines `ErrorReason` (`transport / rate_limit / authentication / authorization /
context_length / content_policy / invalid_request / provider_internal /
unknown`), an `LLMError` carrying `reason` + `retry_after_s`, and
`classify(exc) -> (reason, retryable)`. `stream_retry` uses it to drive backoff.

If the structure is flattened to `str(exc)` above that layer — the agent loop
catching a failure, or a chat-turn error event shaped as
`{"type": "error", "content": "<string>"}` — the UI cannot tell apart:

- a **rate limit** (retryable — show "retrying in Ns", maybe auto-retry) from
- an **auth** failure (fatal — "check your API key / re-login") from
- a **context-length** overflow (fatal — "the conversation is too long; compact
  or start a new chat") from
- a transient **provider_internal** (retryable).

Every failure then looks like the same red string, and no affordance can be
offered because nothing knows what kind of failure it was.

The scope is the **main chat-turn streaming error**. Operational error strings
(retry and compact-failure messages) stay plain.

## 2. Design

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

## 3. Where the classification happens

A chat failure is caught at three layers, and each classifies via
`taxonomy_fields` and emits `reason / retryable / retry_after_s`:

- `agent.py` — the `Agent` class boundary, used by the Agent run.
- `_execute/__init__.py` outer except — the action-level error.
- `dispatcher.py` — the common path for the webui chat turn, which runs through
  the dispatcher's `_run_loop_blocking`. The failure is caught in the
  dispatcher's own except, and the reason flows through `TurnResult`
  (`error_reason` / `error_retryable` / `error_retry_after_s`) into both the
  in-run dispatcher error event and the post-run `chat.py` broadcast.

The frontend side is `assistant-bubble.tsx`, which renders the categorized
headline keyed off `errorReason` with the raw message below;
`ChatResponseData` and `ChatMsg` carry the fields, and `finalize()` captures
them.

Classification is worth doing at the backend boundary on its own terms: API
consumers, logs, and other channels all read the reason, independent of what
the chat UI does with it.

## 4. Verification

Induce each reason and confirm the WS payload's `reason` / `retryable` and the
UI render: a rejected key gives `authentication`, fatal, "check your key"; a 429
gives `rate_limit`, retryable, with the retry hint; an oversized context gives
`context_length`, fatal, "compact". `errors.classify` has unit coverage of the
mapping, alongside a test that the agent boundary preserves an `LLMError`'s
reason unchanged.

Reproducing a deterministic provider failure end to end is the awkward part: the
frontend sends its own selected model rather than the agent default, so changing
the agent model does not change what is exercised. A repeatable failure on the
selected model is what confirms the live render — an expired key for `auth`, or
an OpenRouter `:free` model that 503s for `provider`.

## 5. Limits

The persisted error node carries only the string, not the reason; the taxonomy
travels on the live broadcast. Rendering a categorized error after a reload
would require the stored node to carry the taxonomy too.

## 6. Non-goals

Not a rewrite of the ~991 `except Exception` sites — only the chat-turn LLM
error path is classified-and-surfaced. The blanket-except audit is separate.
Not an auto-retry policy change; this only *exposes* `retryable`/`retry_after_s`
so the UI (and any future policy) can act on it.
