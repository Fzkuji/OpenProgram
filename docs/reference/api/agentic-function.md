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

| Parameter | Type | Default | Description |
|------|------|------|------|
| `expose` | `str` | `"io"` | **Outward-facing**: what others can see about me when they render the DAG. `"io"` = this function's input/output nodes are visible externally, while its direct internal LLM calls are hidden; `"llm"` = the reverse, exposing only the internal LLM exchanges and hiding input/output; `"full"` = everything visible; `"hidden"` = no DAG nodes are written at all |
| `render_range` | `dict` | `None` | **Inward-facing**: how many history nodes to read from the DAG when this function's internal `runtime.exec` assembles its prompt. Shape `{"callers": N, "subcalls": M}`, where both numbers are **node counts (sliced by `seq`)**:<br>• `callers` — nodes written **before** this function's frame started; take the most recent N (`None` default = unlimited, `0` = a full wall)<br>• `subcalls` — nodes already written **after** this function's frame started; take the most recent N (`-1` default = unlimited, so the frame naturally sees its own progress; `N>=0` = set explicitly when you want to truncate the prompt; `0` = wall off in-frame entirely)<br>`{"callers":0,"subcalls":0}` = cut off from both the outside world and your own frame |
| `input` | `dict` | `None` | Per-parameter UI metadata (`description` / `placeholder` / `multiline` / `options` / `hidden`, etc.); the WebUI renders the input form from it |
| `workdir_mode` | `str` | `None` | Working-directory picker mode: `"optional"` / `"hidden"` / `"required"`; any other value raises an error. The consumer is the WebUI—it reads the value by AST-parsing the source text, so it must be written as a literal inside the decorator call to take effect |
| `system` | `str` | `None` | The system prompt for this function's LLM calls (applied over the injected runtime for the duration of the call, then restored afterward) |

The function name, parameter names / types / defaults, and the one-line summary are all read automatically from the function signature and docstring, not repeated in the decorator (see SKILL.md §3).

## Recording to the DAG

- **Entering the function**: write a `code` node (`output=None`, `status="running"`), and store the function docstring into that node's `metadata.doc`, which is prepended to `function_name(args)` when rendering context.
- **`runtime.exec` inside the function body**: each call writes an `llm` node.
- **Exiting the function**: backfill the same `code` node's `output` / `status`.

When `expose="hidden"`, no nodes are written. In standalone runs (with no DAG store installed), all recording is a no-op and the function executes as usual.
