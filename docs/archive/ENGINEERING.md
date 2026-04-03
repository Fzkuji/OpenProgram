# Context API Reference

> `agentic/` — the complete API for Context recording and reading.

---

## @agentic_function

```python
from agentic import agentic_function

@agentic_function(render="summary", summarize=None, compress=False)
def my_function(...): ...
```

Decorator. Every decorated function is unconditionally recorded into the Context tree.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `render` | `str` | `"summary"` | How others see my results via summarize() |
| `summarize` | `dict \| None` | `None` | What context I see when calling the LLM |
| `compress` | `bool` | `False` | After completion, hide my children from summarize() |

### render

Controls the default detail level when OTHER functions view this node through `summarize()`.

| Value | What it shows | Example output |
|-------|---------------|----------------|
| `"trace"` | Everything: prompt, I/O, raw LLM reply | Multi-line with all fields |
| `"detail"` | Signature + I/O on one line | `observe(task="login") → success 1200ms \| input: {...} \| output: {...}` |
| `"summary"` | Name + output snippet | `observe: {"found": true} 1200ms` |
| `"result"` | Return value only | `{"found": true}` |
| `"silent"` | Not shown | *(empty)* |

This is a default. Callers can override it: `ctx.summarize(level="detail")` forces all nodes to render as "detail".

### summarize

Dict of keyword arguments passed to `ctx.summarize()` when `runtime.exec()` auto-generates context.

```python
# See everything (default when summarize=None)
@agentic_function
def plan(task): ...

# Only parent + last 3 siblings
@agentic_function(summarize={"depth": 1, "siblings": 3})
def observe(task): ...

# Isolated: see nothing from the tree
@agentic_function(summarize={"depth": 0, "siblings": 0})
def run_ocr(img): ...
```

Allowed keys: `depth`, `siblings`, `level`, `include`, `exclude`, `branch`, `max_tokens`.
See [Context.summarize()](#contextsummarize) for details on each.

### compress

When `True`, after this function completes, `summarize()` renders only this node's own result — children are NOT expanded, even if `branch=` is used.

```python
@agentic_function(compress=True)
def navigate(target):
    observe(...)    # child — hidden after navigate completes
    act(...)        # child — hidden after navigate completes
    return {"success": True}

# Later, another function sees:
#   navigate: {"success": true} 3200ms
# NOT:
#   navigate: {"success": true} 3200ms
#     observe: {"found": true} 1200ms
#     act: {"clicked": true} 820ms
```

The children are still fully recorded. `tree()` and `save()` always show everything.

### Example

```python
from agentic import agentic_function

@agentic_function
def observe(task):
    """Look at the screen and describe what you see."""
    ...

@agentic_function(render="detail", summarize={"depth": 1, "siblings": 1}, compress=True)
def navigate(target):
    """Navigate to a target UI element."""
    ...
```

---

## runtime.exec()

```python
from agentic import runtime

reply = runtime.exec(
    prompt="What do you see?",
    input={"task": "find login"},
    images=["/tmp/screenshot.png"],
    call=my_llm_provider,
)
```

Calls an LLM and auto-records to the current Context node.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | `str` | *(required)* | Instructions for the LLM |
| `input` | `dict \| None` | `None` | Structured data (serialized as JSON) |
| `images` | `list[str] \| None` | `None` | Image file paths |
| `context` | `str \| None` | `None` | Override auto-generated context |
| `schema` | `dict \| None` | `None` | Expected JSON output schema |
| `model` | `str` | `"sonnet"` | Model name or alias |
| `call` | `Callable \| None` | `None` | LLM provider: `fn(messages, model) -> str` |

### Context injection

When `context=None` (default), runtime.exec() auto-generates context:

```python
# If the function has summarize config:
context = ctx.summarize(**decorator_summarize_dict)

# Otherwise:
context = ctx.summarize()  # all ancestors + all siblings
```

To skip auto-injection, pass `context=""`.
To use custom context, pass `context="your text here"`.

### What gets recorded

On the current Context node:
- `input` ← the `input` parameter
- `media` ← the `images` parameter
- `raw_reply` ← the LLM's response

### Message layout

Messages are ordered for maximum prompt cache hit rate:

```
1. [Context]     ← stable prefix from summarize() (cached across calls)
2. [Understood]  ← assistant ack (keeps prefix cacheable)
3. Prompt+Input  ← new content (changes each call)
4. Schema        ← JSON constraint (if any)
```

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

---

## Context

```python
from agentic import Context
```

Dataclass. One instance per function call. Users never create these directly — `@agentic_function` handles it.

### Fields

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| `name` | `str` | decorator | Function name |
| `prompt` | `str` | decorator | Docstring |
| `params` | `dict` | decorator | Call arguments |
| `output` | `Any` | decorator | Return value |
| `error` | `str` | decorator | Error message if failed |
| `status` | `str` | decorator | `"running"` → `"success"` / `"error"` |
| `parent` | `Context` | decorator | Parent node in the tree |
| `children` | `list` | decorator | Child nodes |
| `render` | `str` | decorator | Default render level |
| `compress` | `bool` | decorator | Hide children after completion |
| `start_time` | `float` | decorator | Start timestamp |
| `end_time` | `float` | decorator | End timestamp |
| `input` | `dict` | runtime.exec() | Data sent to LLM |
| `media` | `list` | runtime.exec() | Image/file paths sent to LLM |
| `raw_reply` | `str` | runtime.exec() | LLM response text |

### Properties

**`path`** — auto-computed tree address.

```
"root/navigate_0/observe_1/run_ocr_0"
```

Format: `{parent_path}/{name}_{index}`. Index counts same-name siblings.

**`duration_ms`** — execution time in milliseconds.

---

### Context.summarize()

```python
ctx.summarize(
    depth=-1, siblings=-1, level=None,
    include=None, exclude=None, branch=None, max_tokens=None,
)
```

Query the tree. Returns a text string for LLM input.

#### Parameters

| Parameter | Type | Default | Effect |
|-----------|------|---------|--------|
| `depth` | `int` | `-1` | Ancestor levels. -1=all, 0=none, 1=parent only |
| `siblings` | `int` | `-1` | Previous siblings. -1=all, 0=none, N=last N |
| `level` | `str \| None` | `None` | Override render level for all nodes |
| `include` | `list[str] \| None` | `None` | Path whitelist (supports `*` wildcard) |
| `exclude` | `list[str] \| None` | `None` | Path blacklist (supports `*` wildcard) |
| `branch` | `list[str] \| None` | `None` | Expand children of named nodes |
| `max_tokens` | `int \| None` | `None` | Token budget (drops oldest siblings first) |

#### Default behavior

All ancestors + all same-level siblings. Siblings' children are NOT shown.

This means every call sees the previous call's output as part of the prefix, plus new content at the end — maximizing prompt cache hits.

#### Examples

```python
# Default: everything (all ancestors + all siblings)
ctx.summarize()

# Parent + last 3 siblings
ctx.summarize(depth=1, siblings=3)

# Nothing (isolated)
ctx.summarize(depth=0, siblings=0)

# Force all nodes to render as detail
ctx.summarize(level="detail")

# Only nodes under navigate_0
ctx.summarize(include=["root/navigate_0/*"])

# Expand observe's children
ctx.summarize(branch=["observe"])

# With token budget
ctx.summarize(max_tokens=1000)
```

#### Output examples

Given this tree, with `verify` as the current node:

```
root
└── navigate("login")
    ├── observe("find login")   → {"found": true}   1200ms
    ├── act("click login")      → {"clicked": true}  820ms
    └── verify("check")         ← current
```

```python
# ctx.summarize()  →
[Ancestor: root()]
[Ancestor: navigate(target="login")]
observe: {"found": true} 1200ms
act: {"clicked": true} 820ms

# ctx.summarize(depth=0, siblings=1)  →
act: {"clicked": true} 820ms

# ctx.summarize(level="detail")  →
[Ancestor: root()]
[Ancestor: navigate(target="login")]
observe(task="find login") → success 1200ms | input:  | output: {"found": true}
act(target="click login") → success 820ms | input:  | output: {"clicked": true}
```

---

### Context.tree()

```python
ctx.tree(indent=0) -> str
```

Full tree view for debugging. Shows ALL nodes regardless of render/compress settings.

```
root …
  navigate ✓ 3200ms → {'success': True}
    observe ✓ 1200ms → {'found': True}
    act ✓ 820ms → {'clicked': True}
    verify ✓ 200ms → {'passed': True}
```

---

### Context.traceback()

```python
ctx.traceback() -> str
```

Error traceback, similar to Python's format.

```
Agentic Traceback:
  navigate(target="login") → error, 4523ms
    observe(task="find login") → success, 1200ms
    act(target="login") → error, 820ms
      error: element not interactable
```

---

### Context.save()

```python
ctx.save(path: str)
```

Save the tree to a file.

- `.md` → human-readable tree (same as `tree()`)
- `.jsonl` → one JSON object per node, with all fields

---

## Module Functions

```python
from agentic import get_context, get_root_context, init_root
```

| Function | Returns | Description |
|----------|---------|-------------|
| `get_context()` | `Context \| None` | Current active node. `None` if outside any `@agentic_function`. |
| `get_root_context()` | `Context \| None` | Root of the tree. Walks up from current node. |
| `init_root(name="root")` | `Context` | Manually create a root. Usually not needed. |
