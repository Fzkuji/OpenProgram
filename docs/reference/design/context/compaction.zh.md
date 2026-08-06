# 上下文压缩

压缩通过把最老的轮次替换成一份 LLM 写的摘要，让长对话保持在模型上下文窗口之内。
本文是压缩的唯一权威文档：结果如何存储、如何改变模型读到的内容、如何与分支和多轮
压缩组合、由哪些不变量保护。摘要在 DAG 上的视觉呈现（胶囊）由
[dag/rendering.md 第九节](../runtime/dag/rendering.md)规定；本文定义渲染所消费的
数据与语义。

## 一、模型：滚动摘要，永远只有一份现役

一个会话同一时刻至多有**一份现役摘要**。再次压缩不会在旧摘要上叠加第二份：
summarizer 接收上一份摘要文本作为输入、吸收它、产出替代品。会话的
`extra_meta._last_summary_id` 指明现役摘要；`extra_meta._last_summary_text`
携带其文本供下一次接力。更老的摘要节点作为遗迹留在盘上，面向图打
`superseded_summary` 标记，此后永不再被查询。

因此下一次 LLM 请求的形状永远是：

```
[系统提示] [现役摘要] [保留尾巴，原文] [新用户消息]
```

——一份摘要，绝不叠加，后面跟着它没有吃掉的轮次。

## 二、数据模型：append-only 的替身

压缩恰好写一个节点，不改任何东西：

| 字段 | 值 |
|---|---|
| `id` | `summary_<hex>` |
| `role` | `llm`，`name = "context/summary"` |
| `output` | `[Previous conversation summary]\n<文本>` |
| `predecessor` | 第一个被覆盖节点的 predecessor（拼接点） |
| `metadata.covers_ids` | 它所替代的链节点的有序 id 列表 |
| `metadata.compaction` | `true` |

由 append-only 推出的规则：

- **不克隆。** 保留尾巴的 id 和 predecessor 原封不动。克隆尾巴会造出第二套 id
  空间，逼所有消费方做翻译。
- **不改边。** 被覆盖节点原样留在链上；第一个保留节点仍指向最后一个被覆盖节点。
  忽略摘要即可随时重建压缩前的视图。
- **不动 head。** 压缩是纯插入。HEAD 停在原来的分支尖上；摘要改变的是该分支
  *渲染*出什么，不是哪条分支处于活动状态。
- **用 id，不用 seq 区间。** `covers_ids` 是被总结内容的记录。DAG 里 seq 区间
  表达不了这件事——姐妹分支的 seq 交错，区间扫描会把死分叉拖进覆盖范围，而且
  HEAD 一动答案就变。区间形式（`metadata.covers = [first_seq, last_seq]`）在
  本设计中不存在，无人读写。
- **`covers_ids` 是真实轮次组成的连续链段。** 它永不包含另一个摘要节点。再压缩
  吃掉"上一份摘要加 k 轮"时，新节点的 `covers_ids` 是旧链段延长这 k 轮的 id——
  覆盖永远以底层对话表达，旧摘要经 `_last_summary_id` 退役，而非嵌套。

## 三、渲染规则：链段替换

`render_context`（context/nodes.py）是决定模型读什么的唯一场所，聊天与
`runtime.exec` 同路。压缩以一条规则进入它：

> 设 S 为会话的现役摘要，L = `covers_ids(S)`，是某条对话链的连续链段。从 head H
> 渲染时：**若 L 的每个节点都在 H 的 predecessor 主链上，则从渲染中剔除 L，并在
> L 的位置接纳 S**（S 自己的拼接点——它的 `predecessor`——正好把它放在链段开始
> 处）。否则按原文渲染主链。

这条规则买到的性质，每一条都是需求而非副作用：

- **摘要真的进入 prompt。** S 靠规则被接纳，而不是指望主链碰巧走到一个无人指向
  的节点。压缩过的分支渲染出的 id 列表就是 `[ROOT, S, 保留尾巴…]`。
- **分支隔离自动成立。** 主链不完整包含覆盖链段的分叉——从覆盖范围内部重试出来
  的、同时代的死姐妹——通不过 ⊆ 检验，按原文渲染。它的上下文从未被压缩过，也
  不该继承一份总结了它没有的轮次的摘要。
- **存储与 HEAD 无关。** 任何时候 checkout 任何分支，渲染结果只由数据决定。
  没有任何渲染结果取决于别的东西运行时 HEAD 恰好在哪。
- **被取代的摘要在此不可见。** 只查询现役摘要；遗迹永不剔除任何东西。

同一条规则、同一份 `covers_ids`，驱动 DAG 的胶囊折叠——图在上下文携带摘要的
分支上显示折叠胶囊，在按原文渲染的分支上显示原始轮次。一件事实，两个投影。

## 四、压缩流水线

`trigger_compaction`（手动 `/compact`）、自动压缩（轮前预算 ≥ 80%）和被动压缩
（provider 溢出报错）都跑同一条 `engine.compact` 流水线：

1. **输入是渲染视图，不是原始链。** 交给切点计算的 history 就是模型当前读到的
   内容：先现役摘要（如有），再保留轮次。在这里喂原始 predecessor 走链，会把
   上一份摘要已经吃掉的轮次再总结一遍，产出覆盖完全重复的第二份摘要。
2. **切分。** `find_cut_index` 挑选切点使保留尾巴装进 `keep_recent_tokens`
   （默认由预算策略给出），向前对齐到用户轮边界；渲染视图的开头元素——如有
   上一份摘要——必然落在被覆盖一侧。
3. **总结。** summarizer 从被覆盖切片写出新摘要，接力 `previous_summary`，
   已总结过的内容不丢失。
4. **持久化。** 一个节点，按第二节的形状。新 `covers_ids` = 旧链段（若覆盖了
   一份摘要）延长新被覆盖轮次的 id。`_last_summary_id` / `_last_summary_text`
   移到新节点。
5. **事件。** `compaction_started` / `compaction_finished`（或
   `compaction_failed`）经会话通道广播；finished 事件携带 `summary_id`、数量
   与 token 增量。历史不足 4 条时短路，给用户可见的 `local_command` 提示。

## 五、HEAD 完整性

压缩曾是能以副作用挪动 HEAD 的若干写者之一。本设计只允许一个移动者：

- **单一写者。** `SessionStore.set_head` 是 HEAD 改变的唯一方式，且只被显式的
  面向用户的移动调用：发消息推进、重试/编辑分叉、checkout、rewind、删分支。
  压缩、会话加载、worker 重启、切换模型、meta 保存永不调用它。
- **append 只在链延长时推进 HEAD。** `append_message` 仅当新节点的
  `predecessor` 等于当前 HEAD 时才把 HEAD 移上去——自然的"对话长了"情形。
  其余插入（摘要拼接、旁支写入、遗迹）不动 HEAD。这取代旧的无条件自动推进
  加各调用方快照/恢复的补偿。
- **镜像只读。** webui 为显示维护内存 `conv` 镜像（消息 + head）。它从存储
  水合、永不回写：`save_meta` 不携带 `head_id`，镜像积累的显示行（如
  `compaction_finished` 事件渲染进聊天流时追加的摘要标记行）永远不可能变成
  存储的 head 或新的存储行。存储永远在镜像上游，重启前后皆然。

## 六、图上显示什么

由 [dag/rendering.md 第九节](../runtime/dag/rendering.md)定义；本侧的下发契约：

- 现役摘要行携带 `covers_ids`——照抄 `metadata.covers_ids`，加上被覆盖轮次的
  caller 子树（被覆盖的轮连同它的工具调用一起折叠），去掉已不存在的 id。
- 被取代的摘要行携带 `superseded_summary: true`，不携带 `covers_ids`。
- graph builder 不做 seq 运算、不做依赖 head 的过滤；它关于覆盖说的每句话都是
  `covers_ids` 的复述。

## 七、不变量及其测试

| 不变量 | 落实/测试位置 |
|---|---|
| 压缩后渲染活动分支得到 `[ROOT, S, 保留尾巴…]`——被覆盖 id 缺席、S 在场 | render_context 测试；场景套件 |
| 压缩永不移动 HEAD | persister 测试；场景套件 |
| 不完整包含覆盖链段的分支按原文渲染 | render_context 分支隔离测试 |
| 再压缩的输入不含已覆盖的原文轮；新 `covers_ids` 延长旧链段 | 压缩流水线测试 |
| 下发时每会话至多一行携带 `covers_ids`；旧摘要带 `superseded_summary` | `test_graph_builder_covers.py` |
| `covers_ids` 永不点名被总结链之外的节点（死分叉在外） | `test_graph_builder_covers.py` |
| HEAD 挺过：worker 重启、会话加载、切模型、meta 保存 | 场景套件（`test_dag_mutation_scenarios.py`） |
| 存储往返：任何镜像行或镜像 head 都不会写回存储 | webui persistence 测试 |

场景套件在真实 `SessionStore` 上端到端跑这些流程（聊天 → 分叉 → checkout →
压缩 → 聊天 → 压缩 → 聊天 → 重启加载），每一步校验 head 与渲染——这里涉及的
跨模块副作用 bug，在部件的单元测试里不会现身。

## 实现状态

今日已实现：

- 第二节的摘要节点形状含 `covers_ids`（persister 写入；遗留的 `covers` seq
  区间仍在并行写入、仍被死掉的剔除路径读取——两者都待删除）。
- 第一节的滚动 `_last_summary_id` / `_last_summary_text` 接力，含 summarizer
  的 `previous_summary` 输入。
- 第六节的图契约：仅现役 `covers_ids`、`superseded_summary` 标记、按
  rendering.md 第九节的胶囊折叠/展开摆位。
- persister 内部的压缩 head 快照/恢复（第五节 append 规则的过渡形态）。

尚未实现——重构批次：

- **第三节的链段替换进 `render_context`。** 今天主链走链永远到不了摘要节点，
  基于 covers 的剔除永不触发：压缩过的分支仍把每个被覆盖轮按原文渲染，摘要
  完全缺席。这是核心缺口；压缩目前对模型读到的内容毫无影响。
- **第四节第 1 步。** `trigger_compaction` 与自动/被动压缩把原始 `get_branch`
  走链喂给切点计算；再压缩因此重新总结原文轮而非"上一份摘要 + 尾巴"，产出
  重复覆盖。
- **第四节第 4 步的覆盖延长**（`covers_ids` = 旧链段 + 新吃掉的轮次）——随
  第 1 步修复自然落地。
- **第五节的 append 规则。** `append_message` 仍无条件自动推进 HEAD；persister
  以快照/恢复补偿。
- **第五节的镜像只读。** `_save_session → save_meta` 仍把镜像的 `head_id` 转发
  进 `SessionStore.update_session`，镜像的聊天流行（含压缩标记）也经
  `save_messages` 回流。这就是幽灵挪头路径。
- **删除 `covers` seq 区间**（persister 的写入、`context/nodes.py` 的读取、
  store append 不变量的豁免）——待第三节落在 `covers_ids` 上之后。
