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
| 视角状态 | `CenterTab.dagView`（`apps/web/lib/state/center-tabs-store.ts`）——不持久化，刷新后回到会话记录 |
| 控件 | `apps/web/components/chat/view-controls.tsx` |
| 图的宿主 | `apps/web/components/chat/dag-view.tsx`——渲染 `#historyPanel` + `.history-body`，即 `pipeline.ts` 与 `render/visibility.ts` 选取的元素 |
| 视角切换 | `apps/web/app/styles/dag/view-host.css` 的 `.center-pane-chat[data-center-view]` |

两个界面同时挂载，靠 `display` 互换：无论当前显示哪个视角，渲染器每次 capture
都往宿主里画，卸载会让图空到下一次 capture。宿主不随尺寸重排——它是一块无限
画布，面板变宽只是露出更多（见下）。

点击图上的节点填充右侧栏的详情 / 上下文视图；这两个视图留在侧栏，因为它们读
的是选中的单个节点，不是整个会话。

### 画布是无限的

没有滚动容器，也没有按内容定尺寸的 SVG。SVG 铺满面板，一切绘制物都在一个
`<g>` 里，这个 `<g>` 带着用户直接操控的 translate + scale。

| 手势 | 效果 |
|---|---|
| 捏合，或 ⌘/ctrl + 滚轮 | 以光标为锚缩放，限 25%–300%——指针下的节点缩放前后都在指针下 |
| 鼠标滚轮（离散格档） | 同样的缩放，用滚轮速率——鼠标没有捏合 |
| 触控板双指滚动 | 平移，两轴都动——滚动就是滚动 |
| 在空白处拖拽 | 平移 |
| 从节点上起手拖拽 | 归节点——单击、双击照常生效 |

**滚轮分流**（`canvas.ts`）：ctrl/⌘ + 滚轮缩放——浏览器把触控板捏合投递成带
`ctrlKey` 的 wheel 事件，⌘+滚轮则是显式缩放和弦——速率按捏合细密连续的
delta 调校。鼠标滚轮按滚轮速率缩放：鼠标没有捏合。macOS 的滚动加速让它的
`deltaY` 变成小数且不定，判据用遗留字段 `wheelDeltaY`——Chromium/WebKit 把
物理格档报成 120 的整数倍（触控板是任意小值），非 mac 用 line 模式 delta
兜底。其余就是触控板双指滚动，做平移、两轴都动——滚动就是滚动。

按内容定尺寸的容器替用户决定了两件不该它决定的事：图能画多大才需要滚动条，
以及"中间"在哪。宽会话给一条横滚动条，深会话给一条竖滚动条，看全貌要在两个轴
上滚，还没有缩小的办法。

**点阵就是坐标系。** 面板背景按布局自己的步长（`COL_W`）每格画一个点，跟着
同一组平移缩放变换，并按布局的原点偏移（`PAD_X` / `PAD_Y`，点阵自内边距再退
半格）对位，让每个节点锚点下面正好是一颗点——偏离格点是任何人不看布局代码也
能发现的 bug。fit 把平移量吸到整像素，正是为此。点的半径也跟着缩放走（收在
1–3px）：定死 1.2px 在放大后的大格子里会细到看不见。

**视角状态跨重绘保持。** 图每次 capture 都重绘，每次都动镜头会在用户读图时把
视野拖走。平移与缩放按会话存在 `store/globals` 里，只有进入另一个会话才重新
fit。改面板尺寸永不重新 fit——用户看图的角度是他自己的。

**HUD。** 输入框右上角——env chip 行的右端：一个 fit 按钮、一组缩放控件、
图例弹层，由 `dag-view.tsx` portal 进输入框的 `#dagHudSlot`，跟着输入框走、
随它长高，只在 DAG 视角显示。缩放控件是一颗胶囊里的 − · 读数 · +：−/+ 每次
步进恰好一个滚轮格（`ZOOM_STEP`），点读数本身重置 100%——按钮没有光标可锚，
两者都以面板中心为锚。读数由 `canvas.ts` 在每次视角变化时命令式写入——把
一次手势的每个 wheel 事件走 React state 会让整棵树每秒重绘六十次。

HUD 不自带任何外观。胶囊列在输入框 env-pill 规则里
（`composer.module.css`），和旁边的 env chip 是同一条 24px 实底胶囊规则——
同底、同内描边、同阴影、同 hover——永远不会跑偏。图例面板穿 `MENU_PANEL`
（`components/chat/top-bar/menu-styles`），全应用弹层菜单共用的那一份框架；
`styles/dag/hud.css` 只留 HUD 内部排版（缩放簇的分段、图例的向上锚位和行）。

| 部件 | 位置 |
|---|---|
| 平移 / 缩放 / fit | `apps/web/lib/runtime-bridge/dag/interaction/canvas.ts`（HUD 按钮走 `zoomStep` / `resetZoom`） |
| 视角状态 | `dag/store/globals.ts` 的 `_viewTx` / `_viewTy` / `_viewScale` / `_viewSession` |
| 画布与点阵 | `apps/web/app/styles/dag/canvas.css` 的 `.history-body` |
| HUD | `apps/web/components/chat/dag-view.tsx` 的 `DagHud`；胶囊外观来自 `composer.module.css` 的 env-pill 规则，图例框架来自 `MENU_PANEL`，内部排版在 `styles/dag/hud.css` |

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

画布在输入框底下一直铺到边——从输入框后面平移过去只是一个手势，fit 会把图
居中在输入框上方的空间里（`canvas.ts::fitCanvas`），所以不需要预留 padding。

### 分支切换在图里

画布上方没有分支条。每条分支的名字是画在图里的按钮，锚在该分支最后一个对话层
节点下方（`render/badges.ts`，第五节）：活跃分支的标签按 lane 色描边加底色，点
其他标签即 checkout。图本身已经画出分支结构，再来一条横条是重复信息，还会横穿
面板右上角的悬浮视角按钮。重命名、删除、merge、attach 留在右侧栏的分支列表里，
那里有这些流程需要的纵向空间。

---

## 〇、先回答"画什么"：两层粒度，默认只画对话层

一张会话图里有两类节点，量级差一个数量级：

| 层 | 节点 | 回答的问题 | 量级 |
|---|---|---|---|
| **对话层** | ROOT、user、llm 回复、spawn 分支根、merge、**手动调用的顶层函数节点**（用户的显式动作，fn-form/run 卡对应的 code 节点本体） | 会话什么形状：几轮、几支、谁开的谁 | 个位数~几十 |
| **执行层** | code（tool call）及其内部子调用 | 某一轮内部干了什么 | 一轮可达几十 |

**默认可见性规则：Viewport 只布局对话层。** 一轮干过的一切——每次函数调用、
每个派出去的 agent——是这一轮的**调用线程**（第十二节）：默认折叠成节点肩上
的一个数字，点节点才展开成旁边的一列真节点。

两条归并让链保持在用户可见的粒度——**一个三角形 = 一次回复到下一条用户消息
之前的全部模型活动**：

* `task_followup` 回复（agent 返回触发的那一轮）不是链节点。函数返回后模型
  接着说话不画新节点，agent 返回是同一件事的另一个尺度——回复归并进它的
  **锚轮**：沿 `predecessor` 爬过所有 followup 找到的那一轮。一个值得点名的
  副作用：老回卷 bug 弄脏过 followup predecessor 的旧数据也解析到同一个锚，
  疤痕从此不再画成幻影分叉。
* spawn 的 agent 内部轮同样不上链。spawn 头就是那个 agent（第十二节），
  它 lane 里的一切归并进它。

```
默认（对话层）:                  点击回复展开它的线程:
◇ROOT                          ◇ROOT
├ ○你好                        ├ ○你好
│ └ △回复                      │ └ △回复
├ ○查天气                      ├ ○查天气
│ └ △回复 ⁹                    │ └ △回复┄┐
                               │        ■ bash
                               │        ■ web_fetch
                               │        ■ 子 agent ⁵
                               │        ■ …(共9行)
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

两轴步长相同（`COL_W == ROW_H`，`dag/types.ts`），整体自原点偏移
`PAD_X` / `PAD_Y`——这就是画布点阵所画的那张方格。

### lane —— 属于第几条分支

**数分支，按出现顺序发列号 0,1,2…，不跳号，不判断"主干"。**

一条分支 = 一条对话链（user → llm → user → …）。三种事件产生新分支：

| 事件 | 新分支的根 | 挂接 |
|---|---|---|
| retry / 改写某轮 | 分叉出的 user / llm 节点 | 与被替换节点共享 predecessor |
| spawn（agent / send_message 派活） | source=agent_spawn 的 user 节点 | caller=发起节点，predecessor 空 |
| merge 出的新主线 | merge 节点本身 | 落 base 分支 lane（见场景 8），不新开 |

**分支按实际占用列紧贴排**：一条分支占用的列 = 起始列到其可见**链**节点的最深
一格；下一条分支从上一条实际占用的最右列 +1 开始，互不重叠。以 fork 根开头的
lane 与它分叉出来的 lane 之间**多留一个空列**——分支是平行版本，两格的空气就是
这句话。tier 按 lane 内归零（后端给 fork 根的 tier 带着旧位置的偏移，不归零会让
单节点分支凭空右移好几列）。**fork lane 内部复刻主干**：lane 的第一列是一根
空的脊线——桥的虚线落在脊线上，线从 fork 根所在行起往下走，每一轮（含 fork
根自己）向右错一列、从脊线上伸出来，和主干轮次从 ROOT 的线上伸出完全同构。
分支以一条线开始，不以节点开始。只有胶囊和被取代的旧摘要——lane 里没有自己
的用户轮次——桥才直接连到字形本身。
线程条目（第十二节）不占 lane 宽度：它的列在 lane
落定之后再选，取锚点右侧第一个空列，选中后立刻占用，第二条展开线程继续往右
走——两条打开的线程不共享格子。

### tier —— 分支内往右缩进几格

**对话层按 role 固定。** 执行层不再按 caller 深度缩进——展开的线程是一列扁平
的时间序（第十二节），因为图要回答的是"这一轮干了什么、按什么顺序"；逐调用
的嵌套是聊天流和 Executions 页的职责。

| 节点 | 层 | 列 |
|---|---|---|
| ROOT | 对话层 | tier 0 |
| user | 对话层 | tier 1 |
| llm 回复、merge | 对话层 | tier 2 |
| 线程条目（调用方块、spawn 头） | 执行层 | 锚点列 +1；嵌套展开的 agent 线程再 +1 |

### depth —— 第几行

链的行号按**结构父树的前序遍历**分配：每个可见链节点独占一行，子树占多少行就
把下方兄弟推多少行。行号**不是**"到根跳数"——那会把同一父的所有孩子叠在一行。

一个"横着长"的例外：fork 根与它平行的那条链兄弟**同一行**（场景 3），同一分叉
点的所有分支共享这一行——它们本就是同一轮的平行版本。每条分支桥接到紧邻左侧
的那条分支，而不是跨回主干，虚线桥因此是一段两格长的同行横线。

线程的行号**递归**分配（第十二节）：锚点的条目从它的下一行起（让开挂在它名下
的分支行）逐行往下；展开的 spawn 自己的线程接着它的行继续，它占的行把父线程
后面的条目往下推。链上锚点的线程落座后，它后面的对话层节点（以及已经落在插入
位置下方的线程行）按占用行数下移——展开是插入，不是覆盖。跨会话 spawn 落到目标会话时是该会话
自己的对话链（lane 0），不是侧枝（场景 12）。

---

## 二、三条全局排版规则

**① 方格网**：`COL_W == ROW_H`，子节点严格落在父节点右下角（45°）。画布的点阵
背景画的就是这张网（见上文"画布是无限的"），于是这一条是眼睛能验的性质，不是
代码作的承诺。

**② 严格对齐 + 紧凑化**：节点落在格点上；**空行上移补齐、空列左移补齐，不保留
空行空列**。本条适用于一切显隐变化：执行子树收起/展开、分支折叠、visibility
过滤——收起后它占的行列立刻腾出。**推论：任何"占位框"都违反本条**——running
状态用节点自身的描边表达（见图例），不画虚线占位节点。

**③ 字形占格，文字是注文**：一个节点占它那个格点；计数用注文灰挂在旁边，绝不
装进按自身文字定尺寸的形状里。跟着标签长的字形，是每个邻居都得拿它量一遍的
字形——这场谈判正是第十二节早先那颗药丸做错的事。所有字形（含第九节的压缩
胶囊）都画在基准圆上，各占恰好一格。名字如今在画布
上**不画任何墨迹**：子 agent 的名字住在 tooltip 和 inspector 里，每个节点仅有的
文字是肩上的折叠数（第十二节）和胶囊的覆盖数（第九节）。

---

## 三、连线：颜色 = 分支，线型 = 类型（正交）

每条 lane 一个颜色（`dag/types.ts` `LANE_COLORS`）。任何线用它所属/指向分支的
lane 色；**绝不给某类线固定颜色**。类型只靠线型：

| 连线类型 | 线型 | 颜色 | 默认 |
|---|---|---|---|
| 同分支父→子 | 实线 | 本分支色 | 显示 |
| retry 分叉桥（起点 → 分支 lane 的脊线顶端，同行） | 虚线 `6 4` 横线；行错开时才走肘线 | 本分支色 | 显示 |
| 调用线程（锚点 → 它的条目，第十二节） | 实线——主干模式下探一层：锚点自己的列一根竖线，每个条目一根右伸横杆（先下后右，和链上连线同构） | 注文灰 | 展开时显示 |
| merge 汇入（peer tip → merge 节点） | 粗实线 2.4px | peer 分支色 | 显示 |
| attach 回流（源 tip → 嵌入位置） | 长虚线 `4 4` | 源分支色 | 两端都可见才画——agent 内部 tip 归并进三角形后不画线，spawn 头在线程上的位置就是返回关系 |
| 分支间通信（send_message） | 点线 `1 5` | 目标分支色 | **hover 才显示**（量大，常驻会糊） |

**所有线一律画到节点中心**，由字形的底色填充盖住线头。不同字形的边缘位置不
同，任何固定偏移的停笔迟早会在三角形斜边上露出缝；死在字形底下的线，对任何
形状都严丝合缝。

---

## 四、节点图例：形状 = 角色，描边 = 状态

**形状**：◇ ROOT · ○ user · △ llm · ■ code · ◉ merge（实心带孔圆，全图唯一的
"汇聚"形状）· ◎ 压缩摘要（双圈圆：基准圆内套细内圈，见第九节）·
■ 子 agent spawn（和其他调用一样的方块——派发 agent 就是一次函数调用；
展开进入 agent 自己的活动，见第十二节）。

**HEAD 是字形自己身上的呼吸光晕**：一圈分支色的 `drop-shadow`，以 2.4 秒的
慢周期胀起又落下，直接盖在形状上（`data-head`）。光贴着字形轮廓走，任何缩放
都跟随——不像画出来的光环圈，那读起来像旁边多了一个更糊的节点；也不像实心
填充，填充改掉了形状词汇本身，而光晕下 HEAD 仍然一眼是三角/圆，只是亮着。
`prefers-reduced-motion` 下脉动凝固成恒定光晕。HEAD 不带自己的覆盖标记：它是
唯一不可能离开上下文窗口的节点——下一次请求就落在它上面。

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
| 折叠数（右上肩，注文灰） | 节点折叠的调用线程的大小——第十二节。数字贴着字形，没有任何包裹形状：带轮廓的东西会被读成节点。展开后消失——调用都在屏上，数得出来 |
| `↗`（右上角） | 跨会话 spawn 的**两侧都标**：目标会话里的分支根（caller 在另一个会话的图里，本会话内挂 ROOT，tooltip "spawn 自 <源会话>"）；源会话里发起 spawn 的那个节点（tooltip "派往 <目标会话>"——否则这轮派了活在自己图里毫无痕迹）。点击跳转对端会话（实现可后置）。**只在跨会话时出现**：同会话 spawn 两端都在图内、头在线程上的位置已表达关系，不加 ↗ |

---

## 五、分支名 badge

- **锚定**：分支**当前可见的最深节点**的**正下方一行**。默认（折叠）视图下就是
  最后一个对话层节点；调用线程展开时徽章跟到线程最底下的条目，收起时自动回去。
  分支归属按 lane（线程条目与锚轮同 lane）。
- **spawn 的 agent 分支不立徽章**：agent 就是线程上的那个三角形（第十二节），
  再立分支药丸是同一事实的第二种画法。从分支行的 head 爬链撞到 spawn 根就
  直接不放。
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
| 1–7 | 基础布局（单轮/多轮/retry/工具缩进/手动函数/综合/收起左移） | 场景 4 的工具调用在默认视图下先表现为肩上折叠数（场景 11），展开后才是线程方块 |
| 8 | merge 多父汇聚 | ◉ 实心带孔圆、落 base 分支 lane、peer 汇入粗实线（peer lane 色）；attach pointer 节点不画，只画线 |
| 9 | 分支间通信（send_message） | 点线 `1 5`、目标分支色、默认隐藏 hover 显示；目标分支末尾加 from_branch user 节点 |
| 10 | spawn 派活 → 返回 | 子 agent 头是发起轮线程上的一个条目（第十二节）：落在派活实际发生的序列位置，前后是它前后的调用。返回不需要额外的线——它触发的 followup 回复归并进同一个锚轮（第〇节），派活、干活、返回从上到下读一列（聊天流仍渲染 Spawned 卡片，显示序提前——见 `ui/invariants.md` 规则 9） |
| 11 | 调用线程默认聚合 | 见第〇节/第十二节：默认收成肩上折叠数，点击展开进布局，收起按规则② 即时回收；各节点展开状态互不影响、可递归 |
| 12 | 状态与徽标图例 | 见第四节：status 画在节点描边上，不画占位框；跨会话 spawn 两侧都标 ↗ |
| 13 | badge 锚定·避让·碰撞·已合并 | 见第五节：锚对话层末节点正下方、锚位被边占才左偏半格、碰撞下移一行、合并即消名（来源进 merge tooltip） |

**回送节点与切换器（语义补充，无独立布局场景）**：send_message 的回送
（子分支答复作为 user 节点回到发起方 lane，`predecessor=发起点`）若与用户等待
期间自己发的消息共享 predecessor，即构成 fork——**回送节点参与 `< N/M >`
切换器**（它是发起方对话的真实延续替代；`source=from_branch` 不做
agent_spawn 那样的隔离，隔离规则见 `ui/invariants.md` 规则 7）。

**子代理再 spawn**：默认派生预算下数据语义已禁止（`agent.max_spawn_depth=1`，
只有主 agent 能 agent()，被 spawn 的 agent 自己干活，见 `ui/invariants.md`
规则 6；调大预算就能再开代）。
渲染层本来就递归（第十二节——agent 线程上的 spawn 是一个普通三角形），历史
数据里的多代委托链照样画得出来。

## 七、渲染管线（代码地图）

```
apps/web/lib/runtime-bridge/dag/
  pipeline.ts        调度：passes → layout → edges → nodes → badges → visibility
  passes/            数据变换，按顺序：
    merge-runs.ts               合并同一节点的连续 run
    collapse-runtime-pairs.ts   把老式 display=runtime 的 user/assistant 包装对折成
                                一行（`caller` 边 schema 之前，包装行自身没有聊天内容，
                                单独画就是重复一列）
    demote-decoration-cards.ts  把 LLM 触发的 runtime 卡片改挂，避免"一条回复既有卡片
                                子节点又有后续 user 轮"被误判成 fork（那会把图劈成两条 lane）
    fold-summaries.ts           折叠压缩胶囊覆盖的区间（第九节）
    thread.ts                   调用线程模型（第〇节/第十二节）：followup 轮与
                                agent 内部轮归并进锚、事件按时间归入锚的线程、
                                按展开集算可见性
  layout/geometry.ts 第一节的实现：链节点按 lane/tier/depth 打包成格点
                     （分支 lane 隔一空列、fork 根落兄弟行），再把每条展开
                     的线程递归安放在锚点旁边，按插入行数下移后续链行，并占用
                     每条线程列。
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
`python scripts/dag_dump.py <session_id>` 打印 lane/tier/depth + ASCII 网格。

## 八、白点 = 上下文覆盖

节点的白色填充表示它**属于下一次 LLM 调用会携带的内容**。没有模式可选，也没
有开关要找：只要图在显示，它就独占面板。单个会话 tab 得到聊天外壳加这张图；
两个会话 tab 分屏时两侧都是 `PeerSessionPane`，外壳——以及这张图——根本不
渲染。所以图旁边永远不会有会话记录，"哪些气泡在视口内"给不出任何读数，白点
就只是覆盖这一个意思。

覆盖集由 `GET /api/sessions/{id}/context-range` 提供：从 head 回溯活跃分支，
止于最近一次压缩摘要。集合外的节点变暗。宿主调
`enterExclusiveCoverageMode`（`apps/web/lib/runtime-bridge/dag/index.ts`）拉取并
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

压缩会用一个摘要覆盖一段节点；被覆盖的节点直接落出 `node_ids`，所以白点只落
在摘要上，覆盖区间里一个都没有。图接下来拿这段区间做什么，见第九节。

刷新时机：`context_stats` 或 `compaction_finished` 到达即重拉覆盖
（`chat-handlers.ts`），所以白点跟着真实上下文走，前端从不自行计算。注意
`aged` 和 `spilled` **不进**渲染签名，所以应用新覆盖时要打掉签名
（`setLastSignature(null)`），否则重绘会静默 no-op，图会一直画着上一份答案。

与压缩的联动：

- `insert_summary_node` 不复制任何东西（dag/overview.md §8）：summary 是带
  `metadata.covers` 的普通 `role=llm` 链上成员，保留尾部保持自己的 id 与
  predecessor。因此分支 id **就是**图上画的 id，`/context-range` 直接返回
  它们——没有翻译层，也没有第二套 id 空间。
- summary 节点在数据关心的每一方面都和其他对话节点一样绘制；只有真正的合成
  桥会被过滤（`graph_layout/filter.py`）。它的胶囊是形状，不是 role——见第
  九节。
- 压缩后被覆盖的前缀落出集合，白点落在 summary 与保留尾部上。
- `compaction_finished` 必须触发 context range 刷新（`chat-handlers.ts`）；
  覆盖和其他一切一样事件驱动——前端永不自行计算上下文成员关系。
- 集合中没有对应图节点的 id（如 `display=runtime` 的 task-followup 行）
  静默忽略：它们在上下文里，但不在图上。

## 九、压缩读作一个胶囊，不是十四个变暗的轮次

把覆盖区间画成变暗，对每个节点说的都对，对整个会话说的却是错的：十四个浅色
圆圈照样占十四行，眼睛照样要走完它们才够得着活的对话。摘要是一个替所有这些
说话的节点，图就该这么画它。

**胶囊**。摘要节点在主线上画成双圈圆：基准圆内套一道细一号的同心内圈。
和其他字形同尺寸、占一格——内圈就是"这一轮装着比它显出来更多的东西"这句话。
内圈跟随外圈的颜色态（inert / superseded 时同为灰）。它仍然是
普通的 `role=llm` 链上成员（dag/overview.md §8）：形状就是形状，不是第四种
role。

**折叠数**。折叠的胶囊在右肩戴着被覆盖节点数的数字——与轮次调用线程的折叠数
（第十二节）同一套词汇，展开即消失，因为那时这摞被画出来了。没有文字注记：
字形（双圈）说明它是什么，数字说明收了多少，幽灵说明它开着，详情在
tooltip 和检查器里。

**折叠按分支生效**。摘要属于上下文携带它的那条分支——活跃链完整包含覆盖链段
的分支（context/compaction.md 第三节）。只有在这条分支上胶囊才折叠：覆盖区间
默认省略，点胶囊以幽灵态放回，再点收起（仅视图状态、永不落盘，新开会话一律
从折叠态起步）。展开态在切分支之间是黏性的：在某条分支上被覆盖轮次按原文
画过，摘要就记为已展开，切回携带分支时区间保持打开而不是重新收拢——看过就是
看过。在其他任何分支上——从覆盖范围内部分出去的、同时代的姐妹——
那些轮次就是活的上下文：按原文、按分支本色渲染，胶囊仍在屏上但呈惰性：保持
本色、无折叠交互、无角标、无白底——节点集合在每条分支上完全一致，变的只是
读法。切换分支时两种读法互换；存储的图分毫不动。

**幽灵态**。展开的区间保持分支本色；"不属于下一次请求"由入边虚线和白底的
缺席来说（白填充只落在上下文集合里的节点上），绝不靠把颜色抽成灰——灰色留给
死历史（失败废弃线、被取代的旧摘要），它们在任何分支上都永远回不了上下文。
可读、可点、明显不是活的："摘要到底有没有抓住我说的话"这个问题能被回答，
而答案永远不会被误当成上下文。

**滚动摘要**。压缩是接力式的：第二次压缩把第一份摘要的文本喂回 summarizer，
`extra_meta._last_summary_id` 指向替代者——下一次请求永远只带一份摘要，不会
叠加。图说同一件事：只有现役摘要拿到 `covers_ids`（胶囊 + 折叠）；被取代的
摘要保留行和胶囊轮廓，但带 `superseded_summary` 标记下发，画成幽灵灰，不折叠
任何东西。

**胶囊的位置**。一个槽位，在所有状态、所有分支上完全一致：覆盖链段的末尾。
展开时它跟在自己的幽灵后面（幽灵 → 胶囊 → 尾巴）；在不携带它的分支上它跟在
同样那几轮的原文后面（白底原文 → 镂空胶囊 → 其余）；折叠就是同一槽位把链段收
起，胶囊因此顶到主干最前。切分支、开合折叠都不改变位置——变的只有颜色和折
叠。所有摆位都是 `fold-summaries.ts` 里仅视图层的克隆改写；存储行在任何状态
下都保留真实的 `predecessor`（区间起点，即 ROOT）。

两种状态下白点都不会落在被覆盖的节点上，因为 `/context-range` 根本不列它。
一件事，一个来源（第八节）。

### 覆盖从哪来

持久化器写的是 `metadata.covers_ids`——摘要所替代的那些链节点的确切 id
（context/persistence.py）。是 id 而不是 seq 区间：在 DAG 里 seq 区间会横跨
姐妹分支，seq 恰好落进 `[first_seq, last_seq]` 的死分叉会被折进一个从未总结
过它的胶囊，而且 HEAD 一动答案就变。`webui/graph_builder.py` 把这份列表原样
下发，加上被覆盖轮次挂着的 caller 子树（被覆盖的轮连同它的调用一起折叠），
丢掉已不存在的 id，结果作为 `covers_ids` 放在摘要行上。

一个字段驱动全部：胶囊形状、折叠、褶皱数量、幽灵标记、检查器的覆盖行。前端不做
任何 seq 运算，也不调第二个接口。

## 十、失败轮以留档存在，不以警报存在

以 `status = error` 收尾的轮是终态节点；重试从它的前驱分叉，对话在新线上继续
（dag/overview.md）。失败线被保留——这正是选择分叉而不是回退的意义——但它永远
不可能重新进入上下文。

所以这样的节点一旦**离开 HEAD 链**，就画成和被覆盖轮同一种灰，检查器标注
`失败轮 · 已留档`。两种状态长得像，是因为在图唯一关心的那根轴上它们**就是**
一样的：在盘上、可读、永不进下一次请求。

这层灰有意覆盖第四节给活跃错误的红色描边。红色的意思是"这件事现在需要你"，
留档的线不是——重试已经发生过了。`!` 字形本身保留，所以这条线为什么终止仍然
一眼可读。

判定的两半缺一不可。只看 `status` 会把你正盯着的、还没重试的错误也刷灰；只看
离开 HEAD 会把每一条兄弟分支都刷灰。节点必须**既是失败、又已被放弃**。

`status` 是存储自己的终态标记，由轮次机制写入（取消的情形见
[运行取消](../execution/turn-cancellation.html)，它保持 `cancelled`、沿用自己的 50%
灰化）。图读它，从不自己判定它。

## 十一、悬停、单击、右键、双击

一个问题一个面。信息窗曾经有三个——悬停卡片、停留后的二段展开、点击弹出的检
查器——单击因此同时干两件事：弹窗又切换线程，弹窗还正好盖在它刚触发的展开
上面。

**悬停 → 简略卡**。快速一瞥：角色（spawn 头标题写 `子 agent · <名字>`——
名字就住在这里，第十二节）、model/tokens、一段短内容预览、折叠调用数。token
数字在节点带实测值时用 `llm.output_tokens`，没有时用 `chars/4`；卡片明说是
哪一种（`tokens` / `tokens（估）`）。悬停稍候出现、移开即走。

**单击 → 节点自己的动作**。折叠/展开它的调用线程（第十二节）。没有别的——
没有窗口和展开抢画面。右栏 Details 仍然静默填充，用户想看随时打开。

**右键 → 同一张卡原地展开**。不是第二个窗口：就是那一个卡片元素在原地加深
（`tooltip.ts expandTooltip`）——所有字段、更长的预览、覆盖状态、上下文站
位、id——动词接在卡底：checkout 到此分支 · 从此节点 fork · fork 并编辑此消
息（仅用户轮）· 复制节点 id · 查看原始 JSON。两个状态共用一个行构建器
（`renderNodeInfo`），不可能各说各话；覆盖行读的是节点绘制时已经戳好的 DOM
标记（`data-ghost`、`data-failed`、`.out-of-context`），卡片和它旁边那张图
也不可能各说各话。展开后卡片变成可交互并停驻——移开鼠标不再收卡、悬停不会
把它刷回简版，点击别处（或执行动词）才收。原始 JSON 复用检查器壳而不是弹模
态——模态会为了给你看图里的一个节点，把图整个拿走。

**双击用户轮 → fork 并编辑**。消息文本落进输入框，HEAD 已经退回它的分叉点。
其他节点保持 checkout 行为——回复和工具结果没有什么可编辑的。

### 每个动作都是既有操作

这里没有给协议添任何新动词：

| 动作 | 路由 | 为什么这就是全部实现 |
|---|---|---|
| checkout | `POST /api/chat/checkout` | 纯 HEAD 移动，和会话记录里的兄弟版本导航发的是同一个 |
| 从节点 fork | `POST /api/chat/checkout` | fork **就是** checkout 加上意图——从一个已经有子节点的 HEAD 发出的轮，定义上就是它们的兄弟，会话记录里的"从此分支"按钮也是这么做的 |
| fork 并编辑 | `POST /api/chat/checkout` 到该节点的**前驱**，再把文本填进输入框 | 用户改完发送；这个发送就是针对新 HEAD 的普通发送，于是分叉发生，协议不变，也不用维护"从节点 X 发"这个概念。选前驱做分叉点，正是因为编辑后的消息必须站在原消息**旁边**而不是后面——和 `POST /api/chat/edit` 产生的形状一致 |

检查器与菜单用命令式方式构建（`render/inspector.ts`），因为图本身就是：它们浮
在渲染器自己拥有的 SVG 之上、锚在节点几何上，在 React 树之外。

**图例**。一张可收起的卡片，说明各形状与两种灰，从画布 HUD 上 fit 按钮旁边打开
（`components/chat/dag-view.tsx`）。默认收起——这套词汇很小、可学会，所以图例
是给最初几次会话用的，不是画布上的常驻件。

## 十二、调用线程：一轮干的事是一个序列，折叠成一个数

一轮干过的一切——每次函数调用、每个派出去的 agent——是一个按时间排序的事件
序列，这一轮的**线程**。调用和 spawn 是同一类事件的两个尺度，所以共用一条线、
一个顺序、一个折叠。一次回复调了 41 个函数、派了两个 agent 的会话，默认是三个
链节点加数字 `43`；展开是 43 个真节点，按实际发生顺序排一列。

**折叠就是折叠**。唯一的标记是节点右上肩的数字：注文灰的数字，贴着字形。不是
徽标形状、不是药丸、不是线上的小方块——任何带轮廓的东西都会被读成节点，而幻影
节点正是折叠最不能引发的误读。没有线程线、没有条目、没有别的。

**展开就是纯展开**。点节点把线程插进布局：一根注文灰实线沿锚点自己的列竖直
向下，每个事件用一根右伸横杆挂在线上——先下后右，主干模式下探一层。每个事件
都是线上的真节点——每次调用一个方块（锚轮 lane 色），spawn
的 agent 也是方块——一行一个事件，从上到下按调用顺序。后面的 user / assistant
节点按插入的行数下移，锚点列上的线程竖线在下一颗三角形之前结束。每条打开的
线程占用自己的列。收起按规则②
回收行。

**头就是 agent，spawn 就是调用**。spawn 根画成方块——派发 agent 就是一次
函数调用，方块就是调用词汇。派发调用节点（`agent` / `send_message`）归并进
它：一次 spawn 一颗字形（没开出 spawn 的派发调用保留自己的方块——那次失败值得
一颗节点）。agent 的内部轮不上链——归并进它（第〇节），展开后回复画三角形、
调用画方块——它自己的线程再右移一列，点方块展开。spawn 方块在屏上期间（即所属轮次
的线程已展开——这层选择已经保证默认画布干净），它右侧贴一枚徽章药丸（与分支
徽章同款，render/badges.ts），作方块的名牌；点击把 agent 链的末端
checkout 成活动分支——接管这个 agent 的对话。徽章永不压住节点：所有可见字形
都预置进徽章碰撞盒，会压住节点的徽章自动下移一行。模型是递归的，画面也是：每一层都按同样的两
条规则读，折叠数在肩上、展开成一列。嵌套展开的线程把父线程后面的条目往下推；
链上锚点的线程同样插入后面的对话轮。展开是插入，不是覆盖。

**没有注文**。agent 的名字住在 tooltip 和 inspector 里（inspector 把节点标题
写作 `子 agent · <名字>`，而不是 role 字段所说的 `user`）；画布只有字形和折叠
数。名字在线上来自 runner 戳的 label（`spawned_from.label`），没有时回落到记录
下来的分支名。

**没有回流线**。agent 的返回触发一轮 followup 回复，而那轮回复归并进线程所属的
同一个锚（第〇节）——派活、干活、返回从上到下读一列。旧的 attach 回流虚线只在
两端都是链上可见节点时才画，而 agent 内部 tip 从此不可见。

**视图状态**。`store/globals.ts` 的 `_threadOpen`，按锚点 id 记（链轮或 spawn
头——同一套词汇）。从不持久化，切会话清空，和第九节完全一致。spawn 头只在它
上方整条线程链都展开时可见；它的条目同理——可见性属于整条祖先链，不属于节点
自己的开关。

## 附录：实现状态

本规范已全部实现。各部分的落点：

| 规范条目 | 实现 |
|---|---|
| 无限画布（平移 / 缩放 / fit / 点阵） | `dag/interaction/canvas.ts` 与 `styles/dag/canvas.css` 的 `.history-body`；视角状态在 `dag/store/globals.ts`；HUD 在 `components/chat/dag-view.tsx` |
| 第一节 lane / tier / depth 布局 | `dag/layout/geometry.ts::computeGeometry`（链 lane 按 tier 打包并 lane 内归零、前序分行、场景3分叉行+间隔列、线程递归安放并下移后续链行、占用线程列）；格点性、无重叠、线程列行、分叉几何均由 `apps/web/scripts/check-dag-subagent.mjs` 真实执行并断言 |
| 第二节 规则③ 字形占格 | 没有任何形状按文字定尺寸，画布上除肩上折叠数与胶囊注记外没有文字 |
| 第四节 HEAD 呼吸光晕 | `render/nodes.ts` 戳 `data-head` 并把分支色写进 `color`；`dag-head-glow` 关键帧在 `styles/dag/nodes.css`（reduced-motion → 恒定光）；所有字形保持空心（`shapes.ts`）；HEAD 指向已归并回复时落到锚上（`pipeline.ts` 经 `threadModel.anchorOf`） |
| 第〇节/第十二节 调用线程聚合 | `passes/thread.ts`（`buildThreadModel`：锚归并、事件归属、递归可见性）；`render/nodes.ts` 画肩上折叠数（`history-thread-count`）；`store/globals.ts` 的 `_threadOpen` |
| 规则②推论（不画占位框） | `shapes.ts`：无 `square_outline`；task 渲染为普通方块 |
| 第四节 状态画在描边上 | `graph_builder` 下发 status；`nodes.ts` 画描边（running 虚线呼吸 / error 红+! / cancelled 灰化） |
| 第五节 badge 锚定 | `render/badges.ts`：锚对话层末节点、锚位有竖线左偏半格、实测像素盒碰撞下移一行 |
| 场景 8 merge 形状与连线 | `shapes.ts` `merge_dot`（◉）；`edges.ts` 汇入线 peer 色 2.4px 实线 |
| 场景 8/10 attach 指针 | 后端 display=runtime 过滤 + `graph_builder` 把 ref 戳到嵌入位置（`attach_returns`），`edges.ts` 画回流长虚线 |
| 第四节 跨会话 ↗ | `graph_builder` 打 `spawn_remote` 标（目标侧）；`nodes.ts` 画 ↗（源侧 `spawn_out` 渲染就绪，等数据源打标） |
| 第一节 spawn 根 tier | `graph_layout`：tier=1 / depth 同行 / lane 开新分支；`task_followup` 无 attach 时挂回接收轮（`filter.py` 兜底） |
| 两个视角共用输入框 | `styles/chat/center-pane.css` 隐藏 `#chatArea` 而非 `#chatView`；由 `apps/web/scripts/check-center-tabs.mjs` 断言 |
| 图内分支标签（checkout 按钮） | `render/badges.ts`；hover 样式在 `styles/dag/badges.css` 的 `.history-branch-tag` |
| 第八节 覆盖查询 | `routes/tree.py::_coverage_nodes` 填 `/context-range` 的 `nodes`；测试见 `tests/unit/context/test_context_range_coverage.py` |
| 第八节 aged / spilled 绘制 | `render/nodes.ts`（stroke-opacity + `▤`），数据来自 `store/globals.ts` 的 `_coverageSet` |
| 第九节 `covers_ids` 下发 | `webui/graph_builder.py` 把 `metadata.covers` 解析成 id；测试见 `tests/unit/dag/test_graph_builder_covers.py` |
| 第九节 胶囊形状 | `shapes.ts` 的 `capsule`（按 `covers_ids` 判定，打 `data-shape` 标让 `_applyShapeSize` 别改它的几何） |
| 第九节 折叠 / 褶皱 / 幽灵 | `passes/fold-summaries.ts`（折叠）、`render/nodes.ts`（褶皱、`已压缩 · N 轮` 注记、幽灵描边）、`render/edges.ts`（幽灵虚线边）、`store/globals.ts` 的 `_summaryExpanded`；由 `apps/web/scripts/check-dag-summary.mjs` 实跑 |
| 第十节 失败留档 | `render/nodes.ts::_isArchivedFailure`——`status=error` **且**离开 HEAD 链；灰覆盖第四节的红 |
| 第十一节 一张卡两个状态 / fork 并编辑 | `dag/interaction/tooltip.ts`：`renderNodeInfo` 喂两个状态，`expandTooltip` 原地加深；`render/inspector.ts` 只构建动词列表（+ 原始 JSON 层），`interaction/nodes.ts` 接线；动作走 `POST /api/chat/checkout` |
| 第十一节 图例 | `components/chat/dag-view.tsx` 的 `DagLegend`（挂在画布 HUD 里），`styles/dag/hud.css` 的 `.dag-legend` |
| 第十二节 调用线程 + agent spawn | `shapes.ts`（spawn → 方块）、`passes/thread.ts`（模型）、`layout/geometry.ts`（递归安放）、`render/edges.ts`（线程点线、中心连线、场景3横桥）、`render/nodes.ts`（`data-thread*`、肩上折叠数）、`render/interaction.ts`（`toggleThreadOpen`）；由 `apps/web/scripts/check-dag-subagent.mjs` 实跑 |
| 第十二节 名字上线 | `task/runner.py::_update_attach_card` 从 task 戳出 `attach.label`；`ws_actions/session.py::_annotate_spawn_origin` 把它带到 spawn 根的 `spawned_from.label`；测试见 `tests/unit/test_task_attach_integration.py` |
