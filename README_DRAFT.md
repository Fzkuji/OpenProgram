<p align="center">
  <img src="docs/images/logo.svg" alt="OpenProgram" width="280">
</p>

<h1 align="center">OpenProgram</h1>

<p align="center">
  <strong>The agent harness where Python schedules and the LLM only reasons when asked.</strong>
</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=for-the-badge"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/actions/workflows/ci.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/Fzkuji/OpenProgram/ci.yml?branch=main&style=for-the-badge&label=build"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="docs/GETTING_STARTED.md">Getting Started</a> ·
  <a href="docs/API.md">API Reference</a> ·
  <a href="docs/philosophy/agentic-programming.md">Philosophy</a> ·
  <a href="docs/README_CN.md">中文</a>
</p>

---

OpenProgram is an agent harness built on a different premise than most. Other frameworks let the LLM decide what to call, when, and in what order — and pay for that with unpredictable execution, context explosion, and no output guarantees. OpenProgram puts Python back in charge: control flow lives in `if` / `for` / `while` like normal code, the LLM is a function you invoke from inside that flow when reasoning is the cheapest way to make a decision. We call it **Agentic Programming**.

You can drive it from a Python script, a terminal UI, a browser, a Slack/Discord/WeChat message, or as a system service that runs in the background and accepts work from all of those at once.

<table>
<tr>
  <td><b>Python controls the flow</b></td>
  <td>Every <code>@agentic_function</code> is real Python code. Schedules its own tool calls. Calls the LLM only when reasoning beats deterministic logic. Nothing the LLM says can break out of your control flow.</td>
</tr>
<tr>
  <td><b>Self-evolving programs</b></td>
  <td><code>create()</code> writes a new agentic function from a description. <code>fix()</code> reads the source plus the failure history and rewrites it. Functions improve themselves through use.</td>
</tr>
<tr>
  <td><b>Context as a tree, not a transcript</b></td>
  <td>Each call records inputs, outputs, retries, and child calls into a structured execution tree. Future LLM calls in the same tree see the relevant history automatically — no manual prompt-stuffing, no flat conversation log.</td>
</tr>
<tr>
  <td><b>Six providers, no lock-in</b></td>
  <td>Anthropic, OpenAI, Gemini APIs plus Claude Code, Codex, Gemini CLIs. CLI providers reuse the user's existing subscription — no extra API key, no per-token billing.</td>
</tr>
<tr>
  <td><b>Persistent worker, multiple front-ends</b></td>
  <td>One background process hosts the chat, the web UI, and the messaging channels at once. Auto-starts at login (launchd / systemd). Survives a terminal crash. Talk to the same agent from a browser, from <code>tmux</code>, or from your phone.</td>
</tr>
<tr>
  <td><b>Memory across sessions</b></td>
  <td>Three-layer memory under <code>~/.agentic/memory/</code>: short-term notes, a curated wiki, and a small core snapshot injected into every conversation. Built on plain markdown + SQLite FTS5 — no embeddings, no opaque vector store, fully grep-able.</td>
</tr>
<tr>
  <td><b>Real applications, not just demos</b></td>
  <td>Ships with two production agents: <a href="https://github.com/Fzkuji/GUI-Agent-Harness">GUI-Agent-Harness</a> (operates desktop apps via vision + agentic functions) and <a href="https://github.com/Fzkuji/Research-Agent-Harness">Research-Agent-Harness</a> (literature → idea → experiments → paper).</td>
</tr>
</table>

---

## Quick install

```bash
pip install "openprogram[all]"
openprogram setup            # interactive wizard: pick provider, log in, verify
```

Or pick the parts you need:

```bash
pip install openprogram                  # core only, no extras
pip install "openprogram[web]"           # + browser UI
pip install "openprogram[gui]"           # + GUI agent (opencv / torch / ultralytics)
pip install "openprogram[browser]"       # + Playwright browser tool
```

After install, `openprogram providers` shows what the library can see on your machine.

---

## Five ways to use it

| Entry point | Command | When to use it |
|---|---|---|
| **Python library** | `import openprogram` | Writing agentic code that other Python code calls. |
| **TUI** | `openprogram chat` | Quick conversation in the terminal. Streaming, history, slash commands. |
| **Web UI** | `openprogram web` (or via worker) | Browser interface with execution-tree visualization, history graph, programs panel. |
| **Skills** | `openprogram install-skills` | Hand the toolkit to Claude Code / Gemini CLI / OpenClaw — they call OpenProgram functions natively. |
| **Worker** | `openprogram worker install` | Persistent background process. Hosts web UI, channel adapters, scheduled jobs. Auto-starts at login. |

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

What just happened: `summarize` is a real Python function. The docstring is the prompt. `runtime.exec()` is the LLM call. Wrap it in an `if`, a `for`, or another `@agentic_function` — Python decides flow, the LLM only generates the bullets.

---

## Why Agentic Programming?

|  | Tool-calling / MCP | Agentic Programming |
|---|---|---|
| Who schedules? | LLM decides | Python decides |
| Functions contain | Code only | Code + LLM reasoning |
| Context | Flat conversation | Structured tree |
| Prompt | Hidden in agent config | Docstring is the prompt |
| Self-improvement | Not built-in | `create` → `fix` → evolve |

MCP is a *transport* protocol. Agentic Programming is an *execution model*. They are orthogonal — OpenProgram speaks MCP, but the scheduler is Python.

[Full rationale →](docs/philosophy/agentic-programming.md)

---

## Documentation

| Section | What's inside |
|---|---|
| [Getting Started](docs/GETTING_STARTED.md) | Three-minute install + first runnable example |
| [API Reference](docs/API.md) | `@agentic_function`, `Runtime`, `Context`, `create_runtime` |
| [Providers](docs/api/providers.md) | All six providers, detection order, CLI vs API trade-offs |
| [Meta Functions](docs/api/meta_function.md) | `create()`, `create_app()`, `fix()`, `create_skill()` |
| [Built-in Functions](docs/api/buildin.md) | `deep_work`, `agent_loop`, `general_action`, `wait` |
| [Worker & Channels](docs/WORKER.md) | Persistent process, system service install, Discord / Telegram / WeChat |
| [Web UI](docs/WEB_UI.md) | Browser interface, execution tree, history graph |
| [Skills Integration](docs/INTEGRATION_CLAUDE_CODE.md) | Use as a Claude Code / Gemini CLI / OpenClaw skill pack |
| [Memory](docs/MEMORY.md) | Three-layer memory, wiki, sleep-time reflection |

---

## Quick reference: built-in apps

```bash
openprogram run sentiment text="I love this!"        # generated function
openprogram run deep_work task="..." level=phd       # autonomous quality loop
openprogram run create_app description="..."         # scaffold a new app
openprogram providers                                # what's available on this machine
openprogram worker status                            # is the background process up?
openprogram web                                      # open the browser UI
```

Two real applications ship with the repo:

| App | What it does |
|---|---|
| [GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness) | Operates desktop apps via vision. Python controls observe → plan → act → verify; LLM only reasons. |
| [Research-Agent-Harness](https://github.com/Fzkuji/Research-Agent-Harness) | Literature survey → idea → experiments → paper writing → cross-model review. Topic to submission-ready PDF. |

---

## Migrating

Coming from another agent framework? OpenProgram is designed to coexist:

- **Claude Code / Gemini CLI users** — `openprogram install-skills` drops a SKILL.md into your CLI. Talk to your existing agent: *"Create a function that extracts emails from text."* The agent calls into OpenProgram for you.
- **OpenClaw users** — see [INTEGRATION_OPENCLAW.md](docs/INTEGRATION_OPENCLAW.md). OpenProgram registers as an OpenClaw skill pack.
- **MCP servers** — OpenProgram speaks MCP both as client and server. Pipe any MCP server in, expose your `@agentic_function`s as MCP tools.

---

## Contributing

This is a **paradigm proposal** with a reference implementation. We welcome:

- discussions and use-case validation
- alternative implementations in other languages
- bug reports and provider integrations
- new built-in functions and applications

See [CONTRIBUTING.md](CONTRIBUTING.md). Quick start:

```bash
git clone https://github.com/Fzkuji/OpenProgram.git
cd OpenProgram
pip install -e ".[all,dev]"
pytest -q
```

---

## Acknowledgements

The tool framework, provider abstraction, and several tool implementations were
ported or adapted from the projects below — each under its own license.

- **[OpenClaw](https://github.com/openclaw/openclaw)** (MIT) — tool-registry layout, provider abstraction, skill-loading via SKILL.md frontmatter.
- **[hermes-agent](https://github.com/NousResearch/hermes-agent)** (MIT) — starting point for `execute_code`, `mixture_of_agents`, the multi-provider `web_search` shape, and the lifecycle-hook design that the memory subsystem now uses.
- **[pi-coding-agent](https://github.com/mariozechner/pi-coding-agent)** (MIT) — canonical `AgentSkill` shape (`<available_skills>` XML formatter, name / description / location).
- **[Claude Code](https://www.anthropic.com/claude-code)** — ergonomics of the default tool set (bash + read / write / edit + glob / grep + apply_patch + todo) and the `todo` tool's JSON schema.
- **Anthropic / OpenAI / Google SDKs** — raw HTTP API contracts; our providers call the wire protocol directly so SDK dependencies stay optional.

File-level docstrings call out direct inspirations where the lineage is more specific.

---

## Community

- 💬 [Discussions](https://github.com/Fzkuji/OpenProgram/discussions)
- 🐛 [Issues](https://github.com/Fzkuji/OpenProgram/issues)
- 📖 [Docs](https://github.com/Fzkuji/OpenProgram/tree/main/docs)

---

## License

MIT — see [LICENSE](LICENSE).
