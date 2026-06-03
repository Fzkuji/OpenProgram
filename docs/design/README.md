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

## runtime/ — agent execution, DAG, async, revert

| Doc | Topic |
|---|---|
| [`runtime/runtime.md`](runtime/runtime.md) | Runtime API behaviour (see also [`../api/runtime.md`](../api/runtime.md)) |
| [`runtime/agent-worktree.md`](runtime/agent-worktree.md) | Agent worktree behaviour |
| [`runtime/async-task-lifecycle.md`](runtime/async-task-lifecycle.md) | Async task lifecycle |
| [`runtime/streaming-resume.md`](runtime/streaming-resume.md) | Streaming + resume |
| [`runtime/revert-layers.md`](runtime/revert-layers.md) | Revert layers (commit / worktree) |
| [`runtime/multi-agent-revert-todo.md`](runtime/multi-agent-revert-todo.md) | Multi-agent revert TODO |
| [`runtime/next-step-decision.md`](runtime/next-step-decision.md) | Next-step decision loop |
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
| [`function/tool-calling.md`](function/tool-calling.md) | Per-turn tool loop mechanics |
| [`function/agentic_function.md`](function/agentic_function.md) | Agentic function usage |
| [`function/function_metadata.md`](function/function_metadata.md) | Function metadata (source of truth) |
| [`function/pure_python.md`](function/pure_python.md) | Pure-python functions |

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
- For function-authoring rules, `function/function_metadata.md` is the source of
  truth — shorter files link to it rather than repeating it.
- The decorator field is `render_range={"callers": N, "subcalls": M}` —
  `callers` caps pre-frame nodes by seq, `subcalls` caps in-frame nodes by seq.
  Both code and docs use these names exclusively.
