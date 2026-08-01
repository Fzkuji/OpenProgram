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
| [`context/context-comparison.md`](context/context-comparison.md) | Context approaches compared against reference projects |
| [`context/context-compaction.html`](context/context-compaction.html) | Context compaction (rendered) |

## memory/ — 记忆系统（实体 + 抽象）

| Doc | Topic |
|---|---|
| [`memory/README.md`](memory/README.md) | 记忆系统总览：架构、设计原则、实施状态 |
| [`memory/memory.md`](memory/memory.md) | Memory subsystem overview |
| [`memory/memory-v2.md`](memory/memory-v2.md) | Memory v2: entity/virtual two-tier + provenance-navigated recall |
| [`memory/entity-memory.md`](memory/entity-memory.md) | 实体记忆：Session-Git + Project-Git，按生命周期组织 |
| [`memory/git-as-entity-memory.md`](memory/git-as-entity-memory.md) | Entity memory on Git: Session-Git + Project-Git |
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
| [`runtime/operations/user-input-requests.md`](runtime/operations/user-input-requests.md) | runtime.ask/confirm 等用户输入 |
| [`runtime/controllability-and-three-surface-sync.md`](runtime/controllability-and-three-surface-sync.md) | 值守/无人值守开关 + 中途干预 + 优雅停 + 三端同步 |
| [`runtime/p3-three-surface-sync.md`](runtime/p3-three-surface-sync.md) | P3 三端同步实施细节 |
| [`runtime/unified-session-context.md`](runtime/unified-session-context.md) | 统一 session context |
| [`runtime/execution/agent-worktree.md`](runtime/execution/agent-worktree.md) | Agent worktree behaviour |
| [`runtime/execution/async-task-lifecycle.md`](runtime/execution/async-task-lifecycle.md) | Async task lifecycle |
| [`runtime/operations/streaming-resume.md`](runtime/operations/streaming-resume.md) | Streaming + resume |
| [`runtime/operations/file-management.md`](runtime/operations/file-management.md) | Revert layers (commit / worktree) |
| [`runtime/operations/multi-agent-revert-todo.md`](runtime/operations/multi-agent-revert-todo.md) | Multi-agent revert TODO |
| [`runtime/dag/session-dag.md`](runtime/dag/session-dag.md) | **authoritative** Session DAG data model (one graph / 3 node roles user·llm·code / caller+predecessor edges / spawn / rendering / assembly / compaction) |
| [`runtime/dag/dag-rendering.md`](runtime/dag/dag-rendering.md) | **authoritative rendering spec**: layout / edges / legend / default visibility, 12 scenarios |
| [`runtime/dag/branch-collaboration.md`](runtime/dag/branch-collaboration.md) | Branch collaboration (communication / dispatch / merge) design and implementation steps |
| [`runtime/execution/dispatcher-split.md`](runtime/execution/dispatcher-split.md) | Dispatcher split design |
| [`runtime/execution/next-step-decision.md`](runtime/execution/next-step-decision.md) | Next-step decision (how the model picks what runs next) |
| [`runtime/execution/agentic-self-recursion.md`](runtime/execution/agentic-self-recursion.md) | Agentic self-recursion ([rendered](runtime/execution/agentic-self-recursion.html)) |
| [`runtime/operations/rewind.md`](runtime/operations/rewind.md) | Rewind |
| [`runtime/operations/branch-naming.md`](runtime/operations/branch-naming.md) | Branch naming ([rendered](runtime/operations/branch-naming.html)) |
| [`runtime/session/README.md`](runtime/session/README.md) | Session subsystem: data model, storage, naming, listing, lifecycle |
| [`runtime/self-update.md`](runtime/self-update.md) | Self-update: staying usable while OpenProgram modifies itself |
| [`runtime/permission-model.md`](runtime/permission-model.md) | 权限系统设计 |
| [`runtime/agent-collaboration.md`](runtime/agent-collaboration.md) | Agent 协作：分支间通信原语 |
| [`runtime/tool-toggle-management.md`](runtime/tool-toggle-management.md) | 工具开关 / 工具集管理设计 |
| [`runtime/additional-working-directories.md`](runtime/additional-working-directories.md) | 会话多工作目录设计 |

## providers/ — LLM providers, credentials, model catalog, thinking/effort

| Doc | Topic |
|---|---|
| [`providers/request-build.md`](providers/request-build.md) | 请求构建流程 |
| [`providers/models/models.md`](providers/models/models.md) | 模型目录最终设计 |
| [`providers/models/thinking-effort.md`](providers/models/thinking-effort.md) | Thinking / effort 子系统（级别定义、数据流、各 provider wire 格式、UI picker） |
| [`providers/models/fast-tier.md`](providers/models/fast-tier.md) | The Fast tier: two-tier detection, storage, wires |
| [`providers/auth/claude-code-direct-oauth.md`](providers/auth/claude-code-direct-oauth.md) | claude-code 直连订阅（砍 Meridian） |
| [`providers/auth/credential-validation-unification.md`](providers/auth/credential-validation-unification.md) | Unified credential validation |
| [`providers/auth/unified-auth-storage.md`](providers/auth/unified-auth-storage.md) | 统一认证存储 |
| [`providers/auth/unified-account-management.md`](providers/auth/unified-account-management.md) | 统一账号管理 + 轮换 |
| [`providers/auth/credential-status-redesign.md`](providers/auth/credential-status-redesign.md) | 凭证状态重设计 |
| [`providers/auth/api-key-resolution-unification.md`](providers/auth/api-key-resolution-unification.md) | API key resolution unification |
| [`providers/reliability/error-retry.md`](providers/reliability/error-retry.md) | Error + retry handling |
| [`providers/reliability/error-taxonomy-propagation.md`](providers/reliability/error-taxonomy-propagation.md) | Error taxonomy + propagation |
| [`providers/reliability/llm-fault-tolerance.md`](providers/reliability/llm-fault-tolerance.md) | LLM fault tolerance（调研） |
| [`providers/reliability/error-and-timeout-mechanism.html`](providers/reliability/error-and-timeout-mechanism.html) | Error + timeout mechanism (rendered) |
| [`providers/network-proxy.md`](providers/network-proxy.md) | Outbound network proxy — survey, comparison, unified design |
| [`providers/auth/credential-connection-unification.md`](providers/auth/credential-connection-unification.md) | Credential/connection unification |
| [`providers/PROBLEM-models-and-bailian.md`](providers/PROBLEM-models-and-bailian.md) | 当前问题：模型清单与百炼 provider（未解问题记录） |

## function/ — function & tool calling

| Doc | Topic |
|---|---|
| [`function/function-calling-unification.md`](function/function-calling-unification.md) | Tool/function calling framework (current) |

> Authoring-facing docs (`@agentic_function` usage, function metadata,
> tool-calling loop, next-step decision, pure-python helpers) moved to the
> user guide at [`../agentic-programming/README.md`](../../capabilities/agentic-programming/README.md).

## cli/ — CLI / TUI, slash commands, ports

| Doc | Topic |
|---|---|
| [`cli/cli-redesign.md`](cli/cli-redesign.md) | CLI / TUI redesign (schema-driven settings, config panel) — current |
| [`cli/ports.md`](cli/ports.md) | Web UI ports (architecture, config, conflict handling) |
| [`cli/slash-commands.md`](cli/slash-commands.md) | Slash commands |
| [`cli/slash-commands-references.md`](cli/slash-commands-references.md) | Slash-command reference snapshot |
| [`cli/drop-run-command.md`](cli/drop-run-command.md) | Function execution path from the Web UI |
| [`cli/cli-naming.md`](cli/cli-naming.md) | CLI naming |
| [`cli/single-port.md`](cli/single-port.md) | Single-port architecture |
| [`cli/config-write-safety.md`](cli/config-write-safety.md) | Config write safety — atomic `update_config` |
| [`cli/tui-upgrade.md`](cli/tui-upgrade.md) | TUI upgrade |

## channels/ — messaging channels

| Doc | Topic |
|---|---|
| [`channels/channel-design.md`](channels/channel-design.md) | Channel design (current) |
| [`channels/channel-audit.md`](channels/channel-audit.md) | Channel audit / reference snapshot |

## ui/ — surfaces, indicators, attachments, GUI agent

| Doc | Topic |
|---|---|
| [`ui/invariants.md`](ui/invariants.md) | Cross-module UI invariants |
| [`ui/chat-turn-visual-spec.html`](ui/chat-turn-visual-spec.html) | Chat-turn visual spec (execution timeline + manual runs + message minimap) |
| [`ui/interaction-feedback.md`](ui/interaction-feedback.md) | The 0ms interaction-feedback rule |
| [`ui/surface-system.md`](ui/surface-system.md) | Surface system |
| [`ui/indicator-dots.md`](ui/indicator-dots.md) | Indicator dots |
| [`ui/attachment-handling.md`](ui/attachment-handling.md) | Attachment handling ([rendered](ui/attachment-handling.html)) |
| [`ui/composer-interaction-modes.md`](ui/composer-interaction-modes.md) | Composer 交互模式 |
| [`ui/gui-agent-context.md`](ui/gui-agent-context.md) | GUI agent context flow |
| [`ui/state-layer.md`](ui/state-layer.md) | Web state layer: per-session vs global stores, session-scope container plan |
| [`ui/project-workspace.md`](ui/project-workspace.md) | Project workspace — files, tabs, multi-session ([prototype](ui/project-workspace-prototype.html)) |

## integrations/ — MCP, skills/plugins, harness standard

| Doc | Topic |
|---|---|
| [`integrations/harness-standard.md`](integrations/harness-standard.md) | Harness standard (plug-in + auto-detect); install: [`../installing-harnesses.md`](../../capabilities/installing-harnesses.md) |
| [`integrations/mcp-integration.md`](integrations/mcp-integration.md) | MCP integration |
| [`integrations/skills-and-plugins.md`](integrations/skills-and-plugins.md) | Skills and plugins |

## extension-gating/

Extension gating design + reference comparison — see
[`extension-gating/README.md`](extension-gating/README.md).

## Cross-cutting

| Doc | Topic |
|---|---|
| [`usage-metering.md`](usage-metering.md) | Usage 子系统（token/cost 记账、ledger、收口点、子进程、消费层） |
| [`framework-overview.md`](framework-overview.md) | 框架总览：一次对话从输入到产出 |
| [`docs-site.md`](docs-site.md) | The documentation site itself (build, nav, bilingual routing) |

## research/ — investigations

| Doc | Topic |
|---|---|
| [`research/execution-trace-model-selection.md`](research/execution-trace-model-selection.md) | Choosing the data model for agent execution traces (span concept, what's novel) |

## plans/ — dated implementation plans

| Doc | Topic |
|---|---|
| [`plans/proactive-implementation.md`](plans/proactive-implementation.md) | Proactive layer implementation plan |
| [`plans/cache-control-passthrough.md`](plans/cache-control-passthrough.md) | Per-block passthrough of Anthropic `cache_control` (landed) |
| [`plans/2026-07-08-credential-connection-unification.md`](plans/2026-07-08-credential-connection-unification.md) | Credential/connection unification migration |
| [`plans/2026-07-08-enabled-models-migration.md`](plans/2026-07-08-enabled-models-migration.md) | Enabled-models migration |
| [`plans/2026-07-08-provider-self-contained-migration.md`](plans/2026-07-08-provider-self-contained-migration.md) | Provider self-contained migration |

## Removed docs

There is no `archive/` directory: superseded docs were deleted outright rather
than moved aside. Recover them from git history if needed.

Previously removed:
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
