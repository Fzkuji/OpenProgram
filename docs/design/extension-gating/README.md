# Extension Gating

Single-source design doc for how OpenProgram controls which extensions (tools, skills, MCP servers) the LLM can use, scoped by agent profile.

## What this folder covers

| File | Read it when |
|---|---|
| [README.md](./README.md) | first time — high-level model + agent.json schema + examples |
| [reference-comparison.md](./reference-comparison.md) | considering a design change — see how claude-code / opencode / hermes do it |
| [implementation.md](./implementation.md) | touching the code — file paths, helper module, gate sites |
| [future-work.md](./future-work.md) | explicitly noted as "not built and not on the roadmap unless someone asks" |

For the broader skills+plugins design (catalogues, discovery, hot reload, etc.) see [`../skills-and-plugins.md`](../skills-and-plugins.md). This folder is the **gating** subset only.

---

## TL;DR

Every extension type the LLM directly sees is gated through the **same shape** in the agent profile:

```yaml
# agent.json
tools:
  disabled: ["bash", "*_dangerous"]   # fnmatch patterns
  allowed:  []                         # whitelist; empty = no constraint
skills:
  disabled: ["devops/*"]
  allowed:  []
  categories: ["research", "writing"]  # skill-only: gate by frontmatter category
mcp:
  disabled: ["slack/*"]                # filters MCP tools named "<server>__<tool>"
  allowed:  []
  required: ["drawio*"]                # agent unavailable when nothing matches
```

Plugins are **not** in this list — plugin is a host-level "contributor". Gating happens on what a plugin contributes (skills / tools / MCP), not on the plugin itself.

---

## The unified model

```
┌────────────────────────────────────────────────────────────────┐
│                       agent profile                            │
│                                                                │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐                 │
│   │ tools    │    │ skills   │    │ mcp      │  ← same shape   │
│   └─────┬────┘    └─────┬────┘    └─────┬────┘                 │
│         │               │               │                      │
│  ┌──────┴──────┐  ┌─────┴──────┐  ┌─────┴──────┐               │
│  │ disabled    │  │ disabled   │  │ disabled   │   fnmatch     │
│  │ allowed     │  │ allowed    │  │ allowed    │   pattern     │
│  │             │  │ categories │  │ required   │   lists       │
│  └─────────────┘  └────────────┘  └────────────┘               │
└────────────────────────────────────────────────────────────────┘
              │
              │  shared helper: openprogram/agents/gating.py
              │
              ▼
   match_any(name, patterns)          —  fnmatch wildcard match
   gate(name, category, disabled,     —  resolve decision
        allowed, categories)              returns reason str | None
   check_required(installed, required) —  list missing patterns
```

### Resolution order (per gate call)

For a single extension (one tool, one skill, one MCP server):

1. **disabled** — if matched, reject outright with reason
2. **allowed** (non-empty) — if not matched, reject (allowlist mode)
3. **categories** (non-empty, skill only) — if item's category not matched, reject
4. **required** (MCP only) — separate hard check: if any required pattern matches nothing installed, the whole agent is unavailable for this turn

Exact names are the trivial case of fnmatch (`"bash"` matches only `bash`). Wildcards (`*`, `?`, `[abc]`) work because we use `fnmatch.fnmatchcase`.

---

## Why this shape

We combined the **per-type field structure** from claude-code (`tools: []`, `disallowedTools: []`, `mcpServers: []`) with the **wildcard expressiveness** from opencode's `permission: Ruleset`. The result:

- Easy to read — each field name says what it gates
- Easy to write — `disabled: [anthropic-skills/*]` is one line, not five
- Backward compatible — old `{disabled: [pdf]}` profiles still work
- Trivial to extend — new extension type = new top-level field with the same {disabled, allowed} shape

See [reference-comparison.md](./reference-comparison.md) for the full claude-code / opencode side-by-side.

---

## Worked examples

### 1. Customer-service agent — chatty, no edit tools

```yaml
id: customer-service
tools:
  disabled: ["write", "edit", "execute_*", "apply_patch"]
skills:
  categories: ["customer-service", "writing"]
mcp:
  disabled: ["*"]   # no MCP at all
```

Effect: LLM sees only read-only + research tools, only skills tagged for customer service or writing, no MCP servers.

### 2. DevOps agent — everything except destructive prod skills

```yaml
id: devops
skills:
  disabled: ["prod-deploy", "drop-database"]
mcp:
  required: ["github*", "k8s*"]   # agent unavailable if these aren't configured
```

Effect: agent loads only if GitHub + k8s MCP are installed; otherwise the dispatcher skips it. Two specific skills are blacklisted.

### 3. Research agent — read-heavy, narrow MCP

```yaml
id: research
tools:
  allowed: ["read", "grep", "glob", "web_search", "web_fetch"]
skills:
  categories: ["research"]
mcp:
  allowed: ["arxiv*", "scholar*"]
```

Effect: tools collapsed to read/search five, only research-tagged skills, only arxiv/scholar MCPs.

---

## When NOT to use gating

Most users should never touch this. The Anthropic SKILL.md design philosophy is **LLM-mediated selection**: every skill has a `description` field, the model reads all of them and picks. Gating is an **escape hatch** for when:

- A skill is dangerous and you want a hard wall (`prod-deploy` never runs in customer-service agent)
- An agent role is so narrow that listing the permitted set is shorter than the description-tuning argument (research agent only ever needs 5 tools)
- A required MCP must exist for the agent to be coherent (research agent without `arxiv*` shouldn't even appear)

Default workflow: **install skills globally, configure no gates, let the LLM pick**. Add a gate when you observe a specific bad behavior, not preemptively.

---

## See also

- [reference-comparison.md](./reference-comparison.md) — how the three reference implementations compare
- [implementation.md](./implementation.md) — code paths and helper module
- [future-work.md](./future-work.md) — items intentionally not built
- [../function-calling-unification.md](../function-calling-unification.md) — broader 6-layer gating doc for the function-calling subsystem (Layers 2/3/5 are what this folder formalises for *all* extensions, not just tools)
- [../skills-and-plugins.md](../skills-and-plugins.md) — original skills + plugins design doc (covers catalogues, discovery, hot reload — not gating)
