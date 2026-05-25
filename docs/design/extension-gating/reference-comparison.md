# Reference-implementation comparison

How the three frameworks we studied control "which extensions an agent can use". Read this when considering a design change so you know what's already been tried.

## Side-by-side

| Aspect | **claude-code-leaked** | **opencode** | **hermes** | **OpenProgram (ours)** |
|---|---|---|---|---|
| Per-agent gating exists | ✅ | ✅ | partial (channel-level only) | ✅ |
| Mechanism | per-type explicit lists | single `permission: Ruleset` | YAML adapter config | per-type lists + fnmatch wildcards |
| Wildcards | ✗ exact names only | ✅ glob patterns | ✗ | ✅ fnmatch |
| Gated types | tools, skills, mcpServers, hooks | unified pattern space (`tools:*`, `mcp:*`, …) | platform adapters | tools, skills, mcp |
| Required-deps | `requiredMcpServers` | (via deny by default) | n/a | `mcp.required` |
| Plugin gating | trust level only | trust + permission ruleset | manifest perms | trust level (host-level, not per-agent) |

## claude-code-leaked

Source: `references/claude-code-leaked/src/tools/AgentTool/loadAgentsDir.ts:75-100`

```typescript
const AgentJsonSchema = z.object({
  description: z.string(),
  prompt: z.string(),
  tools: z.array(z.string()).optional(),              // whitelist
  disallowedTools: z.array(z.string()).optional(),    // blacklist
  skills: z.array(z.string()).optional(),             // names to preload
  mcpServers: z.array(AgentMcpServerSpecSchema).optional(),
  requiredMcpServers: z.array(z.string()).optional(), // patterns; missing = unavailable
  hooks: HooksSchema().optional(),
  permissionMode: z.enum(PERMISSION_MODES).optional(),
  ...
})
```

**Pattern**: each extension type gets its own list field. Lists are exact names — no wildcards. Reading is easy ("this agent uses these tools"), writing for broad cases is verbose.

**Where they go beyond us**:

- `mcpServers` can be either a *reference* to an existing server (`"slack"`) or an *inline definition* — agents can bring their own MCP config without registering it globally.
- `requiredMcpServers` makes the entire agent unavailable when missing (we adopted this as `mcp.required`).
- `hooks` is per-agent — they register session-scoped hooks at agent start. We have global hook dispatch via `openprogram/plugins/hooks.py` but no per-agent scoping yet.

## opencode

Source: `references/opencode/packages/opencode/src/agent/agent.ts:31-50` + `src/permission/index.ts:138-184`

```typescript
Info = Schema.Struct({
  name, description, mode, model, prompt,
  permission: Permission.Ruleset,    // single field gates everything
  ...
})

// Ruleset = list of {pattern, action: "allow" | "deny"}
// evaluated against permission keys like "tools:bash", "mcp:slack/*"
```

**Pattern**: one ruleset per agent, glob-matched against namespaced permission keys (`tools:`, `mcp:`, `skills:`). Each rule is a `{pattern, action}` pair; first match wins.

**Where they go beyond us**:

- Single source of truth — adding a new extension type means picking a namespace prefix (`prompts:*`); no schema change.
- Pattern composition — one rule can express what would take several entries in the per-type approach (`{pattern: "mcp:*", action: "deny"}` kills all MCP).
- Trade-off: more abstract — users have to learn pattern grammar.

**Why we didn't go opencode-style**: we already had `tools.disabled` per type for tools, and we added `skills` and `mcp` to match. Migrating to a single ruleset would have been pure refactor with no new capability except syntactic. Worth revisiting if we ever add a 4th gated type.

## hermes

Source: `references/hermes-agent/plugins/platforms/*/plugin.yaml`

Hermes is platform-adapter focused (Discord, Slack, ntfy, …). Each adapter ships a `plugin.yaml` with permissions, but it's **channel-level** (what this adapter is allowed to do on the network) not agent-level (what this agent role is allowed). No equivalent of agent-profile gating.

**We adopted from hermes**: nothing in this layer.

## Decision rationale (why we ended up here)

We started by mirroring claude-code's per-type field shape because:

1. We already had `AgentSpec.skills.disabled` and `AgentSpec.tools.disabled` from before this design conversation — sunk-cost favoured continuity.
2. The shape is self-documenting (`tools.disabled` says exactly what it does).
3. Adding `allowed` / `categories` / `required` to the same struct is mechanical.

Then we added fnmatch wildcards because:

1. Trivial to implement (single helper `match_any`).
2. Solves the verbosity complaint about pure-list approach without giving up the field-per-type clarity.
3. Backward compatible — exact names are the trivial case of fnmatch.

The result is "claude-code skeleton + opencode wildcards". If a future framework introduces a meaningfully different model, revisit this doc and consider migration.
