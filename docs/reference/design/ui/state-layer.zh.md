# Web 状态层：每会话独立 store

这一页从零解释 web 前端的状态存放在哪里、分屏改造为什么反复出 bug，以及为止住它而拍板
的方案：**每个会话一个 Zustand store 实例，真正共享的数据留在全局 store**。第 6 节记录
这个决定，第 7 节按它跟踪迁移进度。

第 2 到 5 节的盘点描述的是这项工作开始之前的状态层。保留它是因为正是它让那个决定读得懂
——它列为问题的每个字段，要么已在阶段 1 修掉，要么还排在阶段 2。

不预设前端知识。先介绍必要概念，之后全部是对照真实代码的盘点，引用格式为 `文件:行号`。

---

## 1. 三个概念

**store 是一个共享的变量盒子。** web UI 用
[zustand](https://github.com/pmndrs/zustand)——一个小库，创建一个对象，里面既放数据
（`currentSessionId`、`composerDrafts`），也放修改数据的函数（`setCurrentConv`、
`setComposerInput`）。页面任何位置的组件都能直接读这个盒子里的任何字段，不需要一层层
传 props。主盒子在 `web/lib/session-store/index.ts:411`。

**组件订阅一个切片。** 组件调用 `useSessionStore((s) => s.conversations)` 时，只有
`conversations` 变化才会触发它重新渲染。那个选择器函数就是订阅关系。

**store 也可以创建多份。** zustand 并不要求只有一个全局盒子。`createStore` 可以按会话
调用，再用 React context 把属于各自子树的实例递下去。第 6 节定的就是这个形状。

**同一个组件可以被渲染多份。** React 组件是模板。`<Composer />` 在普通布局里出现一次，
在分屏里出现两次。两份跑同样的代码，因此两份从同一个盒子读同一个字段。如果那个字段是
单个全局值，两份就会互相抢。第 5 节的大部分问题都是这一句话。

---

## 2. 主 store 逐字段盘点

`web/lib/session-store/index.ts`（906 行）在 `ConvState` 接口
（`web/lib/session-store/index.ts:46`）里声明形状，在
`web/lib/session-store/index.ts:411` 给初值。下面把每个字段归入三类之一。

### A 类 —— 已按会话隔离

这些字段是 `Record<sessionId, T>` 形态的表。同屏两个会话不会冲突，因为各读各的键。
整个 store 应该收敛到这个形状。

| 字段 | 声明位置 | 内容 |
| --- | --- | --- |
| `conversations` | `web/lib/session-store/index.ts:68` | 侧栏每会话摘要（虽然按键存，但它是一份*列表*，见 C 类） |
| `messagesById` | `web/lib/session-store/index.ts:70` | 所有已加载消息，按消息 id 存 |
| `messageOrder` | `web/lib/session-store/index.ts:72` | 每会话的消息 id 有序列表 |
| `pendingProjectsByChat` | `web/lib/session-store/index.ts:81` | 未发送会话选定的项目，按临时 chat key 存 |
| `runningTasks` | `web/lib/session-store/index.ts:90` | 每会话运行任务；驱动各自 composer 的发送/停止按钮 |
| `trees` | `web/lib/session-store/index.ts:96` | 每会话最新的实时 context 树 |
| `tokens` | `web/lib/session-store/index.ts:103` | 每会话 token 用量 |
| `contextWindow` | `web/lib/session-store/index.ts:112` | 每会话上下文窗口大小 |
| `heads` | `web/lib/session-store/index.ts:115` | 每会话当前 DAG head（选中的分支尖端） |
| `additionalWorkingDirsBySession` | `web/lib/session-store/index.ts:146` | 每会话附加工作目录 |
| `composerDrafts` | `web/lib/session-store/index.ts:182` | 每会话未发送草稿文本，持久化到 localStorage |
| `composerSettingsBySession` | `web/lib/session-store/index.ts:194` | 每会话工具开关/思考强度，持久化 |
| `contextPanelFor` | `web/lib/session-store/index.ts:211` | `/context` 浮窗开在*哪个*会话上——单字段当按会话标志用，见第 5 节。**阶段 1 已删除**：现在是每会话 store 上的 `contextPanelOpen`。 |

`pendingDecisions`（`web/lib/session-store/index.ts:244`）是个值得单独说的混合体：
它是扁平的 FIFO 数组，但每一项自带 `sessionId`
（`web/lib/session-store/types.ts:60`），composer 在
`web/components/chat/composer/index.tsx:352` 把队列过滤到自己的会话。功能上已经按
会话隔离，结构上是一份需要每个消费方都正确过滤的列表。

### B 类 —— 全局单例，但语义属于某个会话

这是问题集合。每一项都是单个值，但它描述的东西属于某一个具体会话或某一个具体窗格。
只有一个会话可见时看不出来；有两个时，第二个窗格要么覆盖第一个，要么被迫读第一个的值。

| 字段 | 声明位置 | 为什么应该按会话隔离 |
| --- | --- | --- |
| `currentSessionId` | `web/lib/session-store/index.ts:74` | "那个"活动会话。两个窗格时有两个，其中一个只是*聚焦*的那个 |
| `activeChatKey` | `web/lib/session-store/index.ts:77` | 同上，用于未发送草稿的临时 `local_*` id |
| `runningTask` | `web/lib/session-store/index.ts:86` | 已废弃，应读 `runningTasks[sid]`；保留只为让旧的 `setRunning(false)` 调用方还能用。**阶段 1 已删除。** |
| `composerInput` | `web/lib/session-store/index.ts:178` | 聚焦会话的*活动*草稿；是 `composerDrafts[focused]` 的镜像。**阶段 1 已删除。** |
| `composerSettings` | `web/lib/session-store/index.ts:193` | 聚焦会话的*活动*设置；是 `composerSettingsBySession[focused]` 的镜像。**阶段 1 已删除。** |
| `composerFocusTick` | `web/lib/session-store/index.ts:206` | 自增计数器，用来让"那个"composer 聚焦输入框；两个 composer 时无法确定谁该响应 |
| `fnFormFunction` | `web/lib/session-store/index.ts:217` | 哪个函数的参数表单替换了输入框。属于某一个 composer，不属于整个应用 |
| `fnFormPrefill` | `web/lib/session-store/index.ts:226` | 该表单的预填参数 |
| `fnFormForkOf` | `web/lib/session-store/index.ts:227` | 重跑时的 fork 锚点节点 |
| `fnFormClosing` | `web/lib/session-store/index.ts:235` | 该表单的关闭动画标志 |
| `welcomeVisible` | `web/lib/session-store/index.ts:165` | 聊天区是否显示欢迎屏——这是每窗格的条件 |
| `transcriptLoadingId` | `web/lib/session-store/index.ts:172` | 只存*一个*在途会话 id；两个窗格可以同时在加载 |
| `branchInfo` | `web/lib/session-store/index.ts:62` | "当前会话"的分支 chip |
| `statusBadge` | `web/lib/session-store/index.ts:65` | 顶栏状态标签；由某一个会话的运行状态推导 |
| `paused` | `web/lib/session-store/index.ts:92` | 暂停标志，原理上应按运行中的会话分 |
| `providerInfo` | `web/lib/session-store/index.ts:94` | 顶栏显示的当前会话 provider/模型 |
| `detailNode` | `web/lib/session-store/index.ts:261` | 右栏显示的选中 DAG 节点 |
| `nodeSelected` | `web/lib/session-store/index.ts:271` | "有 DAG 节点被选中"的闸门 |

`detailNode` 和 `nodeSelected` 列在这里是因为它们*描述*某个会话的 DAG，但它们是容器化
工作明确的非目标——见第 8 节。

### C 类 —— 真正应该全局

这些属于应用本身而非任何会话，应该保持单值。

| 字段 | 声明位置 | 是什么 |
| --- | --- | --- |
| `wsStatus` | `web/lib/session-store/index.ts:48` | WebSocket 连接状态 |
| `agentSettings` | `web/lib/session-store/index.ts:51` | Chat/Exec 模型徽标，镜像自 `window._agentSettings` |
| `conversations` | `web/lib/session-store/index.ts:68` | 侧栏的会话*列表*（所有会话的目录，不是某个会话的视图状态） |
| `rightDock` | `web/lib/session-store/index.ts:256` | 右侧栏展开/收起及当前视图，持久化到 localStorage |

---

## 3. 其他 store

除主 session store 外，`web/lib/state/` 下还有若干较小的 store。它们都不在会话隔离的
关键路径上，但知道各自管什么可以避免以后重复造状态。

- **`web/lib/state/center-tabs-store.ts`**（1020 行）—— 标签条与窗格布局：`tabs`、
  `activeId`、`groups`、`splitWebTabId`、`splitRatio`
  （`web/lib/state/center-tabs-store.ts:129`）。这是*视图*状态，全局是正确的：它描述
  窗口，不描述会话。它也是唯一知道"当前存在分屏"的 store，所以作用域树的会话 id 从这里来。
- **`web/lib/state/center-tab-groups.ts`** —— 对标签布局的纯函数（分组、重排、分屏窗格）。
  自身无状态。
- **`web/lib/state/chat-scroll.ts`** —— 按 chat key 存的滚动位置助手，通过一个存储接口
  持久化（`web/lib/state/chat-scroll.ts:37`）。天然按会话分，只是不在 store 里。
- **`web/lib/state/functions-store.ts`**、**`skills-store.ts`**、
  **`plugins-store.ts`** —— 页面级清单及其筛选/排序/搜索的 UI 状态。确实是全局的；
  这些是设置页，不是会话。
- **`web/lib/state/files-shared.ts`** —— 项目列表、文件读取、按路径分键的文件草稿
  （`web/lib/state/files-shared.ts:144`）。按文件分，不按会话分。

---

## 4. 遗留的 `window.*` 层

还有第二套更老的状态层，早于 React store：直接挂在浏览器 `window` 对象上的可变属性，
由 `web/lib/runtime-bridge/` 下的模块读写。类型就地声明，比如
`web/lib/runtime-bridge/chat-handlers.ts:34` 和
`web/lib/runtime-bridge/conversations.ts:75`：

- `W.currentSessionId` —— 遗留层对"活动会话"的记法。
- `W.conversations` —— 一份重量级的每会话表，存完整消息数组，与 store 里轻量的
  `conversations` 摘要表不是同一个东西。
- `W.isRunning` —— 单个全局"有东西在跑"标志。
- `W.__sessionStore` —— 让遗留代码伸手进 React store 的逃生口，用在
  `web/lib/runtime-bridge/chat-handlers.ts:895`。

### 两层的关系

它们是**手工保持同步的双轨系统**。WebSocket 帧到达 runtime bridge，bridge 更新
`window.*` 的值，*同时*写穿到 React store。`web/lib/runtime-bridge/conv-store-mirror.ts:4`
明确写了这一点：侧栏读 `store.conversations`，所以每一处改动
`window.conversations` 的地方都必须同时走这个镜像，才能让 store 保持权威。

大部分进入的帧是用遗留全局而非 store 做闸门。`data.session_id === W.currentSessionId`
这个模式贯穿 `web/lib/runtime-bridge/chat-handlers.ts`（第 226、249、251、271、451、
524、588、747 行）和 `web/lib/runtime-bridge/conversations.ts`（第 332、387、527、530、
536 行）。每一处都是一个"针对*非聚焦*会话的事件会被丢弃或错投"的地方。

**哪些已经绕开了。** 分屏窗格完全不走遗留 shell。
`web/components/chat/peer-session-pane.tsx:1` 解释了原因：遗留的 `#chatView` shell 是
单例，键在写死的 DOM id（`#chatArea`、`#chatMessages`）上，被大约十个 runtime-bridge
模块读取，因此不可能挂载两次。分屏时 AppShell 把它整个隐藏
（`web/components/app-shell.tsx:552`），每个窗格改为直接从 store 渲染纯 React。窗格
甚至自己发 `load_session` 请求（`web/components/chat/peer-session-pane.tsx:66`），因为
遗留路径里没有任何东西会去加载非聚焦的会话。

**哪些还在用。** 非分屏路径的消息渲染、会话切换、分支徽标刷新，以及大部分 WebSocket
帧路由。遗留层没有死；它是默认路径，分屏才是绕开它的例外。

---

## 5. 分屏改造为什么频出 bug

模式永远是同一个：

> 组件渲染了两份，但它读的状态只有一份。

两份 `<Composer />` 挂载。两份都执行 `useSessionStore((s) => s.composerInput)`。
`composerInput` 只有一个。于是要么两个窗格显示同样的文字，要么后写的赢，要么在右窗格
敲的键出现在左窗格。类型系统不会报任何警告，因为在分屏窗格里读全局字段和在单窗格里读
一样合法。bug 只在运行时、只在分屏、且只在特定交互序列之后才显现——这就是它们一个一个
冒出来而不是一次全暴露的原因。

### 实例：`contextPanelFor`

`/context` 徽标位于 composer 内部，点击弹出该会话上下文构成的分解浮窗。

如果它当初写成一个普通布尔值 `contextPanelOpen`，分屏会渲染两个徽标，都订阅那一个布尔
值。点任意一个徽标会**同时**弹出两个浮窗，关掉一个则两个都关。

修复方式见 `web/lib/session-store/index.ts:209`：不再存"开没开"，改存**开在哪个会话上**：

```ts
/** /context 浮动弹窗：存"哪个会话的面板开着"（null = 关）。分屏时
 *  两个 composer 各渲染一个 badge，按会话区分才不会两边同时弹。*/
contextPanelFor: string | null;
```

消费方随即与自己的会话 id 比较（`web/components/chat/context-badge.tsx:44`）：

```ts
const panelOpen = useSessionStore((s) => s.contextPanelFor != null && s.contextPanelFor === sid);
```

这就是全局分键模式的缩影，而且它能跑：每一份渲染出来的副本都问"这是我的吗？"，只有一个
回答是。

它同时也是第 6 节决定不推广的那个模式。每个消费方仍要跟对的 id 比较，而跟错的 id 比较
——或者跟聚焦会话比较——在类型上依然合法。在每会话 store 下，这个字段就是
`contextPanelOpen`，一个属于徽标自己那个会话的普通布尔值，没有比较可以搞错。

### 镜像问题

`composerInput` 和 `composerSettings` 比普通全局值更糟：它们是**镜像**。真数据在按键
存的表里，这两个字段为聚焦的那个会话额外存一份副本。每次写都必须更新两处，每次切会话
都必须把镜像换过去（`switchChat`）。

镜像还渗进了组件。`web/components/chat/composer/index.tsx:166` 必须按是否绑定分支：

```ts
const input = useSessionStore((s) =>
  bound === null ? s.composerInput : (s.composerDrafts[bound] ?? ""),
);
```

同样的三行三元在 `fast` 和 `unattended` 上重复，在 `composer-session.tsx` 里又重复一次。
每一次重复都是一次把兜底写错的机会，而且 `bound === null` 那一支意味着写错的后果是
"静默用聚焦会话"，而不是报错。窗口间搬移会话时，`desktop-bridge.ts` 和
`tab-transfer-journal.ts` 也不得不重建这份镜像。

阶段 1 把这些全删了。活值归作用域所有，于是没有镜像可言、没有兜底分支，传输路径也没有
东西需要重建。

---

## 5b. 业界的三种做法

凡是"同一种界面可以同时开好几份"的软件都要解决这个问题，主流做法有三种。

**做法一：每个页面一个独立小仓库。** 不设全局仓库，而是每开一个编辑器/面板就现场创建
一份专属状态对象，关掉就销毁。VS Code 是典型——它可以左右开无数个编辑器分组，每个分组、
每个编辑器实例都有自己的状态对象，互相完全不可见；"当前聚焦哪个编辑器"是唯一的顶层信息。
Zustand（我们用的状态库）官方也支持这种"每实例一个 store"的用法。优点是隔离最彻底，缺点
是跨页面共享的数据（比如会话列表）还得另设一层，两层之间要接线。

**做法二：一个全局仓库，内部按 ID 分格 + 组件树挂作用域标签。** 仓库还是一个，但所有
实例相关的数据都存成 `{ id → 数据 }` 的字典；组件树的根上用 React 的 Context（就是我们
的 Provider）声明"这棵树属于 ID 几"，树里组件自动读自己那格。Redux 官方推荐的
normalization（按 ID 归一化存储）就是这个思路；数据请求库 TanStack Query 更彻底，所有缓存
一律按 key 分格，根本不存在"当前那份"的概念。Slack、Discord 这类多频道界面基本都这么做。
优点是共享数据和实例数据在一个仓库里，调试方便、跨会话功能（侧栏列表、全局搜索）好写；
缺点是纪律要靠约定，谁偷偷读了"当前会话"的全局指针就又退化回去。

**做法三：物理隔离。** 浏览器的做法——每个标签页干脆是独立进程，状态想串都串不了。
Chrome 是最极端的例子：主进程管标签条/书签/设置等全局态，每个标签页的网页内容跑在独立的
渲染进程里，进程间只能走 IPC 消息。它付这个代价有特有的理由——跑的是不受信任的第三方代码，
隔离同时是安全边界（Site Isolation 防 Spectre 类攻击）。应用内分屏跑的都是自己的代码，
不需要为"数据别串"付进程级的内存和通信成本，没人这么干。

本项目最终选做法一（见下节），做法二作为曾考虑的替代方案记录在 6 节末尾。

---

## 6. 方案：每个会话一个独立 store 实例

### 拍板的决定

**每个会话拥有自己的 Zustand store 实例。真正共享的数据留在唯一的全局 store。**

组件不指名会话，也不读全局活动切片。它只读"我的草稿"，由外层的 `SessionScopeProvider`
决定那是谁的。于是"同一个组件对两个会话各渲染一份"变成了普通情形，而不是会出事的情形：
两份订阅的是两个不同的 store，抢不起来，因为根本没有共享的东西可抢。

划分依据是**归属**，不是存储形状：

| 层 | 内容 | 访问方式 |
| --- | --- | --- |
| **全局 store** | `wsStatus`、`agentSettings`、`conversations`（列表）、`messagesById`、`rightDock`，以及需要持久化的按会话分键表（`composerDrafts`、`composerSettingsBySession`、`runningTasks`） | `useSessionStore` |
| **会话 store 实例** | 本会话的 `draft`、`settings`、`running`、`contextPanelOpen` | `useSessionScope`，须在 `SessionScopeProvider` 内 |
| **视图态** | 标签条、窗格布局、分屏比例、哪个窗格聚焦 | `center-tabs-store`；属于窗口，不属于会话 |

### 组成部件

`web/lib/session-store/session-scope-registry.ts` 放 store 工厂和模块级
`Map<sid, store>`。实例会缓存，**窗格卸载不销毁**——切走标签再切回来，正在打的草稿还在。
只有会话本身被删除时才丢弃（`dropSessionStore`，由 `removeConversation` 和
`dropChatDraft` 调用）。

`web/lib/session-store/session-scope.tsx` 是 React 层：`SessionScopeProvider sid=…`
和 `useSessionScope(selector)`。

**不存在无绑定路径。** 组件不在 provider 内时 `useSessionScope` 直接抛错，而不是回落到
聚焦会话。静默兜底正是这一层要消灭的 bug，而且它只会在分屏、且经过特定交互序列后才暴露；
抛错让漏包在第一次渲染时就现形。现有两个 provider 覆盖了全部 composer：
`web/components/app-shell.tsx` 里的 `FocusedComposer` 用聚焦 chat key 包住单会话
composer，`PeerSessionPane` 用各自的会话 id 包住每个分屏窗格。

### 为什么用实例，而不是全局分键切片

两种形状都能止住冲突。选实例是因为它消除了**写出这个 bug 的可能性**，而不只是消除当下
这几处。全局分键切片下，每个消费方在每次读写时仍要自己给对键；给错了键——或者干脆不给、
落到聚焦会话——在类型上依然合法。用实例，键由 provider 给一次，组件没有办法误指到别的
会话。

订阅粒度也天然更窄：一次按键只通知本会话的订阅者，不需要对选择器格外小心。

### 需要持久的状态仍归全局 store

实例持有的是活状态。任何需要活过本标签页的东西——localStorage 持久化、跨窗口搬运的
线上格式、侧栏的跨会话视图——都留在全局分键表里。两边双向对齐：

- **实例 → 全局。** 实例的 setter 先更新自己（窗格同一 tick 重绘），再写穿到全局分键
  setter。这些钩子由全局 store 在模块初始化时通过 `installScopeWriteThrough` 装上，
  正是这一点让 import 保持单向。
- **全局 → 实例。** `setComposerInputFor`、`setComposerSettings`、`setRunningTaskFor`
  都会调 `pushToSessionStore`，于是 WS 帧、遗留桥接、跨窗口搬标签也能落进活实例。

实例首次渲染时从全局表取种子值，这是"刷新后重新挂载能看到持久化草稿而不是空框"的原因。

### 后果：镜像字段没了

因为活值归实例所有，全局 store 不再需要"聚焦会话的活切片"这份副本。`composerInput`、
`composerSettings`、`runningTask` 被直接删除，`contextPanelFor`（一个当按会话标志用的
单字段）同样删除。聚焦会话的草稿现在就是 `composerDrafts[activeChatKey]`，在需要的地方
算出来，而不是存两份。

### 曾考虑的替代方案：全局仓库 + 作用域标签

另一条路是保留单个全局 store，把每个按会话的字段都做成 `Record<sid, T>`，再用一个
React context 提供键，让消费方读 `map[scopeKey]` 而不是读全局。`contextPanelFor` 就是
这个模式的缩影：它存的是**哪个**会话开着面板，每个徽标自问"这是我的吗？"。

它能跑通，改动也更小。没有采用，是因为它把失效模式留着了：键仍然每次访问都要传，漏传
仍然静默地表示"聚焦会话"，而且类型系统区分不了一次正确的读取和一次串了作用域的读取。
它对的那部分——用并列的 `Record<sid, T>` 而不是一张嵌套的 `sessions` 表、`messagesById`
保持扁平且全局——原样保留，因为这些表正是实例赖以持久化的东西。

---

## 7. 迁移路径

### 阶段 1 —— 搭作用域、删镜像 —— **已完成**

新建 `session-scope-registry.ts`（store 工厂、实例表、写穿钩子）和
`session-scope.tsx`（`SessionScopeProvider`、`useSessionScope`）。

从全局 store 删除：`composerInput`、`composerSettings`、`runningTask`、
`contextPanelFor`，以及 setter `setRunningTask`、`setContextPanelFor`。

各迁移点：

- `switchChat` 不再保存切出会话的文本、也不再载入切入会话的——两者本来就在分键表里。
  剩下的只有：未发送会话拿到真实 id 时，把 `__new__` 占位键提升过去。
- `web/components/chat/composer/index.tsx` —— 四处
  `bound === null ? 全局 : keyed[bound]` 三元（草稿、`fast`、`unattended`、设置 setter）
  收敛成普通的 `useSessionScope` 读取。`bound` 只保留它真正区分的东西：发送时的
  `background: true`，以及各窗格的 DOM id 后缀。
- `web/components/chat/composer/composer-session.tsx` —— 缩成两个基于作用域的单行 helper；
  context 和 `null` 兜底都没了。三个控件 hook（`use-thinking-effort`、
  `use-tools-toggles`、`use-permission-mode`）无需改动，因为它们消费的正是这两个 helper。
- `web/components/app-shell.tsx` —— portal 出去的 `<Composer />` 换成
  `<FocusedComposer />`，由它用 `activeChatKey ?? currentSessionId ?? "__new__"` 包一层
  provider。正是这一步让无绑定分支可以被删掉，而不只是被绕开。
- `web/components/chat/peer-session-pane.tsx` —— `ComposerSessionProvider` 换成
  `SessionScopeProvider`，于是窗格的整棵 composer 子树（草稿、运行态、设置、`/context`）
  都在作用域内，而不只是控件 hook。
- `web/components/chat/context-badge.tsx` —— `contextPanelFor === sid` 换成
  `useSessionScope(s => s.contextPanelOpen)`。
- 线上格式：`SessionTransferSnapshot` 去掉 `composerInput` / `composerSettings`，
  `ChatTransferState` 去掉 `activeComposerInput` / `activeComposerSettings`。这四个都是
  `activeChatKey` 对应分键项的副本，接收窗口自己查得到。`desktop-bridge.ts` 和
  `tab-transfer-journal.ts` 随之简化。
- 遗留桥接：`runtime-bridge/ui.ts` 和 `chat-handlers.ts` 本来就优先用
  `setRunningTaskFor`，把它们已死的 `setRunningTask` 声明删掉。那个分键 setter 现在还会
  同时推进活实例。

验证：`npx tsc --noEmit` 干净，`npx next build` 通过，`npm run check` 十个套件全绿。
`check-provisional-send` 把一条针对旧镜像守卫的源码形状断言换成了行为断言（改后台会话的
设置不影响聚焦会话，后台会话也不会继承聚焦会话的值）；`check-multi-draft` 和
`check-web-split` 改为按 `composerDrafts[activeChatKey]` 读聚焦草稿。五个检查脚本的模块
解析器另外补了"无扩展名相对导入"分支，以及仓库路径含空格时的 `fileURLToPath` 修正。

### 阶段 2 —— 剩余 B 类字段逐个入容器

剩下每个字段都变成 `SessionScopeState` 上的一个属性，消费方改用 `useSessionScope`。
按大致的独立程度排序：

1. `fnFormFunction` / `fnFormPrefill` / `fnFormForkOf` / `fnFormClosing` →
   作用域上的一个 `fnForm` 属性。涉及
   `web/components/chat/composer/modes/resolve-mode.ts:18`、
   `web/components/chat/composer/modes/fn-form/use-fn-form-state.ts`、
   `use-fn-form-wrapper.ts`、`web/lib/use-pending-run-function.ts`、
   `web/components/sidebar/favorites-list.tsx`、
   `web/components/sidebar/sidebar.tsx`、
   `web/components/chat/messages/runtime-block.tsx`、
   `web/lib/runtime-bridge/functions-panel.ts`。注意侧栏和收藏列表在*打开*表单时并不知道
   该由哪个窗格承载——它们需要一个明确的目标会话，这是一个真实的行为决策，不是机械移植。
2. `welcomeVisible`、`transcriptLoadingId` → 每会话布尔值。消费方：
   `web/components/chat/welcome-screen.tsx`、
   `web/components/chat/messages/message-list.tsx`。
3. `composerFocusTick` → 每会话计数器，这样聚焦一个窗格的输入框不会把另一个拽走。
4. `branchInfo`、`statusBadge`、`paused`、`providerInfo` → 每会话。它们喂给顶栏，而顶栏
   显示的是*聚焦*会话，所以顶栏读聚焦作用域，视觉上没有其他变化。
5. `currentSessionId` / `activeChatKey` → 保留为视图层的*全局聚焦指针*。它们不再表示
   "那个会话"，而是表示"聚焦窗格的会话"，这正是 `center-tabs-store` 已经用 `activeId`
   跟踪的东西。这是最后动的一个，也是对 runtime bridge 影响最大的一个。

风险：每一步都要重新核查 `pendingDecisions` 的路由——它在
`web/components/chat/composer/index.tsx:352` 按 `sessionId` 过滤，作用域搞错的 composer
要么吞掉另一个会话的提问，要么把它显示两遍。fn-form 这一组是单项最大的改动，因为有八个
文件消费它。

规模：大约十五到二十个文件，可拆成五次独立提交，每次都能单独验证。作用域基础设施已经
就位，所以每个字段都是搬家，不是新造机制。

### 阶段 3 —— 遗留 `window.*` 退役

这一阶段在阶段 2 完成前不能开始，因为 bridge 的路由闸门读的是 `W.currentSessionId`，而
在 store 把"聚焦"和"那个会话"区分开之前，没有正确的替代品。

退役条件，必须全部成立：

1. `web/lib/runtime-bridge/chat-handlers.ts` 和 `conversations.ts` 里每一处
   `data.session_id === W.currentSessionId` 闸门都换成按 `data.session_id` 分键的 store
   写入，让后台会话的帧能落地而不是被丢弃。
2. `window.conversations` 没有任何 store 按键表覆盖不到的读取方；此时
   `web/lib/runtime-bridge/conv-store-mirror.ts` 从承重件变成多余件。
3. `W.isRunning` 没有读取方；`runningTasks` 覆盖它。
4. 单例的 `#chatView` DOM shell 消失，处处换成 `PeerSessionPane` 已经证明可行的 React
   路径。这是最重的前置条件——大约十个 runtime-bridge 模块靠写死的 id 访问它。

风险：这一阶段出错的表现是静默丢 WebSocket 帧，而不是渲染错误。每移除一个闸门，都需要
配套验证那个帧仍然到达它的会话。

规模：整个 `web/lib/runtime-bridge/`（十三个模块加 `dag` 子目录）。规模最大的一个阶段，
也是唯一一个"做一半比两个端点都差"的阶段——因此应排在最后，并按模块以一次持续的推进
完成，而不是零敲碎打。

---

## 8. 非目标

**右栏与 DAG 保持单份，跟随聚焦会话。** `detailNode` 和 `nodeSelected`
（`web/lib/session-store/index.ts:261`、`:271`）保持全局。没有任何东西把它们渲染两份：
右栏只有一个 dock（`web/components/right-sidebar/right-sidebar.tsx:407`、`:575`），也没有
让每个窗格各有一份 DAG 的计划。它们继续读聚焦会话就是正确的。

`rightDock` 本身、顶栏徽标的*显示*（按定义就是显示聚焦会话）、以及设置页的各 store，
同理。

**持久化表保持原形状。** 全局 store 里并列的 `Record<sid, T>` 多张表原样保留；它们正是
每会话实例取种子值和持久化所依赖的东西。把它们合成一张嵌套的 `sessions` 对象没有计划，
对正确性也不是必需。

**两个以上窗格不在范围内。** 作用域机制碰巧让 N 个窗格也能工作，但这里没有任何东西是
按 N > 2 设计或验证过的。

---

## 9. 各阶段开销

| 阶段 | 涉及文件 | 主要风险 | 状态 |
| --- | --- | --- | --- |
| 1 —— 搭作用域、删镜像 | 新增 2 个，改动约 10 个（store、composer、app-shell、分屏窗格、徽标、2 个持久化、2 个桥接），外加 5 个检查脚本 | 切会话丢草稿；传输日志格式变更 | 已完成 |
| 2 —— 剩余 B 类字段入作用域 | 约 15–20 个，可拆成 5 次提交 | fn-form 打开目标的语义；`pendingDecisions` 路由 | 待做 |
| 3 —— `window.*` 退役 | 约 13 个 runtime-bridge 模块加 DOM shell | 静默丢 WebSocket 帧；做一半比两个端点都差 | 待做 |

阶段 1 和 2 可独立交付，各自都让代码库比动手前更好。阶段 3 不是，应当作为一个独立项目
对待，而不是渐进清理。

---

## 相关

- [`composer-interaction-modes.zh.md`](composer-interaction-modes.zh.md) ——
  composer 如何在 idle、fn-form、question、approval 之间仲裁
- [`invariants.zh.md`](invariants.zh.md) —— 跨模块 UI 不变量
- [`interaction-feedback.md`](interaction-feedback.md) —— 塑造这些写入顺序的
  乐观状态优先规则
