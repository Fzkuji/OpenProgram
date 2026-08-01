# Session-DAG v2 — 弥合模型与实现的落差

状态：设计稿 —— DAG 论文实验开始前需要落地的裁决集。与 `session-dag.md`
（v1 模型，未被本文修订的部分仍以它为准）及 2026-08-02 的两份审计
（上下文成本审计 + 论文就绪性审计）配套。每节给出问题、裁决、被否掉的备选。

v1 的故事是"会话 DAG 是唯一真相源；每次 LLM 调用的上下文都是图上一条
路径的渲染"。今天有四个缺口打破这个故事：

| # | 缺口 | 性质 |
|---|------|------|
| G1 | `predecessor` 存在 metadata 里；写入端会漏，读取端不敢信 | 实现债 |
| G2 | `render_context` 不看边；分支隔离是 engine 里事后的集合求交 | 实现债 |
| G3 | system prompt（和 memory prefetch）从不进 DAG；三套装配互不一致 | 设计空白 |
| G4 | compaction 违反模型自己的公理（孤根 summary、`k_` 克隆、走私 role） | 设计冲突 |

## 裁决 1：`predecessor` 升为 schema 字段；spawn 成为 store 原语

**问题。** 对话链边只是 `metadata["predecessor"]` 的一个 key。
`Graph.from_dict` 会静默丢弃顶层同名字段；三个 spawn 入口各自记得（或忘记）
传 `spawn_caller`；`get_branch` 用三级猜测链（predecessor → caller → seq 缝合）补偿。

**裁决。**
- `Call` 增加顶层字段 `predecessor: str | None`。序列化写顶层；`from_dict`
  先读顶层，对 v2 之前的旧会话回落读 metadata（读时迁移，不重写历史）。
- 写入端不变量，由 store 的 append 路径强制：所有 ROOT 级对话节点必须带
  predecessor，仅两个例外——(a) 会话首节点、(b) spawn 分支根。违规 append
  直接抛错，不再无声地产生 ROOT 分叉。
- `SessionStore.spawn_branch(caller_node_id, *, source, name=...)` 成为开
  spawn 分支的唯一入口：创建分支根（predecessor=None、caller=发起节点、
  metadata.source）、登记 head、返回分支句柄。现有三个入口改为调用它。
  新的 spawn 调用方不再接触边，也就不可能把边写错。
- `get_branch` 只沿边行走。caller/seq 启发式仅保留在 `legacy=True` 路径
  （行走遇到 v2 之前的节点时），且每次启发式跳跃都记入 decision log ——
  今天的无声猜测变成可审计事件。

**否掉的方案：** 保留 metadata 存储、外挂校验 linter。事后校验救不回已经
接错线的分支；只有做成一等字段，类型系统和写入路径才能强制。

## 裁决 2：路径选择下沉进渲染原语

**问题。** `render_context(graph, head_seq, ...)` 只按 seq 窗口 + expose
选节点。engine 再拿 `get_branch` 的结果做交集，并手工打补丁（排除刚插入的
placeholder、放行分支内 caller 的 code 节点）。论文的核心主张——"上下文是
一条路径的渲染"——实际实现是"上下文是一个时间窗的渲染，再由第二遍过滤扔掉
不该看的"。

**裁决。**
- 新签名：`render_context(graph, head_id, frame_entry_seq, render_range)`。
  原语从 `head_id` 沿 predecessor 链走到根，对主链上每个节点放行其
  caller 子树（按 frame/expose 规则过滤，与 v1 相同）。seq 仍是排序键，
  不再是成员资格判据。
- 成员资格规则，只说一遍：**一个节点进入渲染，当且仅当它沿 caller 上溯的
  最近 ROOT 级祖先在 `head_id` 的 predecessor 主链上，且 frame/expose
  放行。** 这一句话取代 engine 侧的交集、placeholder 排除行走、caller
  放行补丁——三者全部删除。
- `engine._build_messages_from_dag` 缩为：解析 head → 调原语 →
  交给 `render_dag_messages`。没有集合运算。
- 原语是纯函数：不写盘。大节点落盘移出读路径（见裁决 4 的渲染清单）。

**否掉的方案：** 选择逻辑留在 engine、"写文档说明"。两层各管一半成员资格
规则，正是补丁的来源；artifact 评审第一个读的就是这个原语。

## 裁决 3：常量前缀进 DAG；一份装配、一份预算

**问题。** v1 裁决 6 要求全项目统一 system prompt，但从未落地：dispatcher
拼一份真发的，engine 对另一份（从未发送、1.7k token）做预算，exec runtime
拼第三份。memory prefetch 每次调用都追加在 system prompt 尾部，逐 turn
改变前缀，把整条消息历史的跨 turn provider 缓存全部打掉（成本审计找到的
最大可避免开销）。

**裁决。**
- **唯一装配器。** `context.build_system_prompt(agent_profile, tools, mode)`
  是唯一生产者。dispatcher 停止自拼（`_with_tool_runtime_prompt` 收编为
  装配器的一层）；exec runtime 用同一装配器传自己的 profile；预算统计的
  就是上线的那个字符串。加一条测试钉死 装配输出 == 线上输出。
- **prompt 记录在案，而不是隐含。** 装配出的 prompt 哈希变化时（会话开始、
  工具集变化、plan-mode 切换），store 在当前分支 append 一个 `role=code`
  节点 `name="context/system_prompt"`，caller=ROOT，output=全文。渲染把
  主链上最新的这类节点钉为线上 system 消息。任何历史调用的 replay 从此能
  复现当时真正发送的 prompt——v1 做不到。不引入第四种 role；`context/*`
  名字保留并对聊天视图隐藏（与今天隐藏 `summary_` 节点同一机制）。
- **memory prefetch 移出 system prompt。** 预取的记忆渲染为**当次用户节点**
  线上消息内的前缀块，并存入该节点 metadata（`memory_prefetch`）：
  (a) system+tools 段逐 turn 字节级稳定——历史重新命中缓存；
  (b) replay 看到的就是模型当时看到的。该块不参与老化，随所在 turn 一起
  过期，和普通用户内容一样。

**否掉的方案：** 永远把 system prompt 当带外配置。那就是 v1 的现状；它让
"唯一真相源"不成立，预算和 replay 无法修复。

## 裁决 4：compaction 成为合法的图重写；渲染可复现

**问题。** `insert_summary_node` 产生无父的 summary 根（违反单连通）、把
保留尾巴克隆成 `k_` 副本（违反 append-only）、靠 metadata 走私 system
role。渲染在读路径上按"当天的"策略常量现算老化/截断、还带落盘副作用——
同一张图不同日子渲出不同 prompt，"可 replay 任何调用"的主张不成立。

**裁决。**
- **summary 节点入链。** summary 节点为 `role=llm`、
  `name="context/summary"`、`predecessor = 它覆盖的第一个节点的
  predecessor`、`metadata.covers = [first_seq, last_seq]`。head 移到一个
  predecessor 指向 summary 节点的新节点上。不再有 `k_` 克隆：保留尾巴不
  复制——渲染沿主链穿过 summary 节点时，跳过 seq 落在 `covers` 区间内的
  节点、保留其后的一切。compaction 因此回到 append-only：追加两个节点
  （summary + 新 head 链点），零克隆、零孤根，旧主链原样保留为兄弟分支，
  回滚能力与现在完全一致。
- **老化改棘轮 + 记录在案。** TAIL_TURNS 边界只在 turn 提交时推进（绝不
  在 turn 中间动），每个 llm 节点在发起调用的那一刻记录
  `metadata.render_manifest = {policy_version, aged_before_seq, spilled: [...]}`。
  replay 一次调用 = 按清单里的策略渲染，而不是按今天的策略。这同时修掉
  逐调用滚动边界对缓存前缀的破坏（成本审计 #6）。
- **落盘移到写路径。** 超阈值节点在**记录时**落盘（一次、确定性），而不是
  碰巧被渲染时。读路径从此无副作用——裁决 2 本来就要求这一点。
- **单一管线、大声失败。** DAG 渲染是唯一上下文管线。commit-chain 和
  legacy microcompact 两条回退管线删除；渲染抛错则本 turn 可见地失败，
  decision log 记录原因。（Tier-3 死代码能藏那么久，正是因为静默回退
  把它盖住了。）

**否掉的方案：** 给 `k_` 克隆写文档使其"合法"。克隆制造第二套 id 空间，
所有消费方（UI、Context tab、replay）都要做翻译；`covers` 用零复制给出
同样的语义。

## 对论文的影响

- method 一节可以逐字给出裁决 2 的成员资格规则。
- "非破坏性、可复现渲染"成为真命题（清单、写路径落盘、append-only compaction）。
- "唯一真相源"成为真命题（裁决 3 把最后一块带外上下文收进图里）。
- E4（分支/spawn 实验）测的是被强制的语义，不是启发式。

## 实施顺序

1. 裁决 1（schema + spawn 原语）——其余一切都依赖可靠的边。含读时迁移、
   写入不变量、测试。
2. 裁决 2（路径原生渲染）——删除 engine 集合运算。
3. 裁决 3（唯一装配器、prompt 节点、prefetch 搬家）——与 1/2 独立可并行；
   包含最大的成本修复。
4. 裁决 4（compaction 重写、清单、单一管线）——最后，叠在 1+2 之上。

每步落地时现有单测全绿，并新增钉死不变量的测试（append 拒绝、成员资格
规则、装配==线上、对已录制会话的 replay 字节级相等）。
