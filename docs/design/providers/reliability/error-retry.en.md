# Error Handling and Retry Decision Logic for Model Calls

This document describes the logic by which the system "makes choices" at each layer between the start and the failure/success of a single model call within an `@agentic_function`: when to retry, when to give up, and when to declare a permanent error.

## Call Chain

A single `runtime.exec()` triggers a model call, which passes top-down through:

```
runtime.exec() inside @agentic_function
  └─ Runtime.exec() ............. retry loop (this layer)
       └─ _call → _call_via_providers
            └─ AgentSession.run() .... automatic retry layer
                 └─ agent.run() ....... try/except wrapper, produces AssistantMessage
                      └─ agent_loop ... provider streaming HTTP
```

Errors "take shape" in two places:
1. Exceptions thrown inside `agent_loop` are caught by `agent.py`, producing an `AssistantMessage` with `stop_reason="error"`.
2. The provider stream itself emits an `error` event, and `agent_loop` treats it directly as the `final_message`.

In both cases the downstream sees the same thing: a message with `stop_reason="error"`, carrying an `error_message` (which may be empty).

## Layer One: `AgentSession` Automatic Retry

Implementation: `openprogram/agent/session.py::_run_with_retry` + `openprogram/agent/retry.py`.

Configuration `RetrySettings(enabled=True, max_retries=3, base_delay_ms=2000)`. After each `run()`, it checks the last assistant message:

- `is_retryable_error(msg)` is `True` and `max_retries` is not exceeded → retry after backoff.
- Backoff `compute_backoff_ms(attempt) = base * 2^(attempt-1)` → 2s, 4s, 8s.
- Before retrying, pop the previously failed assistant message off the history (`replace_messages(msgs[:-1])`) to avoid contaminating the next prompt.

The decision order in `is_retryable_error`:

```
stop_reason != "error"            → False (not an error, do not retry)
context overflow                  → False (hand off to compaction; retry won't fix it)
error_message is empty            → True
error_message matches _RETRY_PATTERN → True, otherwise False
```

`_RETRY_PATTERN` matches transient-failure signatures such as `overloaded`, `rate limit`, `429`, `5xx`, `service unavailable`, `connection error/refused`, `other side closed`, `fetch failed`, `reset before headers`, and `terminated`.

**Key design point: an empty `error_message` is treated as retryable.** When a provider stream drops mid-flight (connection reset, SSL EOF, gateway jitter), the stream often breaks before any structured error body arrives, so `error_message` is an empty string. If an empty message went through regex matching, it would match no pattern → be judged "not retryable" → this layer's retry would never fire → the error would sink all the way down and finally be papered over in `runtime.py` as an opaque `"Agent session failed"`. Treating an empty message as retryable directly plugs this hole: an error with empty content is almost certainly a dropped stream, and a dropped stream is a transient failure.

## Layer Two: `Runtime.exec()` Retry Loop

Implementation: `openprogram/agentic_programming/runtime.py::Runtime.exec` / `async_exec`.

Constructor parameter `max_retries=3` (default). When `_call` throws an exception:

```
TypeError / NotImplementedError → raise directly (programming error, do not retry)
_is_permanent_error(e)          → raise directly, annotated "failed permanently"
attempt == max_retries - 1      → raise, annotated "failed after N attempts"
otherwise                       → retry after time.sleep(_RETRY_BACKOFF * 2^attempt)
```

Backoff `_RETRY_BACKOFF=1.5`, i.e. 1.5s, 3s, 6s.

**Permanent-error decision** `_is_permanent_error`: it lowercases `type_name: exception_text` and matches against any substring in `_PERMANENT_ERROR_MARKERS`:

```
not a valid image / invalid image / image data is not   ← corrupt image in the request body
login expired / login failed / re-auth / unauthorized    ← auth invalidated on the gateway side
invalid api key / invalid_api_key                        ← credential error
```

The next identical request will fail in exactly the same way for these errors, so retrying merely wastes attempts and wall-clock time. The moment one is hit, give up immediately and spell out "permanently" in the error message, distinguishing it from "failed after N retries" to ease troubleshooting.

## The Two Retry Layers Multiply

Layer one (AgentSession) and layer two (exec) count independently. Worst case: exec retries 3 times, and each time the inner AgentSession retries another 3 times = 9 actual API calls, with backoff bringing total elapsed time to tens of seconds before final failure.

This is the result of historical accretion: the AgentSession layer is the long-standing general-purpose retry, while the exec layer is the Runtime's own protection. With both retained, their responsibilities overlap. If the AgentSession layer is confirmed to be reliably in effect, the exec layer can be lowered to `max_retries=1` (no retry), making retry a single responsibility. Currently both layers are on.

## Boundaries: What Retry Cannot Solve

- **Gateway auth invalidation** (`openai-codex` login expired/failed): a permanent error; the exec layer gives up immediately after hitting `_PERMANENT_ERROR_MARKERS`. Auth must be valid before a batch run; retry is not a remedy.
- **Context overflow**: explicitly excluded by `is_retryable_error`; it should be handled by compaction.
- **Programming errors** (`TypeError` / `NotImplementedError`): the function signature/implementation is wrong, and the exec layer raises directly.

## Diagnosability of Error Messages

When `agent.py` catches an exception, `err_text = f"{type(err).__name__}: {err}"` (degrading to just the type name when the exception text is empty), and the traceback is printed to stderr. The fallback message in `runtime.py` is no longer a bare `"Agent session failed"`, but instead carries a list of `type_name: exception_text` for each attempt along with the failure reason (permanently / after N attempts).
