# Reference-implementation comparison

How three reference frameworks control "which extensions an agent can use". Read this when considering a design change, to see what has already been tried.

## Side-by-side

| Aspect | **claude-code-leaked** | **opencode** | **hermes** | **OpenProgram** |
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

**Where it goes further**:

- `mcpServers` can be either a *reference* to an existing server (`"slack"`) or an *inline definition* — agents can bring their own MCP config without registering it globally.
- `requiredMcpServers` makes the entire agent unavailable when missing — adopted here as `mcp.required`.
- `hooks` is per-agent — session-scoped hooks registered at agent start. OpenProgram has global hook dispatch via `openprogram/plugins/hooks.py` and no per-agent scoping.

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

**Where it goes further**:

- Single source of truth — adding a new extension type means picking a namespace prefix (`prompts:*`), with no schema change.
- Pattern composition — one rule can express what would take several entries in the per-type approach (`{pattern: "mcp:*", action: "deny"}` kills all MCP).
- Trade-off: more abstract — users have to learn pattern grammar.

**Why not opencode-style**: `tools.disabled` already exists per type, and `skills` and `mcp` follow the same shape. Migrating to a single ruleset would be a pure refactor with no new capability except syntactic. Worth revisiting if a 4th gated type is added.

## hermes

Source: `references/hermes-agent/plugins/platforms/*/plugin.yaml`

Hermes is platform-adapter focused (Discord, Slack, ntfy, …). Each adapter ships a `plugin.yaml` with permissions, but it's **channel-level** (what this adapter is allowed to do on the network) not agent-level (what this agent role is allowed). No equivalent of agent-profile gating.

**Adopted from hermes**: nothing in this layer.

## Design rationale

The model mirrors claude-code's per-type field shape because:

1. `AgentSpec.skills.disabled` and `AgentSpec.tools.disabled` already exist, so continuity costs nothing.
2. The shape is self-documenting (`tools.disabled` says exactly what it does).
3. Adding `allowed` / `categories` / `required` to the same struct is mechanical.

fnmatch wildcards sit on top of that shape because:

1. They are trivial to implement (a single helper, `match_any`).
2. They solve the verbosity of a pure-list approach without giving up field-per-type clarity.
3. They are backward compatible — exact names are the trivial case of fnmatch.

The result is a claude-code skeleton with opencode wildcards. If a future framework introduces a meaningfully different model, revisit this doc and consider migration.
