# Context System

Every `@agentic_function` call creates a Context node.
Nodes form a tree. The tree records everything — completely and permanently.

When a function needs to call an LLM, `summarize()` reads the tree
and returns only the relevant parts as text.

**Recording** is automatic and unconditional.
**Reading** is selective and configurable.

## Documents

| File | What it covers |
|---|---|
| [ENGINEERING.md](ENGINEERING.md) | API reference — every class, parameter, method, with examples |
| [PRACTICE.md](PRACTICE.md) | Usage strategies — when to use which settings, session management, cost optimization |

## Code

| File | What it does |
|---|---|
| `agentic/context.py` | Context dataclass + summarize() + tree inspection + persistence |
| `agentic/function.py` | @agentic_function decorator — creates nodes, manages the tree |
| `agentic/runtime.py` | runtime.exec() — calls the LLM, reads from and writes to Context |
| `agentic/__init__.py` | Public API exports |

## Quick Reference

```python
from agentic import agentic_function, runtime

# Minimal: just decorate. Records everything, sees everything.
@agentic_function
def observe(task):
    """Look at the screen."""
    return runtime.exec(prompt=observe.__doc__, call=my_llm)

# Customized: limited context, compressed output.
@agentic_function(
    render="detail",                       # others see my full I/O
    summarize={"depth": 1, "siblings": 3}, # I only see parent + last 3 siblings
    compress=True,                         # after I finish, hide my children
)
def navigate(target):
    """Navigate to target."""
    observe(...)
    act(...)
    return verify(...)
```

## Design Principles

1. **One tree, record everything.** Recording is unconditional and permanent.
2. **Read selectively.** Each function configures what it sees via `summarize`.
3. **Users write pure Python.** No `ctx` parameter, no manual tree management.
4. **Compress at boundaries.** High-level functions hide their internals after completion.
