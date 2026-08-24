<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/logo-lockup-light.gif">
    <img src="docs/images/logo-lockup.gif" alt="OpenProgram" width="440">
  </picture>
</p>

<p align="center">
  <b>Self-Programming AI Assistant. Capture, automate, and refine all your workflows.</b>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2606.15874"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Fzkuji/OpenProgram?style=flat-square&color=blue"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux-lightgrey?style=flat-square">
  <a href="https://github.com/Fzkuji/OpenProgram/actions/workflows/ci.yml"><img alt="Build status" src="https://img.shields.io/github/actions/workflow/status/Fzkuji/OpenProgram/ci.yml?branch=main&style=flat-square&label=build"></a>
  <a href="https://github.com/Fzkuji/GUI-Agent-Harness"><img alt="OSWorld" src="https://img.shields.io/badge/OSWorld_Multi--Apps-79.8%25-brightgreen?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
</p>

<p align="center">
  <a href="docs/start/GETTING_STARTED.md">Getting Started</a> &middot;
  <a href="docs/README.md">Docs</a> &middot;
  <a href="docs/reference/API.md">API Reference</a> &middot;
  <a href="docs/capabilities/agentic-programming/philosophy.md">Philosophy</a> &middot;
  <a href="docs/README.zh.md">中文</a>
</p>

---

> *"The more constraints one imposes, the more one frees oneself."*
> — **Igor Stravinsky**, *Poetics of Music*

**We propose _Agentic Programming_.** An LLM is flexible; code is deterministic. Let the model run everything and you get chaos — unpredictable execution, context explosion, no output guarantees; hard-code everything and you lose the intelligence. A **harness** balances the two, interleaved moment to moment — **Python for the flow you want fixed, the LLM for the judgement you can't script.** ([the full rationale →](docs/capabilities/agentic-programming/philosophy.md))

**Contents**

- [News](#news)
- [Why OpenProgram?](#why-openprogram)
  - [1. Agentic Function — the primitive everything else is built on](#1-agentic-function--the-primitive-everything-else-is-built-on)
  - [2. DAG Context — for native multi-agent systems](#2-dag-context--for-native-multi-agent-systems)
  - [3. Agentic Workflow — for trustworthy & self-evolving agents](#3-agentic-workflow--for-trustworthy--self-evolving-agents)
  - [4. Event Infrastructure — for proactive agents](#4-event-infrastructure--for-proactive-agents)
- [Quick Start](#quick-start)
  - [1. Install](#1-install)
  - [2. Run](#2-run)
  - [3. Included Programs and additional harnesses](#3-included-programs-and-additional-harnesses)
- [Citation](#citation)
- [License](#license)

## News

- **2026-08-24** — **v0.8.0** — context compaction you can see and expand back to the original, plus a bookmarks bar on the built-in Browser.
- **2026-08-17** — **v0.7.0** — built-in browser: multiple panes, bookmarks, History, and Agent control of visible pages.
- **2026-07-21** — **v0.6.0** — multi-agent: `spawn` sub-agents, message across sessions, file-touching branches in git worktrees.
- **2026-06-22** — **Paper accepted** at the KDD 2026 Workshop on Agentic Software Engineering ([arXiv:2606.15874](https://arxiv.org/abs/2606.15874)).
- **2026-06-07** — **v0.5.0** — installable harnesses and multi-account providers with automatic key rotation.
- **2026-05-28** — **v0.4.0** — the Web UI design system.
- **2026-04-04** — **v0.3.0** — built-in Anthropic / OpenAI / Gemini providers.
- **2026-04-03** — **v0.1.0** — first release: `@agentic_function` and the execution DAG.

## Why OpenProgram?

The current OpenProgram release supports macOS and Linux installations, multiple providers, and a Web interface (desktop App or `openprogram web` → http://localhost:18100). Windows native packaging is deferred for a later release decision; Windows and mobile devices can currently use the browser client against a supported remote host. The harness itself provides **four mechanisms — one primitive and the three capabilities it enables.**

### 1. Agentic Function — the primitive everything else is built on

<p align="center">
  <img src="docs/images/highlights/00-agentic-function.png" alt="Agentic Function — one decorator turns a Python function into an agent: the docstring becomes the system prompt, type annotations become the tool schema, runtime.exec() calls become retryable DAG nodes, and plain if/for/return stays deterministic" width="900">
</p>

**An agent is a Python function** — the same triage agent, written both ways:

<table>
<tr><th>Typical harness</th><th>OpenProgram</th></tr>
<tr><td>

```python
TRIAGE_PROMPT = """You are a triage
agent. Classify the ticket as bug,
feature, or question. Reply as JSON."""

TOOLS = [{"type": "function", "function": {
  "name": "triage",
  "parameters": {"type": "object",
    "properties": {"ticket": {"type": "string"}},
    "required": ["ticket"]}}}]

resp = client.chat(TRIAGE_PROMPT, tools=TOOLS)
kind = json.loads(resp)["kind"]     # hope it parses
if kind not in ("bug", "feature"):
    ...                             # and re-prompt by hand
```

</td><td>

```python
@agentic_function
def triage(ticket: str, runtime=None) -> str:
    """Classify the ticket as bug / feature /
    question, then draft a reply."""
    kind = runtime.exec(                    # 🤖 LLM decides
        ticket, choices=["bug", "feature", "question"])
    if kind == "bug":                       # 🐍 you decide
        logs = search_logs(ticket)          # 🐍 plain Python
        return runtime.exec(                # 🤖 LLM writes
            f"Reply using:\n{logs}")
    return runtime.exec("Draft a short reply.")
```

🤖 `runtime.exec()` = **the LLM call** — one retryable DAG node
🐍 everything else = **plain Python**, runs every time

</td></tr>
</table>

**docstring** = the prompt · **type annotations** = the tool schema · `choices=[...]` = a code gate that re-asks until the answer is valid. Same behavior as the left column, with no prompt template and no tool JSON.

### 2. DAG Context — for native multi-agent systems

<p align="center">
  <img src="docs/images/highlights/01-dag-context.png" alt="DAG Context — every user, LLM, and function call is one node on a single flat DAG; each @agentic_function declares in one line what context it reads and exposes, so fork, spawn, cross-session messaging, and worktree isolation all follow" width="900">
</p>

Context is an **addressable node, not a per-agent buffer** — so every multi-agent move is just "point at a different node set":

| Want to… | It's one call |
|---|---|
| Run a sub-agent on a clean context | `spawn_branch(...)` |
| Send a message to another branch, get the reply | `message_branch(message, target=...)` |
| Try an alternative without losing the original | fork the node |
| Let a branch touch files safely | it runs in its own `git worktree` |

### 3. Agentic Workflow — for trustworthy & self-evolving agents

<p align="center">
  <img src="docs/images/highlights/02-agentic-workflow.png" alt="Agentic Workflow — Python drives the flow and code gates enforce the critical steps; a failed validation makes the model re-decide so it cannot skip checks; the agent writes and hot-loads its own @agentic_functions" width="900">
</p>

**A code gate can't be talked past.** When the model's answer fails validation, it is sent back to re-decide — this is the real transcript:

```
llm  → "probably a feature request"
gate ✗ no parseable pick from ["bug", "feature", "question"]
llm  → {"call": "feature"}
gate ✓ → branch taken in Python
```

**And it grows itself:** the agent edits its own `@agentic_function` files with ordinary file tools → a watcher hot-loads them → the new tool is live on the next turn. No `create()` / `fix()` machinery.

### 4. Event Infrastructure — for proactive agents

<p align="center">
  <img src="docs/images/highlights/03-event-infrastructure.png" alt="Event Infrastructure — a unified process-wide event bus that the agent loop, auth, context, channels, and memory all emit onto; anything can subscribe by event type, and a proactive policy layer builds on top" width="900">
</p>

**One bus, every subsystem.** The agent loop, auth, context, channels, and memory all emit the same `Event(type, payload, ts)` envelope, so anything can watch anything:

```python
from openprogram.events import get_event_bus

get_event_bus().subscribe(                       # returns an unsubscribe fn
    lambda e: alert(e.payload),
    types={"context.compaction_recommended", "file.changed"},
)
```

A **foundation, honestly labelled**: the plumbing is in place and the proactive policy layer is its first intended consumer — that part is yours to build.

## Quick Start

### 1. Install

**macOS / Linux CLI or server release:**
```bash
curl -fsSL https://openprogram.io/install | sh
```

macOS desktop users download the unsigned DMG from [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases). Linux users install the complete CLI/server runtime and open the Web UI; no Linux desktop package is published until a complete package passes the public-entry gate. All supported release installations contain the same complete product capabilities. Platform scope, verification, and source-development installation are documented in **[docs/install/install.md](docs/install/install.md)**.

### 2. Run

On macOS, open the desktop App. Or start the Web UI from the command line:

```bash
openprogram web
```

Either way opens **http://localhost:18100**.

### 3. Included Programs and additional harnesses

Every supported release installation already includes the three first-party Programs and their default runtime assets:

| Program | Release status | What it does |
|---|---|---|
| [GUI Agent](https://github.com/Fzkuji/GUI-Agent-Harness) | Included; the product runtime does not ship PyTorch or EasyOCR | Drives desktop apps & OSWorld VMs by vision. |
| [Research Agent](https://github.com/Fzkuji/Research-Agent-Harness) | Included | Literature survey → experiments → paper draft. |
| [Wiki Agent](https://github.com/Fzkuji/Wiki-Agent-Harness) | Included | Turns notes / docs / chats into an Obsidian vault with `[[wikilinks]]`. |
| [Scriptorium](https://github.com/Fzkuji/Scriptorium) | Related | Agent memory you can read; Markdown notes; facts cited to source messages; MCP for Claude Code. |

Third-party harnesses are additional functionality. Mutable extension environments use `openprogram programs install <owner>/<repo>` (or a full git URL); source editing and replacement OCR/browser backends are developer features.

Writing your own installable harness is one layout contract away — the
full guide (install, manage, author, test, publish) is
**[docs/capabilities/installing-harnesses.md](docs/capabilities/installing-harnesses.md)**.

> Need a workflow of your own? Ask the agent in chat to create or update a Program.

For details, see [Getting Started](docs/start/GETTING_STARTED.md), [Install](docs/install/install.md), and [Features](docs/start/features.md).

## Citation

Using OpenProgram in your work, or building on the code? Please cite our paper — and under the AGPL, any derivative you **distribute or run as a network service** must itself be open-sourced under the AGPL, with attribution preserved (see [License](#license)).

> _LLM-as-Code: Agentic Programming for Agent Harness_ — accepted at the **KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)**. [arXiv:2606.15874](https://arxiv.org/abs/2606.15874)

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  eprint    = {2606.15874},
  archivePrefix = {arXiv},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## License

[AGPL-3.0](LICENSE) © 2026 Fzkuji. Free to use, study, modify, and share — but any derivative you distribute **or run as a network service** must also be released under the AGPL, with attribution preserved.
