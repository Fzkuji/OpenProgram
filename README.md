<p align="center">
  <img src="docs/images/logo.svg" alt="OpenProgram" width="300">
</p>

<p align="center">Open Source Agent Harness Framework. Any LLM. Any platform. Agentic Programming Paradigm.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/actions/workflows/ci.yml"><img alt="Build status" src="https://img.shields.io/github/actions/workflow/status/Fzkuji/OpenProgram/ci.yml?branch=main&style=flat-square&label=build"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
</p>

<p align="center">
  <a href="docs/GETTING_STARTED.md">Getting Started</a> &middot;
  <a href="docs/API.md">API Reference</a> &middot;
  <a href="docs/philosophy/agentic-programming.md">Philosophy</a> &middot;
  <a href="docs/README_CN.md">中文</a>
</p>

---

> **Built on the Agentic Programming paradigm.** Current LLM agent frameworks let the LLM control everything — what to do, when, and how. The result? Unpredictable execution, context explosion, and no output guarantees. OpenProgram flips this: **Python controls the flow, LLM only reasons when asked.** See [philosophy](docs/philosophy/agentic-programming.md) for the full rationale.

<p align="center">
  <img src="docs/images/code_hero.png" alt="OpenProgram code example" width="800">
</p>

## Quick Start

Requires **Python 3.11+**.

```bash
pip install "openprogram[web]"                      # install (with web UI)
openprogram setup                                   # connect a provider (interactive)
```

Then chat with it — either in the terminal or the browser:

```bash
openprogram                                         # full-screen TUI
```

For the web UI, just open your browser at **http://localhost:8765** — `openprogram setup` starts the worker in the background so the page is already live.

Both surfaces share the same backend — sessions, settings, web-search defaults are persisted in `~/.agentic/` and visible from either entry point.

## Setup

`openprogram setup` is a wizard that imports credentials from any CLI you've already logged into and asks for missing API keys. Skip it by setting one of these env vars yourself:

```bash
export ANTHROPIC_API_KEY=sk-ant-...                 # Claude
export OPENAI_API_KEY=sk-...                        # GPT
export GOOGLE_API_KEY=...                           # Gemini
```

Or use a CLI provider (no API key, uses your existing subscription):

```bash
npm i -g @anthropic-ai/claude-code && claude login
npm i -g @openai/codex && codex auth
npm i -g @google/gemini-cli && gemini auth login
```

Check what's detected with `openprogram providers`. Auto-detection order: **Claude Code → Codex → Gemini CLI → Anthropic API → OpenAI API → Gemini API**.

## Optional extras

| Extra | Adds | Post-install |
|---|---|---|
| `[web]` | Browser chat UI | — |
| `[anthropic]` / `[openai]` / `[gemini]` | Provider SDKs | — |
| `[browser]` | Playwright (~150 MB) | `playwright install chromium` |
| `[browser-stealth]` | Cloudflare-bypassing browsers | `patchright install chromium && camoufox fetch` |
| `[gui]` | Vision/control deps for GUI harness (~2 GB) | — |
| `[channels]` | Discord / Slack / WeChat bots | — |
| `[all]` | Everything except `[browser-stealth]` | run post-install commands as needed |

## Troubleshooting

<details>
<summary><b>"No provider available"</b></summary>

`openprogram providers` shows what's detected. Common causes: forgot `claude login` / `codex auth`; API key set in a different shell than you're running in; token expired (re-login).

</details>

<details>
<summary><b>"command not found: openprogram"</b></summary>

pip install dir not on PATH. Use `python3 -m openprogram <args>` instead, or add `$(python3 -m site --user-base)/bin` to your PATH.

</details>

<details>
<summary><b>Web UI port in use</b></summary>

`openprogram web --port 8766` — or store the preference via `openprogram config ui`.

</details>

<details>
<summary><b>Local-development install (multi-repo)</b></summary>

For [GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness) / [Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness):

```bash
pip install -e "$OPENPROGRAM_DIR"                   # first
pip install -e "$GUI_HARNESS_DIR"                   # depends on openprogram
pip install -e "$RESEARCH_HARNESS_DIR"
```

`openprogram/programs/applications/{GUI,Research}-Agent-Harness` are symlinks — recreate if a repo moves:

```bash
cd openprogram/programs/applications
rm -f GUI-Agent-Harness && ln -s "$GUI_HARNESS_DIR" GUI-Agent-Harness
rm -f Research-Agent-Harness && ln -s "$RESEARCH_HARNESS_DIR" Research-Agent-Harness
```

`pip install -e` writes absolute paths — rerun it from the new location if you rename a parent folder.

</details>

### Retry and recovery

Transient provider failures are handled at the `Runtime` layer, so you can retry just the LLM call instead of restarting the whole workflow:

```python
from openprogram import Runtime

runtime = Runtime(call=my_llm_call, max_retries=3)
```

`max_retries` counts the total number of attempts, including the first call. In other words:

- `max_retries=1` means try once, then fail immediately
- `max_retries=2` means first call + one retry
- `max_retries=3` means first call + up to two retries
- `max_retries=0` is invalid and raises `ValueError` at `Runtime(...)` construction time

The retry loop is designed for transient provider failures such as rate limits, flaky network requests, and temporary upstream errors. `TypeError` and `NotImplementedError` are treated as implementation errors and are raised immediately instead of being retried.

Retry attempts are recorded in the execution tree, so `context.traceback()` and `context.save("trace.jsonl")` preserve the full failure history:

```python
[
    {"attempt": 1, "reply": None, "error": "ConnectionError: timeout"},
    {"attempt": 2, "reply": "ok", "error": None},
]
```

That retry history also feeds into `fix()`, which means a later repair pass can see what actually failed instead of guessing from scratch.

### `fix()` for broken generated functions

When a generated function fails, `fix()` uses the function source plus recent error context to rewrite it:

```python
from openprogram.programs.functions.meta import create, fix

extract_emails = create("Extract all emails from text as a JSON array", runtime=runtime)

try:
    extract_emails(text="Contact us at hello@example.com")
except Exception:
    extract_emails = fix(
        fn=extract_emails,
        runtime=runtime,
        instruction="Always return valid JSON array output.",
    )
```

Internally this runs a clarify → generate → verify loop, which makes it a good fit for tightening output formats after real failures instead of regenerating from scratch.

A few practical details matter:

- `fix()` can inspect the function source, function name, and recent `Context` failure history
- if retries already happened, those recorded attempts become part of the repair context
- if the verifier never accepts a rewrite within `max_rounds`, `fix()` returns a summary string instead of raising
- if more information is needed and no `ask_user` handler is installed, it can return a follow-up payload like `{"type": "follow_up", "question": "..."}`
- call sites should branch on the return type: `callable` means a repaired function, `dict` means follow-up is required, and `str` means repair exhausted its rounds and returned a failure summary

Use `Runtime(max_retries=...)` for transient API problems, and `fix()` for structural problems in the generated function itself. They complement each other rather than overlapping.

---

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

---

## Key Features

### Automatic Context

Every `@agentic_function` call creates a **Context** node. Nodes form a tree that is automatically injected into LLM calls:

```
login_flow ✓ 8.8s
├── observe ✓ 3.1s → "found login form at (200, 300)"
├── click ✓ 2.5s → "clicked login button"
└── verify ✓ 3.2s → "dashboard confirmed"
```

When `verify` calls the LLM, it automatically sees what `observe` and `click` returned. No manual context management.

### Deep Work — Autonomous Quality Loop

For complex tasks that demand sustained effort and high standards, `deep_work` runs an autonomous plan-execute-evaluate loop until the result meets the specified quality level:

```python
from openprogram.programs.functions.buildin.deep_work import deep_work

result = deep_work(
    task="Write a survey on context management in LLM agents.",
    level="phd",        # high_school → bachelor → master → phd → professor
    runtime=runtime,
)
```

The agent clarifies requirements upfront, then works fully autonomously — executing, self-evaluating, and revising until the output passes quality review. State is persisted to disk, so interrupted work resumes where it left off.

### Self-Evolving Code

Functions can generate new functions, fix broken ones, and scaffold complete apps — all at runtime:

```python
from openprogram.programs.functions.meta import create, create_app, fix

# Generate a function from description
sentiment = create("Analyze text sentiment", runtime=runtime, name="sentiment")
sentiment(text="I love this!")  # → "positive"

# Generate a complete app (runtime + argparse + main)
create_app("Summarize articles from URLs", runtime=runtime, name="summarizer")
# → openprogram/programs/applications/summarizer.py

# Fix a broken function — auto-reads source & error history
# Runs a clarify → generate → verify loop (up to max_rounds=5 by default)
fixed = fix(fn=broken_fn, runtime=runtime, instruction="return JSON, not plain text")
```

The `create → run → fail → fix → run` cycle means programs improve themselves through use.

## Ecosystem

OpenProgram ships with two built-in apps under `openprogram/programs/applications/`:

| App | Description |
|-----|-------------|
| [GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | Autonomous GUI agent that operates desktop apps via vision + agentic functions. Python controls observe→plan→act→verify loops; the LLM only reasons when asked. |
| [Research&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Research-Agent-Harness) | Autonomous research agent: literature survey → idea → experiments → paper writing → cross-model review. Full pipeline from topic to submission-ready paper. |

## API Reference

### Core

| Import | What it does |
|--------|-------------|
| `from openprogram import agentic_function` | Decorator. Records execution into Context tree |
| `from openprogram import Runtime` | LLM runtime. `exec()` calls the LLM with auto-context |
| `from openprogram import Context` | Execution tree. `tree()`, `save()`, `traceback()` |
| `from openprogram import create_runtime` | Create a Runtime with auto-detection or explicit provider (`create_runtime()` checks API keys and CLIs in priority order) |

### Meta Functions

| Import | What it does |
|--------|-------------|
| `from openprogram.programs.functions.meta import create` | Generate a new `@agentic_function` from description |
| `from openprogram.programs.functions.meta import create_app` | Generate a complete runnable app with `main()` |
| `from openprogram.programs.functions.meta import fix` | Fix broken functions via multi-round LLM analysis (clarify → generate → verify loop, up to `max_rounds`) |
| `from openprogram.programs.functions.meta import create_skill` | Generate a SKILL.md for agent discovery |

### Built-in Functions

| Import | What it does |
|--------|-------------|
| `from openprogram.programs.functions.buildin.deep_work import deep_work` | Autonomous plan-execute-evaluate loop with quality levels |
| `from openprogram.programs.functions.buildin.agent_loop import agent_loop` | General-purpose autonomous agent loop |
| `from openprogram.programs.functions.buildin.general_action import general_action` | Give the LLM full freedom to complete a single task |
| `from openprogram.programs.functions.buildin.wait import wait` | LLM decides how long to wait based on context |

### Providers

Six built-in providers: Anthropic, OpenAI, Gemini (API), Claude Code, Codex, Gemini (CLI). All CLI providers maintain **session continuity** across calls. See [Provider docs](docs/api/providers.md) for details.

### API Docs by Topic

- [agentic_function](docs/api/agentic_function.md) — decorator behavior, context injection, auto-save
- [Runtime](docs/api/runtime.md) — `exec()`, retries, response formats, provider wiring
- [Context](docs/api/context.md) — execution tree, `tree()`, `save()`, traceback views
- [Meta Functions](docs/api/meta_function.md) — `create()`, `create_app()`, `fix()`, `create_skill()`
- [Providers](docs/api/providers.md) — built-in runtimes, detection order, CLI vs API tradeoffs

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
openprogram/
├── __init__.py                      # agentic_function, Runtime, Context, create_runtime
├── cli.py                           # `openprogram` command entry point
├── agentic_programming/             # engine — paradigm-essential primitives
│   ├── function.py                  #   @agentic_function decorator
│   ├── runtime.py                   #   Runtime (exec + retry + context injection)
│   ├── context.py                   #   Context tree
│   ├── events.py                    #   streaming events
│   └── persistence.py               #   load / save traces
├── providers/                       # Anthropic, OpenAI, Gemini, Claude Code, Codex, Gemini CLI
├── programs/
│   ├── functions/
│   │   ├── meta/                    #   create / create_app / edit / fix / create_skill
│   │   ├── buildin/                 #   deep_work / agent_loop / general_action / wait / ask_user
│   │   └── third_party/             #   user-generated via `openprogram create`
│   └── applications/                # full apps built on OpenProgram
│       ├── GUI-Agent-Harness/       #   symlink → GUI agent repo (checked out separately)
│       └── Research-Agent-Harness/  #   symlink → Research agent repo (checked out separately)
└── webui/                           # `openprogram web` — browser UI
skills/                              # SKILL.md files for agent integration
examples/                            # runnable demos
tests/                               # pytest suite
```

</details>

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome discussions, alternative implementations in other languages, use cases that validate or challenge the approach, and bug reports.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Acknowledgements

OpenProgram stands on shoulders. The tool framework, provider abstraction, and
several tool implementations were ported or adapted from the projects below —
each under its own license. Enormous thanks to their authors.

- [**OpenClaw**](https://github.com/openclaw/openclaw) (MIT) — layout of the
  tool registry (`name / description / parameters / execute`), provider
  abstraction with `check_fn` + `requires_env` gating, `TOOLSETS` presets,
  skill loading via SKILL.md frontmatter + late-bound `read`. Our full clone
  lives under `references/openclaw/` (gitignored) for browsing.
- [**hermes-agent**](https://github.com/himanshuishere/hermes-agent)
  (MIT) — starting point for `execute_code` (we trimmed the
  Docker / Modal layers), `mixture_of_agents`, and the general shape of the
  multi-provider `web_search` / `image_generate` / `image_analyze` tools.
- [**pi-coding-agent**](https://github.com/mariozechner/pi-coding-agent)
  (MIT) — via OpenClaw's import, the canonical AgentSkill shape
  (`<available_skills>` XML formatter, name / description / location).
- [**Claude Code**](https://www.anthropic.com/claude-code) — overall ergonomics
  of the `DEFAULT_TOOLS` set (bash + read / write / edit + glob / grep / list
  + apply_patch + todo_read / todo_write) and the `todo` tool's JSON schema.
- **Anthropic / OpenAI / Google SDKs** — provider HTTP contracts; our
  providers call the raw HTTP APIs to keep SDK dependencies optional.

Individual tool files call out their direct inspirations in file-level
docstrings where the lineage is more specific.

## License

MIT
