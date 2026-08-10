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

## 子文档

| 文档 | 内容 |
|------|------|
| [`overview.md`](overview.md) | 两级架构（实体/虚拟两层 + provenance 召回），以及 `openprogram/memory/` 里在跑的线性总结链 |
| [`written-marker.zh.md`](written-marker.zh.md) | 记忆怎么知道哪些轮次已经写过，分四层：现在跑的位置游标以及它在分叉处丢掉什么、references 下八个框架各自怎么做、在节点上打「已写」标记的完整设计（含要改哪些文件、多大的改动量）、不受当前实现约束的理想形态 |
| [`written-marker.html`](written-marker.html) | 上述四层的可视化：序号从哪来、分叉时漏掉什么、八个框架并排、走行与三步写入的顺序、以及从记忆自身内容推导的那条路 |
| [`memory-architecture.html`](memory-architecture.html) | 可视化：两个写入入口、五步写入、暂存事务、写入游标、常驻块归谁维护、九个接口方法的接线状况、失败契约 |
| [`memory-comparison.html`](memory-comparison.html) | 可视化：`references/` 下八个框架怎么写长期记忆、怎么记住哪些还没写，八个维度逐条对照，包括分叉之后各家的游标怎么办、各家的常驻块归谁维护，以及我们的选择和两处计划中的改动落在哪一格 |
| [`memory-adoption.html`](memory-adoption.html) | 三层可视化：从那份对照里挑出的四条做法，放进我们的结构各要付什么代价，以及逐条判决（三条采纳，一条按实测的每轮耗时否掉） |
| [`speaker-identity.html`](speaker-identity.html) | 三层可视化：改之前是什么样（几个人共用一通会话、身份断在哪两处）、references下八个框架各自怎么做、我们怎么做的（两个文件，已落地） |
| [`git-as-entity-memory.md`](git-as-entity-memory.md) | 实体层的 git 底座（Session-Git + Project-Git） |
| [`entity-memory.md`](entity-memory.md) | 实体记忆：Session-Git + Project-Git，按生命周期组织 |
| [`virtual-memory.md`](virtual-memory.md) | 抽象记忆：Timeline + Graph + Core，按类型 × 生命周期组织 |

## 实现状态

实体层已就位：Project schema、`session.project_id`、project-git 都已实现。抽象层目前仍是 [`overview.md`](overview.md) 描述的线性总结链——提炼管道还没读 session-git DAG，召回还没做到只注入抽象层，导航工具也尚未注册。UI 上有顶栏项目选择器；Projects 面板、timeline 和 `/memory` 命令尚未建成。

