# Design Documents

This directory contains current design notes for OpenProgram. Treat this file as
the entry point before reading individual design documents.

## Current specs

| Area | Current source |
|---|---|
| Context and session memory | [`context-commit-chain.md`](context-commit-chain.md) |
| Context attach and merge behavior | [`context-attach-merge.md`](context-attach-merge.md) |
| Agent worktree behavior | [`agent-worktree.md`](agent-worktree.md) |
| Runtime API behavior | [`../api/runtime.md`](../api/runtime.md) |
| Function metadata | [`function/function_metadata.md`](function/function_metadata.md) |
| Agentic function usage | [`function/agentic_function.md`](function/agentic_function.md) |
| Tool/function calling framework | [`function-calling-unification.md`](function-calling-unification.md) |
| Per-turn tool loop mechanics | [`tool-calling.md`](tool-calling.md) |
| Function execution path from Web UI | [`drop-run-command.md`](drop-run-command.md) |
| Extension gating | [`extension-gating/README.md`](extension-gating/README.md) |
| Channels | [`channel-design.md`](channel-design.md) |
| Memory subsystem | [`memory.md`](memory.md) |
| MCP integration | [`mcp-integration.md`](mcp-integration.md) |
| Slash commands | [`slash-commands.md`](slash-commands.md) |
| GUI agent context flow | [`gui-agent-context.md`](gui-agent-context.md) |
| Harness standard (how a harness plugs in + auto-detect) | [`harness-standard.md`](harness-standard.md); install procedure: [`../installing-harnesses.md`](../installing-harnesses.md) |
| Entity memory v2 (session/project git) | [`memory-v2.md`](memory-v2.md) |
| Revert layers (snapshot / commit / worktree) | [`revert-layers.md`](revert-layers.md) |
| Web UI ports (architecture, configuration, conflict handling) | [`ports.md`](ports.md) |
| CLI / TUI redesign (schema-driven settings, visual config panel) | [`cli-redesign.md`](cli-redesign.md) |

## Supporting notes

These documents are useful background, but should not override current specs:

| Area | Notes |
|---|---|
| Context engine internals | [`context-engine-spec.md`](context-engine-spec.md), [`cross-turn-tool-context.md`](cross-turn-tool-context.md), [`contextgit.md`](contextgit.md), [`git-as-entity-memory.md`](git-as-entity-memory.md) |
| DAG model investigations | [`dag-node-model.md`](dag-node-model.md), [`dag-edge-split.md`](dag-edge-split.md) |
| Async and streaming behavior | [`async-task-lifecycle.md`](async-task-lifecycle.md), [`streaming-resume.md`](streaming-resume.md) |
| Error handling | [`error-retry.md`](error-retry.md) |
| CLI naming | [`cli-naming.md`](cli-naming.md) |
| Reference snapshots | [`slash-commands-references.md`](slash-commands-references.md), [`channel-audit.md`](channel-audit.md), [`extension-gating/reference-comparison.md`](extension-gating/reference-comparison.md) |

## Archive

Historical audits, demos, and documents that no longer define the current
implementation live in [`archive/`](archive/). They are kept for traceability,
not as implementation guidance.

## Conventions

- Prefer one current source per topic. If a document is only historical, move it
  to `archive/`.
- API reference belongs under `docs/api/`; design rationale belongs here.
- For function-authoring rules, `function/function_metadata.md` is the source of
  truth. Shorter files may link to it, but should not repeat its rules.
- The decorator field is `render_range={"callers": N, "subcalls": M}`
  — `callers` caps pre-frame nodes by seq, `subcalls` caps in-frame
  nodes by seq. Both code and docs use these names exclusively.
