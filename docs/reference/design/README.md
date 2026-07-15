# 设计文档

OpenProgram 当前的设计笔记，按子系统分组，与 `openprogram/` 下的代码布局保持一致。先读本索引，再读你需要的那篇文档。

每个子目录汇集某一领域的设计。在同一分组内，定义*当前*实现的文档排在最前；其余是支撑性的笔记 / 调研，不应覆盖前者。

## context/ — context 引擎、commit、工具老化

| Doc | Topic |
|---|---|
| [`context/context.md`](context/context.md) | Context 层：pipeline + DAG 存储 + ContextCommit + compaction/render + attach/merge + 跨轮工具 + 缺口 |
| [`context/context-composition.md`](context/context-composition.md) | 目标状态：按调用分层（L0/L1/L2）+ 情境上下文 |

## memory/ — 记忆系统（实体 + 抽象）

| Doc | Topic |
|---|---|
| [`memory/README.md`](memory/README.md) | 记忆系统总览：架构、设计原则、实施状态 |
| [`memory/entity-memory.md`](memory/entity-memory.md) | 实体记忆：Session-Git + Project-Git，按生命周期组织 |
| [`memory/virtual-memory.md`](memory/virtual-memory.md) | 抽象记忆：Timeline + Graph + Core，按类型 × 生命周期组织 |

## proactive/ — 事件层 + 主动性（事件驱动）

分两块：**事件底座**（一条统一事件流，给整个框架用）+ **主动性应用**（规则订阅事件流出手）。
两块解耦，可只做底座。先读 event-layer 建立整体认识。

事件底座：

| Doc | Topic |
|---|---|
| [`proactive/event-layer.md`](proactive/event-layer.md) | 统一 Event 模型 + 框架定位 + 框架图 + 事件边界与演进（**已落地：A/B 类事件全在发，gate 可拦**，[可视化](proactive/event-layer.html)） |
| [`proactive/framework-evolution.md`](proactive/framework-evolution.md) | 框架演进：现状 → 目标 → 五步迁移（步 1·2·3 ✅，[可视化](proactive/framework-evolution.html)） |

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
| [`runtime/runtime.md`](runtime/runtime.md) | Runtime API 行为（另见 [`../api/runtime.md`](../api/runtime.md)） |
| [`runtime/user-input-requests.md`](runtime/operations/user-input-requests.md) | runtime.ask/confirm 等用户输入 |
| [`runtime/controllability-and-three-surface-sync.md`](runtime/controllability-and-three-surface-sync.md) | 值守/无人值守开关 + 中途干预 + 优雅停 + 三端同步 |
| [`runtime/p3-three-surface-sync.md`](runtime/p3-three-surface-sync.md) | P3 三端同步实施细节 |
| [`runtime/unified-session-context.md`](runtime/unified-session-context.md) | 统一 session context |
| [`runtime/agent-worktree.md`](runtime/execution/agent-worktree.md) | Agent worktree 行为 |
| [`runtime/async-task-lifecycle.md`](runtime/execution/async-task-lifecycle.md) | 异步任务生命周期 |
| [`runtime/streaming-resume.md`](runtime/operations/streaming-resume.md) | 流式 + 恢复 |
| [`runtime/file-management.md`](runtime/operations/file-management.md) | 回退层（commit / worktree） |
| [`runtime/multi-agent-revert-todo.md`](runtime/operations/multi-agent-revert-todo.md) | 多 agent 回退 TODO |
| [`runtime/session-dag.md`](runtime/dag/session-dag.md) | **权威** Session DAG 数据模型（一张图 / 3 种节点 user·llm·code / called_by 边 / render_context）+ 两路径合并设计（8 步全完成） |
| [`runtime/dag/dag-rendering.md`](runtime/dag/dag-rendering.md) | **权威渲染规范**：布局/连线/图例/默认可见性，12 场景 |
| [`runtime/dag/branch-collaboration.md`](runtime/dag/branch-collaboration.md) | 分支协作（通信 / 派活 / 合并）设计与实现步骤 |
| [`runtime/dispatcher-split.md`](runtime/execution/dispatcher-split.md) | Dispatcher 拆分设计 |

## providers/ — LLM provider、凭证、模型目录、thinking/effort

| Doc | Topic |
|---|---|
| [`providers/request-build.md`](providers/request-build.md) | 请求构建流程 |
| [`providers/models/models.md`](providers/models/models.md) | 模型目录最终设计 |
| [`providers/models/thinking-effort.md`](providers/models/thinking-effort.md) | Thinking / effort 子系统（级别定义、数据流、各 provider wire 格式、UI picker） |
| [`providers/models/fast-tier.md`](providers/models/fast-tier.md) | Fast（高速）档：两层判定、存储与线路 |
| [`providers/auth/claude-code-direct-oauth.md`](providers/auth/claude-code-direct-oauth.md) | claude-code 直连订阅（砍 Meridian） |
| [`providers/auth/credential-validation-unification.md`](providers/auth/credential-validation-unification.md) | 统一凭证校验 |
| [`providers/auth/unified-auth-storage.md`](providers/auth/unified-auth-storage.md) | 统一认证存储 |
| [`providers/auth/unified-account-management.md`](providers/auth/unified-account-management.md) | 统一账号管理 + 轮换 |
| [`providers/auth/credential-status-redesign.md`](providers/auth/credential-status-redesign.md) | 凭证状态重设计 |
| [`providers/auth/api-key-resolution-unification.md`](providers/auth/api-key-resolution-unification.md) | API key 解析统一 |
| [`providers/reliability/error-retry.md`](providers/reliability/error-retry.md) | 错误 + 重试处理 |
| [`providers/reliability/error-taxonomy-propagation.md`](providers/reliability/error-taxonomy-propagation.md) | 错误分类 + 传播 |
| [`providers/reliability/llm-fault-tolerance.md`](providers/reliability/llm-fault-tolerance.md) | LLM 容错（调研） |
| [`providers/reliability/error-and-timeout-mechanism.html`](providers/reliability/error-and-timeout-mechanism.html) | 错误 + 超时机制（已渲染） |

## function/ — function 与工具调用

| Doc | Topic |
|---|---|
| [`function/function-calling-unification.md`](function/function-calling-unification.md) | 工具/函数调用框架（当前） |

> 面向 authoring 的文档（`@agentic_function` 用法、函数元数据、
> 工具调用循环、下一步决策、纯 python 辅助）已移至
> 用户指南 [`../agentic-programming/README.md`](../agentic-programming/README.md)。

## cli/ — CLI / TUI、斜杠命令、端口

| Doc | Topic |
|---|---|
| [`cli/cli-redesign.md`](cli/cli-redesign.md) | CLI / TUI 重设计（schema 驱动的设置、配置面板）—— 当前 |
| [`cli/ports.md`](cli/ports.md) | Web UI 端口（架构、配置、冲突处理） |
| [`cli/slash-commands.md`](cli/slash-commands.md) | 斜杠命令 |
| [`cli/slash-commands-references.md`](cli/slash-commands-references.md) | 斜杠命令参考快照 |
| [`cli/drop-run-command.md`](cli/drop-run-command.md) | 从 Web UI 触发的函数执行路径 |
| [`cli/cli-naming.md`](cli/cli-naming.md) | CLI 命名 |

## channels/ — 消息通道

| Doc | Topic |
|---|---|
| [`channels/channel-design.md`](channels/channel-design.md) | 通道设计（当前） |
| [`channels/channel-audit.md`](channels/channel-audit.md) | 通道审计 / 参考快照 |

## ui/ — surface、指示点、附件、GUI agent

| Doc | Topic |
|---|---|
| [`ui/invariants.md`](ui/invariants.md) | 跨模块 UI 不变量清单 |
| [`ui/chat-turn-visual-spec.html`](ui/chat-turn-visual-spec.html) | 聊天轮次视觉规范（执行时间线 + 手动函数运行 + 消息导航） |
| [`ui/interaction-feedback.md`](ui/interaction-feedback.md) | 交互反馈 0ms 规则 |
| [`ui/surface-system.md`](ui/surface-system.md) | Surface 系统 |
| [`ui/indicator-dots.md`](ui/indicator-dots.md) | 指示点 |
| [`ui/attachment-handling.md`](ui/attachment-handling.md) | 附件处理（[已渲染](ui/attachment-handling.html)） |
| [`ui/composer-interaction-modes.md`](ui/composer-interaction-modes.md) | Composer 交互模式 |
| [`ui/gui-agent-context.md`](ui/gui-agent-context.md) | GUI agent 上下文流转 |

## integrations/ — MCP、skills/plugins、harness 标准

| Doc | Topic |
|---|---|
| [`integrations/harness-standard.md`](integrations/harness-standard.md) | Harness 标准（插件 + 自动探测）；安装：[`../installing-harnesses.md`](../installing-harnesses.md) |
| [`integrations/mcp-integration.md`](integrations/mcp-integration.md) | MCP 集成 |
| [`integrations/skills-and-plugins.md`](integrations/skills-and-plugins.md) | Skills 与 plugins |

## extension-gating/

扩展门控设计 + 参考对比 —— 见
[`extension-gating/README.md`](extension-gating/README.md)。

## 横切关注点

| Doc | Topic |
|---|---|
| [`usage-metering.md`](usage-metering.md) | Usage 子系统（token/cost 记账、ledger、收口点、子进程、消费层） |

## archive/

历史审计、演示以及被取代的文档存放在
[`archive/`](archive/) 中以便追溯，不作为实现指导。

最近归档：
- `model-catalog-dynamic.md` / `model-catalog-per-provider.md` — 迭代草稿，被 `models.md` 取代
- `claude-code-meridian-profile.md` — Meridian proxy 已砍，纯历史
- `*-references.md` — 调研快照/原始研究笔记（slash-commands / tui-upgrade / user-input-requests）

## TODO-doc-code-gaps.md

[`TODO-doc-code-gaps.md`](TODO-doc-code-gaps.md) — 审计发现的文档与代码不对齐待修项，按优先级排列。修完一条删一条。

## 约定

- 每个子系统一个子目录，与 `openprogram/` 对应。新设计文档放进匹配的分组，
  而不是扁平的根目录。当某个主题增长到超过几个文件时，新建一个分组。
- 每个分组先列*当前*的来源；支撑性笔记随后。
- API 参考归在 `docs/api/` 下；设计依据归在这里。
- 函数 authoring 规则以 `../agentic-programming/writing-functions/function-metadata.md` 为
  准——较短的文件链接到它，而不是重复其内容。
- 装饰器字段为 `render_range={"callers": N, "subcalls": M}` ——
  `callers` 按 seq 限制帧前节点数，`subcalls` 按 seq 限制帧内节点数。
  代码和文档都仅使用这两个名字。
