# DAG 渲染规范（布局 · 连线 · 图例 · 默认可见性）

Status: **decided（权威实现标准，2026-07-10 整合）** · 取代 `dag-layout-algorithm.md` + `dag-viewport.md`，吸收 `branch-collaboration.md` 的连线视觉规则

> 右栏 Viewport 的 DAG 小地图怎么画：每个节点放哪、每条线什么样、默认给用户看
> 什么。**本文是权威实现标准**——布局代码照此写，出问题对照本文查。数据语义
> （节点、两条边）见 `session-dag.md`；本文只管画。
>
> 每条规则配示例。**SVG 场景图以 `dag-layout-spec.html` 为权威**（共 13 个
> 场景：1–7 基础布局，8 merge，9 分支间通信，10 spawn 派活回流，11 执行子树
> 聚合，12 状态与徽标图例，13 badge 锚定与碰撞）。本文的 ASCII 图是文字版
> 速览，与 html 等价；冲突时以 html 为准。

---

## 〇、先回答"画什么"：两层粒度，默认只画对话层

一张会话图里有两类节点，量级差一个数量级：

| 层 | 节点 | 回答的问题 | 量级 |
|---|---|---|---|
| **对话层** | ROOT、user、llm 回复、spawn 分支根、merge、**手动调用的顶层函数节点**（用户的显式动作，fn-form/run 卡对应的 code 节点本体） | 会话什么形状：几轮、几支、谁开的谁 | 个位数~几十 |
| **执行层** | code（tool call）及其内部子调用 | 某一轮内部干了什么 | 一轮可达几十 |

**默认可见性规则：Viewport 只布局对话层。** 每个 llm 节点若有执行子树
（沿 `caller` 挂下来的 code 节点），收成节点旁一个 `⚒N` 计数徽标；手动调用的
顶层函数节点同理——节点本体可见，内部子树收进它自己的 `⚒N`（N=直接+传递
子调用数）。点击徽标，该轮的执行子树展开、进入布局；再点收起。展开状态按节点
记忆，切会话清空。

```
默认（对话层）:                  点击 ⚒9 展开那一轮:
◇ROOT                          ◇ROOT
├ ○你好                        ├ ○你好
│ └ △回复                      │ └ △回复
├ ○查天气                      ├ ○查天气
│ └ △回复 ⚒9                   │ └ △回复
                               │     ├ ■bash
                               │     ├ ■web_fetch
                               │     └ ■…(共9)
```

理由：执行层信息在聊天流里已有更好的呈现（每轮的执行树卡片、Executions 页）。
Viewport 的职责是让人一眼看清会话结构；50 个工具方块平铺会把 8 个结构节点
淹没——这正是 2026-07-10 天气会话（66 节点，50+ 是 code）实际发生的事。

> 聊天流与调用树两种视图不受影响：聊天流按 seq 铺顶层、函数嵌套折叠；
> Executions/执行树卡片沿 caller 全展开。同一份数据，三种投影。

---

## 一、一个节点的位置 = (列, 行)

- **列（横向）= lane 起始列 + tier 缩进**
- **行（纵向）= depth**

### lane —— 属于第几条分支

**数分支，按出现顺序发列号 0,1,2…，不跳号，不判断"主干"。**

一条分支 = 一条对话链（user → llm → user → …）。三种事件产生新分支：

| 事件 | 新分支的根 | 挂接 |
|---|---|---|
| retry / 改写某轮 | 分叉出的 user / llm 节点 | 与被替换节点共享 predecessor |
| spawn（task / message_branch 派活） | source=agent_spawn 的 user 节点 | caller=发起节点，predecessor 空 |
| merge 出的新主线 | merge 节点本身 | 落 base 分支 lane（见场景 8），不新开 |

**分支按实际占用列紧贴排**：一条分支占用的列 = 起始列到其子树最深一格；下一条
分支从上一条实际占用的最右列 +1 开始，互不重叠。

### tier —— 分支内往右缩进几格

**对话层按 role 固定；执行层按 caller 深度递增。** 两条规则各管一层，不再冲突
（旧文档未裁决 spawn 根按哪条算——现裁决：spawn 根是对话层 user，tier=1，
它的 caller 指向深节点只决定 spawn 边从哪画来，不决定它自己的缩进）：

| 节点 | 层 | tier |
|---|---|---|
| ROOT | — | 0 |
| user（含 spawn 分支根、回送节点） | 对话层 | 1 |
| llm 回复、merge | 对话层 | 2 |
| code（工具/函数调用） | 执行层 | 3 |
| 执行层内部再调的 | 执行层 | caller 的 tier +1 |

### depth —— 第几行

行号按**结构父树的前序遍历**分配：每个可见节点独占一行，子树占多少行就把下方
兄弟推多少行（**不是**"到根跳数"——那会把同一父的所有孩子叠在一行，曾使场景
3/5/6/7/11/12 全部失真；2026-07-31 对齐 spec.html）。两个"横着长"的例外保留锚
点行：fork 兄弟与它改写的兄弟**同一行**（场景 3）；spawn 分支首节点与 spawn
调用节点**同一行**（场景 10）。跨会话 spawn 落到目标会话时是该会话自己的对话
链（lane 0），不是侧枝（场景 12）。

---

## 二、三条全局排版规则（不变，继承自旧布局文档）

**① 正方形网格**：`COL_W == ROW_H`，子节点在父节点严格右下角（45°）。

**② 严格对齐 + 紧凑化**：节点落网格交点；**空行上移补齐、空列左移补齐，不保留
空行空列**。本条适用于一切显隐变化：执行子树收起/展开、分支折叠、visibility
过滤——收起后它占的行列必须立刻腾出。**推论：任何"占位框"都违反本条**——
running 状态用节点自身的描边表达（见图例），不画虚线占位节点。

**③ 分支不重叠**：见 lane 规则。

---

## 三、连线：颜色 = 分支，线型 = 类型（正交，铁律）

每条 lane 一个颜色（`dag/types.ts` `LANE_COLORS`）。任何线用它所属/指向分支的
lane 色；**绝不给某类线固定颜色**。类型只靠线型：

| 连线类型 | 线型 | 颜色 | 默认 |
|---|---|---|---|
| 同分支父→子 | 实线 | 本分支色 | 显示 |
| retry 分叉桥 | 虚线 `5 4` | 本分支色 | 显示 |
| spawn 边（发起节点 → 分支根） | 点划线 `4 2 1 2` | 子分支色 | 显示 |
| merge 汇入（peer tip → merge 节点） | 粗实线 2.4px | peer 分支色 | 显示 |
| attach 回流（源 tip → 嵌入位置） | 长虚线 `4 4` | 源分支色 | 显示 |
| 分支间通信（send_to_branch） | 点线 `1 5` | 目标分支色 | **hover 才显示**（量大，常驻会糊） |

---

## 四、节点图例：形状 = 角色，描边 = 状态

**形状**：◇ ROOT · ○ user · △ llm · ■ code · ◉ merge（实心带孔圆，全图唯一的
"汇聚"形状）。

**status 映射**（淘汰虚线占位框——状态画在节点自己身上）：

| status | 画法 |
|---|---|
| success | 默认描边 |
| running | 同形状虚线描边 + 呼吸透明度动画 |
| error | 红描边 + 右上角 `!` 角标 |
| cancelled | 整体灰化 50% |

**徽标**（附着在节点上，不占格）：

| 徽标 | 含义 |
|---|---|
| `⚒N`（llm 节点右侧） | 收起的执行子树，N 个子调用；点击展开 |
| `×N`（code 节点右侧） | 循环产生的 N 个同构兄弟折叠（纯显示） |
| `↗`（右上角） | 跨会话 spawn 的**两侧都标**：目标会话里的分支根（caller 在另一个会话的图里，本会话内挂 ROOT，tooltip "spawn 自 <源会话>"）；源会话里发起 spawn 的那个节点（tooltip "派往 <目标会话>"——否则这轮派了活在自己图里毫无痕迹）。点击跳转对端会话（实现可后置）。**只在跨会话时出现**：同会话 spawn 两端都在图内、点划线边已表达关系（场景 10），不加 ↗——角标是"画不出的那条边"的替代品，不是 spawn 的通用装饰 |

---

## 五、分支名 badge

- **锚定**（2026-07-31 修订）：分支**当前可见的最深节点**的**正下方一行**。默认
  （折叠）视图下就是最后一个对话层节点；执行子树展开时徽章跟到最底下的展开
  节点，收起时自动回去。分支归属按 lane（展开的执行节点与所属轮次同 lane）。
- **避让**：锚位被边穿过时（对话延续的下行竖线）才向左偏移半格——徽标永不
  压边。
- **碰撞**：按**实测像素盒**判定（badge 底宽＝名字实测宽度 + 内边距，不是按格子）。
  短名字在方格间距下几乎碰不上；分支名自动取自消息/任务描述时很长，同行锚位的
  盒子会叠——后到者（按分支序）向下顺延一行，直至无碰撞。
- **来源**：badge 只来自 `list_branches` 的**活跃分支**（亮色、可点击 checkout）。
  **合并即消名**（git 语义）：被合并的分支不再画 badge；它的名字进 ◉ merge 节点
  的 tooltip（像 merge commit message 记录来源），session meta 里的名字数据保留。
- 样式沿用 HEAD 标签（`--bg-hover` 圆角底、9px 文字、实测文字宽度撑底）。

---

## 六、场景（SVG 权威在 spec.html，共 13 个）

| # | 场景 | 要点 |
|---|---|---|
| 1–7 | 基础布局（单轮/多轮/retry/工具缩进/手动函数/综合/收起左移） | 规则不变；场景 4 的工具缩进在默认视图下先表现为 ⚒N 徽标（场景 11），展开后才是缩进方块 |
| 8 | merge 多父汇聚 | ◉ 实心带孔圆、落 base 分支 lane、peer 汇入粗实线（peer lane 色）；attach pointer 节点不画，只画线 |
| 9 | 分支间通信（send_to_branch） | 点线 `1 5`、目标分支色、默认隐藏 hover 显示；目标分支末尾加 from_branch user 节点 |
| 10 | spawn 派活 → attach 回流 | spawn 边点划线 `4 2 1 2`（子分支色）；子分支首节点与 spawn 节点**同一行**、新 lane、tier=1；回流长虚线 `4 4` 从子分支 tip 拉回主分支嵌入位置（聊天流里渲染成 Spawned 卡片，显示序提前——见 `ui/invariants.md` 规则 9） |
| 11 | 执行子树默认聚合 | 见第〇节：默认收 ⚒N 徽标，点击展开进布局，收起按规则② 即时回收；各分支展开状态互不影响 |
| 12 | 状态与徽标图例 | 见第四节：status 画在节点描边上，废除占位框；跨会话 spawn 两侧都标 ↗ |
| 13 | badge 锚定·避让·碰撞·已合并 | 见第五节：锚对话层末节点正下方、锚位被边占才左偏半格、碰撞下移一行、合并即消名（来源进 merge tooltip） |

**回送节点与切换器（语义补充，无独立布局场景）**：message_branch 的回送
（子分支答复作为 user 节点回到发起方 lane，`predecessor=发起点`）若与用户等待
期间自己发的消息共享 predecessor，即构成 fork——**回送节点参与 `< N/M >`
切换器**（它是发起方对话的真实延续替代；`source=from_branch` 不做
agent_spawn 那样的隔离，隔离规则见 `ui/invariants.md` 规则 7）。

**子代理再 spawn**：数据语义上已被禁止（`MAX_TASK_DEPTH=1`，只有主 agent
能 task()，被 spawn 的 agent 一律自己干活，见 `ui/invariants.md` 规则 6）。
渲染层仍按场景 10 规则递归兜底——历史数据里的多代委托链（worker 分支的
点划线从子代理的回复节点出发，挂在子代理的 lane 结构下）照样画得出来。

## 七、渲染管线（代码地图）

```
web/lib/runtime-bridge/dag/
  pipeline.ts        调度：passes → layout → edges → nodes → badges → visibility
  passes/            数据变换，按顺序：
    merge-runs.ts               合并同一节点的连续 run
    collapse-runtime-pairs.ts   把老式 display=runtime 的 user/assistant 包装对折成
                                一行（`caller` 边 schema 之前，包装行自身没有聊天内容，
                                单独画就是重复一列）
    demote-decoration-cards.ts  把 LLM 触发的 runtime 卡片改挂，避免"一条回复既有卡片
                                子节点又有后续 user 轮"被误判成 fork（那会把图劈成两条 lane）
    apply-collapse.ts           折叠执行子树、出 ⚒N 徽标
  layout/            lane / depth（本文第一节的实现）。
                     **tier 不在这里算**——由后端
                     `openprogram/webui/graph_layout/tier.py` 算好随节点下发，
                     前端只消费。
  render/edges.ts    第三节的线型表
  render/nodes.ts    第四节的形状 + 状态描边 + 徽标
  render/badges.ts   第五节的分支名 badge
  store/globals.ts   展开状态、lastGraph、签名
```

后端 `openprogram/webui/graph_builder.py` 产出节点数组（含 `branch_name` stamp、
caller/predecessor），`graph_layout/` 做 lane/tier/depth 标注——**tier 具体在
`graph_layout/tier.py`**。验证工具：
`python tools/dag_dump.py <session_id>` 打印 lane/tier/depth + ASCII 网格。

## 八、与实现的已知差距（2026-07-10 盘点；同日全部落地）

按本规范逐项对照现状。8 条差距已于 2026-07-10 实现完毕，下表保留作
对照记录（每条注实现位置）：

| # | 差距 | 规范条目 | 实现 |
|---|---|---|---|
| 1 | 执行子树默认平铺（无聚合 pass、无 ⚒N 徽标） | 第〇节 | ✅ passes/apply-collapse.ts：带执行子调用的节点一律起始折叠；render/nodes.ts 画 ⚒N（spawn 根子树豁免不吞） |
| 2 | 折叠留占位虚线框、占格 | 规则②推论 | ✅ shapes.ts 删除 square_outline；task 回归普通方块 |
| 3 | running 态画成独立虚线占位节点 | 第四节 status | ✅ graph_builder 下发 status；nodes.ts 画描边（running 虚线呼吸 / error 红+! / cancelled 灰化） |
| 4 | badge 锚定在"lane 最深可见节点"（含执行层）、无碰撞顺延 | 第五节 | ✅ render/badges.ts：锚对话层末节点、锚位有竖线左偏半格、实测像素盒碰撞下移一行 |
| 5 | merge 节点无专属形状、汇入线未按 peer 色 | 场景 8 | ✅ shapes.ts merge_dot（◉）；edges.ts 汇入线 peer 色 2.4px 实线 |
| 6 | attach 指针在 viewport 仍画成方块 | 场景 8/10 | ✅ 后端 display=runtime 过滤 + graph_builder 把 ref 戳到嵌入位置（attach_returns），edges.ts 画回流长虚线 |
| 7 | 跨会话 spawn 两侧都无 ↗ 角标（目标侧静默挂 ROOT，源侧毫无痕迹） | 第四节徽标 | ✅ graph_builder 打 spawn_remote 标（目标侧）；nodes.ts 画 ↗（源侧 spawn_out 渲染就绪，等数据源打标） |
| 8 | spawn 根 tier 计算未按"对话层 user=1"裁决 | 第一节 tier | ✅ graph_layout tier=1 / depth 同行 / lane 开新分支；task_followup 无 attach 时挂回接收轮（filter.py 兜底） |

## 9. Context tab 语义（2026-07-31 定案）

History 面板有两个高亮模式（`web/lib/runtime-bridge/dag/types.ts` 的
`HighlightMode`）；在本节之前规范只覆盖绘制本身，未定义 Context 模式的含义。裁定：

- **Viewport** —— 可见集 = 当前聊天滚动窗口内相交的对话气泡
  （`render/visibility.ts`）。纯 UI 便利，无后端语义。
- **Context** —— 可见集 = **下一次 LLM 调用将携带的节点 id 集合**，由
  `GET /api/sessions/{id}/context-range` 提供：从 head 回溯活跃分支，止于
  最近一次压缩摘要。集合外的节点变暗，集合内保持白色填充。

与压缩的联动（曾经坏掉的部分）：

- `insert_summary_node` 把保留尾部复制为 `k_<hex>` 行重新挂链，而 DAG 刻意
  不画 `summary_`/`k_` 节点（`graph_layout/filter.py`）——图上一直显示的是
  **原始行**。因此每个 `k_` 副本存 `original_id` 回指
  （`context/persistence.py`），`/context-range` 经 `original_ids()` 翻译，
  返回的 id 空间恒与图上画的一致。无回指的旧数据原样透传、不高亮——可接受
  的衰减，不做迁移。
- 压缩后被摘要的前缀落出集合 → 变暗；保留尾部维持高亮。**这就是压缩的可视
  化**——独立的摘要节点图形仍然否决（session-dag.md 的裁定：不加第 4 种
  role）。将来若需要显式"此处压缩了 N 轮"标记，必须做成首个保留节点上的
  徽章，不得做成节点。
- `compaction_finished` 必须触发 context range 刷新（`chat-handlers.ts`）；
  Context tab 和其他一切一样事件驱动——前端永不自行计算上下文成员关系。
- 集合中没有对应图节点的 id（如 `display=runtime` 的 task-followup 行）
  静默忽略：它们在上下文里，但不在图上。
