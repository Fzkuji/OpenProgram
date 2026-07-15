# API Reference

> Source: [`openprogram/`](../openprogram/)

## Core Components

| Component | Source File | Description |
|------|--------|------|
| [`agentic_function`](api/agentic-function.md) | `agentic_programming/function.py` | A decorator. Turns a plain function into an Agentic Function; each call is recorded as a node in the session DAG |
| [`Runtime`](api/runtime.md) | `agentic_programming/runtime.py` | The LLM runtime. Computes context from the DAG, calls the LLM, and writes the response back to the DAG |
| [`create_runtime` and the built-in providers](api/providers.md) | `providers/` | Automatically detect or explicitly create a Runtime; supports Anthropic / OpenAI / Gemini / CLI providers |

The session context is a flat DAG (nodes = user messages / LLM calls / function calls); for the architecture see [`openprogram/context/README.md`](../openprogram/context/README.md).

## Writing Functions

There are no meta functions like `create()` / `fix()` — writing, modifying, and validating an `@agentic_function` is done directly with ordinary file-editing tools, following [`skills/agentic-programming/SKILL.md`](../skills/agentic-programming/SKILL.md). That skill is the complete specification: file layout, decorator metadata, the division of labor between the docstring and `content`, the validation checklist, and smoke tests.

## Imports

```python
from openprogram import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.registry import create_runtime
```

Only `agentic_function` is re-exported as a top-level `openprogram` symbol; `Runtime`, `create_runtime`, and the rest must be imported by their full paths.

## Quick Example

```python
from openprogram import agentic_function
from openprogram.providers.registry import create_runtime

@agentic_function
def observe(task: str, runtime) -> str:
    """Report the UI element on screen that matches a task."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Find the UI element for: {task}. Reply with its label only."
        )},
    ])

rt = create_runtime()
print(observe(task="login button", runtime=rt))
rt.close()
```
