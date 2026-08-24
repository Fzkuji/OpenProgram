<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/logo-lockup.gif">
    <source media="(prefers-color-scheme: light)" srcset="images/logo-lockup-light.gif">
    <img src="images/logo-lockup.gif" alt="OpenProgram" width="440">
  </picture>
</p>

<p align="center">
  <b>OpenProgram：自编程 AI Agent 框架</b><br/>
  Agent 自动创建并持续优化自身工作流 · 任意 LLM · macOS 与 Linux release
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
  <a href="start/GETTING_STARTED.md">快速上手</a> &middot;
  <a href="reference/API.md">API 参考</a> &middot;
  <a href="capabilities/agentic-programming/philosophy.md">设计哲学</a> &middot;
  <a href="README.md">English</a>
</p>

---

> *"The more constraints one imposes, the more one frees oneself."*
> —— **Igor Stravinsky**，《Poetics of Music》

**我们提出 _Agentic Programming_。** LLM 灵活,代码确定。让模型掌控一切,得到的是混乱——不可预测的执行、上下文爆炸、没有输出保证;把一切硬编码,又丢掉了智能。**Harness** 在两者之间取得平衡,逐时逐刻地交织——**想固定的流程交给 Python,写不进脚本的判断交给 LLM。**([完整论证 →](capabilities/agentic-programming/philosophy.md))

> 🎉 **论文:** [_LLM-as-Code: Agentic Programming for Agent Harness_](https://arxiv.org/abs/2606.15874) —— 已被 **KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)** 接收。

**目录**

- [新闻](#新闻)
- [为什么是 OpenProgram？](#为什么是-openprogram)
  - [1. DAG 上下文 —— 原生多 agent 系统的地基](#1-dag-上下文--原生多-agent-系统的地基)
  - [2. Agentic 工作流 —— 可信且自我演化的 agent 的地基](#2-agentic-工作流--可信且自我演化的-agent-的地基)
  - [3. 事件基础设施 —— 主动 agent 的地基](#3-事件基础设施--主动-agent-的地基)
- [快速开始](#快速开始)
  - [1. 安装](#1-安装)
  - [2. 运行](#2-运行)
  - [3. 已包含 Programs 与额外 harness](#3-已包含-programs-与额外-harness)
- [贡献](#贡献)
- [致谢](#致谢)
- [引用](#引用)
- [许可证](#许可证)

## 新闻

- **2026-08-24** — **v0.8.1** — 更小的安装包：产品不再捆绑 PyTorch。
- **2026-08-24** — **v0.8.0** — 上下文压缩整条链路（自动压缩、摘要卡片、折叠原文、消息导航同步），以及内置浏览器上可点击展开、样式与工具栏一致的书签栏。
- **2026-08-17** — **v0.7.0** — 完整内置浏览器：多窗格 Browser、书签与紧凑 History、Chrome/Brave/Edge/Chromium 配置导入，以及 Agent 对可见内部网页的 DOM 优先操控。
- **2026-07-21** — **v0.6.0** — 多 Agent 协作：`spawn` N 个子 Agent，跨会话传信，在隔离的 git worktree 里跑会改文件的分支。
- **2026-06-22** — **论文接收** —— KDD 2026 Workshop on Agentic Software Engineering（[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)）。
- **2026-06-07** — **v0.5.0** — 可安装 harness（`openprogram programs install <owner>/<repo>`）、全平台一键安装、多账户 provider 与自动 key 轮换，以及 `rescue` / `doctor` 诊断。
- **2026-05-28** — **v0.4.0** — Web UI 与 TUI 的设计系统基础。
- **2026-04-04** — **v0.3.0** — 内置 Anthropic / OpenAI / Gemini provider。
- **2026-04-03** — **v0.1.0** — 首个版本：`@agentic_function` 装饰器与执行 DAG。

## 为什么是 OpenProgram？

OpenProgram 当前 release 支持 macOS 和 Linux 安装、多 provider，以及 Web 界面（桌面 App 或 `openprogram web` → http://localhost:18100）。Windows 原生打包暂缓到后续 release 决策；目前 Windows 与移动设备可以作为浏览器客户端访问受支持的远程主机。harness 本体提供**三种构建 agent Program 的机制**。

### 1. DAG 上下文 —— 原生多 agent 系统的地基

<p align="center">
  <img src="images/highlights/01-dag-context.png" alt="DAG Context — every user, LLM, and function call is one node on a single flat DAG; each @agentic_function declares in one line what context it reads and exposes, so fork, spawn, cross-session messaging, and worktree isolation all follow" width="900">
</p>

每个用户轮次、LLM 调用、函数调用都是**同一张扁平 DAG 上的一个节点**。两种边赋予它含义:`caller`(谁调了谁)和 `reads`(谁的输出喂进了这次 prompt)——上下文由图组装出来,不靠手工缝合。每个 `@agentic_function` 都是**一行声明的可编程上下文**:`expose` 控制一次调用向父级展示什么,`render_range` 控制一次调用拉进多少历史(`{"callers": 0}` 给出一次性的自隔离草稿上下文,函数返回即回收——prompt 不会无界增长)。

因为上下文是**可寻址的节点而不是每个 agent 一份的缓冲区**,多 agent 不再是外挂:fork 一个分支、`spawn` 一个干净的子 agent、跨会话 `send_message`、把动文件的分支放进隔离的 `git worktree` 里跑——在同一张 DAG 上,每一样都只是"选一组不同的节点当上下文"。

### 2. Agentic 工作流 —— 可信且自我演化的 agent 的地基

<p align="center">
  <img src="images/highlights/02-agentic-workflow.png" alt="Agentic Workflow — Python drives the flow and code gates enforce the critical steps; a failed validation makes the model re-decide so it cannot skip checks; the agent writes and hot-loads its own @agentic_functions" width="900">
</p>

**Python 驱动流程;LLM 只在被要求时推理。** 关键步骤变成**代码关卡**——模型的选择由代码解析和校验,校验不过就让它*重新决策*,而不是悄悄跳过,所以校验不可能被绕开。每次调用都是可重试、可观测的 DAG 节点。这就是执行*可信*的来源:保证写在代码里,不写在模型的善意里。

*自我演化*是一套机制,不是黑箱:agent 用**普通文件编辑工具**编写和修复自己的 `@agentic_function`,文件监听器热加载,新工具下一轮就上线——没有专门的 `create()` / `fix()` 机构。

### 3. 事件基础设施 —— 主动 agent 的地基

<p align="center">
  <img src="images/highlights/03-event-infrastructure.png" alt="Event Infrastructure — a unified process-wide event bus that the agent loop, auth, context, channels, and memory all emit onto; anything can subscribe by event type, and a proactive policy layer builds on top" width="900">
</p>

一条**进程级事件总线**是一切之下的基底:agent 循环、auth、上下文、渠道、记忆都往上面发事件,任何组件都能按事件类型订阅(每个事件都是统一的 `Event(type, payload, ts)` 信封,带 `id` / `origin` / `metadata`)。这里刻意只做**地基**——监视事件流并主动行动的策略层,是这条总线的第一个预期消费者。管线已经就位;主动性留给你在上面搭。

## 快速开始

### 1. 安装

**macOS / Linux CLI 或服务器 release：**
```bash
curl -fsSL https://openprogram.io/install | sh
```

macOS 桌面用户从 [GitHub Releases](https://github.com/Fzkuji/OpenProgram/releases) 下载 unsigned DMG。Linux 用户安装完整 CLI/server runtime，并打开其中的 Web UI；完整桌面包通过公共入口验收前不发布 Linux 桌面产物。所有受支持的 release 安装都具有相同的完整产品能力。校验、平台范围和 source development 安装见 **[install.md](install/install.md)**。

### 2. 运行

macOS 下打开桌面 App，或用命令启动 Web：

```bash
openprogram web
```

都会打开 **http://localhost:18100**。

### 3. 已包含 Programs 与额外 harness

每个受支持的 release 安装都已经包含三项第一方 Programs 及其默认 runtime 资产：

| Program | Release 状态 | 功能 |
|---|---|---|
| [GUI Agent](https://github.com/Fzkuji/GUI-Agent-Harness) | 已包含；产品 runtime 不含 PyTorch 或 EasyOCR | 通过视觉操控桌面应用和 OSWorld 虚拟机。 |
| [Research Agent](https://github.com/Fzkuji/Research-Agent-Harness) | 已包含 | 文献调研 → 实验 → 论文初稿。 |
| [Wiki Agent](https://github.com/Fzkuji/Wiki-Agent-Harness) | 已包含 | 把笔记 / 文档 / 聊天整理成带 `[[wikilinks]]` 的 Obsidian 知识库。 |
| [Scriptorium](https://github.com/Fzkuji/Scriptorium) | 相关项目 | 可读的 Agent 记忆；Markdown 笔记；事实回链到来源消息；为 Claude Code 提供 MCP。 |

第三方 harness 是额外功能。可变扩展环境使用 `openprogram programs install <owner>/<repo>`（或完整 git URL）；源码编辑和 OCR/Browser 后端替换属于开发者功能。

写一个自己的可安装 harness 只差一份布局契约——完整指南(安装、管理、编写、测试、发布)见
**[installing-harnesses.md](capabilities/installing-harnesses.md)**。

> 需要一条自己的工作流？直接在聊天里让 agent 创建或更新 Program。

详情见 [快速上手](start/GETTING_STARTED.md)、[安装](install/install.md) 和 [功能](start/features.md)。

## 贡献

这是一个**范式提案**,附带参考实现。欢迎讨论、其他语言的替代实现、验证或挑战此方法的用例,以及 bug 报告。

详见 [CONTRIBUTING.md](https://github.com/Fzkuji/OpenProgram/blob/main/.github/CONTRIBUTING.md)。

## 致谢

OpenProgram 站在前人的肩膀上。工具框架、provider 抽象和若干工具实现移植或改编自下列项目——各自遵循其原许可证。非常感谢这些作者。

- [**OpenClaw**](https://github.com/openclaw/openclaw)(MIT)—— 工具注册表的布局
  (`name / description / parameters / execute`)、带 `check_fn` + `requires_env`
  门禁的 provider 抽象、`TOOLSETS` 预设、经 SKILL.md frontmatter + 延迟绑定 `read`
  的 skill 加载。完整克隆放在 `references/openclaw/`(已 gitignore)供浏览。
- [**hermes-agent**](https://github.com/himanshuishere/hermes-agent)
  (MIT)—— `execute_code` 的起点(我们裁掉了 Docker / Modal 层)、
  `mixture_of_agents`,以及多 provider 的 `web_search` / `image_generate` /
  `image_analyze` 工具的整体形态。
- [**pi-coding-agent**](https://github.com/mariozechner/pi-coding-agent)
  (MIT)—— 经 OpenClaw 引入的规范 AgentSkill 形态
  (`<available_skills>` XML 格式器,name / description / location)。
- [**Claude Code**](https://www.anthropic.com/claude-code) —— `DEFAULT_TOOLS`
  集合的整体人机工学(bash + read / write / edit + glob / grep / list
  + apply_patch + todo 规划板)以及 todo 工具的 JSON schema。
- **Anthropic / OpenAI / Google SDK** —— provider 的 HTTP 契约;我们的
  provider 直接调原生 HTTP API,让 SDK 依赖保持可选。

血缘更具体的工具文件在文件级 docstring 里各自注明了直接灵感来源。这些 MIT
许可的组件保留其原 MIT 条款;组合作品整体以 AGPL-3.0 分发。

## 引用

在你的工作中使用了 OpenProgram,或基于这份代码构建?请引用我们的论文——并注意在 AGPL 下,任何你**分发或作为联网服务运行**的衍生作品必须同样以 AGPL 开源,并保留署名(见[许可证](#许可证))。

> _LLM-as-Code: Agentic Programming for Agent Harness_ —— 已被 **KDD 2026 Workshop on Agentic Software Engineering (AgenticSE)** 接收。[arXiv:2606.15874](https://arxiv.org/abs/2606.15874)

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

## 许可证

[AGPL-3.0](https://github.com/Fzkuji/OpenProgram/blob/main/LICENSE) © 2026 Fzkuji。可自由使用、研究、修改、分享——但任何你分发**或作为联网服务运行**的衍生作品也必须以 AGPL 发布,并保留署名。
