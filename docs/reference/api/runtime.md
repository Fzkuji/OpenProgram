# Runtime

> Source: [`openprogram/agentic_programming/runtime.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agentic_programming/runtime.py)

The LLM runtime. Wraps an LLM provider, automatically computes context from the session DAG, calls the LLM, and writes the reply back to the DAG.

---

## Class: `Runtime`

```python
class Runtime(call=None, model="default", max_retries=None, api_key=None, skills=None)
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `call` | `Callable \| None` | `None` | A user-supplied LLM function. Signature: `fn(content: list[dict], model: str, response_format: dict) -> str`. Internally it is wrapped into the standard provider path via a `CallableModel`, so DAG recording and history rendering work the same as with a real provider. If neither `call` nor a `"provider:model_id"` model is given, subclass and override `_call()` |
| `model` | `str` | `"default"` | The default model. Two forms: `"provider:model_id"` (e.g. `"anthropic:claude-sonnet-4-6"`) resolves through `openprogram.providers` and streams via the provider layer; any other string is only meaningful together with `call=` or a subclass. Unknown `"provider:model_id"` values raise `ValueError` |
| `max_retries` | `int \| None` | `None` | Maximum number of exec() attempts (including the first call, and must be >= 1). `None` = read the environment variable `OPENPROGRAM_MAX_RETRIES`, defaulting to `6` if unset |
| `api_key` | `str \| None` | `None` | API key for the provider path. `None` = resolved from the credential store (`openprogram providers login`) |
| `skills` | `bool \| list[str] \| None` | `None` | Skill discovery for the system prompt. `None` / `False` = disabled; `True` = probe the default skill directories (user + repo); `list[str]` = explicit directory list. When enabled, an `<available_skills>` block is appended to the system prompt on every `exec()` |

### Attributes

| Attribute | Type | Description |
|------|------|------|
| `model` | `str` | The default model name |
| `max_retries` | `int` | The resolved retry budget |
| `system` | `str` | Assignable system prompt used by `exec()` on the provider path (the `@agentic_function(system=...)` decorator sets it for the duration of a call) |
| `thinking_level` | `str` | Reasoning-effort knob: `"off"` (default) / `"low"` / `"medium"` / `"high"` / `"xhigh"`; passed through to the provider |
| `session_id` | `str` | Stable id across successive `exec()` calls (`"op-<hex>"`); providers use it as the prompt-cache key |
| `on_stream` | `Callable \| None` | Optional callback `fn(event_dict)` for streaming events (text / thinking / tool_use / tool_result) |
| `last_usage` | `dict \| None` | Token usage of the last call: `{input_tokens, output_tokens, total_tokens, cache_read, cache_create, ...}` |

---

## Methods

### `exec()`

```python
Runtime.exec(content, context=None, response_format=None, model=None,
             tools=None, toolset=None, tools_source=None, tools_allow=None,
             tools_deny=None, tool_choice="auto", parallel_tool_calls=True,
             max_iterations=20, choices=None, timeout_s=None, on_retry=None,
             web_search=False, stream_fn=None) -> Any
```

Calls the LLM, with context computed automatically from the session DAG.

**When called inside an `@agentic_function`:**
1. Starting from the current function's DAG node, `render_context` uses `expose` / `render_range` to determine which historical nodes to read this time
2. `render_dag_messages` renders those nodes into messages
3. `_call()` is invoked to send the request
4. The reply is written into the `llm` node that `exec()` opened at the start of the call

**When called with no DAG store installed** (standalone scripts, no dispatcher): `content` is wrapped into a single user message and sent as a single-turn call; nothing is recorded.

A single `@agentic_function` can call `exec()` multiple times; each call is a new `llm` node on the DAG.

#### Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `content` | `list[dict] \| str` | *(required)* | List of content blocks (see format below). A plain string is wrapped into one text block |
| `context` | `str \| None` | `None` | Legacy parameter, ignored — the provider path builds history from the DAG |
| `response_format` | `dict \| None` | `None` | Output format constraint (JSON schema), forwarded to `_call()`. The default provider path does not apply it; honor it by overriding `_call()` in a subclass |
| `model` | `str \| None` | `None` | Override the default model |
| `tools` | `list \| None` | `None` | The tools available to the LLM for this call. Entries may be `@agentic_function`s, `{"spec":..., "execute":...}` dicts, or objects with `.spec` / `.execute`. If set, the tool loop runs until the model returns plain text. **Default (`None`) is not "no tools"**: the call gets the full registered toolset; pass `toolset="none"` for a reasoning-only call, or `tools=[]` for an explicit empty list |
| `toolset` / `tools_source` / `tools_allow` / `tools_deny` | — | `None` | Toolset preset and policy filtering: `toolset` names a preset (`"full"` is the implicit default, `"none"` opts out), `tools_source` filters per channel source, `tools_allow` / `tools_deny` are name allow/deny lists |
| `tool_choice` | `str \| dict` | `"auto"` | `"auto"` / `"required"` / `"none"` / `{"type":"function","name":"X"}` to force a specific tool. Passed through to the provider (OpenAI / Anthropic / Gemini / Bedrock each map it to their own protocol form) |
| `parallel_tool_calls` | `bool` | `True` | Allow multiple tool calls in a single turn; `False` is passed through to providers that support the switch |
| `max_iterations` | `int` | `20` | Upper bound on tool-loop iterations (one iteration = one model call plus its tool execution). The effective value is `max(1, min(50, max_iterations))`, where 50 is the hard limit in `agent_loop.py` |
| `choices` | `dict \| list \| None` | `None` | If set, constrains the **end** of the turn: after the model finishes the full turn, its final reply must pick one of `choices`; `exec` parses and returns the result of that choice. See [next-step-decision](../../capabilities/agentic-programming/choosing-the-next-step/next-step-decision.md) for details |
| `timeout_s` | `float \| None` | `None` | The wall-clock time budget for the entire `exec()` (including all retry sleeps); on timeout, raises `LLMError` (`reason=TIMEOUT`, `retryable=False`). `None` = fall back to the `OPENPROGRAM_EXEC_TIMEOUT_S` environment variable (unset or `0` = unbounded) |
| `on_retry` | `Callable \| None` | `None` | An observation callback invoked before each backoff sleep (once per failed attempt that has a retry queued), receiving a `RetryInfo`; not fired for the terminal failure. Exceptions raised inside the callback are swallowed |
| `web_search` | `bool` | `False` | Enable the provider's native web-search tool for this call, where supported |
| `stream_fn` | — | `None` | Per-call stream-function override (used by the dispatcher and tests to inject a fake or pre-built stream); `None` = the real provider |

#### Content block format

```python
{"type": "text",  "text": "Find the login button."}
{"type": "image", "path": "screenshot.png"}
{"type": "image", "data": "<base64>", "mime_type": "image/png"}
{"type": "video", "path": "clip.mp4"}
{"type": "audio", "path": "recording.wav"}
```

Media blocks take either a `path` (read and base64-encoded automatically, mime type guessed from the extension) or inline `data` + `mime_type`. A text block may carry `"role": "system"` to contribute to the system prompt, and text/image blocks accept `cache_control` for provider prompt caching. Unknown block types are skipped silently.

#### Return value

`str` — the LLM's reply text. With `choices`, returns the parsed decision result (the return value of the selected function, or the selected value itself).

#### Exceptions

- `RuntimeError` — the runtime is closed (`close()` was called)
- `TypeError` / `NotImplementedError` — raised immediately, never retried (programming errors: wrong call signature, no provider configured)
- `LLMError` — raised when retries are exhausted or a non-retryable error is hit; structured fields include `reason` / `retryable` / `http_status` / `retry_after_s` / `attempts` / `elapsed_s` / `provider` / `model`, etc.

---

### `async_exec()`

```python
await Runtime.async_exec(content, context=None, response_format=None, model=None,
                         timeout_s=None, on_retry=None) -> str
```

The async version of `exec()`. Internally calls `_async_call()`, which by default only supports a `call=` function (sync or async — a sync one is adapted automatically); the `"provider:model_id"` path requires overriding `_async_call()` in a subclass. Same `timeout_s` / `on_retry` semantics as `exec()`; retries sleep with `asyncio.sleep`, so external cancellation works. No tool-loop parameters — `async_exec()` is a plain single-reply call.

---

### `_call()`

```python
Runtime._call(content, model="default", response_format=None) -> str
```

The method that actually calls the LLM once (no retry — `exec()` wraps it in the retry loop). The default implementation routes through the provider layer (`AgentSession`) when a provider model or `call=` function is configured, and raises `NotImplementedError` otherwise. **Override this method when subclassing.**

#### Parameters

| Parameter | Type | Description |
|------|------|------|
| `content` | `list[dict]` | The current turn's content blocks (history is rendered from the DAG by the provider path) |
| `model` | `str` | The model name |
| `response_format` | `dict \| None` | Output format constraint |

#### Return value

`str` — the LLM reply text.

---

### `_async_call()`

```python
await Runtime._async_call(content, model="default", response_format=None) -> str
```

The async version of `_call()`. Override this method when subclassing to support an async provider.

---

### `close()`

Releases resources and ends the session; after `close()`, `exec()` raises `RuntimeError`. `Runtime` is also a context manager (`with Runtime(...) as rt:` closes on exit). Subclasses override it to clean up provider-specific resources.

---

### Asking the user

When a front-end session is connected, a runtime can block on user input mid-function: `runtime.ask(prompt, options=..., multi=..., questions=[...], timeout=300.0, default=None)` (one question or several in one card), `runtime.confirm(prompt, default=False)` (yes/no), and `runtime.form(prompt, fields)` (multi-field form). `runtime.can_ask()` reports whether anyone is there to answer (False on headless runs). A declined question raises `UserDeclined`; a timeout returns `default` when given, else raises `AskTimeout`.

---

## Usage

### Option 1: Pass in a call function

```python
from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime

def my_llm(content, model="sonnet", response_format=None):
    # Convert content into your provider's format and send the request
    texts = [b["text"] for b in content if b["type"] == "text"]
    return call_my_api("\n".join(texts), model=model)

runtime = Runtime(call=my_llm, model="sonnet")

@agentic_function
def observe(task):
    """Look at the screen."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Find: {task}"},
        {"type": "image", "path": "screenshot.png"},
    ])
```

### Option 2: Subclass

```python
class AnthropicRuntime(Runtime):
    def __init__(self, api_key, model="sonnet"):
        super().__init__(model=model)
        self.client = anthropic.Anthropic(api_key=api_key)

    def _call(self, content, model="sonnet", response_format=None):
        messages_content = []
        for block in content:
            if block["type"] == "text":
                messages_content.append({"type": "text", "text": block["text"]})
        response = self.client.messages.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": messages_content}],
        )
        return response.content[0].text

runtime = AnthropicRuntime(api_key="sk-...", model="claude-sonnet-4-6")
```

### Multiple Runtimes coexisting

```python
fast = Runtime(call=gemini_call, model="gemini-2.5-flash")
strong = Runtime(call=claude_call, model="sonnet")

@agentic_function
def observe(task):
    """Quick observation with cheap model."""
    return fast.exec(content=[...])

@agentic_function
def plan(goal):
    """Complex planning with strong model."""
    return strong.exec(content=[...])
```

---

## Retry mechanism

`exec()` and `async_exec()` have built-in automatic retries to handle transient LLM API errors (network timeouts, rate limits, server errors, etc.).

### Configuration

```python
# Default: max_retries=None → read env var OPENPROGRAM_MAX_RETRIES, defaulting to 6 if unset
rt = Runtime(call=my_llm)

# No retries (raise an exception on the first failure)
rt = Runtime(call=my_llm, max_retries=1)

# Multiple retries (for an unstable API)
rt = Runtime(call=my_llm, max_retries=5)
```

### Behavior rules

| Situation | Handling |
|------|------|
| API call succeeds | Return the result |
| API raises a transient exception | Record the failed attempt, sleep with exponential backoff (base 1.5 s x 2^attempt, +/-25% jitter; a server `Retry-After` hint is honored as a lower bound; base tunable via `OPENPROGRAM_RETRY_BACKOFF_BASE`), then retry until `max_retries` is reached |
| Permanent error (bad image data, expired auth, invalid API key, or the provider marked the exception `retryable=False`) | Raised immediately as `LLMError` with `retryable=False`, no retry |
| The provider already exhausted its own transport-retry budget (`transport_exhausted`) | Not re-retried — raised as `LLMError` |
| `TypeError` or `NotImplementedError` | Raised immediately, no retry (usually a problem with the provider implementation or the way it's called) |
| All retries fail | Raise a structured `LLMError` (fields such as `reason` / `retryable` / `http_status` / `attempts`), with a full attempt report attached |

### Error report format

When all retries are exhausted, the `LLMError` raised contains the error information for each attempt; its structured fields (`reason` / `retryable` / `http_status` / `attempts` / `elapsed_s`, etc.) can be read directly:

```
LLMError: exec() failed after 3 attempt(s):
Attempt 1: ConnectionError: timeout
Attempt 2: RateLimitError: 429 Too Many Requests
Attempt 3: ConnectionError: timeout
```

### The boundaries of retrying

`max_retries` only handles transient failures at the API level (network timeouts, rate limits, etc.). If the problem lies in the function's own logic or output format, retrying won't fix it — edit the function code directly; see [`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md).
