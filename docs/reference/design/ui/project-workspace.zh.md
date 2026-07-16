# 项目工作区 —— 文件、多 Tab 与多会话

设计记录，2026-07-16。状态：**已提出，未实现。**

目标：把 web 端从"带项目 chip 的聊天"扩展成可用的工作区——浏览、查看项目
文件（多 tab）、每个项目跑多个会话、聊天页配一个本会话概览面板
（Outputs / Subagents / Sources）。参照形态：托管 agent 产品常见的三栏
布局（左聊天、中多 tab 文件查看器、右文件树；项目列表做成可展开表格）。

## 1. 已有资产（复用，不重造）

| 资产 | 位置 | 用于 |
|---|---|---|
| Project 实体层（id/name/path/sessions、settings.json） | `openprogram/store/project_store.py` | 全部 |
| Project WS actions（list/create/remove/config/sessions/workdirs） | `openprogram/webui/ws_actions/project.py` | 列表页、工作区 |
| `/projects` 页（列表 + settings/sessions/info tab） | `web/components/projects/projects-page.tsx` | 演化为新列表页 |
| 聊天组件群（composer、messages、top-bar） | `web/components/chat/` | 工作区左栏 |
| 右侧栏骨架（history/detail/context 视图） | `web/components/right-sidebar/` | 聊天概览面板 |
| Memory 页编辑器（edit/preview 模式、保存） | `web/components/memory/` | 文件编辑（第五期） |
| `wsRequest` + ws action 注册机制 | `web/lib/net/ws-request.ts`、`webui/server.py` | 全部新 API |
| `/api/pick-folder` 原生目录选择 | `web/app/api/pick-folder` | 添加项目 |

真正缺的是两件：(a) 限定在项目内的**文件 API**；(b) 聊天视图从路由单例
改成**按 sessionId 可挂载**。

## 2. 后端：项目文件 API

新模块 `openprogram/webui/ws_actions/files.py`，按既有方式注册。

| Action | 请求 | 应答 |
|---|---|---|
| `project_file_tree` | `project_id`、`path`（相对目录，`""` 为根） | 单层目录：`[{name, type: file\|dir, size, mtime}]` —— 懒加载，一次一层，大仓库不炸 |
| `project_file_read` | `project_id`、`path` | 文本给 `{content, size, mtime, truncated}`；`{binary: true}` / `{too_large: true}` 兜底 |
| `session_artifacts` | `session_id` | `{outputs, subagents, sources}`（见 §5） |

第五期再加 `project_file_write` / `create` / `rename` / `delete`。

`webui/server.py` 的 Starlette app 上加一条 HTTP 路由，服务不适合走
JSON 帧的字节流：

```
GET /files/raw?project_id=...&path=...   → 图片、下载
```

**安全规则**（单一 `_resolve(project_id, path)` 助手，所有 action 必经）：

* `os.path.realpath` 结果必须落在项目路径或该会话 `workdirs` 之内，
  否则拒绝——这是防路径穿越的闸。
* 查看器读取上限约 1 MB；超限答 `too_large`，UI 给原始下载链接。
* 二进制嗅探（前 8 KB 含空字节）→ `binary: true`。
* 点文件照常列出；`.git/`、`node_modules/`、`.venv/`、`__pycache__/`
  显示但默认不展开（逐层加载天然免费）。

## 3. 工作区路由：`/projects/[id]`

Next 路由 `web/app/(shell)/projects/[id]/page.tsx`，三栏：

```
┌────────────┬──────────────────────────┬──────────────┐
│  聊天      │  [tab] [tab] [tab]  [+]  │ 过滤…        │
│ （会话）   │  面包屑  路径            │ ▸ src        │
│            │  ┌────────────────────┐  │ ▸ docs       │
│  composer  │  │ 文件查看器         │  │   file.md    │
└────────────┴──┴────────────────────┴──┴──────────────┘
```

* **右——文件树。** `project_file_tree` 逐层懒加载；过滤框对已加载节点
  做客户端匹配。点文件 → 中栏打开/聚焦对应 tab。
* **中——tab 条 + 查看器。** Tab 状态放一个小 zustand store，按项目 id
  持久化到 `localStorage`（重开工作区，tab 还在）。按扩展名分派查看器：
  代码/文本带行号和高亮，markdown 支持渲染/源码切换，图片走
  `/files/raw`，其余给下载卡。第一期只读。
* **左——聊天。** 现有聊天视图按显式 `sessionId` 挂载，头部加会话切换：
  项目内会话下拉（`list_project_sessions`）+ 新建会话（创建即通过
  `set_session_project` 绑定项目）。多会话 = 工作区内快速切换；侧栏
  recents 照旧可用。

聊天视图解耦（路由单例 → `<ChatView sessionId>`）是全计划唯一的真
重构，所以聊天放第二期，文件两栏先跑起来。

**Agent 与文件联动**（便宜、高价值）：transcript 里 tool call 行中的
文件路径可点击，直接在中栏 tab 打开——看着 agent 改文件，点一下就能看。

## 4. 项目列表页：可展开表格

`/projects` 改成表格——Name / Sources（路径）/ Updated——项目行内展开
显示其会话（`list_project_sessions` 已有）。点会话 →
`/projects/[id]?session=...`。行尾操作：打开工作区、新建会话、⋯ 菜单
（重命名、设置、移除）。现在的 settings/info tab 内容挪进 ⋯ → 设置
弹窗；内容不丢，只是页面不再是左右分栏。

后端补充：project dict 加 `updated_at`（取其会话时间戳最大值，兜底
注册表 ctime）、`rename_project` action。置顶后置。

## 5. 聊天页：会话概览面板

在既有右侧栏（history/detail/context 之外）加默认视图 **Overview**，
由一次 `session_artifacts` 调用 + 实时 ws 事件驱动。

* **Outputs** —— 本会话 `write`/`edit` 碰过的文件，去重、新在前。
  点击 → 跳进项目工作区并打开该文件。
* **Subagents** —— spawn 出的子会话（session DAG 本来就知道）：标签、
  状态，点击 → 聚焦该分支。
* **Sources** —— `read` 过的文件与抓取过的 URL（`web_search`/`fetch`
  tool call），去重。

服务端就是扫一遍该会话已持久化的 tool call——不加新存储；派生数据，
按需重算，会话运行中由事件流增量更新。

## 6. 分期

| 期 | 交付 | 风险 |
|---|---|---|
| **v1** | 文件 WS actions + `/files/raw` + `/projects/[id]`（文件树 + 多 tab 只读查看） | 低——全新代码，无重构 |
| **v2** | 聊天入驻工作区左栏，项目内会话切换 + 新建 | 中——聊天视图解耦 |
| **v3** | `/projects` 可展开表格、`updated_at`、重命名 | 低 |
| **v4** | 聊天右侧栏 Overview（outputs/subagents/sources）+ transcript 文件路径跳转工作区 | 低中 |
| **v5** | 文件管理：编辑保存（memory 页编辑器模式）、新建/重命名/删除、上传/下载 | 中——写路径安全 |

每期独立可发布；v1 单独就兑现核心诉求——接入项目、浏览、管理文件、
多 tab 查看。

## 7. 补充 2026-07-16 —— 统一 tab 模型、run tab

看过原型后定下：

* **一切皆 tab，一个工作区一个项目。** Tab 类型：`session`（聊天）、
  `file`、以及后续的 `run`（program/workflow 运行）。同一套 Tab 组件
  与交互；但是**两个 tab 组**而非一条 tab 条——会话组停靠左侧，文件组
  在中右——聊天和它正在改的文件保持同屏（核心使用回路）。以后把会话
  tab 拖进另一组即可实现双聊天并排。工作区硬绑单个项目，跨项目混排
  在设计上就不可能发生。
* **不设独立工作区路由。** 面板都活在持久的 chat 表面（AppShell）内
  收放；§3 里的聊天视图解耦重构取消。v2 从"会话下拉"改为"会话 tab 组"。
* **Run tab / workflow 可视化**（后续阶段）：workflow 保持纯 Python
  函数（prompt 在 docstring、单一入口），不引入图 DSL。执行图从框架
  本就记录的事件流**派生**（`webui/_exec_dag.py`、`graph_builder.py`、
  session DAG 渲染器），run tab 是活视图：哪个节点在跑、哪些完成、
  点节点看输入输出。与 LangGraph 的刻意对照：先声明再执行 vs
  记录先行——任意 Python 控制流零埋点自动成图。

## 8. 暂不做

* 不做内嵌终端、不做 git 面板——这些 agent 在聊天里就能干。
* 不引入 CodeMirror/Monaco；编辑复用 memory 页的 textarea
  edit/preview 模式，量到不够用再升级。
* v1 不做文件监听/树实时刷新；每个目录节点给刷新按钮，等会话高频改
  文件成为常态再上 fs-events。
