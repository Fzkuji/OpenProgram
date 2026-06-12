# Design Documents

Current design notes for OpenProgram, grouped by subsystem to mirror the code
layout under `openprogram/`. Read this index first, then the doc you need.

Each subdirectory collects the designs for one area. Within a group, the doc
that defines the *current* implementation is listed first; the rest are
supporting notes / investigations that should not override it.

## context/ — context engine, commits, tool aging

| Doc | Topic |
|---|---|
| [`context/context-commit-chain.md`](context/context-commit-chain.md) | Context + session memory commit chain (current) |
| [`context/context-attach-merge.md`](context/context-attach-merge.md) | Attach + merge behaviour |
| [`context/context-engine-spec.md`](context/context-engine-spec.md) | Context engine internals |
| [`context/cross-turn-tool-context.md`](context/cross-turn-tool-context.md) | Tool-result aging across turns |
| [`context/contextgit.md`](context/contextgit.md) | contextgit backing store |

## memory/ — entity / project memory

| Doc | Topic |
|---|---|
| [`memory/memory-v2.md`](memory/memory-v2.md) | Entity memory v2 (session/project git) — current |
| [`memory/memory.md`](memory/memory.md) | Memory subsystem (v1 background) |
| [`memory/git-as-entity-memory.md`](memory/git-as-entity-memory.md) | Git-as-entity-memory rationale |

## proactive/ — 事件层 + 主动性（事件驱动）

分两块：**事件底座**（一条统一事件流，给整个框架用）+ **主动性应用**（规则订阅事件流出手）。
两块解耦，可只做底座。先读 event-layer 建立整体认识。

事件底座：

| Doc | Topic |
|---|---|
| [`proactive/event-layer.md`](proactive/event-layer.md) | 统一 Event 模型 + 框架定位 + 框架图 + 事件边界与演进（**实施中：步 1·2 已落地**，[可视化](proactive/event-layer.html)） |
| [`proactive/framework-evolution.md`](proactive/framework-evolution.md) | 框架演进：现状 → 目标 → 五步迁移（步 1·2 ✅，[可视化](proactive/framework-evolution.html)） |

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

## runtime/ — agent execution, DAG, async, revert

| Doc | Topic |
|---|---|
| [`runtime/runtime.md`](runtime/runtime.md) | Runtime API behaviour (see also [`../api/runtime.md`](../api/runtime.md)) |
| [`runtime/agent-worktree.md`](runtime/agent-worktree.md) | Agent worktree behaviour |
| [`runtime/async-task-lifecycle.md`](runtime/async-task-lifecycle.md) | Async task lifecycle |
| [`runtime/streaming-resume.md`](runtime/streaming-resume.md) | Streaming + resume |
| [`runtime/revert-layers.md`](runtime/revert-layers.md) | Revert layers (commit / worktree) |
| [`runtime/multi-agent-revert-todo.md`](runtime/multi-agent-revert-todo.md) | Multi-agent revert TODO |
| [`runtime/dag-node-model.md`](runtime/dag-node-model.md) | DAG node model investigation |
| [`runtime/dag-edge-split.md`](runtime/dag-edge-split.md) | DAG edge-split investigation |

## providers/ — LLM providers, credentials, fault tolerance

| Doc | Topic |
|---|---|
| [`providers/credential-validation-unification.md`](providers/credential-validation-unification.md) | Unified credential validation (current) |
| [`providers/llm-fault-tolerance.md`](providers/llm-fault-tolerance.md) | LLM fault tolerance |
| [`providers/error-retry.md`](providers/error-retry.md) | Error + retry handling |
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

## archive/

Historical audits, demos, and superseded docs live in
[`archive/`](archive/) for traceability, not as implementation guidance.

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
