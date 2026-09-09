# 设计与实现文档

本目录按子系统维护当前设计、实现位置和未完成边界。代码由 `openprogram/` 和 `apps/` 共同承担，不能仅按核心包目录查找实现。

## 阅读入口

| 目的 | 文档 |
|---|---|
| 查找子系统设计与实现状态 | [实现文档导航](implementation-status.html) |
| 理解目录所有权和兼容入口 | [仓库结构设计](repository-structure.html)及[实现说明](repository-structure-implementation.html) |
| 理解完整执行过程 | [框架概览](framework-overview.zh.md) |
| 查找测试层级和验证要求 | [测试系统](testing/test-system.html) |
| 编写和验收 HTML 实现文档 | [HTML 展示与编写规范](docs-site.zh.html) |

设计正文描述行为和约束；文末“实现状态”集中列出已实现、部分实现、未实现与范围外事项。源码存在、测试通过、发布完成和本地 App 验收是不同状态。索引仅负责导航，不重复维护功能完成率。

## context/ — context 引擎、commit、工具老化

| Doc | Topic |
|---|---|
| [`context/overview.md`](context/overview.md) | Context 层：pipeline + DAG 存储 + ContextCommit + compaction/render + attach/merge + 跨轮工具 + 缺口 |
| [`context/composition.md`](context/composition.md) | 目标状态：按调用分层（L0/L1/L2）+ 情境上下文 |
| [`context/comparison.md`](context/comparison.md) | 与参考项目的 context 方案对比 |
| [`context/context-compaction.html`](context/context-compaction.html) | Context 压缩（已渲染） |

## memory/ — 记忆系统（实体 + 抽象）

| Doc | Topic |
|---|---|
| [`memory/README.md`](memory/README.md) | 记忆系统总览：架构、设计原则、实施状态 |
| [`memory/overview.md`](memory/overview.md) | 记忆子系统：实体/抽象两层 + 溯源导航式回忆，以及当前在跑的总结链（[可视化](memory/memory-architecture.html)） |
| [`memory/entity-memory.md`](memory/entity-memory.md) | 实体记忆：Session-Git + Project-Git，按生命周期组织 |
| [`memory/git-as-entity-memory.md`](memory/git-as-entity-memory.md) | 用 Git 做实体记忆：Session-Git + Project-Git |
| [`memory/virtual-memory.md`](memory/virtual-memory.md) | 抽象记忆：Timeline + Graph + Core，按类型 × 生命周期组织 |

## proactive/ — 事件层 + 主动性（事件驱动）

分两块：**事件底座**（一条统一事件流，给整个框架用）+ **主动性应用**（规则订阅事件流出手）。
两块解耦，可只做底座。先读 event-layer 建立整体认识。

事件底座：

| Doc | Topic |
|---|---|
| [`proactive/event-layer.md`](proactive/event-layer.md) | 统一 Event 模型 + 框架定位 + 框架图 + 事件边界与演进（[可视化](proactive/event-layer.html)） |
| [`proactive/framework-evolution.md`](proactive/framework-evolution.md) | 框架演进：现状 → 目标 → 五步迁移（[可视化](proactive/framework-evolution.html)） |

主动性应用（建在底座上）：

| Doc | Topic |
|---|---|
| [`proactive/overview.md`](proactive/overview.md) | 跟着一个场景走一遍（拦 `rm -rf`），规则 / 出手 / 状态等概念就地讲 |
| [`proactive/events-and-state.md`](proactive/events-and-state.md) | 状态怎么从事件累加（fold）出来——规则能"记住过去"的原理 |
| [`proactive/execution-model.md`](proactive/execution-model.md) | 规则（Policy）怎么写；挡路的 / 旁观的两类有何不同 |
| [`proactive/policies-mvp.md`](proactive/policies-mvp.md) | 三条样板规则，照着写新规则 |
| [`proactive/invariants.md`](proactive/invariants.md) | 框架自己要守的底线（主要是别绕成死循环） |

> 论文/生产级内容（离线回放验证、对抗安全、评估骨架）已归档在
> `proactive/_research_archive/`，以后做加固再取回。

## runtime/ — agent 执行、DAG、异步、回退、可控性

| Doc | Topic |
|---|---|
| [`runtime/overview.md`](runtime/overview.md) | Runtime API 行为（另见 [`../api/runtime.md`](../api/runtime.md)） |
| [`runtime/operations/user-input-requests.md`](runtime/operations/user-input-requests.md) | runtime.ask/confirm 等用户输入 |
| [`runtime/execution/execution-control.html`](runtime/execution/execution-control.html) | **权威设计**：统一暂停、继续、单步、调整、取消、检查点、revision、恢复与多端同步 |
| [`runtime/unified-session-context.md`](runtime/unified-session-context.md) | 统一 session context |
| [`runtime/agent-configuration-ui.html`](runtime/agent-configuration-ui.html) | Agent 配置整体框架：身份、模型、指令、Programs、Skills、MCP、Sessions（[基础配置](runtime/agent-core-configuration-ui.html)、[能力配置](runtime/agent-capability-configuration-ui.html)、[Programs 选择器](runtime/agent-tool-configuration-ui.html)） |
| [`runtime/execution/agent-worktree.md`](runtime/execution/agent-worktree.md) | Agent worktree 行为 |
| [`runtime/execution/async-job-lifecycle.md`](runtime/execution/async-job-lifecycle.md) | 异步任务生命周期 |
| [`runtime/operations/streaming-resume.md`](runtime/operations/streaming-resume.md) | 流式 + 恢复 |
| [`runtime/operations/file-management.html`](runtime/operations/file-management.html) | **权威设计**：文件归因、Review、Undo、历史 Revert、多轮恢复、分支/worktree 对齐和多 agent 所有权 |
| [`runtime/dag/overview.md`](runtime/dag/overview.md) | **权威** Session DAG 数据模型（一张图 / 3 种节点 user·llm·code / caller+predecessor 边 / spawn / 渲染 / 装配 / 压缩） |
| [`runtime/dag/rendering.md`](runtime/dag/rendering.md) | **权威渲染规范**：布局/连线/图例/默认可见性，12 场景 |
| [`runtime/dag/branch-collaboration.md`](runtime/dag/branch-collaboration.md) | 分支协作（通信 / 派活 / 合并）设计与实现步骤 |
| [`runtime/execution/dispatcher-split.md`](runtime/execution/dispatcher-split.md) | Dispatcher 拆分设计 |
| [`runtime/execution/next-step-decision.md`](runtime/execution/next-step-decision.md) | 下一步决策（模型如何选择接下来执行什么） |
| [`runtime/execution/agentic-self-recursion.md`](runtime/execution/agentic-self-recursion.md) | Agentic 自递归（[已渲染](runtime/execution/agentic-self-recursion.html)） |
| [`runtime/operations/branch-naming.md`](runtime/operations/branch-naming.md) | 分支命名（[已渲染](runtime/operations/branch-naming.html)） |
| [`runtime/session/README.md`](runtime/session/README.md) | Session 子系统：数据模型、存储、命名、列表、生命周期 |
| [`runtime/self-update.html`](runtime/self-update.html) | 对话内自主更新：owner 审批、候选激活、验收与恢复 |
| [`runtime/goal-framework-implementation-comparison.html`](runtime/goal-framework-implementation-comparison.html) | **Goal 权威设计**：原生实现对比、controller 与状态流程、异步问题、困难任务停止、重启恢复、Web/TUI 界面和实现证据 |
| [`runtime/sandbox-architecture.html`](runtime/sandbox-architecture.html) | 唯一权威执行安全设计：Authority 权限档、Permission 模式与审批、宿主沙箱边界、框架对照和实现证据 |
| [`runtime/permission-model.md`](runtime/permission-model.md) / [`runtime/sandbox.md`](runtime/sandbox.md) | 指向权威执行安全设计的稳定旧链接入口 |
| [`runtime/ssrf-protection.html`](runtime/ssrf-protection.html) | 出站 URL 与 SSRF：当前缺口、Hermes/OpenClaw/OWASP 对照、分 scope 信任策略、transport 要求与完整验收门槛 |
| [`runtime/agent-collaboration.md`](runtime/agent-collaboration.md) | Agent 协作：分支间通信原语（[工具面](runtime/agent-collab-architecture.html)、[八家参考实现对照](runtime/agent-collab-comparison.html)） |
| [`runtime/tool-toggle-management.md`](runtime/tool-toggle-management.md) | 工具开关 / 工具集管理设计 |
| [`runtime/additional-working-directories.md`](runtime/additional-working-directories.md) | 会话多工作目录设计 |

## providers/ — LLM provider、凭证、模型目录、thinking/effort

| Doc | Topic |
|---|---|
| [`providers/request-build.md`](providers/request-build.md) | 请求构建流程 |
| [`providers/models/overview.md`](providers/models/overview.md) | 模型目录最终设计 |
| [`providers/models/thinking-effort.md`](providers/models/thinking-effort.md) | Thinking / effort 子系统（级别定义、数据流、各 provider wire 格式、UI picker） |
| [`providers/models/fast-tier.md`](providers/models/fast-tier.md) | Fast（高速）档：两层判定、存储与线路 |
| [`providers/auth/claude-code-direct-oauth.md`](providers/auth/claude-code-direct-oauth.md) | claude-code 直连订阅（砍 Meridian） |
| [`providers/auth/credential-validation-unification.md`](providers/auth/credential-validation-unification.md) | 统一凭证校验 |
| [`providers/auth/unified-auth-storage.md`](providers/auth/unified-auth-storage.md) | 统一认证存储 |
| [`providers/auth/unified-account-management.md`](providers/auth/unified-account-management.md) | 统一账号管理 + 轮换 |
| [`providers/auth/credential-status-redesign.md`](providers/auth/credential-status-redesign.md) | 凭证状态 |
| [`providers/auth/api-key-resolution-unification.md`](providers/auth/api-key-resolution-unification.md) | API key 解析统一 |
| [`providers/reliability/error-retry.md`](providers/reliability/error-retry.md) | 错误 + 重试处理 |
| [`providers/reliability/error-taxonomy-propagation.md`](providers/reliability/error-taxonomy-propagation.md) | 错误分类 + 传播 |
| [`providers/reliability/llm-fault-tolerance.md`](providers/reliability/llm-fault-tolerance.md) | LLM 容错（调研） |
| [`providers/reliability/error-and-timeout-mechanism.html`](providers/reliability/error-and-timeout-mechanism.html) | 错误 + 超时机制（已渲染） |
| [`providers/network-proxy.md`](providers/network-proxy.md) | 出站网络代理 |
| [`providers/auth/credential-connection-unification.md`](providers/auth/credential-connection-unification.md) | 凭证/连接统一 |
| [`providers/PROBLEM-models-and-bailian.md`](providers/PROBLEM-models-and-bailian.md) | 模型清单与百炼 provider |

## function/ — function 与工具调用

| Doc | Topic |
|---|---|
| [`function/calling-unification.md`](function/calling-unification.md) | 工具/函数调用框架（当前） |

> 面向 authoring 的文档（`@agentic_function` 用法、函数元数据、
> 工具调用循环、下一步决策、纯 python 辅助）已移至
> 用户指南 [`../agentic-programming/README.md`](../../capabilities/agentic-programming/README.md)。

## cli/ — CLI / TUI、斜杠命令、端口

| Doc | Topic |
|---|---|
| [`cli/redesign.md`](cli/redesign.md) | CLI / TUI 重设计（schema 驱动的设置、配置面板）—— 当前 |
| [`cli/ports.md`](cli/ports.md) | Web UI 端口（配置入口、冲突处理） |
| [`cli/slash-commands.md`](cli/slash-commands.md) | 斜杠命令 |
| [`cli/slash-commands-references.md`](cli/slash-commands-references.md) | 斜杠命令参考快照 |
| [`cli/drop-run-command.md`](cli/drop-run-command.md) | 从 Web UI 触发的函数执行路径 |
| [`cli/naming.md`](cli/naming.md) | CLI 命名 |
| [`cli/single-port.md`](cli/single-port.md) | 单端口架构 |
| [`cli/config-write-safety.md`](cli/config-write-safety.md) | 配置写入安全——原子 `update_config` |
| [`cli/tui-upgrade.md`](cli/tui-upgrade.md) | TUI 升级 |

## channels/ — 消息通道

| Doc | Topic |
|---|---|
| [`channels/design.md`](channels/design.md) | 通道设计（当前） |
| [`channels/audit.md`](channels/audit.md) | 通道审计 / 参考快照 |

## ui/ — surface、指示点、附件、GUI agent

| Doc | Topic |
|---|---|
| [`ui/invariants.md`](ui/invariants.md) | 跨模块 UI 不变量清单 |
| [`ui/chat-turn-visual-spec.html`](ui/chat-turn-visual-spec.html) | 聊天轮次视觉规范（执行时间线 + 手动函数运行 + 消息导航） |
| [`ui/interaction-feedback.md`](ui/interaction-feedback.md) | 交互反馈 0ms 规则 |
| [`ui/surface-system.md`](ui/surface-system.md) | Surface 系统 |
| [`ui/theme-system.html`](ui/theme-system.html) | 主题入口、完整 token 契约、组件消费与桌面浮层传播 |
| [`ui/app-icon.html`](ui/app-icon.html) | macOS App 图标分层素材、Apple 系统外形、打包与旧系统回退边界 |
| [`ui/settings-collapsible-columns.html`](ui/settings-collapsible-columns.html) | 应用主侧栏与 Settings 分类栏折叠；Provider 列表始终展开 |
| [`ui/indicator-dots.md`](ui/indicator-dots.md) | 指示点 |
| [`ui/attachment-handling.md`](ui/attachment-handling.md) | 附件处理（[已渲染](ui/attachment-handling.html)） |
| [`ui/composer-interaction-modes.md`](ui/composer-interaction-modes.md) | Composer 交互模式 |
| [`ui/gui-agent.html`](ui/gui-agent.html) | GUI agent 入口、状态机、结果契约与实现状态 |
| [`ui/state-layer.md`](ui/state-layer.zh.md) | Web 状态层：会话级 vs 全局 store，会话作用域容器方案 |
| [`ui/center-tabs-and-split-layout.html`](ui/center-tabs-and-split-layout.html) | 普通 tab 与复合分屏 tab 的生命周期、显示、持久化和跨窗口转移权威设计 |
| [`ui/project-workspace.md`](ui/project-workspace.md) | 项目工作区——文件、标签页、多会话（[原型](ui/project-workspace-prototype.html)） |

## integrations/ — MCP、skills/plugins、harness 标准

| Doc | Topic |
|---|---|
| [`integrations/harness-standard.md`](integrations/harness-standard.md) | Harness 标准（插件 + 自动探测）；安装：[`../installing-harnesses.md`](../../capabilities/installing-harnesses.md) |
| [`integrations/mcp-integration.md`](integrations/mcp-integration.md) | MCP 集成 |
| [`integrations/skills-and-plugins.md`](integrations/skills-and-plugins.md) | Skills 与 plugins |

## extension-gating/

扩展门控设计 + 参考对比 —— 见
[`extension-gating/README.md`](extension-gating/README.md)。

## 横切关注点

| Doc | Topic |
|---|---|
| [`usage-metering.md`](usage-metering.md) | Usage 子系统（token/cost 记账、ledger、收口点、子进程、消费层） |
| [`framework-overview.md`](framework-overview.md) | 框架总览：一次对话从输入到产出 |
| [`framework-comparison.html`](framework-comparison.html) | 整框架对标：按设计维度和十二家横向比，强在哪、弱在哪、别人有什么我们没想到（图解） |
| [`feature-matrix.html`](feature-matrix.html) | 功能清单对标：同样十二家改按功能清单扫，160 项一张大表，只有别人有的、只有我们有的（图解） |
| [`docs-site.html`](docs-site.zh.html) | 文档站本身（构建、导航、双语路由） |
| [`repository-structure.html`](repository-structure.html) | 仓库边界、超长文件拆分规则与文档信息架构 |
| [`repository-structure-implementation.html`](repository-structure-implementation.html) | 仓库结构的代码位置、兼容边界和验证方法 |

## research/ — 调研

| Doc | Topic |
|---|---|
| [`research/execution-trace-model-selection.md`](research/execution-trace-model-selection.md) | Agent 执行轨迹的数据模型选型（span 概念、创新点） |

## distribution/ — 安装、打包与更新

| Doc | Topic |
|---|---|
| [`distribution/installation-packaging.html`](distribution/installation-packaging.html) | 完整产品安装、打包、平台支持与 Release 产物 |
| [`distribution/automatic-updates.html`](distribution/automatic-updates.html) | Stable Release 发现、macOS/Windows Desktop 校验后打开 installer、managed CLI 原子激活、信任边界、界面状态与实现证据 |
| [`distribution/implementation-plan.md`](distribution/implementation-plan.md) | 不与当前设计重复的历史分发实现证据 |

## plans/ — 配套实施计划

| Doc | Topic |
|---|---|
| [`plans/proactive-implementation.md`](plans/proactive-implementation.md) | 主动性层实施计划 |
| [`plans/cache-control-passthrough.md`](plans/cache-control-passthrough.md) | Anthropic `cache_control` 逐块透传 |
| [`plans/2026-07-08-credential-connection-unification.md`](plans/2026-07-08-credential-connection-unification.md) | 凭证/连接统一迁移 |

## TODO-doc-code-gaps.md

[`TODO-doc-code-gaps.md`](TODO-doc-code-gaps.md) — 文档与代码不一致的待修项，按优先级排列。修完一条删一条。

## 约定

- 每个主题维护一份当前设计，相关实现说明链接到它，不复制设计正文。
- 正文使用现在时；历史提交、日期和逐轮评审记录通过 Git 查询。
- 实现说明依次说明入口、代码所有权、数据与状态变化、失败处理、兼容边界、验证方法，文末集中列出实现状态。
- 所有页面统一以 HTML 展示；需要图示、状态与证据结构的实现文档优先维护为原生 HTML。简短用法可以保留 Markdown 源码。
- 默认 `.md` / `.html` 为全英文，`.zh.md` / `.zh.html` 为中文对照；先更新英文，再同步中文。
- API 文档位于 `docs/reference/api/`；产品用法放在对应产品 Tab。
- 站内使用相对文档链接；仓库源码使用 GitHub 链接。
- 修改后先构建站点，再运行 `python -m scripts.docs_site.checklinks`。
