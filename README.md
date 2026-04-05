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
    <a href="#install">Install</a> •
    <a href="#usage">Usage</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#api-reference">API</a> •
    <a href="#integration">Integration</a> •
    <a href="docs/API.md">Docs</a> •
    <a href="examples/">Examples</a>
  </p>
  <p align="center">
    <a href="docs/README_CN.md">🇨🇳 中文</a>
  </p>
</p>

> 🚀 **This is a paradigm proposal.** We're sharing a new way to think about LLM-powered programming. The code here is a reference implementation — we'd love to see you take these ideas and build your own version, in any language, for any use case.

**Projects built with Agentic Programming:**

| Project | Description |
|---------|-------------|
| [🖥️&nbsp;GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | Autonomous GUI agent that operates desktop apps via vision detection + agentic functions. Uses Agentic Programming to control observe→plan→act→verify loops with deterministic Python flow. |

---

## Motivation

Current LLM agent frameworks place the LLM as the central scheduler — it decides what to do, when, and how. This creates three fundamental problems: **unpredictable execution paths** (the LLM may skip, repeat, or invent steps regardless of defined workflows), **context explosion** (each tool-call round-trip accumulates history), and **no output guarantees** (the LLM interprets instructions rather than executing them).

<p align="center">
  <img src="docs/images/the_problem.png" alt="Motivation: LLM as Scheduler" width="800">
</p>

The core issue: **the LLM controls the flow, but nothing enforces it.** The LLM may follow a workflow, or it may not — there is no strict constraint. Skills, prompts, and system messages are suggestions, not guarantees. The execution path is fundamentally non-deterministic.

## The Idea

<p align="center">
  <img src="docs/images/the_idea.png" alt="The Idea: Python controls flow, LLM reasons" width="800">
</p>

**Give the flow back to Python. Let the LLM focus on reasoning.**

Python handles scheduling, loops, error handling, and data flow. The LLM only answers questions — when asked, where asked.

- **Deterministic flow** — Python controls `if/else/for/while`. The execution path is guaranteed, not suggested.
- **Minimal LLM calls** — The LLM is called only when reasoning is needed. 2 calls instead of 10.
- **Docstring = Prompt** — Change the function's docstring, change the LLM's behavior. No separate prompt files.

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

## Install

```bash
pip install -e .
```

That's it. Zero required dependencies. Provider SDKs are optional:

```bash
pip install -e ".[anthropic]"   # Anthropic Claude API
pip install -e ".[openai]"      # OpenAI API
pip install -e ".[gemini]"      # Google Gemini API
pip install -e ".[all]"         # all provider SDKs
```

### Provider setup

You need at least one LLM provider. Pick whichever you already have:

| Provider | Type | Setup | Cost |
|----------|------|-------|------|
| Claude Code CLI | CLI | `npm i -g @anthropic-ai/claude-code && claude login` | Subscription |
| Codex CLI | CLI | `npm i -g @openai/codex && codex auth` | Subscription |
| Gemini CLI | CLI | `npm i -g @anthropic-ai/gemini-cli` | Free |
| Anthropic API | API | `export ANTHROPIC_API_KEY=sk-ant-...` | Per token |
| OpenAI API | API | `export OPENAI_API_KEY=sk-...` | Per token |
| Gemini API | API | `export GOOGLE_API_KEY=...` | Per token |

Check what's available on your machine:

```bash
agentic providers
```

```
Available LLM providers:

  [+] claude-code     (Claude Code CLI )  model: sonnet            [ready] <-- auto-detected
  [+] codex           (Codex CLI       )  model: o4-mini           [ready]
  [-] anthropic       (Anthropic API   )  model: claude-sonnet-4   [not set]
  [+] openai          (OpenAI API      )  model: gpt-4o            [ready]
  ...
```

---

## Usage

Three ways to use — all equally powerful:

### 1. Python import (recommended)

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

Override the provider if needed:

```python
runtime = create_runtime(provider="openai", model="gpt-4o")
```

Or use a specific provider class directly:

```python
from agentic.providers import AnthropicRuntime
runtime = AnthropicRuntime(model="claude-sonnet-4-20250514")
```

**Meta functions** — generate and fix code with LLMs:

```python
from agentic.meta_functions import create, create_app, fix

# Generate a single function
sentiment = create("Analyze text sentiment", runtime=runtime, name="sentiment")
sentiment(text="I love this!")  # → "positive"

# Generate a complete app (with runtime setup, argparse, main)
create_app("A CLI that summarizes articles from URLs", runtime=runtime, name="summarizer")
# → saves to apps/summarizer.py, runnable with: python apps/summarizer.py <url>

# Fix a broken function
fixed = fix(fn=broken_fn, runtime=runtime, instruction="return JSON, not plain text")
```

### 2. CLI commands

```bash
# Create a function
agentic create "Analyze text sentiment" --name sentiment

# Create a complete app (with runtime + main + argparse)
agentic create-app "A tool that summarizes articles" --name summarizer

# Run a saved function
agentic run sentiment --arg text="I love this project"

# Fix a function
agentic fix sentiment --instruction "return JSON with score"

# List all functions
agentic list

# Use a specific provider
agentic create "..." --name foo --provider openai --model gpt-4o
```

### 3. Skills (Claude Code / Gemini CLI)

Install skills so your LLM agent can use agentic functions directly:

```bash
# Claude Code
cp -r skills/* ~/.claude/skills/

# Gemini CLI
cp -r skills/* ~/.gemini/skills/
```

Then just talk to your agent:

> "Create a function that extracts emails from text"

The agent picks up the `meta-function` skill, calls `agentic create`, and the function handles everything from there. Once created, you can also ask:

> "Run sentiment on 'This is amazing'"

The agent calls `agentic run sentiment --arg text="This is amazing"`.

**Create your own skills:**

```bash
agentic create "Extract key dates from text" --name extract_dates --as-skill
# → saves function to agentic/functions/extract_dates.py
# → saves skill to skills/extract_dates/SKILL.md
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
from agentic.meta_functions import create

summarize = create("Summarize text into 3 bullet points", runtime=runtime)
result = summarize(text="Long article...")
```

LLM writes the code. Framework validates and sandboxes it. You get a real `@agentic_function`.

### 4. Errors recover automatically

```python
runtime = Runtime(call=my_llm, max_retries=2)  # try once + retry once

# Or fix a broken function:
from agentic.meta_functions import fix
fixed_fn = fix(
    fn=broken_fn,
    runtime=runtime,
    instruction="use label instead of coordinates",
)
```

`Runtime.exec()` and `Runtime.async_exec()` record every attempt in the current `Context` node. Transient provider failures are retried automatically; programming errors such as `TypeError` and `NotImplementedError` fail immediately.

---

## API Reference

### Core

| Import | What it does |
|--------|-------------|
| `from agentic import agentic_function` | Decorator. Records execution into Context tree |
| `from agentic import Runtime` | LLM connection. `exec()` calls the LLM with auto-context |
| `from agentic import Context` | Execution tree. `tree()`, `save()`, `traceback()` |
| `from agentic import create_runtime` | Create a Runtime with auto-detection or explicit provider |
| `from agentic import detect_provider` | Detect best available provider (returns name + model) |

### Meta Functions

| Import | What it does |
|--------|-------------|
| `from agentic.meta_functions import create` | Generate a new `@agentic_function` from description |
| `from agentic.meta_functions import create_app` | Generate a complete runnable app with `main()` |
| `from agentic.meta_functions import fix` | Fix broken functions with LLM analysis |
| `from agentic.meta_functions import create_skill` | Generate a SKILL.md for agent discovery |

### Providers

```python
from agentic.providers import AnthropicRuntime    # Claude API (+ prompt caching)
from agentic.providers import OpenAIRuntime       # OpenAI API (+ response_format)
from agentic.providers import GeminiRuntime       # Gemini API (text + image + video)
from agentic.providers import ClaudeCodeRuntime   # Claude Code CLI (subscription)
from agentic.providers import CodexRuntime        # Codex CLI (subscription)
from agentic.providers import GeminiCLIRuntime    # Gemini CLI (free)
```

All CLI providers maintain **session continuity** — context accumulates across `runtime.exec()` calls within the same Runtime instance.

See [Provider docs](docs/api/providers.md) for detailed setup.

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

## Project Structure

```
agentic/
├── __init__.py              # agentic_function, Runtime, Context, create_runtime, ...
├── context.py               # Context tree
├── function.py              # @agentic_function decorator
├── runtime.py               # Runtime class (exec + retry)
├── meta_functions/          # LLM-powered code generation
│   ├── create.py            # create() — generate a single function
│   ├── create_app.py        # create_app() — generate a complete app
│   ├── fix.py               # fix() — rewrite broken functions
│   └── create_skill.py      # create_skill() — generate SKILL.md
├── providers/               # Anthropic, OpenAI, Gemini, Claude Code, Codex, Gemini CLI
└── functions/               # saved generated functions

apps/                        # generated apps (from create_app)
skills/                      # SKILL.md files for agent integration
examples/                    # runnable demos
docs/                        # API reference and guides
tests/                       # pytest suite
```

## Integration

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/GETTING_STARTED.md) | 3-minute setup, provider comparison, runnable examples |
| [Claude Code Integration](docs/INTEGRATION_CLAUDE_CODE.md) | Use without API key via Claude Code CLI |
| [OpenClaw Integration](docs/INTEGRATION_OPENCLAW.md) | Use as OpenClaw skill or MCP tool |
| [API Reference](docs/API.md) | Full API documentation |

---

## Contributing

This project is a **paradigm proposal** with a reference implementation. We welcome:

- 🧠 **Discussions** on the programming model
- 🔧 **Alternative implementations** in other languages or frameworks
- 📝 **Use cases** that validate or challenge the approach
- 🐛 **Bug reports** on the reference implementation

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

If you build something based on Agentic Programming, let us know!

## License

MIT
