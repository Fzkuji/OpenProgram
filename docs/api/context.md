# agentic.Context

```python
class agentic.Context
```

Execution record for one function call. Created automatically by [@agentic_function](agentic_function.md). Users do not instantiate this class directly.

Each `Context` node stores the function's name, arguments, return value, timing, and (if [runtime.exec()](runtime.md) was called) the LLM input/output. Nodes are linked via `parent` and `children` to form a tree.

### Fields

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| `name` | `str` | [@agentic_function](agentic_function.md) | Function name |
| `prompt` | `str` | [@agentic_function](agentic_function.md) | Docstring |
| `params` | `dict` | [@agentic_function](agentic_function.md) | Call arguments |
| `output` | `Any` | [@agentic_function](agentic_function.md) | Return value |
| `error` | `str` | [@agentic_function](agentic_function.md) | Error message if failed |
| `status` | `str` | [@agentic_function](agentic_function.md) | `"running"` → `"success"` or `"error"` |
| `parent` | `Context` | [@agentic_function](agentic_function.md) | Parent node |
| `children` | `list[Context]` | [@agentic_function](agentic_function.md) | Child nodes |
| `render` | `str` | [@agentic_function](agentic_function.md) | Default render level (see [agentic_function](agentic_function.md)) |
| `compress` | `bool` | [@agentic_function](agentic_function.md) | Hide children after completion (see [agentic_function](agentic_function.md)) |
| `start_time` | `float` | [@agentic_function](agentic_function.md) | Start timestamp |
| `end_time` | `float` | [@agentic_function](agentic_function.md) | End timestamp |
| `raw_reply` | `str` | [Runtime.exec()](runtime.md) | LLM response text |

### Properties

#### path

```python
Context.path -> str
```

Auto-computed tree address. Format: `{parent_path}/{name}_{index}`.

The index counts same-name siblings under the same parent: `observe_0` is the first `observe`, `observe_1` is the second, etc.

```
"navigate_0/observe_1/run_ocr_0"
```

#### duration_ms

```python
Context.duration_ms -> float
```

Execution time in milliseconds. Returns `0.0` if the function is still running.

---

### summarize

```python
Context.summarize(depth=-1, siblings=-1, level=None, include=None, exclude=None, branch=None, max_tokens=None) -> str
```

Query the Context tree and return a text string for LLM input. This is how Context data flows into LLM calls — [runtime.exec()](runtime.md) calls this automatically.

**Parameters:**

- **depth** (`int`, default `-1`) — How many ancestor levels to show. `-1` = all, `0` = none, `1` = parent only, `N` = up to N levels.

- **siblings** (`int`, default `-1`) — How many previous siblings to show. `-1` = all, `0` = none, `N` = last N (keeps the N closest to current, in chronological order).

- **level** (`str | None`, default `None`) — Override render level for all nodes. If `None`, each node uses its own `render` setting. Values: `"trace"` / `"detail"` / `"summary"` / `"result"` / `"silent"`.

- **include** (`list[str] | None`, default `None`) — Path whitelist. Only nodes whose path matches are shown. Supports `*` wildcard.

- **exclude** (`list[str] | None`, default `None`) — Path blacklist. Nodes whose path matches are hidden. Supports `*` wildcard.

- **branch** (`list[str] | None`, default `None`) — Expand children of named nodes. By default, siblings are one line each (children not shown). Respects `compress`: compressed nodes are not expanded.

- **max_tokens** (`int | None`, default `None`) — Token budget. When exceeded, drops oldest siblings first. Uses `len(text) / 4` as estimate.

**Default behavior:**

All ancestors (root → parent) + all same-level siblings that completed before this node. Siblings' children are not shown.

**Returns:** `str` — text ready for LLM prompt injection. Empty string if nothing to show.

**Example:**

```python
ctx.summarize()                                 # all ancestors + all siblings
ctx.summarize(depth=1, siblings=3)              # parent + last 3 siblings
ctx.summarize(depth=0, siblings=0)              # nothing (isolated)
ctx.summarize(level="detail")                   # override all render levels
ctx.summarize(include=["root/navigate_0/*"])    # path whitelist
ctx.summarize(branch=["observe"])               # expand observe's children
ctx.summarize(max_tokens=1000)                  # with token budget
```

Given this tree, with `verify` as the current node:

```
root
└── navigate("login")
    ├── observe("find login")   → {"found": true}   1200ms
    ├── act("click login")      → {"clicked": true}  820ms
    └── verify("check")         ← current
```

```python
ctx.summarize()
# [Ancestor: root()]
# [Ancestor: navigate(target="login")]
# observe: {"found": true} 1200ms
# act: {"clicked": true} 820ms

ctx.summarize(depth=0, siblings=1)
# act: {"clicked": true} 820ms
```

---

### tree

```python
Context.tree(indent=0) -> str
```

Full tree view for debugging. Shows ALL nodes regardless of `render` or `compress` settings.

**Example:**

```
root …
  navigate ✓ 3200ms → {'success': True}
    observe ✓ 1200ms → {'found': True}
    act ✓ 820ms → {'clicked': True}
    verify ✓ 200ms → {'passed': True}
```

---

### traceback

```python
Context.traceback() -> str
```

Error traceback in a format similar to Python's.

**Example:**

```
Agentic Traceback:
  navigate(target="login") → error, 4523ms
    observe(task="find login") → success, 1200ms
    act(target="login") → error, 820ms
      error: element not interactable
```

---

### save

```python
Context.save(path: str)
```

Save the full tree to a file.

- `.md` → human-readable tree (same output as [tree()](#tree))
- `.jsonl` → one JSON object per node, with all fields
