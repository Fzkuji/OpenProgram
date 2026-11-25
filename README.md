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
<a id="quick-start"></a>
<a id="features"></a>
<a id="framework"></a>
<a id="workspace"></a>
<a id="agents-and-safety"></a>
<a id="models-and-integrations"></a>
<a id="acknowledgements"></a>

<p align="center"><b>Agents program anything.</b><br>Workflows, apps with AI inside, and the agents themselves. An open-source agent harness where agents are Python programs.</p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/releases/latest"><img alt="Version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Fzkuji/OpenProgram?style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://arxiv.org/abs/2606.15874"><img alt="Paper" src="https://img.shields.io/badge/arXiv-2606.15874-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/"><b>Website</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/"><b>Docs</b></a> · <a href="https://fzkuji.github.io/OpenProgram/docs/install/download.html"><b>Download the App</b></a> · <a href="https://arxiv.org/abs/2606.15874"><b>Paper</b></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="translations/README.zh.md">简体中文</a> · <a href="translations/README.zh-Hant.md">繁體中文</a> · <a href="translations/README.ja.md">日本語</a> · <a href="translations/README.ko.md">한국어</a> · <a href="translations/README.es.md">Español</a> · <a href="translations/README.fr.md">Français</a> · <a href="translations/README.de.md">Deutsch</a> · <a href="translations/README.pt-BR.md">Português</a> · <a href="translations/README.ru.md">Русский</a>
</p>

<p align="center">
  <a href="https://fzkuji.github.io/OpenProgram/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/images/openprogram-tour-dark.webp">
      <source media="(prefers-color-scheme: light)" srcset="docs/images/openprogram-tour-light.webp">
      <img src="docs/images/openprogram-tour-light.webp" alt="OpenProgram tour: one task from start to finish, always-on agents, every device, and agents that program anything" width="100%">
    </picture>
  </a>
</p>

<a id="install"></a>

## Install

**macOS · Linux**

```bash
curl -fsSL https://fzkuji.github.io/OpenProgram/install | sh
```

**Windows (PowerShell)**

```powershell
irm https://fzkuji.github.io/OpenProgram/install.ps1 | iex
```

**Desktop App** — [macOS Apple Silicon](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-arm64.dmg) · [macOS Intel](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-mac-x64.dmg) · [Windows](https://fzkuji.github.io/OpenProgram/docs/latest.html?OpenProgram-win-x64.exe) · [Linux (deb, AppImage)](https://fzkuji.github.io/OpenProgram/docs/install/download.html). The App updates itself.

Then run:

```bash
openprogram        # connect a model, then chat in the terminal
openprogram web    # open the workspace at http://localhost:18100
```

If the website does not answer: `curl -fsSL https://raw.githubusercontent.com/Fzkuji/OpenProgram/main/docs/_static_root/install.sh | sh`. More options: [installation guide](https://fzkuji.github.io/OpenProgram/docs/install/install.html) · [getting started](https://fzkuji.github.io/OpenProgram/docs/start/GETTING_STARTED.html).

## Why OpenProgram

<table>
<tr><td><b>Agents are Python programs</b></td><td>Branches, retries and checks are ordinary code; <code>self(...)</code> is a model call. The model decides, and Python makes sure the required steps run.</td></tr>
<tr><td><b>They program themselves</b></td><td>An agent writes new methods into its own class. The next turn, each one is a tool it can call.</td></tr>
<tr><td><b>A graph, not a transcript</b></td><td>Every turn, model call and function call is a node you can inspect, branch and replay. Sub-agents work on their own branch and hand back only the result.</td></tr>
<tr><td><b>Checked decisions</b></td><td>The model picks from a fixed menu, and code validates the pick before anything runs.</td></tr>
<tr><td><b>One workspace</b></td><td>Chats, files, terminals and a built-in browser side by side, in the desktop App, the web UI, a terminal UI or the CLI.</td></tr>
<tr><td><b>Operates your computer</b></td><td>Drives desktop apps, browser pages and remote VMs in the background, without taking over your screen.</td></tr>
<tr><td><b>Always on</b></td><td>Scheduled tasks, goals that survive restarts, and chat from Telegram, Discord, Slack and WeChat.</td></tr>
<tr><td><b>Any model</b></td><td>300+ providers and local models; several keys per provider, switched automatically at a limit.</td></tr>
</table>

Every feature, with its design: [feature index](https://fzkuji.github.io/OpenProgram/docs/reference/design/README.html).

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

See [writing agents](https://fzkuji.github.io/OpenProgram/docs/capabilities/agentic-programming/writing-functions/agent.html).

## News

- **2026-11-25 · [v1.5.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.5.0)** — Reach this computer from a phone or from anywhere with Tailscale built in, chats on Codex, applications that export as software of their own, and a screenshot tool.
- **2026-11-16 · [v1.4.0](https://github.com/Fzkuji/OpenProgram/releases/tag/v1.4.0)** — Several keys and sign-ins per provider with automatic switching at a limit, task blocks in replies, and a run history for scheduled tasks.
- **2026-11-15 · v1.3.0** — A Providers page with health, quota and spend; the agents working in a conversation listed in Activity.

<details>
<summary>Earlier</summary>

- **2026-11-14 · v1.2.0** — Branches of one conversation run side by side, Stop takes effect at once, and the Windows App arrives.
- **2026-11-10 · v1.1.0** — Rewind from any sent message; the App comes to Linux.
- **2026-11-06 · v1.0.0** — First release of the 1.x line: desktop App with split views, background computer use, meetings and an always-on scheduler.
- **2026-08-17 · v0.7.0** — A built-in browser beside files, chat and the terminal, which agents can read and operate.
- **2026-03-31 · v0.1.0** — First public release: agentic functions, Python functions whose steps a model carries out, recorded in a context tree.

</details>

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

## Contributing

Reports, reproductions, tests, docs and code are all welcome: [issues](https://github.com/Fzkuji/OpenProgram/issues) · [pull requests](https://github.com/Fzkuji/OpenProgram/pulls) · [contributing guide](.github/CONTRIBUTING.md).

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="Maintainer" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

## License

[AGPL-3.0](LICENSE) © 2026 FuzzyKuji.
