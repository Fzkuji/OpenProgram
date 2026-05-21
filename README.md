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
pip install openprogram                             # install (TUI + web UI included)
openprogram setup                                   # connect a provider (interactive)
```

Then chat with it — either in the terminal or the browser:

```bash
openprogram                                         # full-screen TUI
```

For the web UI, just open your browser at **http://localhost:3000** — `openprogram setup` starts the worker in the background (frontend on 3000, FastAPI backend on 8109) so the page is already live.

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

Set `OPENPROGRAM_WEB_PORT=8101` (frontend) or `OPENPROGRAM_BACKEND_PORT=8102` (FastAPI) before starting the worker. Or store the preference via `openprogram config ui`.

</details>

<details>
<summary><b>Local-development install (multi-repo)</b></summary>

For [GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness) / [Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness):

```bash
pip install -e "$OPENPROGRAM_DIR"                   # first
pip install -e "$GUI_HARNESS_DIR"                   # depends on openprogram
pip install -e "$RESEARCH_HARNESS_DIR"
```

`openprogram/functions/agentics/{GUI,Research}-Agent-Harness` are symlinks — recreate if a repo moves:

```bash
cd openprogram/functions/agentics
rm -f GUI-Agent-Harness && ln -s "$GUI_HARNESS_DIR" GUI-Agent-Harness
rm -f Research-Agent-Harness && ln -s "$RESEARCH_HARNESS_DIR" Research-Agent-Harness
```

`pip install -e` writes absolute paths — rerun it from the new location if you rename a parent folder.

</details>

For platform-builder topics — `Runtime` retry semantics, the full `agentic_function` decorator API, the flat-DAG context model — see [docs/API.md](docs/API.md) and the per-topic notes under [docs/api/](docs/api/).

---

## Supported Projects

Three agent applications ship with OpenProgram, under `openprogram/functions/agentics/` — each a full agent built on the `@agentic_function` paradigm:

| Project | What it does |
|---------|--------------|
| [GUI&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | Autonomous GUI agent — operates desktop apps (and OSWorld VMs) by vision: observe → plan → act → verify. Python drives the loop, the LLM only reasons when asked. |
| [Research&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Research-Agent-Harness) | Autonomous research agent — literature survey → idea → experiments → paper writing → cross-model review. Full pipeline from topic to submission-ready paper. |
| [Wiki&nbsp;Agent&nbsp;Harness](https://github.com/Fzkuji/Wiki-Agent-Harness) | Autonomous wiki builder — ingests notes, documents and conversations into a structured, Obsidian-compatible knowledge vault with `[[wikilinks]]`. |

## Why Agentic Programming?

<p align="center">
  <img src="docs/images/the_idea.png" alt="Python controls flow, LLM reasons" width="800">
</p>

| Principle | How |
|-----------|-----|
| **Deterministic flow** | Python controls `if/else/for/while`. Execution is guaranteed, not suggested. |
| **Minimal LLM calls** | Call the LLM only when reasoning is needed. 2 calls, not 10. |
| **Prompt in code** | The per-call prompt lives in the function body (`runtime.exec(content=...)`), not in scattered prompt files. |
| **Self-evolving** | Agents author, fix and improve functions directly — guided by the `agentic-programming` skill. |

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
from openprogram.functions.agentics.deep_work import deep_work

result = deep_work(
    task="Write a survey on context management in LLM agents.",
    level="phd",        # high_school → bachelor → master → phd → professor
    runtime=runtime,
)
```

The agent clarifies requirements upfront, then works fully autonomously — executing, self-evaluating, and revising until the output passes quality review. State is persisted to disk, so interrupted work resumes where it left off.

### Functions that author functions

Writing, fixing and scaffolding `@agentic_function`s is itself agent work — done with ordinary file-editing tools, guided by the **`agentic-programming` skill** (`skills/agentic-programming/SKILL.md`). There are no dedicated `create()` / `fix()` framework calls: they only ever wrapped one LLM call plus a file write, which an agent does directly.

The skill is the complete spec — where the file goes, the decorator's metadata, the docstring vs `content` split, a rule-based validation checklist, and a smoke test. An agent reads it, writes the function, validates it, runs it; the `write → run → fail → fix` cycle still means programs improve through use.

## API Reference

### Core

| Import | What it does |
|--------|-------------|
| `from openprogram import agentic_function` | Decorator. Records each call as a node in the session DAG |
| `from openprogram.agentic_programming.runtime import Runtime` | LLM runtime. `exec()` calls the LLM with DAG-derived context |
| `from openprogram.providers.registry import create_runtime` | Create a Runtime with auto-detection or explicit provider (`create_runtime()` checks API keys and CLIs in priority order) |

### Authoring functions

There are no `create()` / `fix()` meta-functions — writing, editing and
validating `@agentic_function`s is done directly with ordinary
file-editing tools, guided by the **`agentic-programming` skill**
(`skills/agentic-programming/SKILL.md`). That skill is the complete spec:
file layout, the decorator's metadata, the docstring vs `content` split,
and a rule-based validation checklist.

### Built-in Functions

| Import | What it does |
|--------|-------------|
| `from openprogram.functions.agentics.deep_work import deep_work` | Autonomous plan-execute-evaluate loop with quality levels |
| `from openprogram.functions.agentics.ask_user import ask_user` | Ask the user a clarifying question and block until an answer arrives |

### Providers

Six built-in providers: Anthropic, OpenAI, Gemini (API), Claude Code, Codex, Gemini (CLI). All CLI providers maintain **session continuity** across calls. See [Provider docs](docs/api/providers.md) for details.

### API Docs by Topic

- [agentic_function](docs/api/agentic_function.md) — decorator behavior, DAG node recording, the docstring / `content` split
- [Runtime](docs/api/runtime.md) — `exec()`, retries, response formats, provider wiring
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
├── __init__.py                      # agentic_function re-export
├── cli.py                           # `openprogram` command entry point
├── agentic_programming/             # engine — paradigm-essential primitives
│   ├── function.py                  #   @agentic_function decorator
│   ├── runtime.py                   #   Runtime (exec + retry + DAG context)
│   ├── session.py                   #   session lifecycle
│   └── skills.py                    #   SKILL.md discovery
├── context/                         # flat-DAG context model — nodes, storage, render, compute_reads
├── providers/                       # Anthropic, OpenAI, Gemini, Claude Code, Codex, Gemini CLI
├── functions/
│   ├── _registry.py                 #   unified registry for tools + agentic functions
│   ├── tools/                       #   @function leaves — bash, read, edit, web_search, …
│   └── agentics/                    #   @agentic_function modules (each its own dir, code in __init__.py)
│       ├── ask_user/                #     ask the user a clarifying question
│       ├── deep_work/               #     autonomous plan-execute-evaluate loop
│       ├── extract_pdf_figures/     #     PDF figure extraction
│       ├── …                        #     other agentics …
│       ├── GUI-Agent-Harness/       #     GUI agent (separate repo, symlink)
│       ├── Research-Agent-Harness/  #     Research agent (separate repo, symlink)
│       └── Wiki-Agent-Harness/      #     Wiki agent (separate repo, symlink)
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
