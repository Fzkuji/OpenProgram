# agentic.runtime

```python
agentic.runtime.exec(prompt, input=None, images=None, context=None, schema=None, model="sonnet", call=None) -> str
```

Call an LLM and auto-record to the current [Context](context.md) node.

When called inside an [@agentic_function](agentic_function.md):

1. **Reads** from the Context tree — calls [Context.summarize()](context.md#summarize) using the function's `summarize` config to build context text.
2. **Calls** the LLM — builds a message list and passes it to the `call` function.
3. **Records** to the Context node — stores `input`, `media`, and `raw_reply`.

When called outside any `@agentic_function`, works as a plain LLM call with no context injection or recording.

### Parameters

- **prompt** (`str`) — Instructions for the LLM.

- **input** (`dict | None`, default `None`) — Structured data to include. Serialized as JSON under an `[Input]` header.

- **images** (`list[str] | None`, default `None`) — Image file paths to include. Currently passed as text placeholders; actual encoding depends on the `call` provider.

- **context** (`str | None`, default `None`) — Override auto-generated context. If `None`, auto-generates from the Context tree using the function's `summarize` config. If provided, used as-is.

- **schema** (`dict | None`, default `None`) — Expected JSON output schema. Appended as a "return only valid JSON" instruction.

- **model** (`str`, default `"sonnet"`) — Model name or alias. Passed to the `call` function.

- **call** (`Callable | None`, default `None`) — LLM provider function. Signature: `fn(messages: list[dict], model: str) -> str`. If `None`, raises `NotImplementedError`.

### Returns

`str` — the LLM's reply text.

### Context injection

When `context=None` (default):

```python
# If the function has summarize config:
context = ctx.summarize(**decorator_summarize_dict)

# Otherwise:
context = ctx.summarize()  # all ancestors + all siblings
```

To skip auto-injection, pass `context=""`.

### What gets recorded

On the current [Context](context.md) node:

| Field | Source |
|-------|--------|
| `input` | the `input` parameter |
| `media` | the `images` parameter |
| `raw_reply` | the LLM's response |

### Example

```python
from agentic import agentic_function, runtime

@agentic_function(summarize={"depth": 1, "siblings": 1})
def observe(task):
    """Look at the screen and describe what you see."""
    img = take_screenshot()
    return runtime.exec(
        prompt=observe.__doc__,
        input={"task": task},
        images=[img],
        call=my_llm_provider,
    )
```
