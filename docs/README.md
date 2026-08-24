<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="images/logo-lockup-light.gif">
    <img src="images/logo-lockup.gif" alt="OpenProgram" width="440">
  </picture>
</p>

<p align="center">
  <b>OpenProgram: Self-Programming AI Agent Framework</b><br/>
  Agents create and refine their own workflows · Any LLM · macOS and Linux releases
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
  <a href="start/GETTING_STARTED.md">Getting Started</a> &middot;
  <a href="capabilities/agentic-programming/self-programming-ai-agents.md">Self-Programming Agents</a> &middot;
  <a href="comparisons/ai-agent-frameworks.md">Framework Comparison</a> &middot;
  <a href="reference/API.md">API Reference</a> &middot;
  <a href="capabilities/agentic-programming/philosophy.md">Philosophy</a> &middot;
  <a href="README.zh.md">中文</a>
</p>

---

> *"The more constraints one imposes, the more one frees oneself."*
> — **Igor Stravinsky**, *Poetics of Music*

**We propose _Agentic Programming_.** An LLM is flexible; code is deterministic. Let the model run everything and you get chaos — unpredictable execution, context explosion, no output guarantees; hard-code everything and you lose the intelligence. A **harness** balances the two, interleaved moment to moment — **Python for the flow you want fixed, the LLM for the judgement you can't script.** ([the full rationale →](capabilities/agentic-programming/philosophy.md))

**Contents**

- [News](#news)
- [Why OpenProgram?](#why-openprogram)
  - [1. DAG Context — for native multi-agent systems](#1-dag-context--for-native-multi-agent-systems)
  - [2. Agentic Workflow — for trustworthy & self-evolving agents](#2-agentic-workflow--for-trustworthy--self-evolving-agents)
  - [3. Event Infrastructure — for proactive agents](#3-event-infrastructure--for-proactive-agents)
- [Quick Start](#quick-start)
  - [1. Install](#1-install)
  - [2. Run](#2-run)
  - [3. Included Programs and additional harnesses](#3-included-programs-and-additional-harnesses)
- [Related projects](comparisons/related-projects.md)
- [Acknowledgements](comparisons/related-projects.md#acknowledgements)
- [Contributing](comparisons/related-projects.md#contributing)
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

The current OpenProgram release supports macOS and Linux installations, multiple providers, and a Web interface (desktop App or `openprogram web` → http://localhost:18100). Windows native packaging is deferred for a later release decision; Windows and mobile devices can currently use the browser client against a supported remote host. The harness itself provides **three mechanisms for building agent programs.**

### 1. DAG Context — for native multi-agent systems

<p align="center">
  <img src="images/highlights/01-dag-context.png" alt="DAG Context — every user, LLM, and function call is one node on a single flat DAG; each @agentic_function declares in one line what context it reads and exposes, so fork, spawn, cross-session messaging, and worktree isolation all follow" width="900">
</p>

Every user turn, LLM call, and function call is **one node on a single flat DAG**. Two edges give it meaning: `caller` (who invoked whom) and `reads` (whose output fed this prompt) — so context is assembled from the graph, not hand-stitched. Each `@agentic_function` is **programmable context in one line**: `expose` controls what a call reveals to its parent, and `render_range` controls how much history a call pulls in (`{"callers": 0}` gives a throwaway, self-isolated scratch context that's reclaimed when it returns — no unbounded prompt growth).

Because context is an **addressable node rather than a per-agent buffer**, multi-agent stops being a bolt-on: fork a branch, `spawn` a clean sub-agent, `send_message` across sessions, or run a file-touching branch in an isolated `git worktree` — each is just "select a different node set as context" on the same DAG.

### 2. Agentic Workflow — for trustworthy & self-evolving agents

<p align="center">
  <img src="images/highlights/02-agentic-workflow.png" alt="Agentic Workflow — Python drives the flow and code gates enforce the critical steps; a failed validation makes the model re-decide so it cannot skip checks; the agent writes and hot-loads its own @agentic_functions" width="900">
</p>

**Python drives the flow; the LLM reasons only when asked.** Critical steps become **code gates** — the model's choice is parsed and validated by code, and a failed check makes it *re-decide* instead of quietly moving on, so validation can't be skipped. Every call is a retryable, observable DAG node. That's what makes execution *trustworthy*: the guarantees live in code, not in the model's goodwill.

*Self-evolving* is a mechanism, not a black box: the agent writes and fixes its own `@agentic_function`s with **ordinary file-edit tools**, a file watcher hot-loads them, and the new tool is live on the next turn — no dedicated `create()` / `fix()` machinery.

### 3. Event Infrastructure — for proactive agents

<p align="center">
  <img src="images/highlights/03-event-infrastructure.png" alt="Event Infrastructure — a unified process-wide event bus that the agent loop, auth, context, channels, and memory all emit onto; anything can subscribe by event type, and a proactive policy layer builds on top" width="900">
</p>

One **process-wide event bus** is the substrate under everything: the agent loop, auth, context, channels, and memory all emit onto it, and any component can subscribe by event type (every event is a uniform `Event(type, payload, ts)` envelope with `id` / `origin` / `metadata`). This is deliberately a **foundation** — a proactive policy layer that watches the stream and acts is the bus's first intended consumer. The plumbing is in place; the proactivity is yours to build on it.

## Quick Start

### 1. Install

**macOS / Linux CLI or server release:**
```bash
curl -fsSL https://openprogram.io/install | sh
```

macOS desktop users download the unsigned DMG from [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases). Linux users install the complete CLI/server runtime and open the Web UI; no Linux desktop package is published until a complete package passes the public-entry gate. All supported release installations contain the same complete product capabilities. See **[install.md](install/install.md)** for verification, platform scope, and source-development installation.

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
**[installing-harnesses.md](capabilities/installing-harnesses.md)**.

> Need a workflow of your own? Ask the agent in chat to create or update a Program.

For details, see [Getting Started](start/GETTING_STARTED.md), [Install](install/install.md), and [Features](start/features.md).

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

[AGPL-3.0](https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE) © 2026 Fzkuji. Free to use, study, modify, and share — but any derivative you distribute **or run as a network service** must also be released under the AGPL, with attribution preserved.
