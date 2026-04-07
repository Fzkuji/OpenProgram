<p align="center">
  <img src="docs/images/banner.png" alt="Agentic Programming" width="800">
</p>

<h1 align="center">Agentic Programming</h1>

<p align="center">
  <strong>Python functions that think.</strong><br>
  A programming paradigm where Python controls flow and LLM handles reasoning.
</p>

<p align="center">
  <a href="https://pypi.org/project/agentic-programming/"><img src="https://img.shields.io/pypi/v/agentic-programming?color=blue" alt="PyPI"></a>
  <a href="https://pepy.tech/project/agentic-programming"><img src="https://static.pepy.tech/badge/agentic-programming" alt="Downloads"></a>
  <a href="https://github.com/Fzkuji/Agentic-Programming/actions/workflows/ci.yml"><img src="https://github.com/Fzkuji/Agentic-Programming/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Fzkuji/Agentic-Programming/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Fzkuji/Agentic-Programming" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/pypi/pyversions/agentic-programming" alt="Python"></a>
</p>

<p align="center">
  <a href="docs/GETTING_STARTED.md">Getting Started</a> &middot;
  <a href="docs/API.md">API Reference</a> &middot;
  <a href="docs/README_CN.md">中文</a>
</p>

---

> **This is a paradigm proposal.** Current LLM agent frameworks let the LLM control everything — what to do, when, and how. The result? Unpredictable execution, context explosion, and no output guarantees. We flip this: **Python controls the flow, LLM only reasons when asked.**

```python
from agentic import agentic_function, create_runtime

runtime = create_runtime()  # auto-detects best provider

@agentic_function
def summarize(text: str) -> str:
    """Summarize the given text into 3 bullet points."""
    return runtime.exec(content=[{"type": "text", "text": text}])

result = summarize(text="Your long article here...")
```

## Quick Start

```bash
pip install agentic-programming
```

Set up at least one LLM provider:

| Provider | Setup |
|----------|-------|
| Claude Code CLI | `npm i -g @anthropic-ai/claude-code && claude login` |
| Codex CLI | `npm i -g @openai/codex && codex auth` |
| Gemini CLI | `npm i -g @google/gemini-cli` |
| Anthropic API | `pip install "agentic-programming[anthropic]"` then `export ANTHROPIC_API_KEY=...` |
| OpenAI API | `pip install "agentic-programming[openai]"` then `export OPENAI_API_KEY=...` |
| Gemini API | `pip install "agentic-programming[gemini]"` then `export GOOGLE_API_KEY=...` |

Verify with `agentic providers`.

## Why Agentic Programming?

<p align="center">
  <img src="docs/images/the_idea.png" alt="Python controls flow, LLM reasons" width="800">
</p>

| Principle | How |
|-----------|-----|
| **Deterministic flow** | Python controls `if/else/for/while`. Execution is guaranteed, not suggested. |
| **Minimal LLM calls** | Call the LLM only when reasoning is needed. 2 calls, not 10. |
| **Docstring = Prompt** | Change the docstring, change the LLM's behavior. No separate prompt files. |
| **Self-evolving** | Functions generate, fix, and improve themselves at runtime. |

<details>
<summary><strong>The problem with current frameworks</strong></summary>

<p align="center">
  <img src="docs/images/the_problem.png" alt="LLM as Scheduler" width="800">
</p>

Current LLM agent frameworks place the LLM as the central scheduler. This creates three fundamental problems:

- **Unpredictable execution** — the LLM may skip, repeat, or invent steps regardless of defined workflows
- **Context explosion** — each tool-call round-trip accumulates history
- **No output guarantees** — the LLM interprets instructions rather than executing them

The core issue: **the LLM controls the flow, but nothing enforces it.** Skills, prompts, and system messages are suggestions, not guarantees.

</details>

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **Who schedules?** | LLM decides | Python decides |
| **Functions contain** | Code only | Code + LLM reasoning |
| **Context** | Flat conversation | Structured tree |
| **Prompt** | Hidden in agent config | Docstring = prompt |
| **Self-improvement** | Not built-in | `create` → `fix` → evolve |

MCP is the *transport*. Agentic Programming is the *execution model*. They're orthogonal.

## Usage

### Python

```python
@agentic_function
def login_flow(username, password):
    """Complete login flow."""
    observe(task="find login form")       # Python decides what to do
    click(element="login button")         # Python decides the order
    return verify(expected="dashboard")   # Python decides when to stop
```

Every call creates a **Context** node. Nodes form a tree that is automatically injected into LLM calls:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees what `observe` and `click` returned. No manual context management.

### Skills — agent integration

Install skills so your LLM agent can use agentic functions through natural language:

```bash
cp -r skills/* ~/.claude/skills/    # Claude Code
cp -r skills/* ~/.gemini/skills/    # Gemini CLI
```

Then talk to your agent:

> "Create a function that extracts emails from text"

### MCP — any MCP client

```bash
pip install "agentic-programming[mcp]"
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

### Self-Evolving Code

Functions can generate new functions, fix broken ones, and scaffold complete apps — all at runtime:

```python
from agentic.meta_functions import create, create_app, fix

# Generate a function from description
sentiment = create("Analyze text sentiment", runtime=runtime, name="sentiment")
sentiment(text="I love this!")  # → "positive"

# Generate a complete app (runtime + argparse + main)
create_app("Summarize articles from URLs", runtime=runtime, name="summarizer")
# → agentic/apps/summarizer.py

# Fix a broken function — auto-reads source & error history
fixed = fix(fn=broken_fn, runtime=runtime, instruction="return JSON, not plain text")
```

The `create → run → fail → fix → run` cycle means programs improve themselves through use.

## Ecosystem

| Project | Description |
|---------|-------------|
| [GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | Autonomous GUI agent that operates desktop apps via vision + agentic functions. Python controls observe→plan→act→verify loops; the LLM only reasons when asked. |
| [Research&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Research-Agent-Harness) | Autonomous research agent: literature survey → idea → experiments → paper writing → cross-model review. Full pipeline from topic to submission-ready paper. |

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

## Integration

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/GETTING_STARTED.md) | 3-minute setup and runnable examples |
| [Claude Code](docs/INTEGRATION_CLAUDE_CODE.md) | Use without API key via Claude Code CLI |
| [OpenClaw](docs/INTEGRATION_OPENCLAW.md) | Use as OpenClaw skill |
| [API Reference](docs/API.md) | Full API documentation |

<details>
<summary><strong>Project Structure</strong></summary>

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

</details>

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome discussions, alternative implementations in other languages, use cases that validate or challenge the approach, and bug reports.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

MIT
