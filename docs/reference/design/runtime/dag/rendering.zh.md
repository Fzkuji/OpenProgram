# DAG 渲染规范（布局 · 连线 · 图例 · 默认可见性）

> 会话图怎么画：每个节点放哪、每条线什么样、默认给用户看什么。**本文是权威
> 实现标准**——布局代码照此写，出问题对照本文查。数据语义（节点、两条边）见
> `dag/overview.md`；本文只管画。
>
> 每条规则配示例。**SVG 场景图以 `dag-layout-spec.html` 为权威**（共 13 个
> 场景：1–7 基础布局，8 merge，9 分支间通信，10 spawn 派活回流，11 执行子树
> 聚合，12 状态与徽标图例，13 badge 锚定与碰撞）。本文的 ASCII 图是文字版
> 速览，与 html 等价；冲突时以 html 为准。

---

## 图在哪：中央的一个视角

图是聊天面板两个**视角**之一，不是侧面板。每个中央 tab 要么在会话记录视角、
要么在上下文图视角，面板右上角那对控件负责切换——一个视角切换按钮加一个 `…`
会话操作菜单，照 Obsidian 的面板控件做。视角按 tab 记忆，把一个会话停在图上
不影响其他会话。

给图整列宽度是关键：分支多的会话要摆开 lane、tier 和分支名 badge，288px 的
侧栏装不下。

| 部件 | 位置 |
|---|---|
| 视角状态 | `CenterTab.dagView`（`web/lib/state/center-tabs-store.ts`）——不持久化，刷新后回到会话记录 |
| 控件 | `web/components/chat/view-controls.tsx` |
| 图的宿主 | `web/components/chat/dag-view.tsx`——渲染 `#historyPanel` + `.history-body`，即 `pipeline.ts` 与 `render/visibility.ts` 选取的元素 |
| 视角切换 | `web/app/styles/chat.css` 的 `.center-pane-chat[data-center-view]` |

两个界面同时挂载，靠 `display` 互换：无论当前显示哪个视角，渲染器每次 capture
都往宿主里画，卸载会让图空到下一次 capture。重排靠宿主的 `ResizeObserver`
（`_wirePanelResize`）——切视角、拖分屏、改窗口大小都走同一条路。

点击图上的节点填充右侧栏的详情 / 上下文视图；这两个视图留在侧栏，因为它们读
的是选中的单个节点，不是整个会话。

### 输入框属于面板，不属于会话记录

**视角切换隐藏的是会话记录滚动区 `#chatArea`，绝不是 `#chatView`。** 输入框
是单例，portal 进 `#chatView` 里的 `#composer-mount`，并以 `bottom: 0` 锚在
它身上——隐藏这个祖先就会把输入框跟着会话记录一起带走，而挂第二个实例会把
草稿、运行状态、模型行分叉成两份，用户读作一个输入框的东西就有了两套状态。
因此两个视角共用同一个输入框实例，图只替换它上方的滚动区。

在图视角能直接发消息，发出的节点在下一次 capture 出现在图上——发送走的就是
会话记录那条路径，没有改动，图视角不需要知道自己正在显示。

图是**盖住面板**来取代会话记录的，不是去占列里的那个位置：`#chatView` 保持
`flex: 1` 和满高，输入框的 `bottom: 0` 因此仍然落在面板底边，剩下的交给层叠
顺序——图压在输入框 `z-index: 5` 之下。反过来把 `#chatView` 压扁，只会把输入
框拽到面板中间。

`.dag-view` 用 `padding-bottom` 给输入框留出下缘，跟会话记录在
`.chat-messages` 上留的是同一份契约，这样最深的节点不会藏在输入框后面。

### 分支胶囊条

画布上方一行会换行的胶囊，每个活跃分支一个（`web/components/right-sidebar/
branches`，`variant="chips"`）。每个胶囊带分支的 lane 色圆点和名字，HEAD 那
个描边高亮并带徽标。单击胶囊 checkout，hover 出现重命名和删除。

胶囊和侧栏的列表行是**同一个组件**的两种布局，所以 checkout / 重命名 / 删除
只有一份实现，两个界面不可能走偏。差别只在盒子——胶囊按内容宽度排，不拉满一
行。合并与 attach 留在列表布局：选两条分支再星标基准需要横条给不出的纵向空
间，而横条要回答的是更窄的"我在哪条分支上"。

图本身已经画出分支结构，横条不重复它。横条封顶三行，超出滚动，把面板的大头
留给画布。

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
淹没——一个真实的天气会话（66 节点，50+ 是 code）就是这个样子。

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

**对话层按 role 固定；执行层按 caller 深度递增。** 两条规则各管一层，互不冲突。
spawn 根按对话层这条算：它是对话层 user，tier=1；它的 caller 指向深节点只决定
spawn 边从哪画来，不决定它自己的缩进。

| 节点 | 层 | tier |
|---|---|---|
| ROOT | — | 0 |
| user（含 spawn 分支根、回送节点） | 对话层 | 1 |
| llm 回复、merge | 对话层 | 2 |
| code（工具/函数调用） | 执行层 | 3 |
| 执行层内部再调的 | 执行层 | caller 的 tier +1 |

### depth —— 第几行

行号按**结构父树的前序遍历**分配：每个可见节点独占一行，子树占多少行就把下方
兄弟推多少行。行号**不是**"到根跳数"——那会把同一父的所有孩子叠在一行。两个
"横着长"的例外保留锚点行：fork 兄弟与它改写的兄弟**同一行**（场景 3）；spawn
分支首节点与 spawn 调用节点**同一行**（场景 10）。跨会话 spawn 落到目标会话时
是该会话自己的对话链（lane 0），不是侧枝（场景 12）。

---

## 二、三条全局排版规则

**① 正方形网格**：`COL_W == ROW_H`，子节点在父节点严格右下角（45°）。

**② 严格对齐 + 紧凑化**：节点落网格交点；**空行上移补齐、空列左移补齐，不保留
空行空列**。本条适用于一切显隐变化：执行子树收起/展开、分支折叠、visibility
过滤——收起后它占的行列必须立刻腾出。**推论：任何"占位框"都违反本条**——
running 状态用节点自身的描边表达（见图例），不画虚线占位节点。

**③ 分支不重叠**：见 lane 规则。

---

## 三、连线：颜色 = 分支，线型 = 类型（正交）

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

**status 映射**——状态画在节点自己身上，不画独立的虚线占位框：

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
| `↗`（右上角） | 跨会话 spawn 的**两侧都标**：目标会话里的分支根（caller 在另一个会话的图里，本会话内挂 ROOT，tooltip "spawn 自 <源会话>"）；源会话里发起 spawn 的那个节点（tooltip "派往 <目标会话>"——否则这轮派了活在自己图里毫无痕迹）。点击跳转对端会话（实现可后置）。**只在跨会话时出现**：同会话 spawn 两端都在图内、点划线边已表达关系（场景 10），不加 ↗——角标替代的是"画不出的那条边"，不是 spawn 的通用装饰 |

---

## 五、分支名 badge

- **锚定**：分支**当前可见的最深节点**的**正下方一行**。默认（折叠）视图下就是
  最后一个对话层节点；执行子树展开时徽章跟到最底下的展开节点，收起时自动回去。
  分支归属按 lane（展开的执行节点与所属轮次同 lane）。
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
| 1–7 | 基础布局（单轮/多轮/retry/工具缩进/手动函数/综合/收起左移） | 场景 4 的工具缩进在默认视图下先表现为 ⚒N 徽标（场景 11），展开后才是缩进方块 |
| 8 | merge 多父汇聚 | ◉ 实心带孔圆、落 base 分支 lane、peer 汇入粗实线（peer lane 色）；attach pointer 节点不画，只画线 |
| 9 | 分支间通信（send_to_branch） | 点线 `1 5`、目标分支色、默认隐藏 hover 显示；目标分支末尾加 from_branch user 节点 |
| 10 | spawn 派活 → attach 回流 | spawn 边点划线 `4 2 1 2`（子分支色）；子分支首节点与 spawn 节点**同一行**、新 lane、tier=1；回流长虚线 `4 4` 从子分支 tip 拉回主分支嵌入位置（聊天流里渲染成 Spawned 卡片，显示序提前——见 `ui/invariants.md` 规则 9） |
| 11 | 执行子树默认聚合 | 见第〇节：默认收 ⚒N 徽标，点击展开进布局，收起按规则② 即时回收；各分支展开状态互不影响 |
| 12 | 状态与徽标图例 | 见第四节：status 画在节点描边上，不画占位框；跨会话 spawn 两侧都标 ↗ |
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

## 八、白点 = 上下文覆盖

节点的白色填充表示它**属于下一次 LLM 调用会携带的内容**。没有模式可选，也没
有开关要找：只要图在显示，它就独占面板。单个会话 tab 得到聊天外壳加这张图；
两个会话 tab 分屏时两侧都是 `PeerSessionPane`，外壳——以及这张图——根本不
渲染。所以图旁边永远不会有会话记录，"哪些气泡在视口内"给不出任何读数，白点
就只是覆盖这一个意思。

覆盖集由 `GET /api/sessions/{id}/context-range` 提供：从 head 回溯活跃分支，
止于最近一次压缩摘要。集合外的节点变暗。宿主调
`enterExclusiveCoverageMode`（`web/lib/runtime-bridge/dag/index.ts`）拉取并
应用它。

### 覆盖数据形状

同一个响应逐节点带上上下文管线对"仍在携带的内容"施加的两级衰减：

```json
{
  "session_id": "…",
  "node_ids": ["…"],
  "count": 12,
  "nodes": [
    { "node_id": "…", "in_context": true, "aged": false, "spilled": true }
  ]
}
```

| 字段 | 含义 | 后端来源 |
|---|---|---|
| `in_context` | 节点在覆盖集内——每行恒为 true，因为这个列表**就是**覆盖集 | `get_branch` |
| `aged` | 老到结果被折成一行残根的 code 节点 | `openprogram/context/render.py::_aged_code_ids`，边界来自 `context/aging.py` |
| `spilled` | 结果已写入 `large_nodes/`，正文只留引用 | `metadata.spilled`，由 `context/spill.py::spill_if_large` 写入 |

两个标志都取自真实渲染路径调用的那几个函数，而不是平行的另一套实现——
**图永远不自己推导上下文语义**，它问后端，然后把答案画出来。

### 两级衰减怎么画

| 状态 | 画法 |
|---|---|
| 在上下文中 | 白色填充（基线） |
| `aged` | 描边压到 40% 不透明度——读作"还在，但只剩梗概" |
| `spilled` | 节点**左**上角加 `▤` |

`aged` 压的是 **`stroke-opacity`，绝不是 `opacity`**：白点是覆盖信号，整体
变淡会把它一起淡掉，两件独立的事就塌成一件。`▤` 占左上角是因为右上角已被
占用（`!` 报错、`↗` 跨会话 spawn），右下角是折叠徽标；用 `<text>` 画，免得
`_applyVisibility` 找"第一个形状子元素"时把它当成节点本体。

压缩会用一个摘要覆盖一段节点；被覆盖的节点直接落出 `node_ids`，和其他出上
下文的节点一样变暗。摘要节点是否需要专门图形是另一个问题——见第四节"状态与
覆盖画在节点自己的描边上，绝不画占位框"。

刷新时机：`context_stats` 或 `compaction_finished` 到达即重拉覆盖
（`chat-handlers.ts`），所以白点跟着真实上下文走，前端从不自行计算。注意
`aged` 和 `spilled` **不进**渲染签名，所以应用新覆盖时要打掉签名
（`setLastSignature(null)`），否则重绘会静默 no-op，图会一直画着上一份答案。

与压缩的联动：

- `insert_summary_node` 不复制任何东西（dag/overview.md §8）：summary 是带
  `metadata.covers` 的普通 `role=llm` 链上成员，保留尾部保持自己的 id 与
  predecessor。因此分支 id **就是**图上画的 id，`/context-range` 直接返回
  它们——没有翻译层，也没有第二套 id 空间。
- summary 节点和其他对话节点一样绘制；只有真正的合成桥会被过滤
  （`graph_layout/filter.py`）。
- 压缩后被覆盖的前缀落出集合 → 变暗；summary 与保留尾部维持高亮。**这就是
  压缩的可视化**——独立的摘要节点图形不予采纳（依 dag/overview.md：不加第 4
  种 role）。将来若需要显式"此处压缩了 N 轮"标记，必须做成首个保留节点上的
  徽章，不得做成节点。
- `compaction_finished` 必须触发 context range 刷新（`chat-handlers.ts`）；
  覆盖和其他一切一样事件驱动——前端永不自行计算上下文成员关系。
- 集合中没有对应图节点的 id（如 `display=runtime` 的 task-followup 行）
  静默忽略：它们在上下文里，但不在图上。

## 附录：实现状态

本规范已全部实现。各部分的落点：

| 规范条目 | 实现 |
|---|---|
| 第〇节 执行子树聚合 | `passes/apply-collapse.ts`：带执行子调用的节点一律起始折叠；`render/nodes.ts` 画 ⚒N（spawn 根子树豁免不吞） |
| 规则②推论（不画占位框） | `shapes.ts`：无 `square_outline`；task 渲染为普通方块 |
| 第四节 状态画在描边上 | `graph_builder` 下发 status；`nodes.ts` 画描边（running 虚线呼吸 / error 红+! / cancelled 灰化） |
| 第五节 badge 锚定 | `render/badges.ts`：锚对话层末节点、锚位有竖线左偏半格、实测像素盒碰撞下移一行 |
| 场景 8 merge 形状与连线 | `shapes.ts` `merge_dot`（◉）；`edges.ts` 汇入线 peer 色 2.4px 实线 |
| 场景 8/10 attach 指针 | 后端 display=runtime 过滤 + `graph_builder` 把 ref 戳到嵌入位置（`attach_returns`），`edges.ts` 画回流长虚线 |
| 第四节 跨会话 ↗ | `graph_builder` 打 `spawn_remote` 标（目标侧）；`nodes.ts` 画 ↗（源侧 `spawn_out` 渲染就绪，等数据源打标） |
| 第一节 spawn 根 tier | `graph_layout`：tier=1 / depth 同行 / lane 开新分支；`task_followup` 无 attach 时挂回接收轮（`filter.py` 兜底） |
| 两个视角共用输入框 | `chat.css` 隐藏 `#chatArea` 而非 `#chatView`；由 `web/scripts/check-center-tabs.mjs` 断言 |
| 分支胶囊条 | `BranchesPanel variant="chips"` + `BranchItem chip`；`.branches-strip` / `.branch-chip` 在 `chat.css` / `right-dock.css` |
| 第八节 覆盖查询 | `routes/tree.py::_coverage_nodes` 填 `/context-range` 的 `nodes`；测试见 `tests/unit/test_context_range_coverage.py` |
| 第八节 aged / spilled 绘制 | `render/nodes.ts`（stroke-opacity + `▤`），数据来自 `store/globals.ts` 的 `_coverageSet` |
