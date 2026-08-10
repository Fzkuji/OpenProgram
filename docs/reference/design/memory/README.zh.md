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
| [`overview.zh.md`](overview.zh.md) | 当前Source、Topic与派生视图架构，自动writer，权限边界，事务、失败行为和实现记录 |
| [`written-marker.zh.md`](written-marker.zh.md) | 记忆怎么知道哪些轮次已经写过，分四层：已替换的位置游标、references下八个框架、已实现的节点marker，以及仍延期的事件通知方案 |
| [`written-marker.html`](written-marker.html) | 上述四层的可视化：序号从哪来、分叉时漏掉什么、八个框架并排、走行与三步写入的顺序、以及从记忆自身内容推导的那条路 |
| [`memory-architecture.html`](memory-architecture.html) | 可视化：两个写入入口、五步写入、暂存事务、写入游标、常驻块归谁维护、九个接口方法的接线状况、失败契约 |
| [`memory-comparison.html`](memory-comparison.html) | 可视化：`references/` 下八个框架怎么写长期记忆、怎么记住哪些还没写，八个维度逐条对照，包括分叉之后各家的游标怎么办、各家的常驻块归谁维护，以及我们的选择和两处计划中的改动落在哪一格 |
| [`memory-adoption.html`](memory-adoption.html) | 三层可视化：从那份对照里挑出的四条做法，放进我们的结构各要付什么代价，以及逐条判决（三条采纳，一条按实测的每轮耗时否掉） |
| [`speaker-identity.html`](speaker-identity.html) | 三层可视化：改之前是什么样（几个人共用一通会话、身份断在哪两处）、references下八个框架各自怎么做、我们怎么做的（两个文件，已落地），以及这个形状留下的两件事（发信人能在正文里打第二个标签、没有键可以按人过滤记忆）和收口它们的那个字段 |
| [`authority-landscape.html`](authority-landscape.html) | 当前owner/paired权限方法、本地参考框架证据、采用/修改/拒绝记录、执行顺序可视化和实现进度 |
| [`authority-handoff.md`](authority-handoff.md) | 已定案的权限与writer决策、延期边界、review处理结果和实现交接 |
| [`git-as-entity-memory.md`](git-as-entity-memory.md) | 实体层的 git 底座（Session-Git + Project-Git） |
| [`entity-memory.md`](entity-memory.md) | 实体记忆：Session-Git + Project-Git，按生命周期组织 |
| [`virtual-memory.md`](virtual-memory.md) | 抽象记忆：Timeline + Graph + Core，按类型 × 生命周期组织 |

## 实现状态

已提交实现保存append-only Source证据、由模型写入的Topic block，以及Runtime确定性生成的Core、Timeline、Recent和Relations视图。每条Source都带Runtime确定的authority provenance：SessionDB writer和通用`memory_update`事务都从持久化的轮次authority构造，不接受调用方payload里的身份字段；缺少完整authority时创建Source直接失败。自动writer读取SessionDB分支，把成功处理状态记在来源节点上，默认使用聊天agent的provider和模型，`memory.writer.model`只覆盖writer模型；所有修改通过暂存事务安装。

记忆工具、CLI和Web UI已经注册。已提交基线包含writer状态、一次性trusted Source backfill、`memory.backend=none`边界以及从SessionDB到watcher状态的组合集成测试。真实writer验收已处理2条符合写入条件的消息，但正式工作区尚未对152条未引用Source执行历史backfill。

Topic block新增的Source引用必须解析到`trusted` frame，并且必须属于本次事务自己归档的证据，因此任何工具路径都无法引用`pending` Source或把无关Source挂到新段落上。写入失败归入一个封闭的`MemoryWriteFailureCode`枚举，状态文件、CLI、工具、API和Web UI共用同一契约；idle watcher在跨进程锁下逐条持久化终态结果；未配对群聊归档有明确的频率与存储上限。按请求方档位过滤读取在[`authority-handoff.md`](authority-handoff.md)中已完成设计但尚未实现。越权请求hold队列、分支语义provenance、跨会话spawn关系和事件通知writer仍作为独立设计延期。
