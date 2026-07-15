# 参考实现对比

我们研究过的三个框架是如何控制"某个 agent 可以使用哪些扩展"的。在考虑设计变更之前阅读本文，这样你就能知道哪些方案已经尝试过了。

## 横向对比

| 维度 | **claude-code-leaked** | **opencode** | **hermes** | **OpenProgram（我们）** |
|---|---|---|---|---|
| 是否存在按 agent 的门控 | ✅ | ✅ | 部分（仅 channel 级别） | ✅ |
| 机制 | 按类型的显式列表 | 单个 `permission: Ruleset` | YAML adapter 配置 | 按类型的列表 + fnmatch 通配符 |
| 通配符 | ✗ 仅精确名称 | ✅ glob 模式 | ✗ | ✅ fnmatch |
| 被门控的类型 | tools、skills、mcpServers、hooks | 统一的模式空间（`tools:*`、`mcp:*`、…） | platform adapter | tools、skills、mcp |
| 必需依赖 | `requiredMcpServers` | （通过默认 deny） | 无 | `mcp.required` |
| 插件门控 | 仅信任级别 | 信任级别 + permission ruleset | manifest 权限 | 信任级别（host 级别，非按 agent） |

## claude-code-leaked

来源：`references/claude-code-leaked/src/tools/AgentTool/loadAgentsDir.ts:75-100`

```typescript
const AgentJsonSchema = z.object({
  description: z.string(),
  prompt: z.string(),
  tools: z.array(z.string()).optional(),              // 白名单
  disallowedTools: z.array(z.string()).optional(),    // 黑名单
  skills: z.array(z.string()).optional(),             // 需预加载的名称
  mcpServers: z.array(AgentMcpServerSpecSchema).optional(),
  requiredMcpServers: z.array(z.string()).optional(), // 模式；缺失 = 不可用
  hooks: HooksSchema().optional(),
  permissionMode: z.enum(PERMISSION_MODES).optional(),
  ...
})
```

**模式**：每种扩展类型都有自己的列表字段。列表是精确名称——没有通配符。阅读很容易（"这个 agent 用了这些工具"），但在覆盖宽泛场景时书写很啰嗦。

**它们比我们做得更进一步的地方**：

- `mcpServers` 既可以是对一个现有 server 的*引用*（`"slack"`），也可以是一个*内联定义*——agent 可以自带 MCP 配置，而无需全局注册。
- `requiredMcpServers` 在缺失时会让整个 agent 不可用（我们以 `mcp.required` 的形式采纳了这一点）。
- `hooks` 是按 agent 的——它们在 agent 启动时注册 session 作用域的 hook。我们通过 `openprogram/plugins/hooks.py` 提供了全局 hook 分发，但尚无按 agent 的作用域。

## opencode

来源：`references/opencode/packages/opencode/src/agent/agent.ts:31-50` + `src/permission/index.ts:138-184`

```typescript
Info = Schema.Struct({
  name, description, mode, model, prompt,
  permission: Permission.Ruleset,    // 单个字段门控一切
  ...
})

// Ruleset = {pattern, action: "allow" | "deny"} 的列表
// 针对 "tools:bash"、"mcp:slack/*" 这类权限键进行求值
```

**模式**：每个 agent 一份 ruleset，按 glob 匹配带命名空间的权限键（`tools:`、`mcp:`、`skills:`）。每条规则是一个 `{pattern, action}` 对；首条匹配生效。

**它们比我们做得更进一步的地方**：

- 单一事实来源——新增一种扩展类型只需选定一个命名空间前缀（`prompts:*`）；无需改 schema。
- 模式组合——一条规则就能表达在按类型方案里需要好几条才能表达的内容（`{pattern: "mcp:*", action: "deny"}` 会禁用所有 MCP）。
- 权衡：更抽象——用户必须学习模式语法。

**我们为什么没有采用 opencode 风格**：我们已经为 tools 提供了按类型的 `tools.disabled`，并且为对齐又加上了 `skills` 和 `mcp`。迁移到单一 ruleset 将是纯粹的重构，除了语法上的收益之外没有新能力。如果我们将来新增第 4 种被门控的类型，值得重新考虑。

## hermes

来源：`references/hermes-agent/plugins/platforms/*/plugin.yaml`

Hermes 以 platform adapter 为中心（Discord、Slack、ntfy、…）。每个 adapter 都附带一个带权限的 `plugin.yaml`，但它是 **channel 级别**的（该 adapter 在网络上被允许做什么），而非 agent 级别的（该 agent 角色被允许做什么）。没有与 agent-profile 门控对应的东西。

**我们从 hermes 采纳了什么**：在这一层没有采纳任何东西。

## 决策依据（我们为什么走到了这里）

我们一开始照搬了 claude-code 的按类型字段形态，因为：

1. 在这次设计讨论之前，我们就已经有了 `AgentSpec.skills.disabled` 和 `AgentSpec.tools.disabled`——沉没成本偏向于延续。
2. 这种形态是自解释的（`tools.disabled` 准确地说明了它的作用）。
3. 往同一个结构体里加 `allowed` / `categories` / `required` 是机械化的工作。

随后我们加上了 fnmatch 通配符，因为：

1. 实现起来很简单（单个辅助函数 `match_any`）。
2. 在不放弃"每类型一个字段"清晰性的前提下，解决了纯列表方案啰嗦的问题。
3. 向后兼容——精确名称是 fnmatch 的平凡情形。

最终结果是"claude-code 骨架 + opencode 通配符"。如果未来某个框架引入了一个有实质差异的模型，回头看本文并考虑迁移。
