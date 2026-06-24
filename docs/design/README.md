# Design Documents

Current design notes for OpenProgram, grouped by subsystem to mirror the code
layout under `openprogram/`. Read this index first, then the doc you need.

Each subdirectory collects the designs for one area. Within a group, the doc
that defines the *current* implementation is listed first; the rest are
supporting notes / investigations that should not override it.

## context/ — context engine, commits, tool aging

| Doc | Topic |
|---|---|
| [`context/context.md`](context/context.md) | Context layer: pipeline + DAG storage + ContextCommit + compaction/render + attach/merge + cross-turn tool + gaps |
| [`context/context-composition.md`](context/context-composition.md) | Target state: per-call layering (L0/L1/L2) + situational context |

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

## runtime/ — agent execution, DAG, async, revert, controllability

| Doc | Topic |
|---|---|
| [`runtime/runtime.md`](runtime/runtime.md) | Runtime API behaviour (see also [`../api/runtime.md`](../api/runtime.md)) |
| [`runtime/user-input-requests.md`](runtime/user-input-requests.md) | runtime.ask/confirm 等用户输入 |
| [`runtime/controllability-and-three-surface-sync.md`](runtime/controllability-and-three-surface-sync.md) | 值守/无人值守开关 + 中途干预 + 优雅停 + 三端同步 |
| [`runtime/p3-three-surface-sync.md`](runtime/p3-three-surface-sync.md) | P3 三端同步实施细节 |
| [`runtime/unified-session-context.md`](runtime/unified-session-context.md) | 统一 session context |
| [`runtime/agent-worktree.md`](runtime/agent-worktree.md) | Agent worktree behaviour |
| [`runtime/async-task-lifecycle.md`](runtime/async-task-lifecycle.md) | Async task lifecycle |
| [`runtime/streaming-resume.md`](runtime/streaming-resume.md) | Streaming + resume |
| [`runtime/file-management.md`](runtime/file-management.md) | Revert layers (commit / worktree) |
| [`runtime/multi-agent-revert-todo.md`](runtime/multi-agent-revert-todo.md) | Multi-agent revert TODO |
| [`runtime/session-dag.md`](runtime/session-dag.md) | **权威** Session DAG 数据模型（一张图 / 3 种节点 user·llm·code / called_by 边 / render_context）+ 两路径合并设计（8 步全完成） |
| [`runtime/dispatcher-split.md`](runtime/dispatcher-split.md) | Dispatcher split design |

## providers/ — LLM providers, credentials, model catalog, thinking/effort

| Doc | Topic |
|---|---|
| [`providers/thinking-effort.md`](providers/thinking-effort.md) | Thinking / effort 子系统（级别定义、数据流、各 provider wire 格式、UI picker） |
| [`providers/models.md`](providers/models.md) | 模型目录最终设计 |
| [`providers/claude-code-direct-oauth.md`](providers/claude-code-direct-oauth.md) | claude-code 直连订阅（砍 Meridian） |
| [`providers/credential-validation-unification.md`](providers/credential-validation-unification.md) | Unified credential validation |
| [`providers/unified-auth-storage.md`](providers/unified-auth-storage.md) | 统一认证存储 |
| [`providers/unified-account-management.md`](providers/unified-account-management.md) | 统一账号管理 + 轮换 |
| [`providers/credential-status-redesign.md`](providers/credential-status-redesign.md) | 凭证状态重设计 |
| [`providers/api-key-resolution-unification.md`](providers/api-key-resolution-unification.md) | API key resolution unification |
| [`providers/error-retry.md`](providers/error-retry.md) | Error + retry handling |
| [`providers/error-taxonomy-propagation.md`](providers/error-taxonomy-propagation.md) | Error taxonomy + propagation |
| [`providers/llm-fault-tolerance.md`](providers/llm-fault-tolerance.md) | LLM fault tolerance（调研） |
| [`providers/error-and-timeout-mechanism.html`](providers/error-and-timeout-mechanism.html) | Error + timeout mechanism (rendered) |

## function/ — function & tool calling

| Doc | Topic |
|---|---|
| [`function/function-calling-unification.md`](function/function-calling-unification.md) | Tool/function calling framework (current) |

> Authoring-facing docs (`@agentic_function` usage, function metadata,
> tool-calling loop, next-step decision, pure-python helpers) moved to the
> user guide at [`../agentic-programming/README.md`](../agentic-programming/README.md).

## cli/ — CLI / TUI, slash commands, ports

| Doc | Topic |
|---|---|
| [`cli/cli-redesign.md`](cli/cli-redesign.md) | CLI / TUI redesign (schema-driven settings, config panel) — current |
| [`cli/ports.md`](cli/ports.md) | Web UI ports (architecture, config, conflict handling) |
| [`cli/slash-commands.md`](cli/slash-commands.md) | Slash commands |
| [`cli/slash-commands-references.md`](cli/slash-commands-references.md) | Slash-command reference snapshot |
| [`cli/drop-run-command.md`](cli/drop-run-command.md) | Function execution path from the Web UI |
| [`cli/cli-naming.md`](cli/cli-naming.md) | CLI naming |

## channels/ — messaging channels

| Doc | Topic |
|---|---|
| [`channels/channel-design.md`](channels/channel-design.md) | Channel design (current) |
| [`channels/channel-audit.md`](channels/channel-audit.md) | Channel audit / reference snapshot |

## ui/ — surfaces, indicators, attachments, GUI agent

| Doc | Topic |
|---|---|
| [`ui/surface-system.md`](ui/surface-system.md) | Surface system |
| [`ui/indicator-dots.md`](ui/indicator-dots.md) | Indicator dots |
| [`ui/attachment-handling.md`](ui/attachment-handling.md) | Attachment handling ([rendered](ui/attachment-handling.html)) |
| [`ui/composer-interaction-modes.md`](ui/composer-interaction-modes.md) | Composer 交互模式 |
| [`ui/gui-agent-context.md`](ui/gui-agent-context.md) | GUI agent context flow |

## integrations/ — MCP, skills/plugins, harness standard

| Doc | Topic |
|---|---|
| [`integrations/harness-standard.md`](integrations/harness-standard.md) | Harness standard (plug-in + auto-detect); install: [`../installing-harnesses.md`](../installing-harnesses.md) |
| [`integrations/mcp-integration.md`](integrations/mcp-integration.md) | MCP integration |
| [`integrations/skills-and-plugins.md`](integrations/skills-and-plugins.md) | Skills and plugins |

## extension-gating/

Extension gating design + reference comparison — see
[`extension-gating/README.md`](extension-gating/README.md).

## Cross-cutting

| Doc | Topic |
|---|---|
| [`usage.md`](usage.md) | Usage 子系统（token/cost 记账、ledger、收口点、子进程、消费层） |

## archive/

Historical audits, demos, and superseded docs live in
[`archive/`](archive/) for traceability, not as implementation guidance.

Recently archived:
- `model-catalog-dynamic.md` / `model-catalog-per-provider.md` — 迭代草稿，被 `models.md` 取代
- `claude-code-meridian-profile.md` — Meridian proxy 已砍，纯历史
- `*-references.md` — 调研快照/原始研究笔记（slash-commands / tui-upgrade / user-input-requests）

## TODO-doc-code-gaps.md

[`TODO-doc-code-gaps.md`](TODO-doc-code-gaps.md) — 审计发现的文档与代码不对齐待修项，按优先级排列。修完一条删一条。

## Conventions

- One subdirectory per subsystem, mirroring `openprogram/`. New design docs go
  into the matching group, not the flat root. Add a group when a topic grows
  past a couple of files.
- Each group lists the *current* source first; supporting notes follow.
- API reference belongs under `docs/api/`; design rationale belongs here.
- For function-authoring rules, `../agentic-programming/writing-functions/function-metadata.md` is
  the source of truth — shorter files link to it rather than repeating it.
- The decorator field is `render_range={"callers": N, "subcalls": M}` —
  `callers` caps pre-frame nodes by seq, `subcalls` caps in-frame nodes by seq.
  Both code and docs use these names exclusively.
