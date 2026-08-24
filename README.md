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
</p>

<p align="center">
  <a href="docs/start/GETTING_STARTED.md">Getting Started</a> &middot;
  <a href="docs/install/install.md">Install</a> &middot;
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

- [Install](#install)
- [Quick start](#quick-start)
- [News](#news)
- [Why OpenProgram?](#why-openprogram)
  - [1. Agentic Function — the primitive everything else is built on](#1-agentic-function--the-primitive-everything-else-is-built-on)
  - [2. DAG Context — for native multi-agent systems](#2-dag-context--for-native-multi-agent-systems)
  - [3. Agentic Workflow — for trustworthy & self-evolving agents](#3-agentic-workflow--for-trustworthy--self-evolving-agents)
  - [4. Event Infrastructure — for proactive agents](#4-event-infrastructure--for-proactive-agents)
- [Citation](#citation)
- [License](#license)

## Install

```bash
curl -fsSL https://openprogram.io/install | sh
```

macOS desktop: download the unsigned DMG from [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases). Linux uses the same CLI/server runtime and the Web UI; no Linux desktop package is published. Windows native packaging is not in this release.

Platform matrix, PATH, `openprogram doctor`, and source-checkout install: **[Installation](docs/install/install.md)**.

## Quick start

The first `openprogram` run opens a provider setup wizard, then the terminal chat. Re-run the wizard with `openprogram setup`.

```bash
openprogram
```

Open the Web UI at http://localhost:18100:

```bash
openprogram web
```

Confirm with one printed reply:

```bash
openprogram --print "Introduce yourself in one sentence"
```

GUI Agent, Research Agent, and Wiki Agent ship with every supported release. Third-party Programs use `openprogram programs install <owner>/<repo>`. Details: [Getting Started](docs/start/GETTING_STARTED.md).

## News

- **2026-08-17** — Built-in browser: multiple panes, bookmarks, History, and Agent control of visible pages.
- **2026-07-21** — Multi-agent: `spawn` sub-agents, message across sessions, file-touching branches in git worktrees.
- **2026-06-22** — 📄 **Paper accepted** at the KDD 2026 Workshop on Agentic Software Engineering ([arXiv:2606.15874](https://arxiv.org/abs/2606.15874)).
- **2026-06-07** — Installable harnesses and multi-account providers with automatic key rotation.
- **2026-05-28** — The Web UI design system.
- **2026-04-04** — Built-in Anthropic / OpenAI / Gemini providers.
- **2026-04-03** — 🌱 First release: `@agentic_function` and the execution DAG.

## Why OpenProgram?

The current OpenProgram release supports macOS and Linux installations, multiple providers, and a Web interface (desktop App or `openprogram web` → http://localhost:18100). Windows native packaging is deferred for a later release decision; Windows and mobile devices can currently use the browser client against a supported remote host. The harness itself provides **four mechanisms — one primitive and the three capabilities it enables.**

### 1. Agentic Function — the primitive everything else is built on

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/highlights/00-agentic-function.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/highlights/00-agentic-function-light.png">
    <img src="docs/images/highlights/00-agentic-function.png" alt="Agentic Function — expose is shown to later functions, render_range is history context from callers, docstring is the system prompt, llm() is the model message" width="900">
  </picture>
</p>

**An agent is a Python function.** You write it like any other function. The docstring is the system prompt: it tells the model what this agent does. Each argument is input for this run. A `str` argument is the task. In this example the task is the ticket to classify. That is why there is no extra prompt template and no JSON tools list.

Here is an example, compared with the usual way:

<table>
<tr><th>OpenProgram</th><th>The usual way</th></tr>
<tr><td>

```python
@agentic_function
def triage(ticket: str, runtime=None) -> str:
    """Classify the ticket as bug / feature /
    question, then draft a reply."""
    kind = llm(                             # 🤖 LLM decides
        ticket, choices=["bug", "feature", "question"])
    if kind == "bug":                       # 🐍 you decide
        logs = search_logs(ticket)          # 🐍 plain Python
        return llm(                         # 🤖 LLM writes
            f"Reply using:\n{logs}")
    return llm("Draft a short reply.")
```

🤖 `llm()` is the model call<br>
🐍 everything else is ordinary Python, and it runs every time

</td><td>

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

</td></tr>
</table>

`choices=[...]` asks again until the answer is one of those words.

### 2. DAG Context — for native multi-agent systems

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/highlights/01-dag-context.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/highlights/01-dag-context-light.png">
    <img src="docs/images/highlights/01-dag-context.png" alt="DAG Context — every user, LLM, and function call is one node on a single flat DAG; each @agentic_function declares in one line what context it reads and exposes, so fork, spawn, cross-session messaging, and worktree isolation all follow" width="900">
  </picture>
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
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/highlights/02-agentic-workflow.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/highlights/02-agentic-workflow-light.png">
    <img src="docs/images/highlights/02-agentic-workflow.png" alt="Agentic Workflow — Python drives the flow and code gates enforce the critical steps; a failed validation makes the model re-decide so it cannot skip checks; the agent writes and hot-loads its own @agentic_functions" width="900">
  </picture>
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
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/highlights/03-event-infrastructure.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/highlights/03-event-infrastructure-light.png">
    <img src="docs/images/highlights/03-event-infrastructure.png" alt="Event Infrastructure — a unified process-wide event bus that the agent loop, auth, context, channels, and memory all emit onto; anything can subscribe by event type, and a proactive policy layer builds on top" width="900">
  </picture>
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
