# Slash Commands — Reference Implementations Snapshot

调研于 2026-05-25。每个项目记录：来源目录、frontmatter 字段、模板语法、执行模式、参数解析、UI、安全。

参考项目都跟随上游持续演进。当下游想再次同步时，回到这里逐项 diff，把新增设计抽进 `slash-commands.md` 的字段表。

参考路径：`/Users/fzkuji/Documents/LLM Agent Harness/OpenProgram/references/`

---

## 1. claude-code-leaked

**位置**：`references/claude-code-leaked/src/skills/`, `src/utils/argumentSubstitution.ts`, `src/types/command.ts`

### 来源 / 加载顺序

`loadSkillsDir.ts:638-714`：

```
managed (策略下发)
  ↓ 后者覆盖前者
user      ~/.claude/skills/
  ↓
project   .claude/skills/  （从 cwd 向上递归）
  ↓
legacy commands
```

- 并行加载
- `realpath` 去重防 symlink 重复
- `conditionalSkills: Map<sessionId, Skill[]>` 存条件激活的（`paths` 字段）
- lodash memoize 缓存；`clearSkillCaches()` 清除；**无 watch**

### Frontmatter 字段 (`src/utils/frontmatterParser.ts:10-59`)

| 字段 | 类型 | 默认 |
|---|---|---|
| allowed-tools | string[] | 无 |
| description | string | "" |
| argument-hint | string | "" |
| when-to-use | string | "" |
| version | semver | null |
| hide-from-slash-command-tool | "true"\|"false" | false |
| model | "haiku"\|"opus"\|"inherit" | inherit |
| skills | string (逗号分隔) | "" |
| user-invocable | "true"\|"false" | commands/ 下 true, skills/ 下 false |
| hooks | HooksSettings (含 PreToolUse 等) | {} |
| effort | "low"\|"medium"\|"high"\|"max"\|int | inherit |
| context | "inline"\|"fork" | inline |
| agent | string | "general-purpose" |
| paths | string \| string[] | null |
| shell | "bash"\|"powershell" | inherit |

### 命令体语法 (`loadSkillsDir.ts:344-399`, `argumentSubstitution.ts`)

- `$ARGUMENTS`
- `$0`-`$9`（位置）
- `$name`（命名参数）
- `${CLAUDE_SKILL_DIR}`
- `${CLAUDE_SESSION_ID}`
- `` !`cmd` `` 反引号执行 shell；MCP 上下文里跳过
- `` ```! ` ` 代码块同 `` !`...` ``
- **不支持** `@file` 引用、不支持调用其它命令

### 参数解析 (`argumentSubstitution.ts:24-68`)

- `tryParseShellCommand` 优先（支持引号 + 转义）
- 失败 fallback 到 whitespace split
- 空参数返回 `[]`，模板里的 `$0..$9` → 空字符串
- 数字命名参数被拒（与 `$0` 冲突）

### 执行模式 (`src/types/command.ts:25-57`)

```
type: prompt        → ContentBlockParam, getPromptForCommand 动态生成
type: local         → Promise<LocalCommandResult>; 支持 skip (不显示消息)
type: local-jsx     → React (ink) 组件, onDone 回调
context: inline     → 展开进当前会话
context: fork       → 子代理跑 (agent 字段决定 subagent_type)
```

### MCP 集成 (`src/skills/mcpSkillBuilders.ts`)

MCP 工具自动变成 slash，source=`"mcp"`，命名 `mcp:tool-name`，显示 `/mcp:tool-name (MCP) ...`。

### 冲突

后加载者覆盖（managed < user < project）。同 realpath 文件去重，保留首个。

### 安全

- `realpath` + trusted_roots 白名单
- YAML 特殊字符预处理：glob 模式自动加引号（`quoteProblematicValues`）
- shell 选择文件级（不读 settings.defaultShell）

### 独有

- `paths` 条件激活
- `context: fork` 子代理
- `effort` 推理强度
- `hooks` 嵌入命令文件
- `hide-from-slash-command-tool`（hidden）
- 自动 MCP slash 暴露

---

## 2. opencode

**位置**：`references/opencode/packages/opencode/src/config/command.ts`

### 来源

```
Glob: {command,commands}/**/*.md  （绝对路径）
```

Effect Schema 验证 frontmatter + content 合并；无去重；无 watch；无条件激活。

### Frontmatter（极简，仅 4 字段）

| 字段 | 类型 |
|---|---|
| template | string (必填) |
| description | string |
| agent | string |
| model | ConfigModelID |
| subtask | boolean |

### 模板

纯文本展开；`$ARGUMENTS` + `$0-9` 位置参数；frontmatter `arguments:` 字段声明列表。

### 独有

- Effect 类型安全最强
- `subtask: true` 简化版的 claude-code fork
- MCP skill 返回 lazy Promise

---

## 3. openclaw

**位置**：`references/openclaw/src/auto-reply/commands-registry.*`

### 来源

数据驱动 hardcoded 注册表，**无文件扫描**。提供商特定映射（Slack vs Mattermost）。

### 字段 (`commands-registry.types.ts`)

```
ChatCommandDefinition:
  key | string               # 内部 ID
  nativeName | string        # Slack/Mattermost 名称
  nativeAliases? | string[]
  description | string
  descriptionLocalizations? | Map<lang, desc>
  scope | "text" | "native"
  args? | CommandArgDefinition[]
  acceptsArgs | boolean
```

### 独有

- 多提供商路由（resolveNativeName，pluginProvider hook）
- i18n (descriptionLocalizations)
- 参数选项菜单（CommandArgChoiceContext）—— 类似 Discord slash 的 choice dropdown

### SKILL 系统

`SKILL.md` + frontmatter 包含 `emoji`、`requires.anyBins`、`requires.config`、`install[]`（依赖检查 + 安装指导）。

---

## 4. hermes-agent

**位置**：`references/hermes-agent/tools/skills_tool.py`, `agent/skill_commands.py`, `agent/skill_preprocessing.py`

### 来源

```
~/.hermes/skills/   单一目录
  ↓ bundled / hub install / edit 并存
get_external_skills_dirs()   扩展
```

trusted_roots 校验，path traversal 防护。

### Frontmatter

- `metadata.hermes.config`：配置变量声明
- `platforms`: ["darwin", "linux", "win32"]
- `metadata.hermes.*` 优先级 > top-level

### 命令体 (`skill_preprocessing.py`)

- `_substitute_template_vars`
- `_expand_inline_shell`（有 timeout）
- 技能配置注入：`[Skill config: ...]` 块

### 独有

- 平台过滤
- 134 个提示注入 pattern 检测
- 密钥捕获回调（`_secret_capture_callback`）
- 单点目录设计

---

## 5. pi-mono

**位置**：`references/pi-ai/packages/coding-agent/src/core/slash-commands.ts`

### 来源

硬编码 builtin 列表（settings / model / export / import / fork / clone）；**无扩展文件系统扫描**。

### 独有

`SlashCommandSource` 枚举：extension | prompt | skill。但实际只用了 builtin 一种。

属于"反例"——展示了"完全不要扩展机制"的另一种取舍。

---

## 6. 特性矩阵

| 特性 | claude-code | opencode | openclaw | hermes | pi-mono |
|---|---|---|---|---|---|
| 文件系统扫描 | 递归 + symlink-safe | glob | 无 | 单点 | 无 |
| frontmatter 字段数 | 15+ | 5 | n/a | ~6 | 0 |
| `$ARGUMENTS` | ✓ | ✓ | ✗ | ✓ | ✗ |
| `$0..$9` | ✓ | ✓ | ✗ | ✗ | ✗ |
| 命名参数 `$name` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `${ENV_VAR}` | ✓ | ✗ | ✗ | ✓ | ✗ |
| `` !`shell` `` | ✓ | ✗ | ✗ | ✓ (timeout) | ✗ |
| `@file` 引用 | ✗ | ✗ | ✗ | ✗ | ✗ |
| 条件激活 paths | ✓ | ✗ | ✗ | ✗ | ✗ |
| context: fork | ✓ | ~subtask | ✗ | ✗ | ✗ |
| effort 推理强度 | ✓ | ✗ | ✗ | ✗ | ✗ |
| MCP auto-slash | ✓ | ✗ | ✗ | ✗ | ✗ |
| hooks 嵌入 | ✓ | ✗ | ✗ | ✗ | ✗ |
| 多目录覆盖 | ✓ | ✗ | ✗ | ✗ | ✗ |
| realpath 去重 | ✓ | ✗ | ✗ | ✗ | ✗ |
| i18n | ✗ | ✗ | ✓ | ✗ | ✗ |
| provider routing | ✗ | ✗ | ✓ | ✗ | ✗ |
| platform filter | requires?自定义 | ✗ | ✗ | ✓ | ✗ |
| dependency check | ✗ | ✗ | ✓ (requires/install) | ✗ | ✗ |
| 热重载 | ✗ | ✗ | ✗ | ✗ | ✗ |

**五家共有**：description 字段。

**三家及以上共有**：name + description + body 模板、frontmatter 解析、多种执行模式区分。

**独有 / 值得抄**：

```
claude-code  →  context:fork + paths + hooks + effort + realpath dedup + MCP auto-slash
opencode     →  位置参数 $0-9 + arguments[] 显式声明
openclaw     →  requires.anyBins + install hints + i18n descriptions
hermes       →  inline shell 带 timeout + platform filter
pi-mono      →  纯反例
```

**OpenProgram 的设计选择**：把 claude-code 整套照搬，opencode 的 `$0-9 + arguments[]` 合进来，openclaw 的 `requires` 单独保留，hermes 的 timeout 思想用在 `!` shell 块，pi-mono 的"硬编码 builtin"只用于 type: local 的内置命令。

---

## 7. 同步流程

定期（例如每个季度）执行：

1. `cd references && git pull` 拉所有参考项目最新
2. 对照本文件每个项目的字段表，发现：
   - 新增字段 → 评估是否进 `slash-commands.md` §7 字段权威表
   - 新增模板语法 → 评估是否进 §3 命令体语法表
   - 新增执行模式 → 评估是否进 §4
   - 新增安全约束 → 进 §9
3. 更新本文件的"调研于 YYYY-MM-DD"
4. 在 `slash-commands.md` §12 末尾追加 changelog 条目

如果上游做了破坏性变更（比如 claude-code 改了 frontmatter 字段名），**不跟随**——我们的 frontmatter 是稳定 contract，向后兼容优先。把别名映射写在 `frontmatter.py` 的 `_ALIAS_MAP` 里即可读懂旧/新两套写法。
