# Design and implementation documents

This directory maintains subsystem designs, implementation locations and remaining
boundaries. Ownership spans both `openprogram/` and `apps/`; the core package alone
is not a complete implementation map.

## Reading entry points

| Question | Document |
|---|---|
| Where are subsystem designs and implementation status? | [Implementation navigation](implementation-status.html) |
| Which directories own implementation and compatibility? | [Repository design](repository-structure.html) and [implementation map](repository-structure-implementation.html) |
| How does a conversation execute? | [Framework overview](framework-overview.md) |
| Which verification layers apply? | [Test system](testing/test-system.html) |
| How should HTML implementation documents be authored and verified? | [HTML rendering and authoring](docs-site.html) |

Design bodies explain behavior and constraints. An implementation-status appendix
records implemented, partial, unimplemented and out-of-scope items. Source presence,
passing tests, publication and installed-App acceptance are separate states. This
index routes readers to topic owners without maintaining another completion score.

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
| [`proactive/event-layer.md`](proactive/event-layer.md) | Unified Event model, framework placement, diagram, event boundaries ([visualization](proactive/event-layer.html)) |
| [`proactive/framework-evolution.md`](proactive/framework-evolution.md) | Framework evolution: current → target → five migration steps ([visualization](proactive/framework-evolution.html)) |

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
| [`runtime/execution/execution-control.html`](runtime/execution/execution-control.html) | **Authoritative** unified execution control: pause, continue, step, steering, cancellation, checkpoint, revision, recovery, and surface synchronization |
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
| [`runtime/self-update.html`](runtime/self-update.html) | Conversational self-update: owner approval, candidate activation, verification, and recovery |
| [`runtime/goal-framework-implementation-comparison.html`](runtime/goal-framework-implementation-comparison.html) | **Authoritative Goal design**: native implementation comparison, controller and state flow, async questions, hard-task stopping, restart recovery, Web/TUI surfaces, and implementation evidence |
| [`runtime/sandbox-architecture.html`](runtime/sandbox-architecture.html) | Canonical execution-security design: authority tiers, permission modes and approval, host sandbox boundaries, framework comparison, and implementation evidence |
| [`runtime/permission-model.md`](runtime/permission-model.md) / [`runtime/sandbox.md`](runtime/sandbox.md) | Stable link targets that point to the canonical execution-security design |
| [`runtime/ssrf-protection.html`](runtime/ssrf-protection.html) | Outbound URL and SSRF design: current gaps, Hermes/OpenClaw/OWASP comparison, scoped trust policy, transport requirements, and full acceptance gates |
| [`runtime/agent-collaboration.md`](runtime/agent-collaboration.md) | Agent collaboration: cross-branch communication primitives ([tool surface](runtime/agent-collab-architecture.html), [eight reference implementations compared](runtime/agent-collab-comparison.html)) |
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
| [`ui/attachment-handling.html`](ui/attachment-handling.html) | Complete attachment design, framework comparison, and implementation contract |
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
| [`docs-site.html`](docs-site.html) | The documentation site itself (build, nav, bilingual routing) |
| [`repository-structure.html`](repository-structure.html) | Repository boundaries, long-file split policy, and documentation information architecture |
| [`repository-structure-implementation.html`](repository-structure-implementation.html) | Source ownership, compatibility boundaries and verification for repository structure |

## research/ — investigations

| Doc | Topic |
|---|---|
| [`research/execution-trace-model-selection.md`](research/execution-trace-model-selection.md) | Choosing the data model for agent execution traces (span concept, what's novel) |

## distribution/ — installation, packaging, and updates

| Doc | Topic |
|---|---|
| [`distribution/installation-packaging.html`](distribution/installation-packaging.html) | Complete-product installation, packaging, platform support, and release artifacts |
| [`distribution/automatic-updates.html`](distribution/automatic-updates.html) | Stable Release discovery, verified macOS/Windows Desktop installer handoff, managed CLI atomic activation, trust boundaries, UI states, and implementation evidence |
| [`distribution/implementation-plan.md`](distribution/implementation-plan.md) | Historical distribution implementation evidence not duplicated by the current designs |

## plans/ — supporting implementation plans

| Doc | Topic |
|---|---|
| [`plans/proactive-implementation.md`](plans/proactive-implementation.md) | Proactive layer implementation plan |
| [`plans/cache-control-passthrough.md`](plans/cache-control-passthrough.md) | Per-block passthrough of Anthropic `cache_control` |
| [`plans/2026-07-08-credential-connection-unification.md`](plans/2026-07-08-credential-connection-unification.md) | Credential/connection unification migration |

## TODO-doc-code-gaps.md

[`TODO-doc-code-gaps.md`](TODO-doc-code-gaps.md) — Places where the docs and the code disagree, ordered by priority. Delete an entry once it is fixed.

## Conventions

- Maintain one current design per topic. Supporting implementation notes link to it rather than copying its body.
- Use present tense; retrieve historical commits, dates and review rounds from Git.
- Implementation notes explain entry points, source ownership, data and state transitions, failure handling, compatibility and verification, followed by an implementation-status appendix.
- Every published page is HTML. Prefer native HTML for implementation documents with diagrams, state and evidence structures; short usage text may remain Markdown source.
- Default `.md` / `.html` pages are entirely English; `.zh.md` / `.zh.html` pages are Chinese counterparts. Update English first, then synchronize Chinese.
- API documentation belongs under `docs/reference/api/`; product usage belongs in the relevant product tab.
- Use relative document links within the site and GitHub links for repository source.
- Rebuild the site before running `python -m scripts.docs_site.checklinks`.
