# Design Documents

Current design notes for OpenProgram, grouped by subsystem to mirror the code
layout under `openprogram/`. Read this index first, then the doc you need.

Each subdirectory collects the designs for one area. Within a group, the doc
that defines the *current* implementation is listed first; the rest are
supporting notes / investigations that should not override it.

## context/ — context engine, commits, tool aging

| Doc | Topic |
|---|---|
| [`context/overview.md`](context/overview.md) | Context layer: pipeline + DAG storage + ContextCommit + compaction/render + attach/merge + cross-turn tool + gaps |
| [`context/composition.md`](context/composition.md) | Target state: per-call layering (L0/L1/L2) + situational context |
| [`context/comparison.md`](context/comparison.md) | Context approaches compared against reference projects |
| [`context/context-compaction.html`](context/context-compaction.html) | Context compaction (rendered) |

## memory/ — memory system (entity + abstract)

| Doc | Topic |
|---|---|
| [`memory/README.md`](memory/README.md) | Memory system overview: architecture, design principles, implementation status |
| [`memory/overview.md`](memory/overview.md) | Memory subsystem: entity/virtual two-tier + provenance-navigated recall, and the chain running today ([visualization](memory/memory-architecture.html)) |
| [`memory/entity-memory.md`](memory/entity-memory.md) | Entity memory: Session-Git + Project-Git, organized by lifecycle |
| [`memory/git-as-entity-memory.md`](memory/git-as-entity-memory.md) | Entity memory on Git: Session-Git + Project-Git |
| [`memory/virtual-memory.md`](memory/virtual-memory.md) | Abstract memory: Timeline + Graph + Core, organized by type × lifecycle |

## proactive/ — event layer + proactivity (event-driven)

Two parts: the **event base** (one unified event stream for the whole framework) and
**proactivity applications** (rules subscribe to the stream and act). They are decoupled,
so the base is usable alone. Read event-layer first for the overall picture.

Event base:

| Doc | Topic |
|---|---|
| [`proactive/event-layer.md`](proactive/event-layer.md) | Unified Event model, framework placement, diagram, event boundaries (**landed: class A/B events all emitted, gate can block**, [visualization](proactive/event-layer.html)) |
| [`proactive/framework-evolution.md`](proactive/framework-evolution.md) | Framework evolution: current → target → five migration steps (steps 1·2·3 done, [visualization](proactive/framework-evolution.html)) |

Proactivity applications (built on the base):

| Doc | Topic |
|---|---|
| [`proactive/overview.md`](proactive/overview.md) | One scenario end to end (blocking `rm -rf`), introducing rules / actions / state in place |
| [`proactive/events-and-state.md`](proactive/events-and-state.md) | How state folds out of events — why a rule can remember the past |
| [`proactive/execution-model.md`](proactive/execution-model.md) | How to write a Policy; blocking vs observing rules |
| [`proactive/policies-mvp.md`](proactive/policies-mvp.md) | Three sample rules to copy when writing new ones |
| [`proactive/invariants.md`](proactive/invariants.md) | Invariants the framework itself must hold (chiefly: no feedback loops) |

> Paper/production-grade material (offline replay validation, adversarial safety,
> evaluation skeleton) is archived under `proactive/_research_archive/`.

## runtime/ — agent execution, DAG, async, revert, controllability

| Doc | Topic |
|---|---|
| [`runtime/overview.md`](runtime/overview.md) | Runtime API behaviour (see also [`../api/runtime.md`](../api/runtime.md)) |
| [`runtime/operations/user-input-requests.md`](runtime/operations/user-input-requests.md) | User input via runtime.ask/confirm |
| [`runtime/controllability-and-three-surface-sync.md`](runtime/controllability-and-three-surface-sync.md) | Attended/unattended toggle, mid-run intervention, graceful stop, three-surface sync |
| [`runtime/p3-three-surface-sync.md`](runtime/p3-three-surface-sync.md) | P3 three-surface sync implementation detail |
| [`runtime/unified-session-context.md`](runtime/unified-session-context.md) | Unified session context |
| [`runtime/agent-configuration-ui.html`](runtime/agent-configuration-ui.html) | Agent configuration framework: identity, model, instructions, Programs, Skills, MCP, and Sessions ([core settings](runtime/agent-core-configuration-ui.html), [capabilities](runtime/agent-capability-configuration-ui.html), [Programs picker](runtime/agent-tool-configuration-ui.html)) |
| [`runtime/execution/agent-worktree.md`](runtime/execution/agent-worktree.md) | Agent worktree behaviour |
| [`runtime/execution/async-job-lifecycle.md`](runtime/execution/async-job-lifecycle.md) | Async task lifecycle |
| [`runtime/agent-resource-governance.html`](runtime/agent-resource-governance.html) | Agent runtime quotas and task lifecycle governance: current implementation audit, reference comparison, admission, budgets, recovery, visibility, and implementation gates |
| [`runtime/operations/streaming-resume.md`](runtime/operations/streaming-resume.md) | Streaming + resume |
| [`runtime/operations/file-management.html`](runtime/operations/file-management.html) | **Authoritative** file attribution, Review, Undo, historical Revert, multi-turn Restore, branch/worktree alignment, and multi-agent ownership |
| [`runtime/dag/overview.md`](runtime/dag/overview.md) | **authoritative** Session DAG data model (one graph / 3 node roles user·llm·code / caller+predecessor edges / spawn / rendering / assembly / compaction) |
| [`runtime/dag/rendering.md`](runtime/dag/rendering.md) | **authoritative rendering spec**: layout / edges / legend / default visibility, 12 scenarios |
| [`runtime/dag/branch-collaboration.md`](runtime/dag/branch-collaboration.md) | Branch collaboration (communication / dispatch / merge) design and implementation steps |
| [`runtime/execution/dispatcher-split.md`](runtime/execution/dispatcher-split.md) | Dispatcher split design |
| [`runtime/execution/next-step-decision.md`](runtime/execution/next-step-decision.md) | Next-step decision (how the model picks what runs next) |
| [`runtime/execution/agentic-self-recursion.md`](runtime/execution/agentic-self-recursion.md) | Agentic self-recursion ([rendered](runtime/execution/agentic-self-recursion.html)) |
| [`runtime/operations/branch-naming.md`](runtime/operations/branch-naming.md) | Branch naming ([rendered](runtime/operations/branch-naming.html)) |
| [`runtime/session/README.md`](runtime/session/README.md) | Session subsystem: data model, storage, naming, listing, lifecycle |
| [`runtime/self-update.html`](runtime/self-update.html) | Conversational self-update: isolated code changes, external App activation, restart handoff, automatic goal verification, and rollback |
| [`runtime/sandbox-architecture.html`](runtime/sandbox-architecture.html) | Canonical execution-security design: authority tiers, permission modes and approval, host sandbox boundaries, framework comparison, and implementation evidence |
| [`runtime/permission-model.md`](runtime/permission-model.md) / [`runtime/sandbox.md`](runtime/sandbox.md) | Stable link targets that point to the canonical execution-security design |
| [`runtime/ssrf-protection.html`](runtime/ssrf-protection.html) | Outbound URL and SSRF design: current gaps, Hermes/OpenClaw/OWASP comparison, scoped trust policy, transport requirements, and full acceptance gates |
| [`runtime/agent-collaboration.md`](runtime/agent-collaboration.md) | Agent collaboration: cross-branch communication primitives ([tool surface](runtime/agent-collab-architecture.html), [eight reference implementations compared](runtime/agent-collab-comparison.html)) |
| [`runtime/agent-resource-governance.html`](runtime/agent-resource-governance.html) | Agent resource governance: current implementation, framework comparison, durable scheduling and enforceable budget plan |
| [`runtime/tool-toggle-management.md`](runtime/tool-toggle-management.md) | Tool toggles / toolset management design |
| [`runtime/additional-working-directories.md`](runtime/additional-working-directories.md) | Multiple working directories per session |

## providers/ — LLM providers, credentials, model catalog, thinking/effort

| Doc | Topic |
|---|---|
| [`providers/request-build.md`](providers/request-build.md) | Request build pipeline |
| [`providers/models/overview.md`](providers/models/overview.md) | Model catalog, final design |
| [`providers/models/thinking-effort.md`](providers/models/thinking-effort.md) | Thinking / effort subsystem (level definitions, data flow, per-provider wire formats, UI picker) |
| [`providers/models/fast-tier.md`](providers/models/fast-tier.md) | The Fast tier: two-tier detection, storage, wires |
| [`providers/auth/claude-code-direct-oauth.md`](providers/auth/claude-code-direct-oauth.md) | claude-code direct subscription auth (Meridian dropped) |
| [`providers/auth/credential-validation-unification.md`](providers/auth/credential-validation-unification.md) | Unified credential validation |
| [`providers/auth/unified-auth-storage.md`](providers/auth/unified-auth-storage.md) | Unified auth storage |
| [`providers/auth/unified-account-management.md`](providers/auth/unified-account-management.md) | Unified account management + rotation |
| [`providers/auth/credential-file-hardening.html`](providers/auth/credential-file-hardening.html) | File credential persistence hardening: current inventory, user-flow risks, atomic private-write contract, backup/restore boundary, and implementation gates |
| [`providers/auth/credential-status-redesign.md`](providers/auth/credential-status-redesign.md) | Credential status |
| [`providers/auth/api-key-resolution-unification.md`](providers/auth/api-key-resolution-unification.md) | API key resolution unification |
| [`providers/reliability/error-retry.md`](providers/reliability/error-retry.md) | Error + retry handling |
| [`providers/reliability/error-taxonomy-propagation.md`](providers/reliability/error-taxonomy-propagation.md) | Error taxonomy + propagation |
| [`providers/reliability/llm-fault-tolerance.md`](providers/reliability/llm-fault-tolerance.md) | LLM fault tolerance (investigation) |
| [`providers/reliability/error-and-timeout-mechanism.html`](providers/reliability/error-and-timeout-mechanism.html) | Error + timeout mechanism (rendered) |
| [`providers/network-proxy.md`](providers/network-proxy.md) | Outbound network proxy |
| [`providers/auth/credential-connection-unification.md`](providers/auth/credential-connection-unification.md) | Credential/connection unification |
| [`providers/PROBLEM-models-and-bailian.md`](providers/PROBLEM-models-and-bailian.md) | Model list and the Bailian provider |

## function/ — function & tool calling

| Doc | Topic |
|---|---|
| [`function/calling-unification.md`](function/calling-unification.md) | Tool/function calling framework (current) |

> Authoring-facing docs (`@agentic_function` usage, function metadata,
> tool-calling loop, next-step decision, pure-python helpers) moved to the
> user guide at [`../agentic-programming/README.md`](../../capabilities/agentic-programming/README.md).

## cli/ — CLI / TUI, slash commands, ports

| Doc | Topic |
|---|---|
| [`cli/redesign.md`](cli/redesign.md) | CLI / TUI redesign (schema-driven settings, config panel) — current |
| [`cli/ports.md`](cli/ports.md) | Web UI port (config surface, conflict handling) |
| [`cli/slash-commands.md`](cli/slash-commands.md) | Slash commands |
| [`cli/slash-commands-references.md`](cli/slash-commands-references.md) | Slash-command reference snapshot |
| [`cli/drop-run-command.md`](cli/drop-run-command.md) | Function execution path from the Web UI |
| [`cli/naming.md`](cli/naming.md) | CLI naming |
| [`cli/single-port.md`](cli/single-port.md) | Single-port architecture |
| [`cli/config-write-safety.md`](cli/config-write-safety.md) | Config write safety — atomic `update_config` |
| [`cli/tui-upgrade.md`](cli/tui-upgrade.md) | TUI upgrade |

## channels/ — messaging channels

| Doc | Topic |
|---|---|
| [`channels/design.md`](channels/design.md) | Channel design (current) |
| [`channels/audit.md`](channels/audit.md) | Channel audit / reference snapshot |

## ui/ — surfaces, indicators, attachments, GUI agent

| Doc | Topic |
|---|---|
| [`ui/invariants.md`](ui/invariants.md) | Cross-module UI invariants |
| [`ui/chat-turn-visual-spec.html`](ui/chat-turn-visual-spec.html) | Chat-turn visual spec (execution timeline + manual runs + message minimap) |
| [`ui/interaction-feedback.md`](ui/interaction-feedback.md) | The 0ms interaction-feedback rule |
| [`ui/surface-system.md`](ui/surface-system.md) | Surface system |
| [`ui/theme-system.html`](ui/theme-system.html) | Theme entry, complete token contract, component consumption, and desktop-overlay propagation |
| [`ui/app-icon.html`](ui/app-icon.html) | macOS app icon source layers, Apple-managed enclosure, packaging, and legacy fallback boundary |
| [`ui/settings-collapsible-columns.html`](ui/settings-collapsible-columns.html) | Collapsible app and Settings nav; Providers list stays expanded |
| [`ui/indicator-dots.md`](ui/indicator-dots.md) | Indicator dots |
| [`ui/attachment-handling.md`](ui/attachment-handling.md) | Attachment handling ([rendered](ui/attachment-handling.html)) |
| [`ui/composer-interaction-modes.md`](ui/composer-interaction-modes.md) | Composer interaction modes |
| [`ui/gui-agent.html`](ui/gui-agent.html) | GUI agent entry, state machine, result contract, and implementation status |
| [`ui/state-layer.md`](ui/state-layer.md) | Web state layer: per-session vs global stores, session-scope container plan |
| [`ui/center-tabs-and-split-layout.html`](ui/center-tabs-and-split-layout.html) | Authoritative single-tab and composite split-tab lifecycle, rendering, persistence, and transfer design |
| [`ui/project-workspace.md`](ui/project-workspace.md) | Project workspace — files, tabs, multi-session ([prototype](ui/project-workspace-prototype.html)) |

## integrations/ — MCP, skills/plugins, harness standard

| Doc | Topic |
|---|---|
| [`integrations/harness-standard.md`](integrations/harness-standard.md) | Harness standard (plug-in + auto-detect); install: [`../installing-harnesses.md`](../../capabilities/installing-harnesses.md) |
| [`integrations/mcp-integration.md`](integrations/mcp-integration.md) | MCP integration |
| [`integrations/skills-and-plugins.md`](integrations/skills-and-plugins.md) | Skills and plugins |
| [`integrations/extension-management.html`](integrations/extension-management.html) | Unified Web management for Plugins, Skills, and MCP servers |

## extension-gating/

Extension gating design + reference comparison — see
[`extension-gating/README.md`](extension-gating/README.md).

## Cross-cutting

| Doc | Topic |
|---|---|
| [`usage-metering.md`](usage-metering.md) | Usage subsystem (token/cost accounting, ledger, collection point, subprocesses, consumers) |
| [`framework-overview.md`](framework-overview.md) | Framework overview: one conversation from input to output |
| [`framework-comparison.html`](framework-comparison.html) | Whole-framework comparison against twelve reference implementations by design axis: where we lead, where we lag, and what they have that we never considered (rendered) |
| [`feature-matrix.html`](feature-matrix.html) | The same twelve implementations scanned by feature list instead of design axis: 160 user-facing features in one grid, what only they have, what only we have (rendered) |
| [`docs-site.md`](docs-site.md) | The documentation site itself (build, nav, bilingual routing) |
| [`repository-structure.html`](repository-structure.html) | Repository boundaries, long-file split policy, and documentation information architecture |
| [`repository-structure-implementation.md`](repository-structure-implementation.md) | Implementation ledger for the repository structure design |

## research/ — investigations

| Doc | Topic |
|---|---|
| [`research/execution-trace-model-selection.md`](research/execution-trace-model-selection.md) | Choosing the data model for agent execution traces (span concept, what's novel) |

## distribution/ — installation, packaging, and updates

| Doc | Topic |
|---|---|
| [`distribution/installation-packaging.html`](distribution/installation-packaging.html) | Complete-product installation, packaging, platform support, and release artifacts |
| [`distribution/automatic-updates.html`](distribution/automatic-updates.html) | Stable Release discovery, Desktop verified DMG handoff, managed CLI atomic activation, trust boundaries, UI states, and implementation evidence |
| [`distribution/implementation-plan.md`](distribution/implementation-plan.md) | Historical distribution implementation evidence not duplicated by the current designs |

## plans/ — dated implementation plans

| Doc | Topic |
|---|---|
| [`plans/proactive-implementation.md`](plans/proactive-implementation.md) | Proactive layer implementation plan |
| [`plans/cache-control-passthrough.md`](plans/cache-control-passthrough.md) | Per-block passthrough of Anthropic `cache_control` (landed) |
| [`plans/2026-07-08-credential-connection-unification.md`](plans/2026-07-08-credential-connection-unification.md) | Credential/connection unification migration |

## Removed docs

There is no `archive/` directory: superseded docs were deleted outright rather
than moved aside. Recover them from git history if needed.

Previously removed:
- `model-catalog-dynamic.md` / `model-catalog-per-provider.md` — iteration drafts, superseded by `models.md`
- `claude-code-meridian-profile.md` — the Meridian proxy was dropped; purely historical
- `*-references.md` — investigation snapshots / raw research notes (slash-commands / tui-upgrade / user-input-requests)

## TODO-doc-code-gaps.md

[`TODO-doc-code-gaps.md`](TODO-doc-code-gaps.md) — Places where the docs and the code disagree, ordered by priority. Delete an entry once it is fixed.

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
