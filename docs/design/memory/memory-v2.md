# Memory v2 — 实体/虚拟两级 + Provenance 导航召回

> 状态: 设计稿 + 实施中。取代当前线性总结链 (`journal → wiki → core`)。
> 前置阅读: [`git-as-entity-memory.md`](git-as-entity-memory.md) (实体层最初设计)、
> [`memory.md`](memory.md) (v1 实现)。
> 路径口径: 所有状态都在 `~/.openprogram/` (= `get_state_dir()`) 下;命名 profile 用
> `~/.openprogram-<profile>/`。早期文档里的 `~/.agentic/` 与 `sessions-git/` 均已废弃
> (见 `openprogram/paths.py` 的一次性迁移)。

## 0. 一句话

记忆分**实体层**(git 存的、不可变、完整的真实历史)和**虚拟层**(从实体提炼的、紧凑的、带指针的索引)。LLM 调用时**只注入虚拟层**;需要原始细节时,LLM 顺着虚拟层里的 **provenance 指针**,用工具自己导航回实体层去取。

```
        ┌──────────────────────── 实体层 (raw, git, 完整) ────────────────────────┐
        │   Session-Git                          Project-Git                        │
        │   每会话一个 repo                        绑用户工作目录 (真实代码/文档仓)      │
        │   每 turn 一 commit                      agent 改文件 → 自动 commit          │
        │   · 绑了项目 → <项目>/.openprogram/sessions/<id>/                          │
        │   · 随手聊   → <state>/sessions/<id>/   (默认项目仅逻辑标签, 无独立 repo)     │
        └────────────────────────────────┬─────────────────────────────────────────┘
                                          │  持续提炼 (distillation), 带 provenance
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
              ┌────────────────────┐          ┌────────────────────┐
              │  时间轴 (Journal)    │          │  知识图谱 (Wiki)     │   ← 虚拟层 (derived)
              │  "何时发生了什么"     │          │  "实体之间什么关系"   │      每条都带指针
              │  bi-temporal         │          │  bi-temporal edges   │      回指实体层
              └──────────┬──────────┘          └──────────┬──────────┘
                         └──────────────┬─────────────────┘
                                        ▼
                              ┌──────────────────┐
                              │   召回 (recall)    │  只把虚拟层注入 LLM context
                              │   只注入虚拟        │  LLM 看到指针 → 用工具导航回实体层
                              └──────────────────┘
```

## 0.5 实施状态 (截至 2026-05)

本文是**目标设计**。当前代码实现到哪一步:

| Phase | 内容 | 状态 |
|---|---|---|
| **0** | baseline 修复 (LLM 桥 / watcher 延后 / ingest 读对字段) | ✅ 已完成 (见 §9) |
| **1** | 实体层 Project: schema + 绑定 + project-git + session 落项目内 | ✅ 已完成 (`store/project_store.py` + `store/session_store.py`) |
| **2** | 提炼管道重写: 读 session-git DAG → 时间轴 + 图 (provenance, bi-temporal) | ❌ 未开始 |
| **3** | 召回重写: 只注入虚拟 + 导航工具 | ❌ 未开始 |
| **4** | 物化视图 + core 重建 + hybrid search (向量) | ❌ 未开始 |
| **5** | UI: Projects 面板 / timeline / `/memory` | ⚠ 部分 (topbar 项目选择器已做, 其余未做) |

**关键缺口 (Phase 2 的核心)**: 实体层 (git) 已建好,但虚拟层目前**仍是 v1 的 journal/wiki/core,而且还没真正读实体层** —— `memory/wiki/ingest.py` 喂给 LLM 的是 `get_branch()` 渲染出来的对话文本,不是 session-git 的 `Call` DAG;project-git 的 commit 历史更是从未被读取。所以"实体→虚拟"这一跳还没打通,实体层对记忆质量的贡献目前 ≈ 0。

> **2026-06 进展**: Phase 2 的前置读层已落地 (Unit D, commit e48af986)。
> `openprogram/store/session/provenance.py` 给出了 `Provenance` dataclass +
> 不带 LLM 的读原语 (`iter_nodes_since` 增量游标 / `node_provenance` 坐标 /
> `session_commits` / `project_commits`),memory 可 `from openprogram.store import
> Provenance, iter_nodes_since`。Phase 2 剩下的是 Stage 2 抽取器 (吃 `Call` DAG →
> 时间轴事件 + 图实体) 和把 `session_watcher` / `wiki/ingest` 从 `get_branch` 文本
> 切到这个读层。详见 [`entity-session-cache.md`](entity-session-cache.md) §5–§6。

**§0 概览图里的"默认项目"** 已从最初设计的"兜底 git 仓"简化为**纯逻辑标签**(见 §2.5),图与正文以 §2.5 为准。

**已知待修 (非本文档范围, 代码层)**: `sessions-git → sessions` 改名后,记忆实体层的 `<state>/sessions/` 与 `agentic_programming` 的 ask_user IPC 目录 (`paths.get_sessions_dir()` 同样指向 `<state>/sessions/`) 撞了同一个目录,需要给其中一个换名 (如 ask_user 改用 `<state>/followups/`)。

## 1. 设计动机 / 跟现有方案的区别

### 现状问题

v1 实现 (`memory.md`) 是**线性有损链**: `raw chat → 抽 0-10 facts → journal → wiki → core`。每一层都丢信息,且抽象层 (wiki) 是从上一层有损总结来的,**不直接读实体层**。结果: 实体层 (sessions/) 和抽象层 (wiki) 是两套不相通的东西,中间管道还断过 (`build_default_llm` 返回 None,见 §9,已修)。

### 跟主流框架对比

| 框架 | 实体层 | 抽象层 | 召回方式 | 时间维度 | 知识图谱 |
|---|---|---|---|---|---|
| Claude Code | CLAUDE.md + 会话 | auto-memory MEMORY.md (索引+topic) | 注入索引,topic 按需读 | ❌ | ❌ |
| OpenClaw | MEMORY.md + 日记 | 同上 + wiki 插件 | 注入 + 语义搜索 | ❌ | ⚠ 弱 |
| mem0 | — | 向量 DB | RAG 切块灌入 | ⚠ 写入时间 | ❌ |
| Letta/MemGPT | 对话历史 | tiered (core/recall/archival) | LLM 工具搬运 | ⚠ | ❌ |
| Zep/Graphiti | — | temporal knowledge graph | 图查询 | ✅ bi-temporal | ✅ |
| **本设计** | **git (session+project)** | **时间轴 + 知识图谱** | **注入虚拟,LLM 导航回实体** | **✅ bi-temporal** | **✅** |

### 四个新颖点 (论文角度)

1. **Git 作为 episodic memory 的底座**。实体记忆不是自研存储,直接用 git: commit 不可变 = 真相不可篡改; log = 时间线; checkout = 时光机; branch = 探索过的分支; 而且 agent 能用标准工具 (`git log` / `grep` / `diff`) 自己读。可审计、可复现、可回溯。

2. **Provenance-pointer 索引,而非替代**。虚拟层不取代实体层,而是给它建一个**带坐标的导航地图**。每条虚拟记忆都挂一个指针 `(project, session, commit, timestamp)`,指回它在实体层的出处。解决了"有损总结丢上下文"的根本问题——**任何时候都能顺着指针钻回 ground truth**。

3. **LLM 自导航召回 (map → territory),而非 RAG 灌块**。传统 RAG 把相关 chunk 切出来塞进 context,污染上下文且丢结构。本设计只注入紧凑的虚拟地图,LLM 读到"2026-05 在项目 X 修了 Windows bug,完整历史在 session local_13d5",**需要细节时自己用工具走过去取**。context 小、保真度满、检索由 agent 主导。

4. **时间轴 + 知识图谱双投影,都 bi-temporal**。同一个 git 底座投影出两个正交视图: 时间轴回答"何时",知识图谱回答"什么关系"。两者都记两个时间——`event_time`(事情发生的时间) 和 `ingestion_time`(我们记下来的时间)——支持时间旅行查询和矛盾检测。

## 2. 实体层 (Entity Memory)

### 2.1 心智模型: 每个会话都属于某个 project

核心简化: **没有"无主"的会话**。每个会话都属于一个 project,区别只在于这个 project 是不是用户真实的工作目录。

```
session 创建时:
  指定了工作目录路径? (顶部 work_dir 选择器)
    是 → 绑到那个路径的 Project-Git (用户真实代码仓/文档仓),
         会话仓落在 <项目>/.openprogram/sessions/<id>/
    否 → 默认项目 (逻辑标签 project_id="default"),
         会话仓落在 home 根 <state>/sessions/<id>/
```

这样实体层永远有清晰归属,虚拟层也永远能按 project 聚合。

### 2.2 磁盘布局

```
~/.openprogram/                          ← 状态根 (get_state_dir())
├── sessions/<session_id>/              ← Session-Git, 随手聊 (无绑定项目)
│   ├── .git/                            每 turn 一 commit
│   ├── meta.json                        title / agent_id / project_id / head
│   ├── history/NNNN-<role>-<id>.json    DAG 节点 (user/llm/code)
│   ├── context/                         给 LLM 的物化视图 (messages.json + commits/)
│   └── workdir/                         此会话的临时工作目录
│
├── sessions/locations.json            ← 会话位置索引: 落在项目内的会话 → 真实路径
│
├── projects/
│   └── projects.json                   project 注册表 (id → {name, path, sessions, status})
│                                        默认项目只是一条逻辑标签, 不建独立 repo
│
└── memory/                             ← 虚拟层 (见 §3)

<用户工作目录>/                          ← Project-Git (绑定时用的真实仓)
├── .git/                               复用已有的; 没有则 git init。agent 改文件 → 自动 commit
└── .openprogram/sessions/<id>/         ← 绑了此项目的会话, repo 落在项目内
```

### 2.3 Session-Git (已实现,保留)

现状已经做好,见 `<state>/sessions/<id>/` (`store/git_session.py`)。每个节点是 `Call` (role = user / llm / code),边是 `called_by` (调用链) + `reads` (上下文引用)。**v2 不改 Session-Git 的存储,只在 `meta.json` 里加了 `project_id` 字段**(已加)。

绑定了真实项目的会话,其 repo 不在 home 根,而在 `<项目>/.openprogram/sessions/<id>/`,由 `sessions/locations.json` 索引指向 (`SessionStore._record_location` / `_session_dir`)。这样一个项目的"代码历史 (project-git) + 对话历史 (session-git)"都待在项目目录里,跟着项目走。

### 2.4 Project-Git

Project = 一个长期工作单元,关联:
- 一个**文件系统目录** (用户的真实代码仓 / 文档仓)
- **多个 session** (在这个项目上的多次对话)
- 名字 / 描述 / 状态

```python
@dataclass
class Project:
    id: str                       # proj_<8hex of path>, 或 "default"
    name: str
    path: str                     # 绝对路径; 默认项目 = "" (无 repo)
    is_default: bool              # True 表示默认项目 (逻辑标签)
    session_ids: list[str]        # 反向索引
    status: str                   # "active" | "paused" | "done"
    created_at: float
```

注册表在 `<state>/projects/projects.json`,key 是路径派生的 `proj_<8hex>` (同一目录永远映射到同一 project)。`resolve_project(path)` 复用目录已有的 `.git`,没有就 `git init` (`ProjectGit.ensure_init`)。

**自动 commit (Strategy A,沿用原设计)**: turn 结束时,若 session 绑了真实 project 且 agent 改过文件:
```
if not is_dirty_before_agent_touched():     # 工作树原本干净
    git add -A && git -c user.name=<agent> commit -m "[agent <session>] turn <N>: <user msg>"
else:
    # 用户有未提交改动 → 不污染,跳过 + UI 警告
    skip + warn
```
commit 用 agent 身份 (`-c user.name/email` 覆盖),在用户自己的 repo 里也能跟用户的 commit 区分开。

### 2.5 默认项目 (Default-Project) 的语义

默认项目是一条**纯逻辑标签**,不建独立 git 仓。理由: 随手聊不绑任何目录,它产生的文件 (如果有) 落在该会话自己的 `workdir/` 里,所以一个"默认项目仓"永远是空的、纯属冗余。这类会话只带 `project_id="default"` 用于分组 / scope 过滤,它们的**实体记忆就是会话仓本身** (落在 home 根 `<state>/sessions/<id>/`)。

只有当会话绑定了真实工作目录时,才会出现一个真 git 仓 (见 §2.4)。即: **真实路径 → 真 repo;没路径 → 只有标签**。这避免了实体层出现"一堆空的 default 仓"这种无意义膨胀。

## 3. 虚拟层 (Virtual Memory)

两个 projection,都从实体层提炼,都带 provenance 指针,都 bi-temporal。

> 现状: 本节描述的 timeline/graph **尚未实现** (Phase 2)。当前在跑的是 v1 的
> journal/wiki/core (见 `memory.md`)。下面是目标形态。

### 3.1 Provenance 指针 (核心数据结构)

每条虚拟记忆挂一个指针,指回实体层出处:

```python
@dataclass
class Provenance:
    project_id: str               # 哪个项目
    session_id: str               # 哪次会话
    commit: str | None            # session-git 的哪个 commit (可选)
    node_ids: list[str]           # DAG 里哪几个节点 (可选, 精确到消息)
    event_time: float             # 事情发生的时间 (wall clock)
    ingestion_time: float         # 我们提炼记下的时间
```

`event_time` vs `ingestion_time` = bi-temporal 的两个轴。能回答:
- "上周三那次重构后,代码变成啥样了" (按 event_time)
- "我们什么时候才知道 X 库不稳的" (按 ingestion_time)

### 3.2 时间轴 (Temporal / Journal)

按时间组织的事件流。回答"何时发生了什么"。

```
~/.openprogram/memory/timeline/
├── 2026-05.jsonl               # 按月分片, append-only
└── ...

# 一条记录
{
  "id": "ev_abc",
  "summary": "在 OpenProgram 修了 Windows cp1252 编码 bug, 涉及 38 个文件",
  "kind": "work",               # work | decision | learning | event
  "provenance": {
    "project_id": "proj_openprogram",
    "session_id": "local_13d5",
    "commit": "73bfc05",
    "event_time": 1779900000,
    "ingestion_time": 1779986400
  },
  "entities": ["project.openprogram", "issue.cp1252"]   # 关联到图节点
}
```

### 3.3 知识图谱 (Graph / Wiki)

实体 + 关系。回答"什么和什么是什么关系"。**这是把 v1 的 `wiki/<kind>/` 升级成真图**——现在只有孤立的实体页,v2 加边和时间。

```
~/.openprogram/memory/graph/
├── entities.jsonl              # 点
├── edges.jsonl                 # 边 (带 bi-temporal + provenance)
└── views/                      # 物化的可读视图
    ├── entity/<slug>.md        # 每个实体一页 (兼容现有 wiki 阅读习惯)
    └── ...

# entity
{"id": "project.openprogram", "type": "project", "name": "OpenProgram",
 "attrs": {"path": "C:\\Users\\fzkuji\\OpenProgram", "lang": "python"}}

# edge (带 bi-temporal + provenance)
{"from": "issue.cp1252", "to": "commit.73bfc05", "relation": "fixed-by",
 "event_time": 1779900000, "ingestion_time": 1779986400,
 "provenance": {"project_id": "proj_openprogram", "session_id": "local_13d5"},
 "confidence": 0.95, "superseded_by": null}
```

**矛盾处理**: 新边跟旧边冲突时,不删旧边,标 `superseded_by` 指向新边。保留历史 + 支持"我们曾经以为 X,后来发现 Y"这种 time-aware 查询。

### 3.4 Scope 标签 (跨项目隔离)

每个 entity / edge 带 scope,查询时按当前上下文过滤:

```
scope: "global"                  # 跨所有项目 (e.g. 用户语言偏好)
scope: "project:openprogram"     # 仅此项目
scope: "agent:research"          # 仅此 agent
```

比 Claude Code (纯目录层级) 和 OpenClaw (纯 per-agent) 都灵活——图天然支持多维标签过滤,文件系统层级做不到。在 OpenProgram 项目里聊天,只投影 `global` + `project:openprogram` 子图。

### 3.5 Core (始终注入的最小快照)

不是独立一层,而是虚拟层的**最小投影**: 从时间轴取最近高信号事件 + 从图取高频/高置信实体,组装成 ≤2KB 的 snippet。注入每次 system prompt。**Core 里的每一条也带指针**,所以 LLM 看到 core 就知道往哪钻。

## 4. 召回机制 (两条链路打通)

### 4.1 注入: 只给虚拟层

LLM 每次调用,system prompt 注入的是:

```
═══════════════════════════════════════════════
OpenProgram 记忆 — 项目: OpenProgram, 最后整理 2026-05-29
═══════════════════════════════════════════════
[时间轴 · 最近]
· 2026-05-28 修了一批 Windows 兼容 bug (38 文件)        ↪ session:local_13d5
· 2026-05-29 重构 CLI 成 verb 制, 加了 rescue/logs       ↪ session:local_7cd1

[图谱 · 当前项目相关]
· OpenProgram 在 C:\Users\fzkuji\OpenProgram (python)
· cp1252-bug ──fixed-by──► commit 73bfc05               ↪ session:local_13d5
· worker ──listens-on──► :18109

需要细节: memory_open_session(<id>) / memory_git_log(<project>) / memory_timeline(<entity>)
═══════════════════════════════════════════════
```

**不灌任何 raw chat。** 全是紧凑的带指针摘要。

### 4.2 导航: LLM 顺指针自取

LLM 需要原始细节时,调导航工具走回实体层 (Phase 3 新增):

| 工具 | 干啥 | 落到实体层哪 |
|---|---|---|
| `memory_open_session(session_id, [turn])` | 读某会话的原始消息 | `<sessions>/<id>/history/` |
| `memory_git_log(project_id, [since])` | 看某项目的提交历史 | Project-Git |
| `memory_git_show(project_id, commit)` | 看某次改了什么 | git show |
| `memory_timeline(entity\|since\|until)` | 时间轴切片 | virtual timeline |
| `memory_graph_neighbors(entity, hops)` | 图的邻居 | virtual graph |
| `memory_search(query)` | 跨虚拟层 hybrid 搜索 | virtual (FTS + 向量) |

例: LLM 读到 core 里"cp1252-bug fixed-by 73bfc05 ↪ session:local_13d5",想知道当时具体怎么修的 → 调 `memory_git_show("proj_openprogram", "73bfc05")` 拿到 diff,或 `memory_open_session("local_13d5")` 读当时对话。**虚拟层给坐标,实体层给真相,LLM 自己走完这条路。**

## 5. 提炼管道 (实体 → 虚拟)

### 5.1 触发

- **增量 (session-end)**: 会话 idle → 提炼这一会话的新 commit
- **批量 (sleep, 每天 03:00)**: 重新整理、消歧、矛盾检测、重建 core
- **压缩前 flush (借鉴 OpenClaw)**: context 压缩前插一轮,让 agent 把还在对话里的关键信息先落实体层

### 5.2 五阶段 (读实体 git,不读旧总结链)

```
Stage 1: collect   — 从 session-git + project-git 拉自上次提炼以来的新 commit
                     (读 DAG 节点全量: user/llm/code + reads 边, 不是只读对话文本)
Stage 2: extract   — LLM 一遍, 抽时间轴事件 + 图实体/关系, 每条挂 provenance
Stage 3: link      — 新实体跟现有图做 alias resolution ("worker"/"后端"/"daemon" → 同一点)
Stage 4: reconcile — 矛盾检测, 旧边标 superseded, 不删
Stage 5: project   — 重新投影 core.md / entity views / timeline 分片
```

Stage 2 是最贵的、最需要 prompt 调优的 (Graphiti 这块迭代了几个月)。可以先用规则版 (pattern match "I prefer X" → edge) 起步,prompt 版逐步替换。

### 5.3 关键: 直接读 DAG,不读旧总结

v1 管道读的是"已经抽过的对话文本"。v2 **直接读 session-git 里的 `Call` DAG**——包括 `code` 节点 (agent 跑了什么工具、什么参数、什么结果) 和 `reads` 边 (什么影响了决策)。这些正是图谱投影的金矿 (`agent ──ran──► pytest ──produced──► 3 failures`),v1 被压扁成文本扔了。

> 这是当前最大的缺口 (§0.5): 实体层已建好, 但 `memory/wiki/ingest.py` 至今读的还是
> `db.get_branch()` 的渲染文本, 没读 DAG, 也没碰 project-git。Phase 2 第一刀就是把这根管子接通。

## 6. Schema 总览

```
~/.openprogram/
├── sessions/<id>/               实体: 会话 (已实现, meta 带 project_id)
│   └── (绑了项目的会话改落 <项目>/.openprogram/sessions/<id>/, 由 sessions/locations.json 索引)
├── projects/
│   └── projects.json            project 注册表 (默认项目=逻辑标签, 无 default/.git)
├── memory/                       虚拟层 (Phase 2 起新增 timeline/graph; 现为 v1 journal/wiki/core)
│   ├── timeline/YYYY-MM.jsonl   虚拟: 时间轴
│   ├── graph/
│   │   ├── entities.jsonl       虚拟: 图的点
│   │   ├── edges.jsonl          虚拟: 图的边 (bi-temporal + provenance)
│   │   └── views/entity/*.md    虚拟: 可读视图
│   ├── core.md                  虚拟: 最小注入快照
│   ├── index/
│   │   ├── graph.sqlite         图查询 + FTS + 时间索引
│   │   └── embeddings.sqlite    向量 (hybrid search, 可选)
│   └── .state/                  提炼进度 / 锁
<用户工作目录>/.git/              实体: 真实 project (agent 改文件自动 commit)
```

## 7. 实施分期

| Phase | 内容 | 依赖 | 工作量 | 状态 |
|---|---|---|---|---|
| **0** | 修 baseline (LLM 桥 / watcher / ingest 字段), 让 v1 跑通 | — | 1d | ✅ 完成 |
| **1** | Project 概念: schema + session.project_id + 默认项目(标签) + 绑定 + project-git auto-commit | Session-Git | 3-4d | ✅ 完成 |
| **2** | 提炼管道重写: 读 session-git DAG → 时间轴 + 图 (带 provenance, bi-temporal) | 1 | 5-7d | ❌ |
| **3** | 召回重写: 只注入虚拟 + 导航工具 (memory_open_session / git_log / timeline / graph_neighbors) | 2 | 3-4d | ❌ |
| **4** | 物化视图投影 + core.md 重建 + hybrid search (向量) | 2,3 | 3-5d | ❌ |
| **5** | UI: Projects panel + session 回溯 timeline + `/memory` slash command | 1 | 3-5d | ⚠ 部分 |

剩余 ~2-3 周。每个 phase 独立可验证。

**Phase 2 建议的落地顺序** (先证明管道, 再砸钱调最贵的 Stage 2):
1. ~~先定 `Provenance` dataclass + 一个不带 LLM 的薄读写层~~ ✅ 已落地
   (`store/session/provenance.py`, commit e48af986)。剩 timeline/graph 的 JSONL schema;
2. 把提炼触发器接到**读 session-git DAG (+ project-git log)**, 而不是 `get_branch()` 文本——
   现在用 `iter_nodes_since` / `session_commits` / `project_commits` 接, 先把"实体→虚拟"管子接通;
3. 先写**规则版抽取器** (pattern match) 跑通端到端、带上真 provenance, 再换 LLM 版;
4. 加导航工具, 召回才能真正缩成"只注入虚拟"。

## 8. 跟现有代码的关系

**复用**:
- Session-Git (`<state>/sessions/`, `store/git_session.py`) — 实体层第一块,直接用
- Project-Git (`store/project_store.py`) — 实体层第二块,已实现
- `Call` DAG (`context/nodes.py`) — 实体层的节点模型,直接用
- `MemoryProvider` 抽象接口 (`memory/provider.py`) — 召回 hook 形状保留
- sleep 调度器骨架 (`memory/scheduler.py`, `sleep/runner.py`) — 改成读 git
- FTS 索引 (`memory/index.py`) — 扩成 graph.sqlite

**替换**:
- `memory/builtin/summarizer.py` 的事实抽取 / `memory/wiki/ingest.py` 的读渲染文本 → Stage 2 读 DAG 的实体/关系抽取
- `memory/wiki/` 的孤立主题页 → `graph/` (点+边+时间)
- 线性 `journal → wiki → core` 链 → fan-out from git

**新增**:
- `memory/graph/` (entities/edges/views)
- `memory/timeline/`
- 导航工具 (`functions/tools/memory/` 扩充)
- bi-temporal + provenance 字段

**删除**:
- `memory/journal/` 日记层 (被 timeline 取代; raw 真相已在 session-git)

## 9. Phase 0 baseline 的 bug (已修复, 2026-05)

原先 baseline 完全不产出记忆 (wiki/journal/core 全空)。根因是一串**连环 bug**,现已全部修复:

1. **`build_default_llm()` 返回 None** (`memory/llm_bridge.py`) — `_read_default_model()` 原来手抄 `agents.json` 索引文件,索引不存在的新装机器上就返 None → 整个记忆子系统静默禁用。改为委托 `agents.manager.get_default()` (有 索引 → DEFAULT_AGENT_ID → 第一个 agent 的回退链)。
2. **watcher 静默丢数据** (`memory/session_watcher.py`) — 拿不到 LLM 时原来 `return True` (标记已处理),等于永久丢弃这次会话的记忆。改为 `return False` (延后重试);原始对话永久存在 session-git 里,延后不丢任何东西。
3. **传错参数** — `ingest_session` 收的是 `runtime=` (带 `.exec` 的 Runtime),不是 `llm=` callable;watcher 先用 `_build_runtime()` 预检再传入。
4. **生成步骤没给工具** (`memory/wiki/ingest.py`) — `runtime.exec` 原来不带工具,写 0 个文件。补 `tools_allow=["read","write","edit","list","glob","grep","apply_patch"]` (避开 bash 的 schema bug)。
5. **`X | None` schema bug** (`functions/_runtime.py`) — PEP 604 联合类型参数缺 `type` 键,导致 codex HTTP 400 (全局影响,不止记忆)。`_python_type_to_json_schema` 已认 `types.UnionType`。
6. **读错字段** (`memory/wiki/ingest.py`) — `_render_conversation` 原来只读 `m["content"]`,但 DAG 节点把正文存在 `output`/`input` 里,导致每个真实会话都渲染成空、被判成"空/测试会话"。改为 `content or output or input`。

修复后已验证 wiki 页能真正生成 (`People/Fzkuji.md`、`Projects/OpenProgram/Architecture.md`),测试数据随后清掉。**注意: 这条链路修的是 v1 的 journal/wiki/core 管道;v2 的 Phase 2 会用读 DAG 的新管道取代它 (见 §5.3)。**

## 10. 论文角度的贡献点 (备忘)

1. **Git-native episodic memory for LLM agents** — 用版本控制系统作为 agent 长期记忆的不可变底座,支持回溯/分支/标准工具检索。
2. **Provenance-linked virtual memory** — 总结层不替代源,而是带坐标索引源;解决有损总结的根本矛盾 (压缩 vs 保真)。
3. **LLM-navigated recall** — agent 读紧凑地图、按需导航回源,对比 RAG 的盲目切块灌入;更小 context、更高保真、agent 主导检索。
4. **Dual bi-temporal projection** — 同一底座的时间轴 + 知识图谱双投影,均双时间轴,支持时间旅行与矛盾追踪。

评估方向 (备忘): context 占用 vs 召回准确率的 trade-off; 多会话长程一致性; 矛盾检测召回率; 跟 RAG / Zep / mem0 baseline 对比。
