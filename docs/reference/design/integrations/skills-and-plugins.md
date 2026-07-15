# Skills & Plugins — 设计稿 v2（博采众长，全功能）

目标：把 claude-code / opencode / hermes 三家的 skill & plugin 能力**全部**纳入，遇到栈不同就换等价物，并在我们独有的宿主优势上做差异化优化。

参考实现：
- `references/claude-code-leaked/src/{skills,plugins,commands/{skills,plugin}}`
- `references/opencode/packages/opencode/src/{skill,plugin}`、`packages/plugin`、`.opencode/{skills,plugins}`
- `references/hermes-agent/{skills,plugins,optional-skills}`、`web/src/plugins`

---

## 1. 概念与文件约定

### Skill
单位：一个目录，含 `SKILL.md` + 可选 `references/`、`templates/`、其它资源。

`SKILL.md` frontmatter（claude-code 标准 + opencode/hermes 增量）：

```yaml
---
name: my-skill
description: One-line trigger description (LLM reads this to decide invocation)
category: devops              # hermes 风格分组
optional: false               # hermes optional-skills 取代目录区分
allowed-tools: [Read, Edit]   # claude-code: 限定可用工具
triggers:                     # 显式触发条件（我们的扩展，三家都未真用）
  keywords: ["deploy", "ci"]
  file_patterns: ["*.yml"]
  slash: "/deploy"
version: 1.0.0
author: ...
---
```

来源（合并显示，冲突时后者覆盖前者）：
1. **Bundled** — `openprogram/skills_bundled/<name>/`（随包发布，对标 claude-code `src/skills/bundled`）
2. **User** — `~/.openprogram/skills/<name>/`
3. **Project** — `<project>/skills/<name>/`（已存在）
4. **Plugin-provided** — 已启用 plugin 贡献
5. **Remote-pulled** — `~/.openprogram/cache/skills/<name>/`（对标 opencode discovery，按 index 远端拉取）

资源布局（hermes 约定）：`SKILL.md` + `references/` + `templates/`。

### Plugin
单位：一个包，含 manifest + 入口。**三种 manifest 都支持**，统一解析：
- `plugin.json`（claude-code / hermes 风格）
- `pyproject.toml` 内 `[tool.openprogram.plugin]`（Python 原生）
- `package.json` 内 `"openprogram"` 字段（Node 原生，opencode 风格）

manifest 字段：
```jsonc
{
  "name": "...",
  "version": "...",
  "description": "...",
  "deprecated": false,            // opencode 风格
  "compatibility": ">=0.1.0",     // opencode 风格 minVersion 检查
  "trust": "community",           // community | verified
  "entrypoints": {
    "commands":   "...",
    "skills":     "./skills",
    "agents":     "...",
    "hooks":      "...",
    "mcpServers": "...",
    "providers":  "...",          // opencode 风格：LLM provider 注入
    "web":        "./web/dist"    // hermes 风格 dashboard，可注册前端
  },
  "sidebar": [                     // 我们的扩展：plugin 注册一等公民侧栏项
    { "label": "My Tool", "icon": "...", "route": "/plugin/my-plugin/tool" }
  ],
  "options": { /* JSON Schema, 对照 PluginOptionsDialog.tsx */ }
}
```

来源：
1. **Installed via pip** — Python 包，扫描 entry_points group `openprogram.plugins`
2. **Installed via npm** — Node 包，`~/.openprogram/plugins/node_modules/<name>/`（opencode 风格 PluginLoader）
3. **Local path** — `~/.openprogram/plugins/<name>/`（hermes 风格，手动放）
4. **Project-pinned** — `<project>/.openprogram/plugins.json`（声明启用、版本、source）

---

## 2. 后端

### 目录
```
openprogram/
  skills/
    loader.py           # 五来源合并加载
    discovery.py        # 远端 index 按需 pull（对标 opencode discovery.ts）
    watcher.py          # watchdog 文件监听，热重载 + WS 广播
  skills_bundled/       # 内置 skills
    ...
  plugins/
    loader.py           # 多 manifest 解析、安装、卸载
    sandbox.py          # 分层沙箱：subprocess / in-process
    marketplace.py      # 多 marketplace、claude-code schema 适配
    trust.py            # 信任策略 + 持久化
    bundled/
  webui/routes/
    skills.py
    plugins.py
```

### Skills API（`routes/skills.py`）
| Method | Path | 说明 |
|---|---|---|
| GET | `/api/skills` | 五来源合并列表，含 source / enabled / category / optional |
| GET | `/api/skills/{name}` | SKILL.md 全文 + frontmatter + 资源文件树 |
| POST | `/api/skills` | 新建到 project / user |
| DELETE | `/api/skills/{name}` | 仅删 project / user / remote-cache |
| POST | `/api/skills/{name}/toggle` | enable/disable |
| POST | `/api/skills/{name}/invoke-trace` | 返回上次 LLM 调用本 skill 的注入记录（独有） |
| GET | `/api/skills/discovery/sources` | 列已注册的远端 index |
| POST | `/api/skills/discovery/sources` | 添加远端 index |
| POST | `/api/skills/discovery/pull` | 按 index 拉单个 skill 到 cache |
| WS | `skills:changed` | watcher 触发 |

### Plugins API（`routes/plugins.py`）
| Method | Path | 说明 |
|---|---|---|
| GET | `/api/plugins` | 已装列表 + 状态 + 错误 |
| GET | `/api/plugins/{name}` | 详情 + manifest + entrypoints 加载状态 |
| POST | `/api/plugins/install` | `{source: pip|npm|git|path, spec, ref}` |
| POST | `/api/plugins/{name}/uninstall` | |
| POST | `/api/plugins/{name}/toggle` | |
| POST | `/api/plugins/{name}/reload` | 对标 claude-code `/reload-plugins` |
| POST | `/api/plugins/{name}/validate` | 对标 `ValidatePlugin.tsx`，dry-run 检查 |
| GET / POST | `/api/plugins/{name}/options` | options JSON Schema 读写 |
| POST | `/api/plugins/{name}/trust` | 设置 trust 等级，影响沙箱策略 |
| GET | `/api/plugins/marketplaces` | 列 marketplace |
| POST | `/api/plugins/marketplaces` | 添加（兼容 claude-code marketplace schema） |
| GET | `/api/plugins/marketplace/{id}/index` | 浏览，搜索 / 分类 / 分页 |
| WS | `plugins:changed` | |
| WS | `plugins:error` | 加载失败实时推送 |

### Plugin 贡献入口的接合点
| 入口类型 | 注入到 |
|---|---|
| skills | skill 注册表（与文件系统 skills 合流） |
| commands | `availableFunctions`，附 `source=plugin:<name>` tag |
| mcpServers | 现有 `openprogram/mcp/` registry，`/mcp` 页面零改动 |
| providers | provider 注册表（对应 openprogram provider 体系，opencode 风格） |
| agents | subagent 注册表 |
| hooks | 事件总线（PreToolUse / PostToolUse / SessionStart / Stop 等） |
| web | 静态资源挂载到 `/plugin/<name>/static/`，Next.js 动态路由 `/plugin/[name]/[...slug]` 渲染 |
| sidebar | 推到 sidebar store 的 plugin section（独有） |

### 沙箱（分层）
| trust | 加载方式 | 失败行为 |
|---|---|---|
| `verified` | in-process import | 加载失败仅禁用该 plugin |
| `community` | subprocess + RPC（stdin/stdout JSON-RPC），CPU / 内存 / FS 限额 | 进程崩溃不影响主进程 |
| `untrusted` | 拒绝加载，UI 弹 trust 确认 | — |

Hook 执行始终走 subprocess（即使 verified），与 claude-code 一致。

---

## 3. 前端

### 侧栏新增（`web/components/sidebar/sidebar.tsx`）
```
New chat
Functions
Skills        ← 新增
Plugins       ← 新增
MCP Servers
Memory
Chats
─────────── (plugin 注册的侧栏项动态插入到此分隔下)
<Plugin A nav>
<Plugin B nav>
```

### 路由
- `/skills` — 列表（按 category 分组、optional 折叠）、详情、新建、远端 index 管理
- `/plugins` — Installed / Marketplace / Errors 三 tab
  - Installed：`UnifiedInstalledCell`-style 行
  - Marketplace：marketplace 选择器 + 卡片浏览 + 安装（移植 `BrowseMarketplace / AddMarketplace / DiscoverPlugins`）
  - Errors：移植 `PluginErrors`
- `/plugin/[name]/[...slug]` — 动态渲染 plugin 自带前端（来自 `web/dist`）
- `/skills/[name]/trace` — Skill 调用记录可视化（独有）

### 组件
```
web/components/skills/
  skills-list.tsx, skill-detail.tsx, new-skill-dialog.tsx,
  discovery-sources.tsx, invoke-trace.tsx
web/components/plugins/
  installed-list.tsx, marketplace-browser.tsx, add-marketplace-dialog.tsx,
  plugin-options-dialog.tsx, plugin-trust-warning.tsx, plugin-errors.tsx,
  validate-plugin.tsx, plugin-detail.tsx, plugin-host.tsx (iframe / dynamic mount)
```

### Store
`lib/skills-store.ts`、`lib/plugins-store.ts`，WS 订阅 `skills:changed` / `plugins:changed` / `plugins:error`。

---

## 4. 独有优化（三家都没有 / 都很弱）

1. **Plugin 一等公民侧栏项**：manifest `sidebar: [...]` → 自动注入侧栏，等同内置导航
2. **Skill 调用 trace**：每次 SkillTool 调用记录 `{ skill, injected_md_hash, accessed_refs, ts }`，右栏 / `/skills/[name]/trace` 可视化
3. **Skill 热重载**：watchdog 监听五来源任一变动 → 重新解析 → WS 广播，无需 `/reload`
4. **三 manifest 统一**：`plugin.json` / `pyproject.toml` / `package.json` 任一形式都能识别，迁移零成本
5. **跨生态 marketplace 互通**：claude-code marketplace schema 适配层，能直接添加其官方 / 第三方 marketplace
6. **双轨包管理**：pip + npm 双轨，opencode 的 Node plugin 和 hermes 的 Python plugin 都能装
7. **显式 triggers**：`triggers.{keywords, file_patterns, slash}`，对话/文件/命令任一命中即激活提示
8. **Provider 作为一等贡献类型**：从 opencode 引入，让 LLM provider 也能用 plugin 形式分发
9. **沙箱分层**：trust 等级映射到加载策略，不是一刀切
10. **Validate dry-run**：安装前 `POST /validate` 检查 manifest / 入口 / 依赖 / 兼容性，对标 `ValidatePlugin.tsx`

---

## 5. 来自三家的能力清单覆盖确认

**claude-code**：bundled skills ✓、SKILL.md frontmatter ✓、SkillTool 工具调用 ✓、Marketplace / AddMarketplace / BrowseMarketplace / ManageMarketplaces ✓、ManagePlugins / DiscoverPlugins / ValidatePlugin / PluginErrors / PluginOptionsDialog / PluginTrustWarning / UnifiedInstalledCell ✓、ReloadPlugins ✓、commands/skills/agents/hooks/mcpServers 五入口 ✓

**opencode**：远端 skill discovery（IndexSkill schema）✓、npm 包形态 plugin ✓、Provider plugin ✓、PluginPackage / resolvePluginTarget / compatibility / deprecated 字段 ✓、Effect-style 并发与重试（换 httpx+tenacity）✓

**hermes**：按领域分组 skills ✓（category）、optional 区分 ✓（optional 字段）、`SKILL.md` + `references/` + `templates/` 资源布局 ✓、`plugin_api.py` 入口 ✓、`dashboard/dist/` 前端贡献 ✓（升级为 web entrypoint + sidebar 注册）

---

## 6. 分期落地

- **M1 Skills 全量**（覆盖三家 skill 能力）
  - 五来源 loader + watchdog + WS
  - SKILL.md 解析（含 triggers / category / optional）
  - `/skills` 页面 + bundled 默认集
  - SkillTool 内置工具 + invoke trace
  - 远端 discovery（opencode 等价物）

- **M2 Plugins 本地**
  - 三 manifest 统一解析
  - pip / npm / git / path 四种来源安装
  - 沙箱分层 + trust
  - commands / skills / mcpServers / providers / hooks / agents 入口注入
  - Installed / Errors 页面 + Validate / Options / Reload

- **M3 Plugins 前端与侧栏**
  - `web` entrypoint 资源挂载
  - `/plugin/[name]/[...slug]` 动态渲染
  - sidebar 注册项

- **M4 Marketplace 全套**
  - 多 marketplace、claude-code schema 适配
  - BrowseMarketplace / AddMarketplace / DiscoverPlugins 移植

---

## 7. 待确认（不阻塞，但要选一下）

1. `~/.openprogram/` 还是沿用现有项目约定的某个全局目录
2. Provider plugin 入口怎么和 OpenProgram 现有 provider 抽象对齐（要看 `openprogram/providers/`）
3. Hook 事件命名是否对齐 claude-code（PreToolUse 等）
