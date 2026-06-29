<p align="center">
  <img src="../docs/images/logo.svg" alt="OpenProgram" width="300">
</p>

<p align="center">Open-source Agent Harness framework. Works with any LLM and platform. The Agentic Programming paradigm.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/actions/workflows/ci.yml"><img alt="Build status" src="https://img.shields.io/github/actions/workflow/status/Fzkuji/OpenProgram/ci.yml?branch=main&style=flat-square&label=build"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
</p>

<p align="center">
  <a href="GETTING_STARTED.md">Quick Start</a> &middot;
  <a href="README.md">Docs Home</a> &middot;
  <a href="API.md">API Reference</a> &middot;
  <a href="philosophy/agentic-programming.md">Design Philosophy</a> &middot;
  <a href="../README.md">English</a>
</p>

---

> **Built on the Agentic Programming paradigm.** Today's LLM agent frameworks let the LLM control everything — what to do, when to do it, how to do it. The result? Unpredictable execution, context blowup, no output guarantees. OpenProgram inverts this: **Python controls the flow, the LLM reasons only when asked.** See the [Design Philosophy](philosophy/agentic-programming.md) for details.

<p align="center">
  <img src="../docs/images/code_hero.png" alt="OpenProgram code example" width="800">
</p>

## Quick Start

### Prerequisites

OpenProgram requires at least one LLM provider. Set up any one of the following:

| Provider | Setup |
|--------|------|
| Claude Code CLI | `npm i -g @anthropic-ai/claude-code && claude login` |
| Codex CLI | `npm i -g @openai/codex && codex auth` |
| Gemini CLI | `npm i -g @google/gemini-cli` |
| Anthropic API | `export ANTHROPIC_API_KEY=...` |
| OpenAI API | `export OPENAI_API_KEY=...` |
| Gemini API | `export GOOGLE_API_KEY=...` |

Then pick how you want to use it:

### Path A: Python — write agentic code

Install the package and start coding right away:

One-step install of the host (web UI + terminal UI + browser/channels):

```bash
git clone https://github.com/Fzkuji/OpenProgram && cd OpenProgram
./scripts/install.sh              # macOS/Linux   ·   Windows:  .\scripts\install.ps1
```

The default install includes everything lightweight — the web UI, the TUI, the Research / Wiki agent programs, browser tools, and channels; the GUI agent is installed on demand (`openprogram programs install gui`, which downloads PyTorch); `--minimal` installs only the slim host. The provider SDKs (anthropic / openai / google-genai) are installed by default.

A harness is installed as an **OpenProgram program** into `openprogram/functions/agentics/<Harness>/`
and is **registered automatically**. The general approach (which works for your own harnesses too) is to clone it into that directory and then run its install script;
the pure-Python Research / Wiki programs also have a one-line shortcut command:

```bash
openprogram programs install research      # or wiki (for the GUI agent see its README; you must run its bundled install script)
```

```python
from openprogram import agentic_function
from openprogram.providers.registry import create_runtime

runtime = create_runtime()

@agentic_function
def login_flow(username, password):
    """Complete the login flow."""
    observe(task="find login form")       # Python decides what to do
    click(element="login button")         # Python decides the order
    return verify(expected="dashboard")   # Python decides when to stop
```

### Path B: Skills — let your LLM agent use it

After installing OpenProgram per "Path A", register the skills:

```bash
openprogram install-skills                # auto-detects Claude Code / Gemini CLI
```

Or manually:

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cp -r OpenProgram/skills/* ~/.claude/skills/    # Claude Code
cp -r OpenProgram/skills/* ~/.gemini/skills/    # Gemini CLI
```

Then talk to the agent: *"Create a function that extracts email addresses from text"*

The agent recognizes the skill, calls `openprogram create`, and the generated function handles everything from there.

Use `openprogram providers` to verify your configuration.

**Each provider supports multiple accounts** (each account is a named profile): use `openprogram providers use <provider> [profile]` to select which account a given provider currently runs on, and manage them (list / add / activate / rename / delete) from the same set of panels in the CLI, the Web UI (Settings → Providers), or the TUI (`/login <provider>`). Add **multiple API keys** to a single account to get automatic rotation — when a key is rate-limited, it is automatically cooled down and the next one takes over. See [docs/features.md](features.md#multi-account--key-rotation) for details.

### Path C: Web UI

A browser-based interface for running functions live, managing conversations, and viewing the execution tree. The installer (Path A) has already built the web UI, so just launch it:

```bash
openprogram web
```

Open `http://localhost:18100`. The Next.js frontend runs on 18100, and the FastAPI backend runs on 18109 by default. Light and dark themes are supported (Settings → General).

---

## Supported Projects

OpenProgram ships with three agent applications, located in `openprogram/functions/agentics/` — each is a complete agent built on the `@agentic_function` paradigm:

| Project | Functionality |
|------|------|
| [GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | An autonomous GUI agent — operates desktop applications (and OSWorld virtual machines) through vision: observe → plan → act → verify. Python controls the loop; the LLM reasons only when asked. |
| [Research&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Research-Agent-Harness) | An autonomous research agent — literature review → idea → experiments → paper writing → cross-model peer review. The full pipeline from topic selection to submission. |
| [Wiki&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Wiki-Agent-Harness) | Autonomous wiki construction — organizes notes, docs, and conversations into a structured, Obsidian-compatible knowledge base with `[[wikilinks]]`. |

## Why Agentic Programming?

<p align="center">
  <img src="../docs/images/the_idea.png" alt="Python controls the flow, the LLM does the reasoning" width="800">
</p>

| Principle | How |
|------|------|
| **Deterministic flow** | Python controls `if/else/for/while`. The execution path is guaranteed, not suggested. |
| **Minimal LLM calls** | Call the LLM only when reasoning is needed. 2 calls, not 10. |
| **Instructions live in code** | The prompt for each call lives in `runtime.exec(content=...)` inside the function body, not scattered across separate prompt files. |
| **Self-evolving** | Functions are written, fixed, and improved by the agent directly — following the `agentic-programming` skill. |

<details>
<summary><strong>The problem with current frameworks</strong></summary>

<p align="center">
  <img src="../docs/images/the_problem.png" alt="The LLM as the scheduler" width="800">
</p>

Today's LLM agent frameworks put the LLM in the position of central scheduler. This causes three fundamental problems:

- **Unpredictable execution** — the LLM may skip, repeat, or invent steps on its own, ignoring the intended workflow
- **Context blowup** — every tool-call round trip accumulates history
- **No output guarantees** — the LLM is "understanding" instructions, not "executing" them

The core issue: **the LLM controls the flow, but nothing enforces it.** Skills, prompts, and system messages are only suggestions, not guarantees.

</details>

|  | Tool-Calling / MCP | Agentic Programming |
|--|---------------------|---------------------|
| **Who schedules?** | The LLM decides | Python decides |
| **A function contains** | Pure code | Code + LLM reasoning |
| **Context** | A flat conversation | A structured tree |
| **Prompt** | Hidden in the agent config | Docstring = prompt |
| **Self-improvement** | Not built in | `create` → `fix` → evolve |

MCP is a *transport layer*. Agentic Programming is an *execution model*. The two are orthogonal.

---

## Core Features

### Automatic context

Every `@agentic_function` call creates a **function node**, and every `runtime.exec()` creates an **exec node**. The nodes form a tree that is automatically injected into LLM calls:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s
│   └── _exec → "found login form at (200, 300)"
├── click ✓ 2.5s
│   └── _exec → "clicked login button"
└── verify ✓ 3.2s
    └── _exec → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees the return values of `observe` and `click`. No manual context management needed.

### Deep Work — an autonomous quality loop

For complex tasks that demand sustained effort and high standards, `deep_work` runs an autonomous plan-execute-evaluate loop until the output reaches the specified quality level:

```python
from openprogram.functions.agentics.deep_work import deep_work

result = deep_work(
    task="Write a survey paper on context management in LLM agents.",
    level="phd",        # high_school → bachelor → master → phd → professor
    runtime=runtime,
)
```

The agent first confirms the requirements, then works fully autonomously — executing, self-evaluating, and revising until it passes the quality review. State is persisted to disk, so interrupted work can resume from where it left off.

### Functions that write functions

Writing, fixing, and scaffolding `@agentic_function` is itself the agent's job — done directly with ordinary file-editing tools, following the **`agentic-programming` skill** (`skills/agentic-programming/SKILL.md`). There is no dedicated `create()` / `fix()` framework call: those used to be nothing more than a wrapper around one LLM call plus one file write, which the agent can do on its own.

The skill is the complete spec — where files go, decorator metadata, the division of labor between the docstring and `content`, the validation checklist, and the smoke test. The `write → run → fail → fix` loop still means programs improve themselves in use.

## API Reference

### Core

| Import | Functionality |
|------|------|
| `from openprogram import agentic_function` | The decorator. Each call is recorded as a node in the session DAG |
| `from openprogram.agentic_programming.runtime import Runtime` | The LLM runtime. `exec()` calls the LLM, with context computed automatically from the DAG |
| `from openprogram.providers.registry import create_runtime` | Creates a Runtime, with auto-detection or an explicitly specified provider |

### Writing functions

There are no meta-functions like `create()` / `fix()` — writing, modifying, and validating `@agentic_function` is done directly with ordinary file-editing tools, following the **`agentic-programming` skill** (`skills/agentic-programming/SKILL.md`). That skill is the complete spec: file layout, decorator metadata, the division of labor between the docstring and `content`, and the validation checklist.

### Built-in functions

| Import | Functionality |
|------|------|
| `from openprogram.functions.agentics.deep_work import deep_work` | An autonomous plan-execute-evaluate loop with quality levels |
| `from openprogram.functions.agentics.ask_user import ask_user` | Asks the user a clarifying question and blocks for the answer |

### Providers

Six built-in providers: Anthropic, OpenAI, Gemini (API), Claude Code, Codex, Gemini (CLI). All CLI providers maintain **session continuity** across calls. See the [Provider documentation](api/providers.md) for details.

## Integration

| Guide | Description |
|------|------|
| [Getting Started](GETTING_STARTED.md) | Get going in 3 minutes, with runnable examples |
| [Claude Code](INTEGRATION_CLAUDE_CODE.md) | Use it through the Claude Code CLI, no API key needed |
| [OpenClaw](INTEGRATION_OPENCLAW.md) | Use it as an OpenClaw skill |
| [API Reference](API.md) | Complete API documentation |

<details>
<summary><strong>Project structure</strong></summary>

```
openprogram/
├── __init__.py                      # agentic_function re-export
├── cli.py                           # `openprogram` command entry point
├── agentic_programming/             # the paradigm engine
│   ├── function.py                  #   @agentic_function decorator
│   ├── runtime.py                   #   Runtime (exec + retry + DAG context)
│   ├── session.py                   #   session lifecycle
│   └── skills.py                    #   SKILL.md discovery
├── context/                         # flat DAG context model — nodes / storage / render / render_context
├── providers/                       # Anthropic, OpenAI, Gemini, Claude Code, Codex, Gemini CLI
├── functions/
│   ├── _registry.py                 #   unified registry for tools + agentic functions
│   ├── tools/                       #   @function leaf tools — bash / read / edit / grep / semble_search / web_search, etc.
│   └── agentics/                    #   @agentic_function modules (one directory each, code in __init__.py)
│       ├── ask_user/                #     asks the user a clarifying question
│       ├── deep_work/               #     autonomous plan-execute-evaluate loop
│       ├── extract_pdf_figures/     #     PDF figure extraction
│       ├── …                        #     other agentic functions …
│       ├── GUI-Agent-Harness/       #     autonomous GUI agent (separate repo, symlinked)
│       ├── Research-Agent-Harness/  #     autonomous research agent (separate repo, symlinked)
│       └── Wiki-Agent-Harness/      #     autonomous wiki construction agent (separate repo, symlinked)
└── webui/                           # `openprogram web` browser UI
skills/                              # SKILL.md files for agent integration
examples/                            # runnable examples
tests/                               # pytest test suite
```

</details>

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome discussion, alternative implementations in other languages, use cases that validate or challenge the approach, and bug reports.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for details.

## License

[AGPL-3.0](../LICENSE) © 2026 Fzkuji. Free to use, study, modify, and distribute — but any derivative work that is **distributed or run as a networked service** must also be open-sourced under AGPL and retain attribution.
