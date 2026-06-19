# 抽象记忆 (Virtual Memory)

## 1. 概念

抽象记忆是从实体记忆提炼出的紧凑索引。它不替代实体层，而是给实体层建一张**带坐标的导航地图**。

核心属性：
- **Provenance-linked**：每条记忆都带指针回指实体层出处
- **Bi-temporal**：每条记忆记两个时间——`event_time`（事情发生时）和 `ingestion_time`（记下来时）
- **不可变追加**：旧记录不删除，冲突时标 `superseded_by`

当前抽象记忆有三种类型（+ v1 过渡期的 Journal/Wiki）：

| 类型 | 回答的问题 | 存储 | 状态 |
|------|-----------|------|------|
| Timeline | 何时发生了什么 | `memory/timeline/YYYY-MM.jsonl` | Phase 2 待实现 |
| Knowledge Graph | 什么和什么是什么关系 | `memory/graph/{entities,edges}.jsonl` | Phase 2 待实现 |
| Core.md | LLM 每次必看的最小快照 | `memory/core.md` | v1 已有，Phase 2 重写 |
| Journal/Wiki (v1) | 同上（粗粒度） | `memory/short-term/`, `memory/wiki/` | 运行中，待被取代 |

## 2. Timeline（时间轴）

按时间组织的事件流。回答"何时发生了什么"。

### 2.1 创建

**触发**：
- Session 空闲（idle ≥30 min）→ watcher 触发提炼
- Daily sleep（03:00）→ 批量整理

**过程**：
1. 从实体层读新增的 DAG 节点（`iter_nodes_since()`）
2. LLM 提取事件（Stage 2: extract）
3. 每个事件挂 `Provenance` 指针
4. Append 到对应月份的 JSONL 文件

### 2.2 存储

```
~/.openprogram/memory/timeline/
├── 2026-05.jsonl
├── 2026-06.jsonl
└── ...
```

单条记录：
```json
{
  "id": "ev_abc",
  "summary": "在 OpenProgram 修了 Windows cp1252 编码 bug，涉及 38 个文件",
  "kind": "work",
  "provenance": {
    "project_id": "proj_openprogram",
    "session_id": "local_13d5",
    "commit": "73bfc05",
    "event_time": 1779900000,
    "ingestion_time": 1779986400
  },
  "entities": ["project.openprogram", "issue.cp1252"]
}
```

`kind` 枚举：`work` | `decision` | `learning` | `event`

### 2.3 读取

- 按时间范围：`memory_timeline(since, until)`
- 按关联实体：`memory_timeline(entity="project.openprogram")`
- 召回时注入最近高信号事件到 Core.md

### 2.4 更新

Append-only。同一事实被重新提炼时，旧记录标 `superseded_by` 指向新记录 ID，不删除。

### 2.5 删除

不删除。理由：事件是历史事实的记录，删除会破坏时间线完整性。标记 superseded 即可。

## 3. Knowledge Graph（知识图谱）

实体 + 关系的图结构。回答"什么和什么是什么关系"。

### 3.1 创建

**触发**：同 Timeline（session-end / daily sleep）。

**过程**：
1. 提炼管道 Stage 2 从 DAG 抽取实体和关系
2. Stage 3 做 alias resolution（"worker"/"后端"/"daemon" → 同一节点）
3. Stage 4 做矛盾检测（新边与旧边冲突 → 标 superseded）
4. Append 到 JSONL

### 3.2 存储

```
~/.openprogram/memory/graph/
├── entities.jsonl           点
├── edges.jsonl              边（带 bi-temporal + provenance）
└── views/
    └── entity/<slug>.md     每个实体一个可读页面
```

实体：
```json
{
  "id": "project.openprogram",
  "type": "project",
  "name": "OpenProgram",
  "attrs": {"path": "/Users/fzkuji/OpenProgram", "lang": "python"},
  "scope": "global"
}
```

边：
```json
{
  "from": "issue.cp1252",
  "to": "commit.73bfc05",
  "relation": "fixed-by",
  "event_time": 1779900000,
  "ingestion_time": 1779986400,
  "provenance": {"project_id": "proj_openprogram", "session_id": "local_13d5"},
  "confidence": 0.95,
  "superseded_by": null
}
```

### 3.3 读取

- 邻居遍历：`memory_graph_neighbors(entity, hops=2)`
- Hybrid search：`memory_search(query)` — FTS + 可选向量
- Scope 过滤：查询时按当前上下文过滤（`global` + `project:<current>`）

### 3.4 更新

- **Alias resolution**：相同实体的不同名称合并为一个节点
- **矛盾处理**：新边与旧边冲突时，旧边标 `superseded_by` 指向新边，不删除旧边
- **Views 重建**：Stage 5 重新投影 `views/entity/*.md`

### 3.5 删除

不删除。旧边标 `superseded_by`，保留完整历史。

### 3.6 Scope 标签

每个 entity/edge 带 scope，查询时按上下文过滤：

```
scope: "global"                  跨所有项目（如用户语言偏好）
scope: "project:openprogram"     仅此项目
scope: "agent:research"          仅此 agent
```

## 4. Core.md（注入快照）

LLM 每次调用时注入 system prompt 的最小记忆快照。≤2KB。

### 4.1 创建/更新

**触发**：sleep::deep（daily 03:00）重新生成。

**来源**：
- Timeline 中最近的高信号事件（top-N by recency × importance）
- Graph 中高频/高置信的实体和关系

**每一行都带 provenance 指针**（`↪ session:<id>`），LLM 看到后可用导航工具深入。

### 4.2 读取

每次 LLM 调用时，Core.md 内容被注入 system prompt：

```
═══════════════════════════════════════════════
OpenProgram 记忆 — 项目: OpenProgram, 最后整理 2026-06-18
═══════════════════════════════════════════════
[时间轴 · 最近]
· 2026-06-15 重构 Functions 页面为三 tab 布局            ↪ session:local_fc03
· 2026-06-17 修复 CLI attended mode 问题                ↪ session:local_d125

[图谱 · 当前项目相关]
· OpenProgram 在 /Users/fzkuji/OpenProgram (python, next.js)
· worker ──listens-on──► :18109

需要细节: memory_open_session(<id>) / memory_git_log(<project>)
═══════════════════════════════════════════════
```

### 4.3 删除

覆盖式更新。每次 sleep::deep 重新生成整个文件，旧内容被完全替换。

## 5. Journal/Wiki (v1 过渡)

### 5.1 当前状态

v1 的三层架构仍在运行：

```
short-term/YYYY-MM-DD.md  → (sleep::light) →  wiki/<kind>/<slug>.md  → (sleep::deep) →  core.md
```

- **short-term**：session-end 时追加 0–10 条事实到当日文件
- **wiki**：sleep::deep 把 short-term 事实提升为知识页面
- **core.md**：sleep::deep 从 wiki 投影最小快照

### 5.2 问题

v1 管道从 `get_branch()` 渲染的对话文本读取，不读 DAG 节点。丢失了：
- 工具调用链（agent 跑了什么、参数、结果）
- `reads` 边（什么影响了决策）
- project-git commit 历史

### 5.3 未来

Phase 2 完成后，v1 管道被新的 Timeline + Graph 取代：
- `short-term/` → Timeline 替代
- `wiki/` → Graph 替代
- `core.md` → 从 Timeline + Graph 重新投影

过渡期两套并存，新管道验证通过后移除旧管道。

## 6. 提炼管道（实体 → 抽象）

### 6.1 触发时机

| 触发 | 频率 | 目的 |
|------|------|------|
| Session-end | 会话空闲 ≥30 min | 增量提炼新 turn |
| Sleep (03:00) | 每天一次 | 批量整理、消歧、矛盾检测、重建 core |
| Pre-compress | context 接近上限时 | flush 对话中的关键信息到实体层 |

### 6.2 五阶段 (Five Stages)

```
Stage 1: Collect    从 session-git + project-git 拉自上次提炼以来的新 commit
                    读 DAG 节点全量（user/llm/code + reads 边）

Stage 2: Extract    LLM 一遍：抽取时间轴事件 + 图实体/关系，每条挂 provenance

Stage 3: Link       新实体跟现有图做 alias resolution

Stage 4: Reconcile  矛盾检测，旧边标 superseded，不删

Stage 5: Project    重新投影 core.md / entity views / timeline 分片
```

Stage 2 是最贵的（需要 LLM），可先用规则版（pattern match）起步，prompt 版逐步替换。

### 6.3 关键：直接读 DAG

v1 读渲染文本。v2 直接读 session-git 里的 `Call` DAG，包括 `code` 节点（工具调用）和 `reads` 边（上下文引用）。这些是图谱投影的关键数据来源。

读层已实现：`store/session/provenance.py` 提供 `iter_nodes_since()` / `node_provenance()` / `session_commits()` / `project_commits()`。

## 7. 召回机制

### 7.1 注入（只给抽象）

LLM 每次调用时注入：
- Core.md（≤2KB，始终注入）
- 可选：按当前 query 召回的 timeline/graph 结果

不注入任何 raw chat。

### 7.2 导航（LLM 自取）

LLM 需要实体层细节时，调导航工具：

| 工具 | 作用 | 落到实体层哪 |
|------|------|-------------|
| `memory_open_session(session_id, turn)` | 读某会话原始消息 | Session-Git history/ |
| `memory_git_log(project_id, since)` | 看某项目提交历史 | Project-Git |
| `memory_git_show(project_id, commit)` | 看某次改了什么 | git show |
| `memory_timeline(entity\|since\|until)` | 时间轴切片 | Virtual timeline |
| `memory_graph_neighbors(entity, hops)` | 图的邻居 | Virtual graph |
| `memory_search(query)` | 跨虚拟层 hybrid 搜索 | Virtual (FTS + 向量) |
