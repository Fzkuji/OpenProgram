<p align="center">
  <img src="docs/images/banner.png" alt="Agentic Programming: Redefining Agent Flow Control" width="900">
</p>

<p align="center">
  <h1 align="center">🧬 Agentic Programming</h1>
  <p align="center">
    <strong>Python functions that think.</strong><br>
    A programming paradigm where Python and LLM co-execute functions.
  </p>
  <p align="center">
    <a href="docs/README_CN.md">🇨🇳 中文</a>
  </p>
</p>

## Table of Contents

- [Motivation](#motivation)
- [Core Idea](#core-idea)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [Python](#1-python--write-agentic-code)
  - [Skills](#2-skills--agent-integration)
  - [MCP](#3-mcp--any-mcp-client)
- [Core Concepts](#core-concepts)
  - [Agentic Functions](#agentic-functions)
  - [Automatic Context](#automatic-context)
  - [Self-Evolving Code](#self-evolving-code)
  - [Error Recovery](#error-recovery)
- [API Reference](#api-reference)
- [Comparison](#comparison)
- [Project Structure](#project-structure)
- [Contributing](#contributing)

---

> 🚀 **This is a paradigm proposal.** We're sharing a new way to think about LLM-powered programming. The code here is a reference implementation — we'd love to see you take these ideas and build your own version, in any language, for any use case.

**Projects built with Agentic Programming:**

| Project | Description |
|---------|-------------|
| [🖥️&nbsp;GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | Autonomous GUI agent that operates desktop apps via vision + agentic functions. Python controls observe→plan→act→verify loops; the LLM only reasons when asked. |

---

## Motivation

Current LLM agent frameworks place the LLM as the central scheduler — it decides what to do, when, and how. This creates three fundamental problems:

- **Unpredictable execution** — the LLM may skip, repeat, or invent steps regardless of defined workflows
- **Context explosion** — each tool-call round-trip accumulates history
- **No output guarantees** — the LLM interprets instructions rather than executing them

<p align="center">
  <img src="docs/images/the_problem.png" alt="The Problem: LLM as Scheduler" width="800">
</p>

The core issue: **the LLM controls the flow, but nothing enforces it.** Skills, prompts, and system messages are suggestions, not guarantees.

---

## Core Idea

<p align="center">
  <img src="docs/images/the_idea.png" alt="The Paradigm: Python controls flow, LLM reasons" width="800">
</p>

**Give the flow back to Python. Let the LLM focus on reasoning.**

| Principle | How |
|-----------|-----|
| **Deterministic flow** | Python controls `if/else/for/while`. The execution path is guaranteed, not suggested. |
| **Minimal LLM calls** | The LLM is called only when reasoning is needed. 2 calls instead of 10. |
| **Docstring = Prompt** | Change the function's docstring, change the LLM's behavior. No separate prompt files. |
| **Self-evolving** | Functions generate, fix, and improve themselves at runtime via meta functions. |

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

---

## Quick Start

```bash
git clone https://github.com/Fzkuji/Agentic-Programming.git
cd Agentic-Programming
pip install -e .
```

Set up at least one LLM provider:

| Provider | Setup |
|----------|-------|
| Claude Code CLI | `npm i -g @anthropic-ai/claude-code && claude login` |
| Codex CLI | `npm i -g @openai/codex && codex auth` |
| Gemini CLI | `npm i -g @anthropic-ai/gemini-cli` |
| Anthropic API | `pip install -e ".[anthropic]"` then `export ANTHROPIC_API_KEY=...` |
| OpenAI API | `pip install -e ".[openai]"` then `export OPENAI_API_KEY=...` |
| Gemini API | `pip install -e ".[gemini]"` then `export GOOGLE_API_KEY=...` |

Verify with `agentic providers`.

---

## Usage

### 1. Python — write agentic code

```python
from agentic import agentic_function, create_runtime

runtime = create_runtime()  # auto-detects best available provider

@agentic_function
def summarize(text: str) -> str:
    """Summarize the given text into 3 bullet points."""
    return runtime.exec(content=[
        {"type": "text", "text": text},
    ])

result = summarize(text="Your long article here...")
```

Override the provider when needed:

```python
runtime = create_runtime(provider="openai", model="gpt-4o")
```

### 2. Skills — agent integration

Install skills so your LLM agent can use agentic functions through natural language:

```bash
cp -r skills/* ~/.claude/skills/    # Claude Code
cp -r skills/* ~/.gemini/skills/    # Gemini CLI
```

Then talk to your agent:

> "Create a function that extracts emails from text"

The agent picks up the skill, calls `agentic create`, and the generated function handles everything from there. Once created:

> "Run sentiment on 'This is amazing'"

### 3. MCP — any MCP client

Run the built-in MCP server so any MCP-compatible client (Claude Desktop, Cursor, etc.) can use agentic functions:

```bash
pip install -e ".[mcp]"
```

Add to your MCP client config:

```json
{
    "mcpServers": {
        "agentic": {
            "command": "python",
            "args": ["-m", "agentic.mcp"]
        }
    }
}
```

Exposes five tools: `list_functions`, `run_function`, `create_function`, `create_application`, `fix_function`.

---

## Core Concepts

### Agentic Functions

Every `@agentic_function` can call `runtime.exec()` to invoke an LLM. The framework auto-injects execution context into the prompt. Python controls the flow — the LLM only reasons when explicitly asked.

```python
@agentic_function
def login_flow(username, password):
    """Complete login flow."""
    observe(task="find login form")       # Python decides what to do
    click(element="login button")         # Python decides the order
    return verify(expected="dashboard")   # Python decides when to stop
```

### Automatic Context

Every call creates a **Context** node. Nodes form a tree that is automatically injected into LLM calls:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees what `observe` and `click` returned. No manual context management.

### Self-Evolving Code

Functions can generate new functions, fix broken ones, and scaffold complete apps — all at runtime:

```python
from agentic.meta_functions import create, create_app, fix

# Generate a function from description
sentiment = create("Analyze text sentiment", runtime=runtime, name="sentiment")
sentiment(text="I love this!")  # → "positive"

# Generate a complete app (runtime + argparse + main)
create_app("Summarize articles from URLs", runtime=runtime, name="summarizer")
# → agentic/apps/summarizer.py — runnable with: python agentic/apps/summarizer.py <url>

# Fix a broken function — auto-reads source & error history
fixed = fix(fn=broken_fn, runtime=runtime, instruction="return JSON, not plain text")
```

The `create → run → fail → fix → run` cycle means programs improve themselves through use.

### Error Recovery

`Runtime` retries transient failures automatically. For deeper issues, `fix()` rewrites the function:

```python
runtime = create_runtime(max_retries=3)

try:
    result = extract(text="Acme closed at $42.50")
except Exception:
    extract = fix(fn=extract, runtime=runtime)  # LLM analyzes errors and rewrites
    result = extract(text="Acme closed at $42.50")
```

Every attempt is recorded in the Context tree — `fix()` reads the full error history to diagnose the root cause, not just the symptom.

---

## API Reference

### Core

| Import | What it does |
|--------|-------------|
| `from agentic import agentic_function` | Decorator. Records execution into Context tree |
| `from agentic import Runtime` | LLM runtime. `exec()` calls the LLM with auto-context |
| `from agentic import Context` | Execution tree. `tree()`, `save()`, `traceback()` |
| `from agentic import create_runtime` | Create a Runtime with auto-detection or explicit provider |

### Meta Functions

| Import | What it does |
|--------|-------------|
| `from agentic.meta_functions import create` | Generate a new `@agentic_function` from description |
| `from agentic.meta_functions import create_app` | Generate a complete runnable app with `main()` |
| `from agentic.meta_functions import fix` | Fix broken functions via LLM analysis |
| `from agentic.meta_functions import create_skill` | Generate a SKILL.md for agent discovery |

### Providers

Six built-in providers: Anthropic, OpenAI, Gemini (API), Claude Code, Codex, Gemini (CLI). All CLI providers maintain **session continuity** across calls. See [Provider docs](docs/api/providers.md) for details.

---

## Comparison

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **Who schedules?** | LLM decides | Python decides |
| **Functions contain** | Code only | Code + LLM reasoning |
| **Context** | Flat conversation | Structured tree |
| **Prompt** | Hidden in agent config | Docstring = prompt |
| **Self-improvement** | Not built-in | `create` → `fix` → evolve |

MCP is the *transport*. Agentic Programming is the *execution model*. They're orthogonal.

---

## Project Structure

```
agentic/
├── __init__.py              # agentic_function, Runtime, Context, create_runtime
├── function.py              # @agentic_function decorator
├── runtime.py               # Runtime (exec + retry + context injection)
├── context.py               # Context tree
├── meta_functions/          # Self-evolving code generation
│   ├── create.py            #   create() — generate a function
│   ├── create_app.py        #   create_app() — generate a complete app
│   ├── fix.py               #   fix() — rewrite broken functions
│   └── create_skill.py      #   create_skill() — generate SKILL.md
├── providers/               # Anthropic, OpenAI, Gemini, Claude Code, Codex, Gemini CLI
├── mcp/                     # MCP server (python -m agentic.mcp)
├── functions/               # saved generated functions
└── apps/                    # generated apps (from create_app)
skills/                      # SKILL.md files for agent integration
examples/                    # runnable demos
tests/                       # pytest suite
```

## Integration

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/GETTING_STARTED.md) | 3-minute setup and runnable examples |
| [Claude Code](docs/INTEGRATION_CLAUDE_CODE.md) | Use without API key via Claude Code CLI |
| [OpenClaw](docs/INTEGRATION_OPENCLAW.md) | Use as OpenClaw skill |
| [API Reference](docs/API.md) | Full API documentation |

---

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome discussions, alternative implementations in other languages, use cases that validate or challenge the approach, and bug reports.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT
