# Runtime

> Source: [`openprogram/agentic_programming/runtime.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agentic_programming/runtime.py)

The LLM runtime. Wraps an LLM provider, automatically computes context from the session DAG, calls the LLM, and writes the reply back to the DAG.

---

## Class: `Runtime`

```python
class Runtime(call=None, model="default")
```

### Constructor parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `call` | `Callable \| None` | `None` | The LLM provider function. Signature: `fn(content: list[dict], model: str, response_format: dict) -> str`. If not provided, you must subclass and override `_call()` |
| `model` | `str` | `"default"` | The default model name; can be overridden on each call |
| `max_retries` | `int \| None` | `None` | Maximum number of exec() attempts (including the first call, and must be >= 1). `None` = read the environment variable `OPENPROGRAM_MAX_RETRIES`, defaulting to `6` if unset |

### Attributes

| Attribute | Type | Description |
|------|------|------|
| `model` | `str` | The default model name |

---

## Methods

### `exec()`

```python
Runtime.exec(content, context=None, response_format=None, model=None,
             tools=None, toolset=None, tools_source=None, tools_allow=None,
             tools_deny=None, tool_choice="auto", parallel_tool_calls=True,
             max_iterations=20, choices=None, timeout_s=None, on_retry=None) -> Any
```

Calls the LLM, with context computed automatically from the session DAG.

**When called inside an `@agentic_function`:**
1. Starting from the current function's DAG node, `render_context` uses `expose` / `render_range` to determine which historical nodes to read this time
2. `render_dag_messages` renders those nodes into messages
3. `_call()` is invoked to send the request
4. The reply is written as a new `llm` node and appended to the DAG

**When called outside an `@agentic_function`:** the LLM is called directly, with no context computation and no DAG write (it degrades to a single-turn call).

A single `@agentic_function` can call `exec()` multiple times; each call is a new `llm` node on the DAG.

#### Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `content` | `list[dict]` | *(required)* | List of content blocks (see format below) |
| `context` | `str \| None` | `None` | Manually override the automatically computed context. `None` = compute automatically from the DAG |
| `response_format` | `dict \| None` | `None` | Output format constraint (JSON schema), passed to `_call()` |
| `model` | `str \| None` | `None` | Override the default model |
| `tools` | `list \| None` | `None` | The tools available to the LLM for this call. If set, the tool loop runs until the model returns plain text |
| `toolset` / `tools_source` / `tools_allow` / `tools_deny` | — | `None` | Toolset and policy filtering |
| `tool_choice` | `str \| dict` | `"auto"` | `"auto"` / `"required"` / `"none"` / `{"type":"function","name":"X"}` to force a specific tool. Passed through to the provider (OpenAI / Anthropic / Gemini / Bedrock each map it to their own protocol form) |
| `parallel_tool_calls` | `bool` | `True` | Allow multiple tool calls in a single turn; `False` is passed through to providers that support the switch |
| `max_iterations` | `int` | `20` | Upper bound on tool-loop iterations (one iteration = one model call plus its tool execution). The effective value is `min(50, max_iterations)`, where 50 is the hard limit in `agent_loop.py` |
| `choices` | `dict \| list \| None` | `None` | If set, constrains the **end** of the turn: after the model finishes the full turn, its final reply must pick one of `choices`; `exec` parses and returns the result of that choice. See [next-step-decision](../../capabilities/agentic-programming/choosing-the-next-step/next-step-decision.md) for details |
| `timeout_s` | `float \| None` | `None` | The wall-clock time budget for the entire `exec()` (including all retries); on timeout, raises `LLMError` (`reason=timeout`) |
| `on_retry` | `Callable \| None` | `None` | An observation callback invoked before each retry, receiving a `RetryInfo`; exceptions raised inside the callback are ignored |

#### Content block format

```python
{"type": "text",  "text": "Find the login button."}
{"type": "image", "path": "screenshot.png"}
{"type": "audio", "path": "recording.wav"}
{"type": "file",  "path": "data.csv"}
```

#### Return value

`str` — the LLM's reply text. With `choices`, returns the parsed decision result (the return value of the selected function, or the selected value itself).

#### Exceptions

- `RuntimeError` — called twice within the same `@agentic_function`
- `TypeError` — an async call function was passed in (use `async_exec()` instead)
- `NotImplementedError` — no call function configured
- `LLMError` — raised when retries are exhausted or a non-retryable error is hit; structured fields include `reason` / `retryable` / `http_status` / `attempts`, etc.

---

### `async_exec()`

```python
await Runtime.async_exec(content, context=None, response_format=None, model=None) -> str
```

The async version of `exec()`. Internally calls `_async_call()`.

Parameters and behavior are identical to `exec()`. If a synchronous call function is passed in, it is adapted automatically (no error).

---

### `_call()`

```python
Runtime._call(content, model="default", response_format=None) -> str
```

The method that actually calls the LLM. **Override this method when subclassing.**

#### Parameters

| Parameter | Type | Description |
|------|------|------|
| `content` | `list[dict]` | The full content list (context + user content) |
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
| API raises an exception (other than `TypeError` / `NotImplementedError`) | Record the failed attempt, then keep retrying until `max_retries` is reached |
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

```python
runtime = Runtime(call=my_llm, max_retries=3)

try:
    result = my_agentic_function(...)
except Exception:
    my_agentic_function = fix(
        fn=my_agentic_function,
        runtime=runtime,
        instruction="Handle empty input and always return valid JSON.",
    )
```
