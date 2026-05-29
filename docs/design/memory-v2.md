# Memory v2 — 实体/虚拟两级 + Provenance 导航召回

> 状态: 设计稿。取代当前线性总结链 (`short-term → wiki → core`)。
> 前置阅读: [`git-as-entity-memory.md`](git-as-entity-memory.md) (实体层最初设计)、
> [`memory.md`](memory.md) (当前实现)。

## 0. 一句话

记忆分**实体层**(git 存的、不可变、完整的真实历史)和**虚拟层**(从实体提炼的、紧凑的、带指针的索引)。LLM 调用时**只注入虚拟层**;需要原始细节时,LLM 顺着虚拟层里的 **provenance 指针**,用工具自己导航回实体层去取。

```
        ┌──────────────────────── 实体层 (raw, git, 完整) ────────────────────────┐
        │   Session-Git            Project-Git            Default-Project-Git       │
        │   每会话一个 repo         绑用户工作目录          home 隐藏目录的兜底 repo   │
        │   每 turn 一 commit       agent 改文件自动 commit  没指定路径的会话都进这    │
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

## 1. 设计动机 / 跟现有方案的区别

### 现状问题

当前实现 (`memory.md`) 是**线性有损链**: `raw chat → 抽 0-10 facts → short-term → wiki → core`。每一层都丢信息,且抽象层 (wiki) 是从上一层有损总结来的,**不直接读实体层**。结果: 实体层 (sessions-git) 和抽象层 (wiki) 是两套不相通的东西,中间管道还断了 (`build_default_llm` 返回 None,见 §9)。

### 跟主流框架对比

| 框架 | 实体层 | 抽象层 | 召回方式 | 时间维度 | 知识图谱 |
|---|---|---|---|---|---|
| Claude Code | CLAUDE.md + 会话 | auto-memory MEMORY.md (索引+topic) | 注入索引,topic 按需读 | ❌ | ❌ |
| OpenClaw | MEMORY.md + 日记 | 同上 + wiki 插件 | 注入 + 语义搜索 | ❌ | ⚠ 弱 |
| mem0 | — | 向量 DB | RAG 切块灌入 | ⚠ 写入时间 | ❌ |
| Letta/MemGPT | 对话历史 | tiered (core/recall/archival) | LLM 工具搬运 | ⚠ | ❌ |
| Zep/Graphiti | — | temporal knowledge graph | 图查询 | ✅ bi-temporal | ✅ |
| **本设计** | **git (session+project)** | **时间轴 + 知识图谱** | **注入虚拟,LLM 导航回实体** | **✅ bi-temporal** | **✅** |

### 三个新颖点 (论文角度)

1. **Git 作为 episodic memory 的底座**。实体记忆不是自研存储,直接用 git: commit 不可变 = 真相不可篡改; log = 时间线; checkout = 时光机; branch = 探索过的分支; 而且 agent 能用标准工具 (`git log` / `grep` / `diff`) 自己读。可审计、可复现、可回溯。

2. **Provenance-pointer 索引,而非替代**。虚拟层不取代实体层,而是给它建一个**带坐标的导航地图**。每条虚拟记忆都挂一个指针 `(project, session, commit, timestamp)`,指回它在实体层的出处。解决了"有损总结丢上下文"的根本问题——**任何时候都能顺着指针钻回 ground truth**。

3. **LLM 自导航召回 (map → territory),而非 RAG 灌块**。传统 RAG 把相关 chunk 切出来塞进 context,污染上下文且丢结构。本设计只注入紧凑的虚拟地图,LLM 读到"2026-05 在项目 X 修了 Windows bug,完整历史在 session local_13d5",**需要细节时自己用工具走过去取**。context 小、保真度满、检索由 agent 主导。

4. **时间轴 + 知识图谱双投影,都 bi-temporal**。同一个 git 底座投影出两个正交视图: 时间轴回答"何时",知识图谱回答"什么关系"。两者都记两个时间——`event_time`(事情发生的时间) 和 `ingestion_time`(我们记下来的时间)——支持时间旅行查询和矛盾检测。

## 2. 实体层 (Entity Memory)

### 2.1 心智模型: 每个会话都属于某个 project

核心简化: **没有"无主"的会话**。每个会话都属于一个 project,区别只在于这个 project 是不是用户真实的工作目录。

```
session 创建时:
  指定了工作目录路径?
    是 → 绑到那个路径的 Project-Git (用户真实代码仓/文档仓)
    否 → 绑到 Default-Project (home 隐藏目录里的兜底 git 仓)
```

这样实体层永远有清晰归属,虚拟层也永远能按 project 聚合。

### 2.2 三种 git 仓

```
~/.agentic/
├── sessions-git/<session_id>/          ← Session-Git (已实现)
│   ├── .git/                            每 turn 一 commit
│   ├── meta.json                        title / agent_id / project_id / head
│   ├── history/NNNN-<role>-<id>.json    DAG 节点 (user/llm/code)
│   └── workdir/                         此会话的临时工作目录
│
├── projects/
│   ├── projects.json                    project 注册表 (id → {name, path, sessions, status})
│   └── default/                         ← Default-Project-Git (兜底)
│       └── .git/                        没指定路径的会话,其"项目历史"落这
│
└── memory/                              ← 虚拟层 (见 §3)

<用户工作目录>/                           ← Project-Git (绑定时用的真实仓)
└── .git/                                agent 改文件 → 自动 commit
```

### 2.3 Session-Git (已实现,保留)

现状已经做好,见 `sessions-git/<id>/`。每个节点是 `Call` (role = user / llm / code),边是 `called_by` (调用链) + `reads` (上下文引用)。**v2 不改 Session-Git 的存储,只补一个 `project_id` 字段到 `meta.json`。**

### 2.4 Project-Git

Project = 一个长期工作单元,关联:
- 一个**文件系统目录** (用户的真实代码仓 / 文档仓,或兜底的 default)
- **多个 session** (在这个项目上的多次对话)
- 名字 / 描述 / 状态

```python
@dataclass
class Project:
    id: str                       # proj_xxx, 或 "default"
    name: str
    path: str                     # 绝对路径; default 项目 = ~/.agentic/projects/default
    is_default: bool              # True 表示兜底项目
    session_ids: list[str]        # 反向索引
    status: str                   # "active" | "paused" | "done"
    created_at: float
```

**绑定逻辑**:
- 用户在真实目录里启动 / 显式 `openprogram --project <path>` → 该路径的 git 仓 (没有就 `git init`)
- 没指定 → `default` 项目

**自动 commit (Strategy A,沿用原设计)**: turn 结束时,若 session 绑了真实 project 且 agent 改过文件:
```
if git_status_clean_except_agent_edits():
    git add -A && git commit -m "[agent <session>] turn <N>: <user msg>"
else:
    # 用户有未提交改动 → 不污染,UI 警告
    skip + warn
```

### 2.5 Default-Project 的意义

home 隐藏目录里的 `projects/default/` 是个真 git 仓。所有"随手聊、没绑项目"的会话,它们的项目级历史 (如果产生了文件) 落这里。等于给"通用对话期"一个统一的归属,避免实体层出现归属真空。

## 3. 虚拟层 (Virtual Memory)

两个 projection,都从实体层提炼,都带 provenance 指针,都 bi-temporal。

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
~/.agentic/memory/timeline/
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

实体 + 关系。回答"什么和什么是什么关系"。**这是把当前 `wiki/<kind>/` 升级成真图**——现在只有孤立的实体页,v2 加边和时间。

```
~/.agentic/memory/graph/
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
· worker ──listens-on──► :8109

需要细节: memory_open_session(<id>) / memory_git_log(<project>) / memory_timeline(<entity>)
═══════════════════════════════════════════════
```

**不灌任何 raw chat。** 全是紧凑的带指针摘要。

### 4.2 导航: LLM 顺指针自取

LLM 需要原始细节时,调导航工具走回实体层:

| 工具 | 干啥 | 落到实体层哪 |
|---|---|---|
| `memory_open_session(session_id, [turn])` | 读某会话的原始消息 | sessions-git/<id>/history/ |
| `memory_git_log(project_id, [since])` | 看某项目的提交历史 | Project-Git / Default-Git |
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

当前管道读的是"已经抽过的 0-10 facts"。v2 **直接读 session-git 里的 Call DAG**——包括 `code` 节点 (agent 跑了什么工具、什么参数、什么结果) 和 `reads` 边 (什么影响了决策)。这些正是图谱投影的金矿 (`agent ──ran──► pytest ──produced──► 3 failures`),当前被压扁成文本扔了。

## 6. Schema 总览

```
~/.agentic/
├── sessions-git/<id>/           实体: 会话 (已实现, 加 project_id)
├── projects/
│   ├── projects.json            project 注册表
│   └── default/.git/            兜底 project
├── memory/
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
<用户工作目录>/.git/              实体: 真实 project
```

## 7. 实施分期

| Phase | 内容 | 依赖 | 工作量 |
|---|---|---|---|
| **0** | 修当前 `build_default_llm` 返 None 的 bug, 让 baseline 跑通 | — | 1d |
| **1** | Project 概念: schema + session.project_id + default-project + 绑定逻辑 + project-git auto-commit | Session-Git (已有) | 3-4d |
| **2** | 提炼管道重写: 读 session-git DAG → 时间轴 + 图 (带 provenance, bi-temporal) | 1 | 5-7d |
| **3** | 召回重写: 只注入虚拟 + 导航工具 (memory_open_session / git_log / timeline / graph_neighbors) | 2 | 3-4d |
| **4** | 物化视图投影 + core.md 重建 + hybrid search (向量) | 2,3 | 3-5d |
| **5** | UI: Projects panel + session 回溯 timeline + `/memory` slash command | 1 | 3-5d |

总 ~3-4 周。每个 phase 独立可验证。

## 8. 跟现有代码的关系

**复用**:
- Session-Git (`sessions-git/`, `store/git_session.py`) — 实体层第一块,直接用
- `Call` DAG (`context/nodes.py`) — 实体层的节点模型,直接用
- `MemoryProvider` 抽象接口 (`memory/provider.py`) — 召回 hook 形状保留
- sleep 调度器骨架 (`memory/scheduler.py`, `sleep/runner.py`) — 改成读 git
- FTS 索引 (`memory/index.py`) — 扩成 graph.sqlite

**替换**:
- `memory/builtin/summarizer.py` 的"0-10 facts"抽取 → Stage 2 的实体/关系抽取
- `memory/wiki/` 的孤立主题页 → `graph/` (点+边+时间)
- 线性 `short-term → wiki → core` 链 → fan-out from git

**新增**:
- `memory/graph/` (entities/edges/views)
- `memory/timeline/`
- Project schema + project-git (`projects/`)
- 导航工具 (`functions/tools/memory/` 扩充)
- bi-temporal + provenance 字段

**删除**:
- `memory/short-term/` 日记层 (被 timeline 取代; raw 真相已在 session-git)

## 9. 当前 baseline 的 bug (Phase 0 先修)

`build_default_llm()` (`memory/llm_bridge.py`) 在你的机器上返回 None,导致:
1. session-end watcher 启动但拿不到 LLM
2. 每次扫到 idle 会话, 看 `llm is None` → **标记已处理然后返回** → 数据永久丢失
3. wiki / journal / core 全空

根因待查 (读 agents.json → model registry 某一环静默失败)。修了之后 baseline 会真的开始 ingest。**v2 的提炼管道也依赖这个 LLM 桥,所以无论如何先修。**

## 10. 论文角度的贡献点 (备忘)

1. **Git-native episodic memory for LLM agents** — 用版本控制系统作为 agent 长期记忆的不可变底座,支持回溯/分支/标准工具检索。
2. **Provenance-linked virtual memory** — 总结层不替代源,而是带坐标索引源;解决有损总结的根本矛盾 (压缩 vs 保真)。
3. **LLM-navigated recall** — agent 读紧凑地图、按需导航回源,对比 RAG 的盲目切块灌入;更小 context、更高保真、agent 主导检索。
4. **Dual bi-temporal projection** — 同一底座的时间轴 + 知识图谱双投影,均双时间轴,支持时间旅行与矛盾追踪。

评估方向 (备忘): context 占用 vs 召回准确率的 trade-off; 多会话长程一致性; 矛盾检测召回率; 跟 RAG / Zep / mem0 baseline 对比。
