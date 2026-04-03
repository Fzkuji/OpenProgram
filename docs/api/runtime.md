# agentic.Runtime

```python
class agentic.Runtime(call=None, model="default")
```

LLM runtime. Wraps a provider and handles Context integration.

Create once, use everywhere. When `exec()` is called inside an [@agentic_function](agentic_function.md):

1. **Reads** from the Context tree — calls [Context.summarize()](context.md#summarize) to build context text.
2. **Prepends** context as the first text block in the content list.
3. **Calls** `_call()` with the full content list.
4. **Records** `raw_reply` on the current Context node.

When called outside any `@agentic_function`, works as a plain LLM call with no context injection or recording.

### Constructor

```python
Runtime(call=None, model="default")
```

- **call** (`Callable | None`) — LLM provider function. Signature: `fn(content: list[dict], model: str, response_format: dict) -> str`. If `None`, subclass and override `_call()`.

- **model** (`str`, default `"default"`) — Default model name. Can be overridden per-call.

### Two ways to use

**1. Pass a call function:**

```python
rt = Runtime(call=my_func, model="gemini-2.5-flash")
```

**2. Subclass and override `_call()`:**

```python
class GeminiRuntime(Runtime):
    def _call(self, content, model="default", response_format=None):
        # convert content list → Gemini API format
        # return reply text
```

---

### exec

```python
Runtime.exec(content, context=None, response_format=None, model=None) -> str
```

Call the LLM with automatic Context integration.

**Parameters:**

- **content** (`list[dict]`) — List of content blocks:
  ```python
  {"type": "text", "text": "Find the login button."}
  {"type": "image", "path": "screenshot.png"}
  {"type": "audio", "path": "recording.wav"}
  {"type": "file", "path": "data.csv"}
  ```

- **context** (`str | None`, default `None`) — Override auto-generated context. If `None`, auto-generates from the Context tree.

- **response_format** (`dict | None`, default `None`) — Output format constraint (JSON schema). Passed to `_call()` for provider-native handling.

- **model** (`str | None`, default `None`) — Override the default model for this call.

**Returns:** `str` — the LLM's reply text.

**Guard:** Raises `RuntimeError` if called twice in the same `@agentic_function`.

---

### async_exec

```python
Runtime.async_exec(content, context=None, response_format=None, model=None) -> str
```

Async version of `exec()`. Calls `_async_call()` instead of `_call()`.

---

### _call

```python
Runtime._call(content, model="default", response_format=None) -> str
```

Override this in subclasses. Receives the full content list (context + user content) and returns reply text.

### _async_call

```python
Runtime._async_call(content, model="default", response_format=None) -> str
```

Async version of `_call()`.

---

### Example

```python
import google.generativeai as genai
from agentic import agentic_function, Runtime

genai.configure(api_key="...")

def gemini_call(content, model="gemini-2.5-flash", response_format=None):
    text_parts = [b["text"] for b in content if b["type"] == "text"]
    response = genai.GenerativeModel(model).generate_content("\n".join(text_parts))
    return response.text

rt = Runtime(call=gemini_call, model="gemini-2.5-flash")

@agentic_function
def observe(task):
    """Look at the screen and describe what you see."""
    return rt.exec(content=[
        {"type": "text", "text": f"Find: {task}"},
        {"type": "image", "path": "screenshot.png"},
    ])
```
