# Slash Commands — Unified Design

设计目标：把 OpenProgram 现在散落在 CLI 写死表、Web composer 写死表、`/api/plugins/commands`、MCP prompts、skills 这几条互不相通的"指令源"，合并成一份**统一的 slash 命令登记表**，五个层级、一份格式、一套渲染、一个 UI。

参照实现见 `docs/design/slash-commands-references.md`。本文档凡是写"照搬 X"的地方，意思是直接复用那个项目的设计选择。

---

## 1. 来源层级

加载顺序由低到高，**高优先级覆盖同名低优先级**；被覆盖的不丢失，仍可通过 `/source:name` 显式调用。

| Layer | 来源 | 目录 / 接口 | 谁写 | 热重载 |
|---|---|---|---|---|
| L0 | built-in | 源码硬编码 | OpenProgram 自己 | 否（要重启） |
| L1 | plugins | `~/.openprogram/plugins/<pkg>/...` 的 `entrypoints.commands` | 插件作者 | 插件 reload 时 |
| L2 | mcp-prompts | 已连接 MCP 服务的 `list_prompts()` | MCP 服务 | session 重连时 |
| L3 | skills | `~/.openprogram/skills/<name>/SKILL.md` | 技能作者 / 用户 | watcher 监听 |
| L4 | user | `~/.openprogram/commands/**/*.md` | 当前用户 | watcher 监听 |
| L5 | project | `<cwd>/.openprogram/commands/**/*.md` | 项目维护者 | watcher 监听 |

覆盖规则照搬 claude-code：后加载者赢；同来源内同名按 realpath 去重防 symlink 重复加载。

显式命名空间格式：`/(plugin)name`、`/(mcp:linear)name`、`/(skill)name`、`/(user)name`、`/(project)name`。括号内是 source label。

冲突时菜单展示主条目 + 「这个名字还有 N 个其它来源，按 ⇥ 切换」的 hint，照搬 claude-code 的 disambiguation UI 思路。

---

## 2. 文件格式

照搬 claude-code 的 markdown + YAML frontmatter，字段集合并入 claude-code + opencode + openclaw + hermes 的并集，再裁掉对我们没意义的（i18n、provider routing、platform filter）。

```markdown
---
# 标识 ----------------------------------------------------------
name: review              # 可省，默认取文件名
aliases: [r, rev]         # 可选；同名表里也走覆盖规则
description: 按团队规范 review 当前 diff
when-to-use: |            # 长描述；进入 picker 详情面板
  当用户希望对未提交改动做一轮风格 + bug review 时用。
hidden: false             # true 则不出现在补全菜单，但仍可显式触发

# 参数 ----------------------------------------------------------
arguments:                # 位置参数声明（opencode 风格）
  - name: target
    description: 文件路径或目录，默认当前 diff
    required: false
argument-hint: "[target]"  # 菜单里灰字提示（claude-code）

# 执行 ----------------------------------------------------------
type: prompt              # prompt | local | local-jsx，默认 prompt
context: inline           # inline | fork，默认 inline
agent: general-purpose    # 仅 context: fork 时生效
model: inherit            # inherit | opus | sonnet | haiku | <full id>
effort: medium            # low | medium | high | max | <int>
allowed-tools:            # fork 模式下传给子 agent 的工具白名单
  - Read
  - Grep

# 触发条件（claude-code 独有）-----------------------------------
paths:                    # 通配；命中时该命令才出现在补全里
  - "src/**/*.{ts,tsx}"
  - "**/*.py"
requires:                 # openclaw 风格的前置依赖检查
  any-bins: [git]
  config: [openai_api_key]

# 钩子 ----------------------------------------------------------
hooks:                    # 与 plugins/hooks 共用事件名
  PreToolUse: ...

# 元 -----------------------------------------------------------
version: 1.0.0
---
请审查 {{target}} 这段代码：
- 找潜在 bug
- 检查是否符合 CONVENTIONS.md
- 输出 patch 建议

附加上下文：
$ARGUMENTS

最近 commit:
!`git log -5 --oneline`

当前 diff：
@`git diff --staged`
```

字段权威表见第 7 节。

---

## 3. 命令体模板语法

照搬 claude-code 的全部，加 opencode 的 `$0..$9`，加 hermes 的 timeout-bounded shell。

| 语法 | 含义 | 来源 |
|---|---|---|
| `$ARGUMENTS` | 用户输入命令后的整串文本 | claude-code |
| `$0`..`$9` | 第 N 个位置参数（shell 风格分词） | opencode |
| `{{name}}` | 按 `arguments:` 声明的命名参数 | opencode + 自创 |
| `${OPENPROGRAM_COMMAND_DIR}` | 命令文件所在目录绝对路径 | claude-code |
| `${OPENPROGRAM_SESSION_ID}` | 当前会话 id | claude-code |
| `${OPENPROGRAM_CWD}` | 当前工作目录 | 新增 |
| `` !`cmd` `` 或代码块 `` ```! ` ` | 在 host shell 执行，stdout 拼回 prompt；2s timeout | claude-code + hermes |
| `` @`path` `` | 读文件内容拼回 prompt；路径必须在 trusted_roots 内 | 新增 |
| `<<command-name>>name<</command-name>>` | 引用另一条命令并展开（递归保护，最多 3 层） | 新增 |

参数解析照搬 claude-code `tryParseShellCommand`：先 shell-quote 分词，失败 fallback 到 whitespace split。空参数返回空列表，模板里的 `$0..$9` 解析成空字符串。

数字命名参数（`name: "0"`）拒绝注册，与 `$0` 冲突。

Shell 执行的安全模型：默认禁用，需要在配置里 `commands.allow_shell: true` 才能跑 `` !`...` ``。MCP 上下文里禁用所有 `!` 块。

---

## 4. 执行模式

照搬 claude-code 的三态，加 opencode 的 subtask 概念。

```
type: prompt            （默认）
  渲染模板 → 当作用户消息塞进当前会话 → 走正常 agent loop

type: local
  调用 host 注册的 LocalCommandHandler；返回 LocalCommandResult
  保留给内置命令：/compact /clear /new /web /model 等

type: local-jsx
  暂不实现。Web UI 可以渲染 React 组件作为命令结果（claude-code 用 ink）
  我们这边走 server-pushed structured event，留接口

context: inline          （默认）
  渲染后的 prompt 进当前会话上下文

context: fork
  开 task 子 agent 跑（已有 functions/tools/task）
  agent 字段决定 subagent_type；allowed-tools 决定可见工具集
  子 agent 返回的最终消息以「命令结果」形式呈现，不污染主上下文
```

`context: fork` 等价于"敲 `/review` 自动转成调一次 `task(subagent_type=general-purpose, prompt=...)`"。这一步把 claude-code 的 fork 模式直接嫁接到我们已有的 subagent 机制上。

---

## 5. 触发条件（paths / requires）

照搬 claude-code 的 `paths`、openclaw 的 `requires`：在不满足条件时**从补全菜单隐藏**，但用户仍可手动敲完整命令触发——触发时再做一次硬校验，失败给清晰报错。

`paths: ["src/**/*.py"]`：当前会话最近 touch 过的文件（或显式 `@file` 引用的）命中 glob 时才显示。

`requires.any-bins: [git, rg]`：`which` 检查至少一个可用。失败则 hint「需要 `git` / `rg`，请先安装」。

`requires.config: [openai_api_key]`：当前 profile 配过该键。

`requires.platform: [darwin, linux]`：从 hermes 借来的平台过滤。

---

## 6. 钩子绑定

命令可以声明自己的临时 hook（仅在这条命令的执行期间生效）。事件名复用 `openprogram/plugins/hooks.py:HookEvent`。

```yaml
hooks:
  PreToolUse:
    - matcher: Bash
      command: !`echo "blocked by /review" >&2; exit 2`
  PostToolUse:
    - matcher: Edit
      handler: built-in:auto-stage
```

handler 形式两种：

- `!` 反引号块 → 跑 shell，stdout 进日志，exit code 决定 allow/deny
- `built-in:<id>` → 调 host 注册的命名 handler（首发不做，留接口）

需要等 hooks 子系统补齐"拦截 / 改写"语义后才完整可用。本节先把 schema 占住，避免后面破坏性变更。

---

## 7. Frontmatter 字段权威表

| 字段 | 类型 | 默认 | 含义 | 借自 |
|---|---|---|---|---|
| name | string | 文件名 stem | 命令名 | 通用 |
| aliases | string[] | [] | 别名，独立走覆盖表 | claude-code |
| description | string | "" | 一行说明，菜单展示 | 通用 |
| when-to-use | string \| md | "" | 长说明，详情面板 | claude-code |
| hidden | bool | false | 隐藏出补全 | claude-code (isHidden) |
| arguments | list | [] | 位置参数声明 | opencode |
| argument-hint | string | 自动生成 | 菜单灰字提示 | claude-code |
| type | enum | prompt | 执行模式 | claude-code |
| context | enum | inline | inline / fork | claude-code |
| agent | string | "general-purpose" | fork 时的 subagent_type | claude-code |
| model | enum/string | inherit | 模型覆盖 | claude-code |
| effort | enum/int | inherit | 推理强度 | claude-code |
| allowed-tools | string[] | inherit | 工具白名单 | claude-code |
| paths | string[] | null | glob 条件激活 | claude-code |
| requires | object | {} | 前置依赖 | openclaw |
| hooks | object | {} | 临时钩子 | claude-code |
| version | semver | null | 升级提示用 | claude-code |
| user-invocable | bool | true | 是否对应 `/name` 触发，false 时只能模型调 | claude-code |
| shell | enum | inherit | bash / powershell，`!` 块用 | claude-code |

未声明的字段一律保留进 `extras` dict，不报错（向前兼容）。

---

## 8. UI

补全菜单按 source 分组，组内按字母序，跨组按 source 优先级（project > user > skill > mcp > plugin > builtin）。每条展示：

```
/review                  按团队规范 review 当前 diff      [project]
  [target]
```

搜索按 fuzzy（name + description + when-to-use）。

详情面板（按 ⇥ 展开）：

```
/review (project)
─────────────────────────────────────
按团队规范 review 当前 diff

参数：[target] (optional)
模式：inline · model: inherit · effort: medium
来源：.openprogram/commands/review.md
```

冲突态：菜单条目右侧标 `(+2 more)`，⇥ 切换不同 source 的实现。

技能（L3）和 MCP prompts（L2）自动注入；技能命令默认 `context: fork`、`agent: general-purpose`，MCP prompts 默认 `type: prompt + inline`（因为它们本来就是 prompt 模板）。

---

## 9. 安全

- 路径加载：`realpath` 解析后必须落在 trusted_roots（`~/.openprogram/`、`$cwd/.openprogram/`）内，否则拒绝。
- YAML 解析：`yaml.safe_load`，禁用任意类型构造。
- Glob：`fnmatch` 风格，禁用 `..` 与绝对路径。
- `!` shell 块：默认禁用，2s timeout，禁止 fork bomb，stdout 限制 64KB。
- `@` 文件引用：必须在 trusted_roots 内或显式被 `--allow-file <abs>` 授权。
- 来源标签固定由 loader 写入，不允许 frontmatter 自报 `source:`。

---

## 10. 工程实现

### 10.1 目录布局

```
openprogram/commands/
├── __init__.py             # 对外 API: list_commands / get / dispatch
├── loader.py               # 扫描 + 解析 + 合并五个 layer
├── frontmatter.py          # YAML 解析 + 字段校验
├── template.py             # $ARGUMENTS / {{name}} / !`...` / @`...` 渲染
├── conditions.py           # paths / requires 评估
├── registry.py             # 进程内合并表 + 冲突索引
├── dispatch.py             # type/context 分支
├── watcher.py              # inotify/fsevents 监听 L3-L5
└── _ref.py                 # 给 web/cli 用的轻量 view 投影
```

### 10.2 数据流

```
启动 / reload
  → loader.scan_all_layers()
  → for each layer: read files / call provider (plugins, mcp.list_prompts)
  → frontmatter.parse + validate
  → registry.merge(layer, items)        覆盖 + 别名 + 冲突索引

用户敲 /review xxx
  → cli or web 转发到 dispatch.invoke(name, raw_args, session_ctx)
  → registry.resolve(name) → CommandSpec
  → conditions.check(spec, session_ctx) → ok / blocked-with-reason
  → template.render(spec.body, parsed_args, env)
  → dispatch by type:
       prompt + inline → session.append_user_message(rendered)
       prompt + fork   → task.run(agent=spec.agent, prompt=rendered, tools=allowed)
       local           → handler(session_ctx, parsed_args)
```

### 10.3 API

后端：

```
GET    /api/commands                    # 合并后的统一列表（含 source、metadata）
GET    /api/commands/{name}             # 单条详情（含 body 模板预览）
POST   /api/commands/{name}/invoke      # body: {session_id, raw_args}
POST   /api/commands/reload             # 强制重扫
GET    /api/commands/conflicts          # 冲突表（同名多源）
```

`/api/plugins/commands` 保留为兼容入口，内部 redirect 到 `/api/commands?source=plugin`。

前端：

`web/components/chat/composer/use-slash-menu.ts` 改读 `/api/commands`，删掉内部硬编码列表（保留 dispatcher 兼容层，把 client 侧 `/compact` `/clear` 等映射到 builtin local 命令）。

CLI：

`openprogram/_cli_chat/handlers.py:_handle_slash` 改造：先查 registry，命中后走 dispatch；未命中再走目前那串写死的 if/else（这部分逐步迁移成 type: local 的 builtin 命令）。

### 10.4 迁移路径

```
Phase 1  扫描器 + frontmatter + 渲染 + registry + /api/commands   [基础]
Phase 2  L4 (~/.openprogram/commands) + L5 (.openprogram/commands)
Phase 3  L3 skills 自动注入 (skills/loader 暴露 to_command_spec())
Phase 4  L2 mcp prompts 自动注入 (mcp/registry 已有 list_prompts)
Phase 5  L1 plugins 接入新表（plugins/loader 已有 _commands，做一层 adapter）
Phase 6  context: fork 接 task 工具
Phase 7  paths / requires 触发条件评估
Phase 8  watcher 热重载
Phase 9  builtin 命令逐步迁移成 type: local + frontmatter
Phase 10 hooks 字段在 hooks 子系统升级后启用
```

每个 Phase 独立可发布；Phase 1-2 + 5 完成时已经能给用户带来主要价值。

---

## 11. 不实现的部分（明确舍弃）

| 来源 | 设计 | 不抄的原因 |
|---|---|---|
| openclaw | i18n (descriptionLocalizations) | 我们是英文 + 简中两套，运行时切换没价值 |
| openclaw | provider routing (Slack vs Mattermost) | 单一 host，没有多 provider 命名 |
| hermes | platforms 过滤 (darwin/linux/win32) | requires.platform 替代 |
| hermes | 提示注入 134 pattern 检测 | 移到独立的 prompt-injection scanner 子系统 |
| claude-code | local-jsx React 组件 | Web UI 用 structured event 替代，CLI 不实现 |
| pi-mono | 纯硬编码 | 反例 |

---

## 12. 版本与升级

`version: 1.0.0` 字段 + 来源仓库的 git hash（如有）一起塞 registry。

L1（plugins）走插件 autoupdate 子系统（已有）。

L3（skills）走 skills discovery diff（已有）。

L4 / L5 用户写的，不自动更新。

L0（builtin）跟随 OpenProgram 版本。

`docs/design/slash-commands-references.md` 周期性重扫五家参考项目的实现，发现新设计后回到本文档 §2/§3 增补字段，不破坏既有 frontmatter（额外字段进 extras）。
