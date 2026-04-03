# agentic_function

```python
agentic.agentic_function(fn=None, *, render="summary", summarize=None, compress=False)
```

Decorator that records function execution into the [Context](context.md) tree.

Every decorated function is unconditionally recorded. On entry, a new [Context](context.md) node is created and attached to the current parent. On exit, the node is updated with the return value (or error) and timing.

### Parameters

- **render** (`str`, default `"summary"`) — How other functions see this node's results when they call [Context.summarize()](context.md#summarize).

  | Value | Output |
  |-------|--------|
  | `"trace"` | Prompt + full I/O + raw LLM reply + error |
  | `"detail"` | `name(params) → status duration \| input \| output` |
  | `"summary"` | `name: output_snippet duration` |
  | `"result"` | Return value only (JSON) |
  | `"silent"` | Not shown |

  This is a default. Callers can override it per-query with `ctx.summarize(level="detail")`.

- **summarize** (`dict | None`, default `None`) — What context this function sees when it calls [runtime.exec()](runtime.md).

  Dict of keyword arguments passed to [Context.summarize()](context.md#summarize). If `None`, `runtime.exec()` calls `ctx.summarize()` with defaults (all ancestors + all siblings).

  Common patterns:
  ```python
  summarize=None                            # see everything (default)
  summarize={"depth": 1, "siblings": 3}     # parent + last 3 siblings
  summarize={"depth": 0, "siblings": 0}     # see nothing
  summarize={"siblings": 1}                 # all ancestors + last sibling
  ```

  See [Context.summarize()](context.md#summarize) for all available keys.

- **compress** (`bool`, default `False`) — After this function completes, hide its children from [Context.summarize()](context.md#summarize).

  When `True`, other functions see only this node's rendered result — the children (sub-calls) are not expanded. The children are still fully recorded; [Context.tree()](context.md#tree) and [Context.save()](context.md#save) always show everything.

### Example

```python
from agentic import agentic_function

# Simplest usage: all defaults.
@agentic_function
def observe(task):
    """Look at the screen and describe what you see."""
    ...

# Customized: limited context, compressed output.
@agentic_function(render="detail", summarize={"depth": 1, "siblings": 3}, compress=True)
def navigate(target):
    """Navigate to a target UI element."""
    observe(f"find {target}")
    act(target)
    return {"success": True}
```
