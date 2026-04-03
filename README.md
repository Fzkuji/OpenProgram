# Agentic Programming

> A programming paradigm where Python and LLM co-execute functions.

![Role Reversal — from LLM-as-controller to Python+LLM cooperation](docs/images/role_reversal.png)

**Traditional approach**: LLM calls tools one by one (slow, fragile, context-heavy).  
**Agentic Programming**: Python functions bundle deterministic code + LLM reasoning together. The LLM works *inside* the function, not outside it.

---

## How It Works

Every Agentic Function has two runtimes cooperating:

![Dual Runtime — Python handles deterministic work, LLM handles reasoning](docs/images/dual_runtime_detail.png)

```python
from agentic import agentic_function, Runtime

rt = Runtime(call=my_llm, model="gemini-2.5-flash")

@agentic_function
def observe(task):
    """Look at the screen and find all visible UI elements.
    Check if the target described in task is visible."""
    
    # ── Python Runtime (deterministic) ──
    img = take_screenshot()
    ocr = run_ocr(img)
    elements = detect_all(img)
    
    # ── LLM Runtime (reasoning) ──
    return rt.exec(content=[
        {"type": "text", "text": f"Task: {task}\nOCR: {ocr}\nElements: {elements}"},
        {"type": "image", "path": img},
    ])
```

**Docstring = Prompt.** Change the docstring → change the LLM behavior. Everything else is normal Python.

---

## Quick Start

```python
from agentic import agentic_function, Runtime, get_root_context

# 1. Create a Runtime (once)
rt = Runtime(call=my_llm_func, model="gemini-2.5-flash")

# 2. Define functions
@agentic_function
def observe(task):
    """Look at the screen."""
    return rt.exec(content=[
        {"type": "text", "text": f"Find: {task}"},
    ])

@agentic_function
def click(element):
    """Click an element."""
    return rt.exec(content=[
        {"type": "text", "text": f"Click: {element}"},
    ])

@agentic_function
def login_flow(username, password):
    """Complete login flow."""
    observe(task="find login form")
    click(element="login button")
    return observe(task="verify dashboard")

# 3. Run
login_flow(username="admin", password="secret")

# 4. Inspect
print(get_root_context().tree())
```

Output:
```
login_flow ✓ 8800ms → ...
  observe ✓ 3100ms → ...
  click ✓ 2500ms → ...
  observe ✓ 3200ms → ...
```

---

## Architecture

![Full Architecture](docs/images/full_architecture.png)

---

## Core Components

### `Runtime` — LLM Connection

A class that wraps your LLM provider. Create once, use everywhere.

```python
# Option 1: pass a call function
rt = Runtime(call=my_func, model="gemini-2.5-flash")

# Option 2: subclass
class GeminiRuntime(Runtime):
    def _call(self, content, model="default", response_format=None):
        # your API logic
        return reply_text
```

`exec()` takes a unified content list — text, images, audio, files all in one format:

```python
rt.exec(content=[
    {"type": "text", "text": "Analyze this screenshot."},
    {"type": "image", "path": "screenshot.png"},
])
```

### `@agentic_function` — Auto-Tracking Decorator

Wraps any function to automatically record execution: name, params, output, errors, timing, and call hierarchy.

```python
@agentic_function
def navigate(target):
    """Navigate to the target by observing and acting."""
    obs = observe(task=f"find {target}")
    act(target=target)
    return verify(expected=target)
```

Produces a Context tree:
```
navigate ✓ 3200ms → {success: True}
  observe ✓ 1200ms → {target_visible: True}
  act ✓ 820ms → {clicked: True}
  verify ✓ 200ms → {passed: True}
```

### `Context` — Execution Record

Every function call creates a Context node. The tree is inspectable, serializable, and debuggable.

```python
from agentic import get_root_context

root = get_root_context()
print(root.tree())           # human-readable tree
print(root.traceback())      # error chain
root.save("logs/run.jsonl")  # machine-readable
root.save("logs/run.md")     # human-readable
```

### `render` — Visibility Control

Control how much of a function's data is visible to sibling functions:

```python
@agentic_function                       # default: summary
def observe(task): ...

@agentic_function(render="detail")      # siblings see full input/output
def observe(task): ...

@agentic_function(render="silent")      # invisible to siblings
def internal_helper(x): ...
```

| Level | What siblings see |
|-------|-------------------|
| `trace` | prompt + full I/O + raw LLM reply |
| `detail` | full input and output |
| `summary` | one-line summary (default) |
| `result` | return value only |
| `silent` | nothing |

---

## Comparison

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **Direction** | LLM → calls tools | Python + LLM cooperate |
| **Functions contain** | Python code only | Python code + LLM reasoning |
| **Execution** | Single runtime (CPU) | Dual runtime (Python + LLM) |
| **Context** | Implicit (one conversation) | Explicit (Context tree + render) |
| **Prompt** | Hardcoded in agent | Docstring = prompt |

MCP is the **transport** (how to call). Agentic Programming is the **execution model** (how functions run). They are orthogonal.

---

## Install

```bash
pip install -e .
```

## Project Structure

```
agentic/
├── __init__.py      # Exports: agentic_function, Runtime, Context, ...
├── context.py       # Context tree: tracking, summarize, tree/traceback, save
├── function.py      # @agentic_function decorator
└── runtime.py       # Runtime class — exec() + _call()

examples/
└── main.py          # Entry point example

docs/
├── API.md           # API overview
└── api/             # Per-component API docs
```
