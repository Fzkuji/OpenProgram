# Memory — 记忆系统设计

## 定义

记忆 = **实体记忆**（完整不可变的真实历史）+ **抽象记忆**（从实体提炼的紧凑索引）。

实体记忆是 ground truth，基于 git，每 turn 一 commit，不可篡改。抽象记忆是从实体层派生的导航地图，每条都带 provenance 指针回指实体层出处。LLM 只注入抽象记忆；需要细节时，LLM 自己顺着指针导航回实体层去取。

## 架构

```
实体记忆 (raw, git, immutable, complete)
  ├─ Session-Git    每会话一个 repo，每 turn 一 commit
  └─ Project-Git    绑用户工作目录，agent 改文件 → 自动 commit
         │
         │  提炼 (distillation)：5-stage pipeline, 带 provenance
         ▼
抽象记忆 (derived, compact, provenance-linked)
  ├─ Timeline       时间轴事件流（何时发生了什么）
  ├─ Graph          知识图谱（实体之间什么关系）
  └─ Core.md        ≤2KB 注入快照（LLM 每次都看到）
         │
         │  召回 (recall)：只注入抽象，LLM 用工具导航回实体
         ▼
LLM Context
```

## 设计原则

1. **Git-native** — 实体记忆直接用 git，不造轮子。commit 不可变、log 是时间线、checkout 是时光机。
2. **Provenance-linked** — 抽象层不替代实体层，而是给它建索引。每条抽象记忆带坐标 `(project, session, commit, timestamp)` 指回出处。
3. **Bi-temporal** — 每条记忆记两个时间：`event_time`（事情发生时）和 `ingestion_time`（记下来时）。支持时间旅行查询和矛盾检测。
4. **LLM-navigated recall** — 不灌 raw chat 进 context。只注入紧凑地图，LLM 按需用工具走回实体层取细节。

## 实施状态

| Phase | 内容 | 状态 |
|-------|------|------|
| 0 | Baseline 修复（LLM 桥 / watcher / ingest） | ✅ |
| 1 | 实体层：Project schema + session.project_id + project-git | ✅ |
| 2 | 提炼管道重写：读 session-git DAG → timeline + graph | ❌ 未开始 |
| 3 | 召回重写：只注入抽象 + 导航工具 | ❌ 未开始 |
| 4 | 物化视图 + hybrid search（向量） | ❌ 未开始 |
| 5 | UI：Projects 面板 / timeline / `/memory` | ⚠️ 部分 |

## 子文档

| 文档 | 内容 |
|------|------|
| [`entity-memory.md`](entity-memory.md) | 实体记忆：Session-Git + Project-Git，按生命周期组织 |
| [`virtual-memory.md`](virtual-memory.md) | 抽象记忆：Timeline + Graph + Core，按类型 × 生命周期组织 |

