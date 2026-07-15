# agentic_function

> Source: [`openprogram/agentic_programming/function.py`](https://github.com/Fzkuji/OpenProgram/blob/main/openprogram/agentic_programming/function.py)

`@agentic_function` turns an ordinary Python function into an Agentic Function: each call is recorded as a `code` node in the session DAG, and the `runtime.exec` calls inside the function body are recorded as `llm` nodes.

For the full authoring conventions—file layout, the division of labor between the docstring and `content`, parameter metadata, the validation checklist, and smoke tests—see [`skills/agentic-programming/SKILL.md`](https://github.com/Fzkuji/OpenProgram/blob/main/skills/agentic-programming/SKILL.md). This document only covers the decorator itself.

## Usage

```python
from openprogram import agentic_function

@agentic_function
def f(x: str, runtime) -> str:
    """One-line summary of what f does."""
    return runtime.exec(content=[{"type": "text", "text": f"...{x}..."}])
```

You can use bare `@agentic_function` or the parameterized form `@agentic_function(...)`.

## Decorator parameters

### Agentic-specific parameters

| Parameter | Type | Default | Description |
|------|------|------|------|
| `expose` | `str` | `"io"` | **Outward-facing**: what others can see about me when they render the DAG. `"io"` = only the function's name and return value are visible externally, while its internals (LLM exchanges, sub-calls) are hidden; `"llm"` = the reverse, exposing only the internal LLM exchanges and hiding the function's own name/return value and nested code sub-calls; `"full"` = everything visible (docstring + params + output + LLM replies + internals); `"hidden"` = no DAG nodes are written at all. Any other value raises `ValueError` at decoration time |
| `render_range` | `dict` | `None` | **Inward-facing**: how many history nodes to read from the DAG when this function's internal `runtime.exec` assembles its prompt. Shape `{"callers": N, "subcalls": M}`, where both numbers are **node counts (sliced by `seq`)**:<br>• `callers` — nodes written **before** this function's frame started; take the most recent N (`None` default = unlimited, `0` = a full wall)<br>• `subcalls` — nodes already written **after** this function's frame started; take the most recent N (`-1` default = unlimited, so the frame naturally sees its own progress; `N>=0` = set explicitly when you want to truncate the prompt; `0` = wall off in-frame entirely)<br>`{"callers":0,"subcalls":0}` = cut off from both the outside world and your own frame |
| `input` | `dict` | `None` | Per-parameter UI metadata; the WebUI renders the input form from it. Supported fields per parameter: `description` (label next to the name), `placeholder` (example text), `multiline` (`True` = textarea), `options` (list of allowed values, rendered as a dropdown and emitted as a JSON-schema `enum`), `hidden` (`True` = exclude from the form and from the LLM tool schema) |
| `workdir_mode` | `str` | `None` | Working-directory picker mode: `"optional"` / `"hidden"` / `"required"`; any other value raises `ValueError`. The consumer is the WebUI—it reads the value by AST-parsing the source text, so it must be written as a literal inside the decorator call to take effect |
| `system` | `str` | `None` | The system prompt for this function's LLM calls (applied over the injected runtime for the duration of the call, then restored afterward) |

### Tool-registration parameters

Every `@agentic_function` is also registered as an LLM-callable tool in the shared registry (`openprogram.functions`), alongside `@function`-decorated tools. These parameters control that registration and share their names and semantics with `@function`:

| Parameter | Type | Default | Description |
|------|------|------|------|
| `as_tool` | `bool` | `True` | Register this function as an LLM-callable tool. `False` = Python-direct-invoke only |
| `name` | `str` | `None` | Tool name override. Default: the function's `__name__` |
| `description` | `str` | `None` | Tool description override. Default: the function's docstring |
| `parameters` | `dict` | `None` | JSON-schema parameter override. Default: auto-generated from the signature's type hints plus `input` metadata (runtime-injected and `hidden` parameters excluded) |
| `label` | `str` | `None` | Human-readable label shown in tool UIs |
| `toolset` | `tuple` | `()` | Toolset names this tool belongs to (used by `exec(toolset=...)` presets) |
| `unsafe_in` | `tuple` | `()` | Channel sources in which the tool is considered unsafe and filtered out |
| `check_fn` | `Callable` | `None` | Per-call gate: called before dispatch; a falsy result blocks the call |
| `requires_env` | `tuple` | `()` | Environment variable names that must be set for the tool to be offered |
| `can_use` | `Callable` | `None` | Dynamic availability predicate evaluated at tool-resolution time |
| `max_result_chars` | `int` | `None` | Truncation cap for the tool result fed back to the model. `None` = the registry default `DEFAULT_MAX_RESULT_CHARS` (30,000 chars) |
| `persist_full` | `bool` | `False` | Persist the untruncated result to disk so the agent can read it back |
| `head_ratio` | `float` | `None` | When truncating, the fraction kept from the head, rest from the tail. `None` = the registry default `DEFAULT_HEAD_RATIO` (0.7) |
| `requires_approval` | — | `None` | Approval requirement forwarded to the tool registry (same shape as `@function`) |
| `cache` | `bool` | `False` | Memoize results on `(name, args)` for tool-dispatched calls |
| `cache_ttl` | `float` | `300.0` | Cache lifetime in seconds when `cache=True` |
| `timeout` | `float` | `None` | Hard wall-clock kill for a tool-dispatched call, in seconds; on expiry the model receives an error result |
| `available_if` | `Callable` | `None` | Import-time gate: if it returns falsy (or raises), the decorator is skipped entirely and the module-level name stays a plain function — no wrapper, no registration |
| `defer` | `bool` | `False` | Register as a deferred tool (schema loaded on demand instead of shipped with every call) |
| `register_globally` | `bool` | `True` | `False` = build the tool but keep it out of the global registry |

The function name, parameter names / types / defaults, and the one-line summary are all read automatically from the function signature and docstring, not repeated in the decorator (see SKILL.md §3).

## Runtime injection

Parameters named `runtime`, `exec_runtime`, or `review_runtime` are auto-injected: if the caller passes none (or `None`), the runtime is taken from the current call chain, or — for an entry-point call — created via `create_runtime()` (auto-detection) and closed again when the function returns. A function may declare more than one runtime parameter; all of them are filled with the same runtime. These parameters never appear in the LLM tool schema or the WebUI form.

## Introspection and safety

- `fn.spec` — the auto-generated JSON-schema tool spec (`{"name", "description", "parameters"}`); `fn.execute(**kwargs)` invokes the wrapper with LLM-provided kwargs.
- Self-recursion backstop: a function that re-enters itself more than 5 levels deep raises `RecursionError` (the model is also steered away from self-calls by an injected situational prompt).
- Pre-invocation hooks (`add_pre_invocation_hook` / `remove_pre_invocation_hook`) run at the top of every call and may raise `CancelledError` to abort it (this is how the WebUI stop button works).

## Recording to the DAG

- **Entering the function**: write a `code` node (`output=None`, `status="running"`), and store the function docstring into that node's `metadata.doc`, which is prepended to `function_name(args)` when rendering context.
- **`runtime.exec` inside the function body**: each call writes an `llm` node.
- **Exiting the function**: backfill the same `code` node's `output` / `status`.

When `expose="hidden"`, no nodes are written. In standalone runs (with no DAG store installed), all recording is a no-op and the function executes as usual.
