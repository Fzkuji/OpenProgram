# Future work — explicitly NOT built

Extensions to the gating model that are considered and **deliberately not built**. Recorded here so the next person with the same idea finds the reasoning instead of redoing the analysis.

The governing rule: **default to reference parity (claude-code / opencode / hermes); add new mechanisms only when there is a concrete user pain point**. Every item below fails that test.

If a user later hits one of these as a real problem, this doc gets the symptom added and the implementation proceeds.

---

## 1. Per-session gating toggle

**Idea**: let the user override the agent profile for one chat session — "for this conversation, also enable `prod-deploy`".

**Why it comes up**: occasionally an agent role's gating is too tight for a one-off task and editing the profile feels heavy.

**Why it stays unbuilt**:
- No reference framework has this. claude-code's `permissionMode` is per-agent, not per-session. opencode's ruleset is per-agent. hermes has no per-session escape hatch.
- The existing workflow is: edit `agent.json`, save, the next turn picks it up. Hot reload is fast.
- Adding a per-session override creates a state-management problem (where does the override live? does it survive page reload? does it leak between WS reconnects?) not worth solving until there is evidence the simpler "edit profile" workflow is insufficient.

**When to revisit**: if users start filing issues like "I keep editing agent.json back and forth for this one debugging task".

---

## 2. Plugin subprocess sandbox

**Idea**: run plugin code in a subprocess with restricted FS / network access — `subprocess.Popen` with seccomp-bpf or similar.

**Why it comes up**: plugins ship arbitrary Python; a malicious plugin could exfiltrate API keys or rm -rf the workspace.

**Why it stays unbuilt**:
- claude-code, opencode, hermes all load plugins in-process. None sandbox plugin code.
- Plugin **trust levels** already exist (`plugin.json` declares `trust: "verified" | "community"`), and dangerous capabilities are gated at the trust level rather than at OS level.
- Real sandboxing on macOS / Windows is genuinely hard — seccomp is Linux-only, App Sandbox is macOS-only, neither covers the cross-platform development case.
- The threat model is not strong enough to justify the engineering cost: plugin install is an explicit user action with a manifest review step.

**When to revisit**: if **automatic** plugin install is ever supported (e.g. an LLM that decides to install a plugin mid-conversation), the threat model changes and a sandbox becomes worth the cost.

---

## 3. Skill `requires` chain

**Idea**: a SKILL.md frontmatter `requires: [other-skill]` field — invoking `prod-deploy` automatically loads `kubectl-helpers` first.

**Why it comes up**: skills sometimes depend on other skills' context. Today a user has to chain `/skill A /skill B` manually.

**Why it stays unbuilt**:
- No reference framework has it. Anthropic's SKILL.md spec has no `requires` field.
- LLM-mediated selection assumes the model reads all SKILL descriptions and picks. If skill A "needs" skill B, the right answer is usually "merge them" or "make A's description say it works best together with B" — not adding a dep graph the user has to maintain.
- Dependency resolution introduces ordering questions (are deps loaded before the parent?), conflict questions (two skills requiring incompatible third skills), and version questions that exist nowhere else in the codebase.

**When to revisit**: if skill authors start documenting "use this skill together with X" in human-readable form repeatedly, that is a real signal that a `requires` field would replace boilerplate.

---

## 4. Hook-level gating

**Idea**: claude-code's `hooks` per-agent field — register PreToolUse / PostToolUse handlers scoped to one agent profile, not globally.

**Why it comes up**: hooks (`openprogram/plugins/hooks.py`) dispatch to all loaded plugins. There is no way to say "this agent runs this hook, that agent doesn't."

**Why it stays unbuilt**:
- Hook dispatch exists but is barely used — only `chat.before_send` and `tool.before_use` are wired. Adding per-agent scoping before the global mechanism is exercised would be premature.
- claude-code's per-agent hooks exist but their documentation suggests most users register hooks via plugins (host-level), not per-agent.
- The unified gating model already covers the **what tools/skills/MCP** question. Hooks are a **how does the call go through** question — orthogonal. Adding per-agent hooks does not change the gating model, it only adds another knob.

**When to revisit**: when someone writes a plugin that needs to behave differently per agent. Likely months out.

---

## 5. Single-ruleset migration (opencode-style)

**Idea**: replace the three blocks (`tools`, `skills`, `mcp`) with a single `permission: [{pattern, action}]` list.

**Why it comes up**: cleaner, single source of truth, easier to add a 4th extension type later.

**Why it stays unbuilt**:
- The per-type field shape is self-documenting. Migration would be a pure refactor with no new capability except syntactic.
- Backward compatibility cost — every agent profile in users' `~/.openprogram/agents/` would need migration code or a deprecation cycle.
- Opencode's ruleset is **first-match-wins**, which means rule ordering matters. The per-type structure has no ordering concerns (each block is independent), which removes one class of mistake.

**When to revisit**: when a 5th or 6th gated extension type makes the schema feel bloated. At 3 types the per-type shape still pays for itself.

---

## How to revisit any of these

1. Find the symptom — a user complaint, a real incident, or a feature request with a concrete use case.
2. Cross-reference with this doc for the original reasoning.
3. If the original reasoning is invalidated (e.g. "no reference has this" is no longer true because opencode added it last month, or "no user pain" is no longer true after several issues about it), proceed with implementation and update this doc with the new context.
4. If the original reasoning still holds, the analysis here is the answer to the request.
