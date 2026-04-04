<p align="center">
  <h1 align="center">🧬 Agentic Programming</h1>
  <p align="center">
    <strong>Python functions that think.</strong><br>
    A programming paradigm where Python and LLM co-execute functions.
  </p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#api">API</a> •
    <a href="docs/API.md">Docs</a> •
    <a href="examples/">Examples</a>
  </p>
  <p align="center">
    <a href="docs/README_CN.md">🇨🇳 中文</a>
  </p>
</p>

> 🚀 **This is a paradigm proposal.** We're sharing a new way to think about LLM-powered programming. The code here is a reference implementation — we'd love to see you take these ideas and build your own version, in any language, for any use case.

---

## The Problem

LLM agents today work like this: **LLM thinks → calls a tool → thinks → calls another tool.** Every step is a round-trip. Context bloats. The LLM is both the brain *and* the scheduler.

## The Idea

What if Python handles the scheduling, and LLM only handles the *reasoning*?

```python
@agentic_function
def observe(task):
    """Look at the screen and describe what you see."""
    
    img = take_screenshot()       # Python: deterministic
    ocr = run_ocr(img)            # Python: deterministic
    
    return runtime.exec(content=[ # LLM: reasoning
        {"type": "text", "text": f"Task: {task}\nOCR: {ocr}"},
        {"type": "image", "path": img},
    ])
```

**Docstring = Prompt.** Change the docstring, change the behavior. Everything else is Python.

---

## Quick Start

```bash
pip install -e .
```

```python
from agentic import agentic_function, Runtime

runtime = Runtime(call=my_llm, model="sonnet")

@agentic_function
def greet(name):
    """Greet someone in a creative way."""
    return runtime.exec(content=[
        {"type": "text", "text": f"Say hello to {name} creatively."},
    ])

result = greet(name="World")
print(result)                    # "Hey World! 🌍✨"
print(greet.context.tree())      # execution trace
```

---

## How It Works

### 1. Functions call LLMs

Every `@agentic_function` can call `runtime.exec()` to invoke an LLM. The framework auto-injects execution context (what happened before) into the prompt.

```python
@agentic_function
def login_flow(username, password):
    """Complete login flow."""
    observe(task="find login form")
    click(element="login button")
    return verify(expected="dashboard")
```

### 2. Context tracks everything

Every call creates a **Context** node. Nodes form a tree:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees what `observe` and `click` returned. No manual context management.

### 3. Functions create functions

```python
from agentic.meta_function import create

summarize = create("Summarize text into 3 bullet points", runtime=runtime)
result = summarize(text="Long article...")
```

LLM writes the code. Framework validates and sandboxes it. You get a real `@agentic_function`.

### 4. Errors recover automatically

```python
runtime = Runtime(call=my_llm, max_retries=2)  # auto-retry on failure

# Or fix a broken function:
from agentic.meta_function import fix
fixed_fn = fix(fn=broken_fn, runtime=runtime, instruction="use label instead of coordinates")
```

---

## API

| Component | What it does |
|-----------|-------------|
| [`@agentic_function`](docs/api/agentic_function.md) | Decorator. Records execution into Context tree |
| [`Runtime`](docs/api/runtime.md) | LLM connection. `exec()` calls the LLM with auto-context |
| [`Context`](docs/api/context.md) | Execution tree. `tree()`, `save()`, `traceback()` |
| [`create()`](docs/api/meta_function.md) | Generate new functions from descriptions |
| [`fix()`](docs/api/meta_function.md) | Fix broken functions with LLM analysis |

### Built-in Providers

```python
from agentic.providers import AnthropicRuntime   # Claude (+ prompt caching)
from agentic.providers import OpenAIRuntime       # GPT (+ response_format)
from agentic.providers import GeminiRuntime       # Gemini
from agentic.providers import ClaudeCodeRuntime   # Claude Code CLI (no API key)
```

See [Provider docs](docs/api/providers.md) for setup.

---

## vs Tool-Calling

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **Who schedules?** | LLM | Python |
| **Functions contain** | Code only | Code + LLM reasoning |
| **Context** | One big conversation | Structured tree |
| **Prompt** | Hidden in agent | Docstring = prompt |

MCP is the *transport*. Agentic Programming is the *execution model*. They're orthogonal.

---

## Install

```bash
pip install -e .                    # core (zero dependencies)
pip install -e ".[anthropic]"       # + Claude
pip install -e ".[openai]"          # + GPT
pip install -e ".[gemini]"          # + Gemini
pip install -e ".[all]"             # everything
```

## Project Structure

```
agentic/
├── __init__.py          # agentic_function, Runtime, Context, create, fix
├── context.py           # Context tree
├── function.py          # @agentic_function decorator
├── runtime.py           # Runtime class (exec + retry)
├── meta_function.py     # create() + fix()
└── providers/           # Anthropic, OpenAI, Gemini, Claude Code CLI

examples/                # 5 runnable demos
docs/api/                # API reference
tests/                   # 161 tests
```

## Contributing

This project is a **paradigm proposal** with a reference implementation. We welcome:

- 🧠 **Discussions** on the programming model
- 🔧 **Alternative implementations** in other languages or frameworks
- 📝 **Use cases** that validate or challenge the approach
- 🐛 **Bug reports** on the reference implementation

If you build something based on Agentic Programming, let us know!

## License

MIT
