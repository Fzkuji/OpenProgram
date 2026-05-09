<p align="center">
  <img src="docs/images/logo.svg" alt="OpenProgram" width="280">
</p>

<h1 align="center">OpenProgram</h1>

<p align="center">
  <strong>The agent harness where Python schedules and the LLM only reasons when asked.</strong>
</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/actions/workflows/ci.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/Fzkuji/OpenProgram/ci.yml?branch=main&style=for-the-badge&label=Build"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=for-the-badge&logo=github"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/discussions"><img alt="Discussions" src="https://img.shields.io/badge/Chat-Discussions-purple?style=for-the-badge&logo=github"></a>
</p>

<p align="center">
  <a href="docs/GETTING_STARTED.md">Getting Started</a> ·
  <a href="docs/API.md">API Reference</a> ·
  <a href="docs/philosophy/agentic-programming.md">Philosophy</a> ·
  <a href="docs/WORKER.md">Worker</a> ·
  <a href="docs/WEB_UI.md">Web UI</a> ·
  <a href="docs/README_CN.md">中文</a>
</p>

<p align="center">
  <img src="docs/images/code_hero.png" alt="OpenProgram code example" width="860">
</p>

---

**OpenProgram is an agent harness built on a different premise than every other LLM framework you've used.** Instead of giving the model the steering wheel and praying it doesn't crash, OpenProgram puts Python back in charge: real `if` / `for` / `while` control flow, real type signatures, real exceptions — and the LLM is just a function you call when reasoning beats logic. We call it **Agentic Programming**.

That single inversion fixes the three things that make existing frameworks fragile: **execution becomes deterministic** (Python decides what runs), **context stays bounded** (a structured execution tree, not an exploding transcript), and **prompts live in the code** (the docstring is the prompt — change it, change the behavior). Functions can also rewrite themselves at runtime: `create()` writes a new agentic function from a description, `fix()` reads the source plus the failure history and patches it.

You can drive the same runtime from a Python script, a terminal UI, a browser, a Slack/Discord/WeChat message, or as a system service that runs in the background and accepts work from all of those at once.

<table>
<tr>
  <td><b>🐍 Python in charge</b></td>
  <td>Every <code>@agentic_function</code> is real Python. Schedules its own tool calls. Calls the LLM only when reasoning beats deterministic logic. Nothing the model says can break out of your control flow.</td>
</tr>
<tr>
  <td><b>♻️ Self-evolving programs</b></td>
  <td><code>create()</code> writes a new agentic function from a one-line description. <code>fix()</code> reads the source plus recent failure history and rewrites it. <code>create_app()</code> scaffolds a complete runnable app with <code>main()</code>. Programs improve themselves through use.</td>
</tr>
<tr>
  <td><b>🌳 Context as a tree, not a transcript</b></td>
  <td>Each call records inputs, outputs, retries, and child calls into a structured execution tree. Future LLM calls in the same tree see the relevant history automatically — no manual prompt-stuffing, no flat conversation log.</td>
</tr>
<tr>
  <td><b>🔌 Six providers, no lock-in</b></td>
  <td>Anthropic, OpenAI, Gemini APIs plus Claude Code, Codex, Gemini CLIs. CLI providers reuse the user's existing subscription — no extra API key, no per-token billing. Switch with one line: <code>create_runtime(provider="...")</code>.</td>
</tr>
<tr>
  <td><b>🎛️ Five front-ends, one runtime</b></td>
  <td>Python library, terminal UI, browser UI, Skills bundle for Claude Code / Gemini CLI / OpenClaw, and a persistent worker. The worker hosts the web UI and channel adapters at once; auto-starts at login (launchd / systemd) and survives terminal crashes.</td>
</tr>
<tr>
  <td><b>🧠 Memory that lasts across sessions</b></td>
  <td>Three-layer memory under <code>~/.agentic/memory/</code>: short-term notes, a curated wiki, and a small core snapshot injected into every conversation. Built on plain markdown + SQLite FTS5 — no embeddings, no opaque vector store, fully grep-able and git-friendly.</td>
</tr>
<tr>
  <td><b>📦 Real applications, not just demos</b></td>
  <td>Ships with two production agents: <a href="https://github.com/Fzkuji/GUI-Agent-Harness">GUI-Agent-Harness</a> (operates desktop apps via vision + agentic functions) and <a href="https://github.com/Fzkuji/Research-Agent-Harness">Research-Agent-Harness</a> (literature → idea → experiments → paper writing → cross-model review).</td>
</tr>
</table>

---

## How it works

<p align="center">
  <img src="docs/images/the_problem.png" alt="The problem with LLM-as-scheduler" width="860">
</p>

Most agent frameworks place the LLM as the central scheduler. The model decides which tool to call, when to call it, and what to do with the result. This sounds flexible, but produces three failure modes:

- **Unpredictable execution** — the LLM may skip, repeat, or invent steps regardless of defined workflows.
- **Context explosion** — every tool call accumulates history, every history token costs more reasoning quality.
- **No output guarantees** — the LLM interprets instructions; nothing enforces them.

<p align="center">
  <img src="docs/images/the_idea.png" alt="Python controls flow, LLM reasons" width="860">
</p>

OpenProgram inverts the relationship: **the program is in charge, the LLM is a tool**. Python's control structures decide what runs and when. The LLM is invoked from inside the code only when reasoning is the cheapest way to make a decision. Each invocation is recorded in an execution tree that the next call can see automatically. Prompts live in docstrings, so the prompt and the code that uses it can never drift apart.

|  | Tool-Calling / MCP | Agentic Programming |
|---|---|---|
| **Who schedules?** | LLM decides | Python decides |
| **Functions contain** | Code only | Code + LLM reasoning |
| **Context** | Flat conversation | Structured tree |
| **Prompt** | Hidden in agent config | Docstring is the prompt |
| **Self-improvement** | Not built-in | `create` → `fix` → evolve |

MCP is the *transport*. Agentic Programming is the *execution model*. They're orthogonal — OpenProgram speaks MCP both as client and server.

[Full rationale →](docs/philosophy/agentic-programming.md)

---

## Quick install

```bash
pip install "openprogram[all]"     # everything: providers + web UI + GUI harness + browser
openprogram setup                  # interactive wizard: pick provider, log in, verify
```

Or pick the parts you need:

```bash
pip install openprogram                  # core only (pure Python, no deps)
pip install "openprogram[openai]"        # + OpenAI SDK   (also [anthropic], [gemini])
pip install "openprogram[web]"           # + browser UI
pip install "openprogram[gui]"           # + GUI agent (opencv / torch / ultralytics — ~2 GB)
pip install "openprogram[browser]"       # + Playwright tool
playwright install chromium              # one-time chromium binary fetch (~150 MB)
```

After install, `openprogram providers` shows what the library can see on your machine.

---

## Five ways to use it

| Entry point | Command | When to use |
|---|---|---|
| **Python library** | `import openprogram` | Writing agentic code that other Python code calls. |
| **TUI** | `openprogram chat` | Quick conversation in the terminal. Streaming, history, slash commands. |
| **Web UI** | `openprogram web` | Browser interface with execution-tree visualization, history graph, programs panel. |
| **Skills** | `openprogram install-skills` | Hand the toolkit to Claude Code / Gemini CLI / OpenClaw — they call OpenProgram functions natively. |
| **Worker** | `openprogram worker install` | Persistent background process. Hosts web UI, channels, scheduled jobs. Auto-starts at login. |

The worker, web UI, and TUI all read and write the same conversation database — switch between them mid-conversation, the state follows.

---

## A 30-second example

```python
from openprogram import agentic_function, create_runtime

runtime = create_runtime()              # auto-detects provider

@agentic_function
def summarize(text):
    """Summarize this text into 3 bullet points."""
    return runtime.exec(content=[{"type": "text", "text": text}])

print(summarize("Agentic Programming is a paradigm where ..."))
```

The docstring is the prompt. `runtime.exec()` is the LLM call. Wrap it in `if`, `for`, or another `@agentic_function` — Python decides flow, the LLM only generates the bullets.

Generate a function instead of writing one:

```python
from openprogram.programs.functions.meta import create, fix

extract_emails = create("Extract all emails from text as a JSON array", runtime=runtime)
extract_emails(text="Contact us at hello@example.com")          # → ["hello@example.com"]

# When it breaks, fix it from the failure history:
fixed = fix(fn=extract_emails, runtime=runtime, instruction="always return valid JSON")
```

---

## Built-in applications

| App | What it does |
|---|---|
| [**GUI-Agent-Harness**](https://github.com/Fzkuji/GUI-Agent-Harness) | Operates desktop apps via vision. Python runs observe → plan → act → verify; the LLM only reasons. |
| [**Research-Agent-Harness**](https://github.com/Fzkuji/Research-Agent-Harness) | Literature survey → idea → experiments → paper writing → cross-model review. Topic to submission-ready PDF. |

Both ship as `@agentic_function`s — call them from Python, run them from the TUI, or invoke them through Claude Code as a skill.

---

## Provider matrix

| Provider | Mode | How to set it up |
|---|---|---|
| Claude Code CLI | subscription | `npm i -g @anthropic-ai/claude-code && claude login` |
| Codex CLI | subscription | `npm i -g @openai/codex && codex auth` |
| Gemini CLI | subscription | `npm i -g @google/gemini-cli` |
| Anthropic API | per-token | `export ANTHROPIC_API_KEY=...` |
| OpenAI API | per-token | `export OPENAI_API_KEY=...` |
| Gemini API | per-token | `export GOOGLE_API_KEY=...` |

`create_runtime()` auto-detects in this order. Override explicitly with `create_runtime(provider="anthropic", model="claude-sonnet-4-6")`.

---

## Documentation

| Section | What's covered |
|---|---|
| [Getting Started](docs/GETTING_STARTED.md) | Three-minute install + first runnable example. |
| [API Reference](docs/API.md) | `@agentic_function`, `Runtime`, `Context`, `create_runtime`. |
| [Providers](docs/api/providers.md) | All six providers, detection order, CLI vs API trade-offs. |
| [Meta Functions](docs/api/meta_function.md) | `create()`, `create_app()`, `fix()`, `create_skill()`. |
| [Built-in Functions](docs/api/buildin.md) | `deep_work`, `agent_loop`, `general_action`, `wait`. |
| [Worker & Channels](docs/WORKER.md) | Persistent process, system service install, Discord / Telegram / WeChat. |
| [Web UI](docs/WEB_UI.md) | Browser interface, execution tree, history graph. |
| [Memory](docs/MEMORY.md) | Three-layer memory, wiki, sleep-time reflection. |
| [Skills Integration](docs/INTEGRATION_CLAUDE_CODE.md) | Use as a Claude Code / Gemini CLI / OpenClaw skill pack. |
| [Philosophy](docs/philosophy/agentic-programming.md) | Full rationale for the Agentic Programming paradigm. |

---

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome:

- Discussions and use-case validation
- Alternative implementations in other languages
- Bug reports and provider integrations
- New built-in functions and applications

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
pip install -e ".[all,dev]"
pytest -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for code style, PR process, and project layout.

---

## Acknowledgements

OpenProgram stands on shoulders. The tool framework, provider abstraction, and several tool implementations were ported or adapted from the projects below — each under its own license.

- **[OpenClaw](https://github.com/openclaw/openclaw)** (MIT) — tool registry layout, provider abstraction with `check_fn` + `requires_env` gating, `TOOLSETS` presets, skill loading via SKILL.md frontmatter.
- **[hermes-agent](https://github.com/NousResearch/hermes-agent)** (MIT) — starting point for `execute_code`, `mixture_of_agents`, multi-provider `web_search` shape, and the lifecycle-hook design that the memory subsystem now uses.
- **[pi-coding-agent](https://github.com/mariozechner/pi-coding-agent)** (MIT) — canonical `AgentSkill` shape (`<available_skills>` XML formatter).
- **[Claude Code](https://www.anthropic.com/claude-code)** — ergonomics of the default tool set and the `todo` tool's JSON schema.
- **Anthropic / OpenAI / Google SDKs** — raw HTTP API contracts; our providers call the wire protocol directly so SDK dependencies stay optional.

File-level docstrings call out direct inspirations where the lineage is more specific.

---

## Community

- 💬 [Discussions](https://github.com/Fzkuji/OpenProgram/discussions) — questions, ideas, use cases
- 🐛 [Issues](https://github.com/Fzkuji/OpenProgram/issues) — bug reports, feature requests
- 📖 [Docs](https://github.com/Fzkuji/OpenProgram/tree/main/docs) — full documentation tree

---

## License

MIT — see [LICENSE](LICENSE).
