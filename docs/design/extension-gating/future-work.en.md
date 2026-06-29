# Future work — explicitly NOT built

Items considered during the design conversation that we **chose not to build**. Recording them here so the next person who has the same idea finds the previous reasoning and doesn't redo the analysis from scratch.

The rule we adopted partway through this design conversation: **default to reference parity (claude-code / opencode / hermes); add new mechanisms only when there is a concrete user pain point**. The items below all failed that test at the time they were proposed.

If a user later hits one of these as a real problem, this doc should be updated with the symptom, and the implementation should proceed.

---

## 1. Per-session gating toggle

**Idea**: let the user override the agent profile for one chat session — "for this conversation, also enable `prod-deploy`".

**Why proposed**: occasionally an agent role's gating is too tight for a one-off task and editing the profile feels heavy.

**Why we didn't build it**:
- No reference framework has this. claude-code's `permissionMode` is per-agent, not per-session. opencode's ruleset is per-agent. hermes has no per-session escape hatch.
- The existing workflow is: edit `agent.json`, save, the next turn picks it up. Hot reload is fast.
- Adding a per-session override creates a state-management problem (where does the override live? does it survive page reload? does it leak between WS reconnects?) we don't want to solve until there's evidence the simpler "edit profile" workflow is insufficient.

**When to revisit**: if users start filing issues like "I keep editing agent.json back and forth for this one debugging task".

---

## 2. Plugin subprocess sandbox

**Idea**: run plugin code in a subprocess with restricted FS / network access — `subprocess.Popen` with seccomp-bpf or similar.

**Why proposed**: plugins ship arbitrary Python; a malicious plugin could exfiltrate API keys or rm -rf the workspace.

**Why we didn't build it**:
- claude-code, opencode, hermes all load plugins in-process. None sandbox plugin code.
- We already have plugin **trust levels** (`plugin.json` declares `trust: "verified" | "community"`), and we gate dangerous capabilities at the trust level rather than at OS level.
- Real sandboxing on macOS / Windows is genuinely hard — seccomp is Linux-only, App Sandbox is macOS-only, neither covers the cross-platform development case.
- The threat model isn't strong enough to justify the engineering cost: plugin install is an explicit user action with a manifest review step.

**When to revisit**: if we ever support **automatic** plugin install (e.g. an LLM that decides to install a plugin mid-conversation), the threat model changes and a sandbox becomes worth the cost.

---

## 3. Skill `requires` chain

**Idea**: a SKILL.md frontmatter `requires: [other-skill]` field — invoking `prod-deploy` automatically loads `kubectl-helpers` first.

**Why proposed**: skills sometimes depend on other skills' context. Today the user has to chain `/skill A /skill B` manually.

**Why we didn't build it**:
- Not in any reference framework. Anthropic's SKILL.md spec has no `requires` field.
- The LLM-mediated selection model assumes the model reads all SKILL descriptions and picks. If skill A "needs" skill B, the right answer is usually "merge them" or "make A's description say it works best together with B" — not adding a dep graph the user has to maintain.
- Dep resolution introduces ordering questions (deps loaded before parent?), conflict questions (two skills require incompatible third skills?), and version questions we don't currently have anywhere else in the codebase.

**When to revisit**: if skill authors start documenting "use this skill together with X" in human-readable form repeatedly, we have a real signal that a `requires` field would replace boilerplate.

---

## 4. Hook-level gating

**Idea**: claude-code's `hooks` per-agent field — register PreToolUse / PostToolUse handlers scoped to one agent profile, not globally.

**Why proposed**: today our hooks (`openprogram/plugins/hooks.py`) dispatch to all loaded plugins. There's no way to say "this agent runs this hook, that agent doesn't."

**Why we didn't build it**:
- We have hook dispatch but limited use of it — only `chat.before_send` and `tool.before_use` are wired. Adding per-agent scoping before the global mechanism is exercised is premature.
- claude-code's per-agent hooks exist but their documentation suggests most users register hooks via plugins (host-level), not per-agent.
- The unified gating model (this folder) already covers the **what tools/skills/MCP** question. Hooks are a **how does the call go through** question — orthogonal. Adding per-agent hooks doesn't change the gating model, just adds another knob.

**When to revisit**: when someone writes a plugin that needs to behave differently per agent. Likely months out.

---

## 5. Single-ruleset migration (opencode-style)

**Idea**: replace the three blocks (`tools`, `skills`, `mcp`) with a single `permission: [{pattern, action}]` list.

**Why proposed**: cleaner, single source of truth, easier to add a 4th extension type later.

**Why we didn't build it**:
- The per-type field shape is already self-documenting. Migration would be pure refactor with no new capability except syntactic.
- Backward compatibility cost — every agent profile currently in users' `~/.openprogram/agents/` would need migration code or a deprecation cycle.
- Opencode's ruleset is **first-match-wins**, which means rule ordering matters. The per-type structure has no ordering concerns (each block is independent), which is one less footgun.

**When to revisit**: when we add a 5th or 6th gated extension type and the schema feels bloated. Today we have 3, which is the limit before it stops paying for itself.

---

## How to revisit any of these

1. Find the symptom — a user complaint, a real incident, or a feature request with a concrete use case.
2. Cross-reference with this doc to see what the original reasoning was.
3. If the original reasoning is invalidated (e.g. "no reference has this" is no longer true because opencode added it last month, or "no user pain" is no longer true because we got 5 issues about it), proceed with implementation and update this doc to record the new context.
4. If the original reasoning still holds, push back on the request with the analysis here.
