<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="../docs/images/logo-lockup-light.gif">
    <img src="../docs/images/logo-lockup.gif" alt="OpenProgram" width="440">
  </picture>
</p>

<p align="center"><b>开源 Agent Harness：Agent 就是能自我改进的 Python 程序。</b></p>

<p align="center">
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/.github/CHANGELOG.md"><img alt="版本" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FFzkuji%2FOpenProgram%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=blue&style=flat-square"></a>
  <a href="https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE"><img alt="许可证" src="https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square"></a>
  <img alt="平台" src="https://img.shields.io/badge/platforms-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey?style=flat-square">
</p>

<p align="center">
  <a href="https://fuzzykuji.github.io/openprogram-site/">官网</a> · <a href="#install">安装</a> · <a href="https://fuzzykuji.github.io/openprogram-site/docs/README.zh.html">文档</a>
</p>

<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.ru.md">Русский</a>
</p>

- **Agent 就是 Python 类。** 控制流程、检查和重试都是普通代码，`self(...)` 就是一次模型调用。
- **自我编程。** Agent 往自己的类里写新方法，下一轮起就能当工具用。
- **图上下文。** 每次调用都是同一张 DAG 里的节点；子 Agent 在自己的分支上运行，只交回结果。
- **一个工作区。** 聊天、文件、终端和 Agent 能操作的内置浏览器并排摆开。
- **模型随意换。** Anthropic、OpenAI、Gemini 和本地推理服务，每个服务商可配多个账号。

<a id="install"></a>

## 安装

```bash
curl -fsSL https://fuzzykuji.github.io/openprogram-site/install | sh
```

Windows（v1.2.0 起）：`irm https://fuzzykuji.github.io/openprogram-site/install.ps1 | iex`。macOS 桌面 App 是 DMG，在[发行页](https://github.com/FuzzyKuji/OpenProgram/releases)下载；Linux App 是同一页上的 deb 和 AppImage，Windows App 从 v1.2.0 起提供。网站打不开时：`curl -fsSL https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/main/docs/_static_root/install.sh | sh`。更多方式见[安装说明](https://fuzzykuji.github.io/openprogram-site/docs/install/install.zh.html)。

## 快速开始

```bash
openprogram        # 配置向导，然后进入终端聊天
openprogram web    # 工作区，地址 http://localhost:18100
```

接下来：[快速上手](../docs/start/GETTING_STARTED.zh.html) · [模型配置](../docs/models/README.zh.html) · [全部文档](../docs/README.zh.html)

## 一眼看懂

```python
from openprogram import Agent

class SupportAgent(Agent):
    def triage(self, ticket: str) -> str:
        """Classify the ticket, then draft a reply."""
        kind = self(ticket, choices=["bug", "question"])
        if kind == "bug":
            logs = search_logs(ticket)  # 你自己的辅助函数
            return self(f"Reply using:\n{logs}")
        return self("Draft a short reply.")
```

模型负责判断，Python 保证必须的步骤一定执行。参见[编写 Agent](../docs/capabilities/agentic-programming/writing-functions/agent.zh.html)。

## 功能

每一项都链接到对应的设计文档。

### 框架

| | |
|---|---|
| **[Agent 类](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/function/agentic-program.zh.html)** | Agent 是一个 Python 类；分支、重试和检查都是代码，`self(...)` 是一次模型调用。 |
| **[执行 DAG](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/dag/overview.zh.html)** | 每条用户消息、模型调用和函数调用都是节点，可以查看、分支和重放。 |
| **[上下文](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/context/overview.zh.html)** | 每次调用读取选出的节点，而不是不断变长的聊天记录，并自动压缩。 |
| **[自我编程](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/workflows/workflow.zh.html)** | Agent 往自己的类里写新方法，下一轮起就能当工具用。 |
| **[受检查的决策](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent/next-step-decision.zh.html)** | 模型从固定选项里选，代码先验证再执行。 |
| **[子 Agent](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/collaboration/agent-collaboration.zh.html)** | 子 Agent 在自己的分支上运行，可用独立 Git worktree，只交回结果。 |
| **[事件](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/proactive/event-layer.zh.html)** | 类型化事件总线；插件可以观察事件或拦截工具调用。 |
| **[常驻 Agent](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/always-on-agents.zh.html)** | 在后台持续运行、对事件做出反应的 Agent。 |
| **[目标与恢复](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/reliability/goal-and-recovery.zh.html)** | 长期目标在重启后继续，从中断处恢复。 |
| **[容错](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/reliability/llm-fault-tolerance.zh.html)** | 每次模型调用都有重试、超时和错误分类。 |

### 工作区

| | |
|---|---|
| **[桌面与网页界面](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/README.zh.html)** | macOS 和 Linux 上用 App，v1.2.0 起 Windows 也有 App；网页界面可在这些系统或服务器上使用。 |
| **[分屏](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/center-tabs-and-split-layout.zh.html)** | 聊天、文件、终端和网页并排摆开。 |
| **[内置浏览器](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/browser/built-in-browser.zh.html)** | Agent 能读取和操作网页，你在旁边看着。 |
| **[集成终端](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/workspace/integrated-terminal.zh.html)** | 工作区里的终端，和 Agent 共用。 |
| **[Computer use](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/computer-use/computer-and-browser-use.zh.html)** | 操作桌面应用、浏览器页面或远程虚拟机。 |
| **[CLI 与终端界面](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/cli/README.zh.html)** | 在终端里使用同一个 Agent。 |
| **[远程访问](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/ui/remote-web-access.zh.html)** | 从其他设备访问你自己的实例，仅限本人。 |

### Agent 与安全

| | |
|---|---|
| **[Agent 配置](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/agent-configuration/agent-configuration-ui.zh.html)** | 把模型、指令、工具和记忆权限保存成可复用的 Agent。 |
| **[记忆](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/memory/overview.zh.html)** | 带来源的记忆，支持检索和后台写入。 |
| **[权限与沙箱](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/security/sandbox-architecture.zh.html)** | 批准或拒绝工具调用并附上说明；命令在沙箱里运行。 |
| **[Programs](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/runtime/programs/programs-architecture.zh.html)** | 工具和应用都是可安装的 Program。 |

### 模型与集成

| | |
|---|---|
| **[模型服务](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/README.zh.html)** | Anthropic、OpenAI、Gemini 等，每个服务商可配多个账号。 |
| **[本地模型](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/providers/models/local-models.zh.html)** | 本地推理服务，可选身份验证。 |
| **[MCP](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/mcp-integration.zh.html)** | 使用 MCP 服务，或把 OpenProgram 作为 MCP 服务提供出去。 |
| **[Skills 与插件](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/integrations/extensions/skills-and-plugins.zh.html)** | 扩展 Agent 的知识和可调用的工具。 |
| **[聊天渠道](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/channels/README.zh.html)** | 在 Telegram、Discord、Slack 和微信里和 Agent 对话。 |
| **[自动更新](https://fuzzykuji.github.io/openprogram-site/docs/reference/design/distribution/automatic-updates.zh.html)** | 签名发布，原地更新。 |

## 引用

```bibtex
@inproceedings{qi2026llmascode,
  title     = {LLM-as-Code: Agentic Programming for Agent Harness},
  author    = {Qi, Junjia and Fu, Zichuan and Gao, Jingtong and Zhang, Wenlin and Yan, Hanyu and Wu, Xian and Zhao, Xiangyu},
  booktitle = {KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)},
  year      = {2026},
  url       = {https://arxiv.org/abs/2606.15874},
}
```

## 许可证

[AGPL-3.0](../LICENSE) © 2026 FuzzyKuji。使用、修改、分发和网络服务的义务以许可证全文为准。

## 致谢

感谢提供代码、问题报告、复现、测试、文档和审查的贡献者。

<!-- contributors-avatars -->
<p>
<a href="https://github.com/FuzzyKuji"><img src="https://avatars.githubusercontent.com/u/9404270?v=4&amp;s=48" width="24" height="24" alt="维护者" /></a>
<a href="https://github.com/Qi202"><img src="https://github.com/Qi202.png?size=48" width="24" height="24" alt="Qi202" /></a>
<a href="https://github.com/GithungDang"><img src="https://github.com/GithungDang.png?size=48" width="24" height="24" alt="GithungDang" /></a>
<a href="https://github.com/basil-k-aji-dev"><img src="https://github.com/basil-k-aji-dev.png?size=48" width="24" height="24" alt="basil-k-aji-dev" /></a>
<a href="https://github.com/binyangzhu000-sudo"><img src="https://github.com/binyangzhu000-sudo.png?size=48" width="24" height="24" alt="binyangzhu000-sudo" /></a>
</p>
<!-- /contributors-avatars -->

[问题反馈](https://github.com/Fzkuji/OpenProgram/issues) · [拉取请求](https://github.com/Fzkuji/OpenProgram/pulls)
