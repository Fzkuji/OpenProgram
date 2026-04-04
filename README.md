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

runtime = Runtime(call=my_llm, model="gemini-2.5-flash")

@agentic_function
def observe(task):
    """Look at the screen and find all visible UI elements.
    Check if the target described in task is visible."""
    
    # ── Python Runtime (deterministic) ──
    img = take_screenshot()
    ocr = run_ocr(img)
    elements = detect_all(img)
    
    # ── LLM Runtime (reasoning) ──
    return runtime.exec(content=[
        {"type": "text", "text": f"Task: {task}\nOCR: {ocr}\nElements: {elements}"},
        {"type": "image", "path": img},
    ])
```

**Docstring = Prompt.** Change the docstring → change the LLM behavior. Everything else is normal Python.

---

## Quick Start

```python
from agentic import agentic_function, Runtime

# 1. Create a Runtime (once)
runtime = Runtime(call=my_llm_func, model="gemini-2.5-flash")

# 2. Define functions
@agentic_function
def observe(task):
    """Look at the screen."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Find: {task}"},
    ])

@agentic_function
def click(element):
    """Click an element."""
    return runtime.exec(content=[
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
print(login_flow.context.tree())
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

## How Context Works

Every `@agentic_function` call creates a **Context** node. Nodes form a tree that mirrors your call hierarchy:

```
root (implicit)
├── login_flow(username="admin")          ← top-level call
│   ├── observe(task="find login form")   ← child #1
│   │   ├── run_ocr(img)                  ← grandchild
│   │   └── detect_all(img)               ← grandchild
│   ├── click(element="login button")     ← child #2
│   └── observe(task="verify dashboard")  ← child #3
└── (next top-level call...)
```

**Automatic context injection:** When `runtime.exec()` is called inside an `@agentic_function`, it automatically reads the tree via `ctx.summarize()` and prepends the execution history to the LLM prompt. This means:

- Each function **sees what happened before it** (ancestors + siblings)
- **Siblings' children are hidden** by default (one-line summaries)
- The context grows **incrementally** — maximizing prompt cache hits

```python
# What the LLM sees when observe() calls runtime.exec():
#
# Execution Context (most recent call last):
#     - login_flow(username="admin")
#         """Complete login flow."""
#         - login_flow.observe(task="find login form")
#             return {"found": true}
#             Status: success, 1200ms
#         - login_flow.click(element="login button")
#             return {"clicked": true}
#             Status: success, 820ms
#         - login_flow.observe(task="verify dashboard")  <-- Current Call
#             """Look at the screen."""
```

Control what each function sees with `summarize=` and what others see with `render=`:

```python
@agentic_function(summarize={"depth": 1, "siblings": 3})
def focused_task():  # sees only parent + last 3 siblings
    ...

@agentic_function(compress=True)
def high_level():    # others see only this node's result, not its children
    ...
```

---

## Error Recovery

Agentic Programming has built-in error recovery at two levels:

### Level 1: `exec()` Retry

`Runtime.exec()` automatically retries on transient failures (network errors, rate limits). Programming errors (`TypeError`, `NotImplementedError`) are never retried.

```python
# Retry up to 3 times on transient errors
runtime = Runtime(call=my_llm, model="gpt-4o", max_retries=3)

@agentic_function
def analyze(data):
    """Analyze data."""
    return runtime.exec(content=[...])  # auto-retries on failure
```

### Level 2: `fix()` — Code-Level Recovery

When a `create()`-generated function fails repeatedly, `fix()` sends the broken code + error log to the LLM and gets a rewritten version:

```python
from agentic.meta_function import create, fix

# 1. Generate a function
summarize = create("Summarize text into 3 bullets", runtime=runtime)

# 2. Call it — might fail
try:
    result = summarize(text=data)
except Exception as e:
    # 3. Fix it
    summarize = fix(
        description="Summarize text into 3 bullets",
        code=original_code,
        error_log=str(e),
        runtime=runtime,
    )
    # 4. Try again with the fixed version
    result = summarize(text=data)
```

The flow: **create → call → fail → fix → call again → succeed**.

---

## Core Components

### `Runtime` — LLM Connection

A class that wraps your LLM provider. Create once, use everywhere.

```python
# Option 1: pass a call function
runtime = Runtime(call=my_func, model="gemini-2.5-flash")

# Option 2: subclass
class GeminiRuntime(Runtime):
    def _call(self, content, model="default", response_format=None):
        # your API logic
        return reply_text
```

`exec()` takes a unified content list — text, images, audio, files all in one format:

```python
runtime.exec(content=[
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

root = login_flow.context
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

@agentic_function(render="detail")      # siblings also see LLM raw_reply
def observe(task): ...

@agentic_function(render="silent")      # invisible to siblings
def internal_helper(x): ...
```

| Level | What siblings see |
|-------|-------------------|
| `summary` | name, docstring, params, output, status, duration (default) |
| `detail` | summary + LLM raw\_reply |
| `result` | name + return value only |
| `silent` | nothing |

---

### `create()` — Meta Function

Generate new agentic functions from natural language at runtime:

```python
from agentic import Runtime
from agentic.meta_function import create

runtime = Runtime(call=my_llm, model="gemini-2.5-flash")

# Create a function from description
summarize = create(
    "Summarize text into 3 bullet points",
    runtime=runtime,
)

# Use it like any other agentic function
result = summarize(text="Long article here...")
```

The generated function is a real `@agentic_function` — it has Context tracking, can call `runtime.exec()`, and can be nested inside other agentic functions.

Safety: generated code runs in a sandbox with restricted builtins (no imports, no file I/O, no eval).

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

## Built-in Providers

内置了三个常用 LLM 的 Runtime 实现，每个都是可选依赖：

```bash
# 按需安装
pip install anthropic   # for AnthropicRuntime
pip install openai      # for OpenAIRuntime
pip install google-genai # for GeminiRuntime
```

```python
# Anthropic Claude
from agentic.providers import AnthropicRuntime
rt = AnthropicRuntime(api_key="sk-ant-...", model="claude-sonnet-4-20250514")

# OpenAI GPT
from agentic.providers import OpenAIRuntime
rt = OpenAIRuntime(api_key="sk-...", model="gpt-4o")

# Google Gemini
from agentic.providers import GeminiRuntime
rt = GeminiRuntime(api_key="...", model="gemini-2.5-flash")
```

所有 provider 支持 text + image content blocks。AnthropicRuntime 自动启用 prompt caching，OpenAIRuntime 支持 response_format。

详见 [Provider 文档](docs/api/providers.md)。

---

## Installation

```bash
# Basic install (core only, no provider dependencies)
pip install -e .

# With a specific provider
pip install -e ".[anthropic]"   # AnthropicRuntime (Claude)
pip install -e ".[openai]"      # OpenAIRuntime (GPT)
pip install -e ".[gemini]"      # GeminiRuntime (Gemini)

# All providers
pip install -e ".[all]"

# With dev tools (pytest)
pip install -e ".[dev]"
```

Or install providers separately:
```bash
pip install anthropic   # >= 0.30.0
pip install openai      # >= 1.30.0
pip install google-genai # >= 1.0.0
```

## Project Structure

```
agentic/
├── __init__.py        # Exports: agentic_function, Runtime, Context, create, fix
├── context.py         # Context tree: tracking, summarize, tree/traceback, save
├── function.py        # @agentic_function decorator
├── runtime.py         # Runtime class — exec() + _call() + retry
├── meta_function.py   # Meta functions — create() + fix()
└── providers/         # Built-in Runtime implementations
    ├── anthropic.py   # AnthropicRuntime (Claude)
    ├── openai.py      # OpenAIRuntime (GPT)
    └── gemini.py      # GeminiRuntime (Gemini)

examples/
├── main.py            # Basic entry point (Gemini)
├── claude_demo.py     # End-to-end demo (Claude Code CLI)
├── meta_demo.py       # Meta function demo
├── code_review.py     # Code review pipeline
├── data_analysis.py   # Data analysis with render levels
└── meta_chain.py      # Dynamic function chain with create()

docs/
├── API.md             # API overview
└── api/               # Per-component API docs
    ├── agentic_function.md
    ├── context.md
    ├── meta_function.md   # create() + fix()
    ├── runtime.md         # exec() + retry
    └── providers.md       # Provider 配置指南
```
