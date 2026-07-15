# 扩展门控（Extension Gating）

单一来源的设计文档，说明 OpenProgram 如何按 agent profile 控制 LLM 可使用哪些扩展（工具、技能、MCP 服务器）。

## 本目录涵盖的内容

| 文件 | 何时阅读 |
|---|---|
| [README.md](./README.md) | 第一次阅读 —— 高层模型 + agent.json schema + 示例 |
| [reference-comparison.md](./reference-comparison.md) | 考虑改动设计时 —— 看看 claude-code / opencode / hermes 是怎么做的 |
| [implementation.md](./implementation.md) | 改动代码时 —— 文件路径、辅助模块、门控落点 |
| [future-work.md](./future-work.md) | 明确标注为“尚未构建，且除非有人提出否则不在路线图上” |

关于更广义的 skills+plugins 设计（目录、发现、热重载等），见 [`../skills-and-plugins.md`](../integrations/skills-and-plugins.md)。本目录只覆盖**门控**这一子集。

---

## TL;DR

LLM 直接看到的每一种扩展类型，都在 agent profile 中通过**相同的结构**进行门控：

```yaml
# agent.json
tools:
  disabled: ["bash", "*_dangerous"]   # fnmatch 模式
  allowed:  []                         # 白名单；为空表示无约束
skills:
  disabled: ["devops/*"]
  allowed:  []
  categories: ["research", "writing"]  # 仅 skill：按 frontmatter category 门控
mcp:
  disabled: ["slack/*"]                # 过滤名为 "<server>__<tool>" 的 MCP 工具
  allowed:  []
  required: ["drawio*"]                # 无任何匹配时该 agent 不可用
```

Plugin **不在**这个列表中 —— plugin 是宿主层面的“贡献者”。门控作用于 plugin 所贡献的东西（skills / tools / MCP），而非 plugin 本身。

---

## 统一模型

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

### 解析顺序（每次门控调用）

对于单个扩展（一个工具、一个技能、一个 MCP 服务器）：

1. **disabled** —— 若匹配，直接拒绝并给出原因
2. **allowed**（非空）—— 若不匹配，则拒绝（白名单模式）
3. **categories**（非空，仅 skill）—— 若该项的 category 不匹配，则拒绝
4. **required**（仅 MCP）—— 单独的硬性检查：若任一 required 模式匹配不到任何已安装项，则本轮整个 agent 不可用

精确名称是 fnmatch 的退化情形（`"bash"` 只匹配 `bash`）。通配符（`*`、`?`、`[abc]`）之所以可用，是因为我们使用了 `fnmatch.fnmatchcase`。

---

## 为什么是这个结构

我们把 claude-code 的**按类型分字段结构**（`tools: []`、`disallowedTools: []`、`mcpServers: []`）与 opencode 的 `permission: Ruleset` 的**通配符表达力**结合在了一起。结果是：

- 易读 —— 每个字段名都说明了它门控的是什么
- 易写 —— `disabled: [anthropic-skills/*]` 一行搞定，而不用写五行
- 向后兼容 —— 旧的 `{disabled: [pdf]}` profile 仍然可用
- 极易扩展 —— 新增一种扩展类型 = 新增一个顶层字段，沿用相同的 {disabled, allowed} 结构

完整的 claude-code / opencode 并排对比见 [reference-comparison.md](./reference-comparison.md)。

---

## 实战示例

### 1. 客服 agent —— 善于对话，无编辑类工具

```yaml
id: customer-service
tools:
  disabled: ["write", "edit", "execute_*", "apply_patch"]
skills:
  categories: ["customer-service", "writing"]
mcp:
  disabled: ["*"]   # 完全不启用 MCP
```

效果：LLM 只看到只读类 + 检索类工具，只看到标记为客服或写作的技能，没有任何 MCP 服务器。

### 2. DevOps agent —— 除破坏性生产技能外全部启用

```yaml
id: devops
skills:
  disabled: ["prod-deploy", "drop-database"]
mcp:
  required: ["github*", "k8s*"]   # 若未配置这些则该 agent 不可用
```

效果：仅当安装了 GitHub + k8s MCP 时该 agent 才加载；否则 dispatcher 会跳过它。两个特定技能被列入黑名单。

### 3. 研究 agent —— 偏重读取，MCP 范围狭窄

```yaml
id: research
tools:
  allowed: ["read", "grep", "glob", "web_search", "web_fetch"]
skills:
  categories: ["research"]
mcp:
  allowed: ["arxiv*", "scholar*"]
```

效果：工具收缩为读取/检索五个，只看到标记为 research 的技能，只看到 arxiv/scholar 的 MCP。

---

## 何时不应使用门控

大多数用户永远都不该碰这个。Anthropic SKILL.md 的设计哲学是**由 LLM 居中选择**：每个技能都有 `description` 字段，模型读完全部后自行挑选。门控是一个**逃生口**，适用于以下情形：

- 某个技能很危险，你想要一道硬墙（`prod-deploy` 在客服 agent 中永远不会运行）
- 某个 agent 的角色极其狭窄，以至于直接列出允许集合比反复调教 description 还要简短（研究 agent 永远只需要 5 个工具）
- 某个 required MCP 必须存在，agent 才说得通（没有 `arxiv*` 的研究 agent 干脆就不该出现）

默认工作流：**全局安装技能、不配置任何门控、让 LLM 自行挑选**。只在你观察到某个具体的不良行为时才加门控，不要预防性地加。

---

## 另见

- [reference-comparison.md](./reference-comparison.md) —— 三个参考实现之间如何对比
- [implementation.md](./implementation.md) —— 代码路径与辅助模块
- [future-work.md](./future-work.md) —— 有意未构建的条目
- [../function-calling-unification.md](../function/function-calling-unification.md) —— 针对 function-calling 子系统的更广义 6 层门控文档（本目录所形式化的，正是其中针对*所有*扩展而非仅工具的第 2/3/5 层）
- [../skills-and-plugins.md](../integrations/skills-and-plugins.md) —— 最初的 skills + plugins 设计文档（涵盖目录、发现、热重载 —— 不含门控）
