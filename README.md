<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/logo-lockup-light.gif">
    <img src="docs/images/logo-lockup.gif" alt="OpenProgram" width="440">
  </picture>
</p>

<!-- Compatibility anchors for existing incoming links. -->
<a id="1-agentic-function-the-primitive-everything-else-is-built-on"></a>
<a id="2-dag-context-for-native-multi-agent-systems"></a>
<a id="3-agentic-workflow-for-trustworthy-self-evolving-agents"></a>
<a id="4-event-infrastructure-for-proactive-agents"></a>
<a id="integrated-projects"></a>
<a id="why-openprogram"></a>
<a id="1-agentic-function--the-primitive-everything-else-is-built-on"></a>
<a id="2-dag-context--for-native-multi-agent-systems"></a>
<a id="3-agentic-workflow--for-trustworthy--self-evolving-agents"></a>
<a id="4-event-infrastructure--for-proactive-agents"></a>
<a id="also-in-the-product"></a>

<p align="center"><b>An open-source agent harness where agents are Python programs that improve themselves.</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="Platforms" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">Website</a> · <a href="#install">Install</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/">Documentation</a>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="translations/README.zh.md">简体中文</a> · <a href="translations/README.zh-Hant.md">繁體中文</a> · <a href="translations/README.ja.md">日本語</a> · <a href="translations/README.ko.md">한국어</a> · <a href="translations/README.es.md">Español</a> · <a href="translations/README.fr.md">Français</a> · <a href="translations/README.de.md">Deutsch</a> · <a href="translations/README.pt-BR.md">Português</a> · <a href="translations/README.ru.md">Русский</a>
</p>

- **Agents are Python classes.** Control flow, checks and retries are ordinary code; `self(...)` is a model call.
- **Self-programming.** Agents write new methods into their own class, and they become tools on the next turn.
- **Graph context.** Every call is a node in one DAG; sub-agents run on their own branch and return only their result.
- **One workspace.** Chat, files, terminal and a built-in browser the agent can drive, side by side.
- **Any model.** Anthropic, OpenAI, Gemini and local servers, with several accounts per provider.

<a id="install"></a>

## Install

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

On Windows (from v1.2.0): `irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`. The macOS desktop App is a DMG on the [releases page](https://github.com/FuzzyKuji/OpenProgram/releases); the Linux App is a deb or AppImage on the same page, and the Windows App arrives in v1.1.1. If the website does not answer: `curl -fsSL https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/docs/_static_root/install.sh | sh`. More options in [installation](https://fuzzykuji.github.io/openprogram-site/docs/install/install.html).

## Quick start

```bash
openprogram        # setup wizard, then terminal chat
openprogram web    # workspace at http://localhost:18100
```

Next: [getting started](docs/start/GETTING_STARTED.html) · [models](docs/models/README.html) · [all docs](docs/README.html)

## A quick look

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # your own helper
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

The model decides; Python makes sure the required steps run. See [writing agents](docs/capabilities/agentic-programming/writing-functions/agent.html).

## Features

Each entry links to its design document.

### Framework

| | |
|---|---|
| **[Agent classes](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.html)** | An agent is a Python class; branches, retries and checks are code, `self(...)` is a model call. |
| **[Execution DAG](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.html)** | Every user turn, model call and function call is a node you can inspect, branch and replay. |
| **[Context](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.html)** | Each call reads a selection of nodes instead of a growing transcript, with automatic compaction. |
| **[Self-programming](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.html)** | Agents write new methods into their own class; they become tools on the next turn. |
| **[Checked decisions](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.html)** | The model picks from a fixed menu and code validates the pick before anything runs. |
| **[Sub-agents](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.html)** | Sub-agents run on their own branch, optionally in a Git worktree, and return only their result. |
| **[Events](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.html)** | A typed event bus; plugins observe events or gate tool calls. |
| **[Always-on agents](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.html)** | Agents that keep running in the background and react to events. |
| **[Goals and recovery](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.html)** | Long-running goals survive restarts and resume where they stopped. |
| **[Fault tolerance](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.html)** | Retries, timeouts and error classification for every model call. |

### Workspace

| | |
|---|---|
| **[Desktop and web UI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.html)** | The App on macOS and Linux, and from v1.2.0 on Windows; the web UI on any of them or a server. |
| **[Split panes](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.html)** | Chats, files, terminals and web pages side by side. |
| **[Built-in browser](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.html)** | A browser the agent reads and operates while you watch. |
| **[Integrated terminal](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.html)** | Terminals in the workspace, shared with the agent. |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.html)** | Operate desktop apps, browser pages or a remote VM. |
| **[CLI and TUI](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.html)** | The same agent from the terminal. |
| **[Remote access](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.html)** | Reach your own instance from another device, owner-only. |

### Agents and safety

| | |
|---|---|
| **[Configurable Agents](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.html)** | Save model, instructions, tools and memory access as a reusable Agent. |
| **[Memory](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.html)** | Source-backed memory with retrieval and background writing. |
| **[Permissions and sandbox](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.html)** | Approve or deny tool calls with a note; commands run under a sandbox. |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.html)** | Tools and applications as installable Programs. |

### Models and integrations

| | |
|---|---|
| **[Providers](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.html)** | Anthropic, OpenAI, Gemini and more, several accounts per provider. |
| **[Local models](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.html)** | Local inference servers, with optional authentication. |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.html)** | Use MCP servers, or expose OpenProgram as one. |
| **[Skills and plugins](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.html)** | Extend what the agent knows and can call. |
| **[Channels](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.html)** | Talk to your agents from Telegram, Discord, Slack and WeChat. |
| **[Automatic updates](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.html)** | Signed releases that update in place. |

## Citation

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## License

[AGPL-3.0](LICENSE) © 2026 FuzzyKuji. See the license for use, modification, distribution and network-service obligations.

## Acknowledgements

Thanks to contributors who provide code, reports, reproductions, tests, documentation and reviews.

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

[Issues](https://github.com/Fzkuji/OpenProgram/issues) · [Pull requests](https://github.com/Fzkuji/OpenProgram/pulls)
