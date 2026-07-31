# Web 状态层：现状盘点与多页面容器化方案

这一页从零解释 web 前端的状态存放在哪里、哪些已经按会话隔离、哪些还是单个全局值、
最近的分屏改造为什么反复出 bug，以及状态层应该变成什么样，才能让"同屏两个会话"
不再是特例。

不预设前端知识。先介绍三个必要概念，之后全部是对照真实代码的盘点，引用格式为
`文件:行号`。

---

## 1. 三个概念

**store 是一个共享的变量盒子。** web UI 用
[zustand](https://github.com/pmndrs/zustand)——一个小库，创建一个对象，里面既放数据
（`currentSessionId`、`composerInput`），也放修改数据的函数（`setCurrentConv`、
`setComposerInput`）。页面任何位置的组件都能直接读这个盒子里的任何字段，不需要一层层
传 props。主盒子在 `web/lib/session-store/index.ts:411`。

**组件订阅一个切片。** 组件调用 `useSessionStore((s) => s.composerInput)` 时，只有
`composerInput` 变化才会触发它重新渲染。那个选择器函数就是订阅关系。

**同一个组件可以被渲染多份。** React 组件是模板。`<Composer />` 在普通布局里出现一次，
在分屏里出现两次。两份跑同样的代码，因此两份从同一个盒子读同一个字段。如果那个字段是
单个全局值而不是按会话分键的表，两份就会互相抢。第 5 节的大部分问题都是这一句话。

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
| `contextPanelFor` | `web/lib/session-store/index.ts:211` | `/context` 浮窗开在*哪个*会话上——单字段当按会话标志用，见第 5 节 |

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
| `runningTask` | `web/lib/session-store/index.ts:86` | 注释里已明确标为废弃，应读 `runningTasks[sid]`；保留只为让旧的 `setRunning(false)` 调用方还能用。写时在 `web/lib/session-store/index.ts:678` 镜像 |
| `composerInput` | `web/lib/session-store/index.ts:178` | 聚焦会话的*活动*草稿；是 `composerDrafts[focused]` 的镜像。每次按键在 `web/lib/session-store/index.ts:703` 写入 |
| `composerSettings` | `web/lib/session-store/index.ts:193` | 聚焦会话的*活动*设置；是 `composerSettingsBySession[focused]` 的镜像。镜像逻辑在 `web/lib/session-store/index.ts:727` |
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

这就是整个设计模式的缩影。状态没有变成每窗格存储，而是变成**由会话标识**，于是每一份
渲染出来的副本都能问"这是我的吗？"，且只有一个回答是。

`composerDrafts` 和 `composerSettingsBySession` 已经是这个形状。剩下的每一个 B 类字段
都是尚未完成这一变换的地方。

### 镜像问题

`composerInput` 和 `composerSettings` 比普通全局值更糟：它们是**镜像**。真数据在按键
存的表里，这两个字段为聚焦的那个会话额外存一份副本。每次写都必须更新两处
（输入在 `web/lib/session-store/index.ts:703`，设置在
`web/lib/session-store/index.ts:727`），每次切会话都必须把镜像换过去
（`switchChat`，`web/lib/session-store/index.ts:367`）。

镜像还渗进了组件。`web/components/chat/composer/index.tsx:166` 必须按是否绑定分支：

```ts
const input = useSessionStore((s) =>
  bound === null ? s.composerInput : (s.composerDrafts[bound] ?? ""),
);
```

同样的三行三元在 `fast`（`web/components/chat/composer/index.tsx:460`）和
`unattended`（`web/components/chat/composer/index.tsx:480`）上重复，在
`web/components/chat/composer/composer-session.tsx:39` 里又重复一次。每一次重复都是一次
把兜底写错的机会。窗口间搬移会话时，`web/lib/desktop-bridge.ts:677` 和
`web/lib/tab-transfer-journal.ts:249` 也不得不重建这份镜像。

---

## 6. 方案：会话作用域即容器

### 核心概念

**会话作用域**是某个 React 组件做出的一项声明："我下面这棵子树里的一切都属于会话 X"。
每个会话相关的 hook 读取这项声明，自动操作那个会话的切片。组件不再指名会话，也不再读
全局活动切片；它们只读"我这个会话的草稿"，由 provider 决定那是哪一个。

这个机制在代码库的一角已经存在。
`web/components/chat/composer/composer-session.tsx:22` 创建了一个存放 chat key 的
React context，`web/components/chat/composer/composer-session.tsx:35` 暴露了解析它的 hook：

```ts
export function useBoundComposerSettings() {
  const bound = useComposerSessionKey();
  return useSessionStore((s) =>
    bound === null
      ? s.composerSettings
      : (s.composerSettingsBySession[bound] ?? s.composerSettings),
  );
}
```

分屏窗格在 `web/components/chat/peer-session-pane.tsx:234` 把 composer 包在里面，已有
三个控件 hook 消费它：`use-permission-mode.ts:48`、`use-tools-toggles.ts:29`、
`use-thinking-effort.ts:73`（都在 `web/components/chat/composer/controls/` 下）。

**方案就是把它从 composer 局部的小技巧提升为状态层的主访问方式。** 两处改动使其通用：

1. provider 上移。不再只包 composer，而是让 `SessionScopeProvider` 包住每个窗格的整棵
   子树——消息列表、composer、context 徽标、欢迎屏。单窗格布局也包一个，用聚焦会话包住
   整个聊天视图。
2. 去掉 `null` 兜底。今天 `null` 表示"跟随聚焦会话"，这保住了旧行为，但也让"读全局"这条
   路径依然合法。等每棵子树都包上之后，无作用域的读取就成了 bug，可以加 lint 规则禁止
   组件直接碰 `s.composerInput`、`s.currentSessionId`、`s.fnFormFunction`。

### 三层分层

| 层 | 内容 | 访问方式 |
| --- | --- | --- |
| **全局态** | `wsStatus`、`agentSettings`、`conversations`（列表）、`rightDock`、偏好 | 直接从 store 读 |
| **会话态容器** | `sessions: Record<sid, SessionState>`，每份含 `messages`、`draft`、`settings`、`running`、`panels`、`scroll` | 只能通过作用域感知的 hook 读 |
| **视图态** | 标签条、窗格布局、分屏比例、哪个窗格聚焦 | `center-tabs-store`；属于窗口，不属于会话 |

目标的会话切片，把今天散落的表归拢起来：

```ts
interface SessionState {
  messageIds: string[];
  draft: string;
  settings: ComposerSettings;
  running: RunningTask | null;
  head: string | null;
  tokens: TokenUsage | null;
  contextWindow: number | null;
  tree: TreeNode | null;
  additionalWorkingDirs: string[];
  panels: { contextOpen: boolean; fnForm: FnFormState | null };
  transcriptLoading: boolean;
  welcomeVisible: boolean;
}
```

这究竟是字面上的一张嵌套表，还是保持今天并列的 `Record<sid, T>` 多张表，是一个有真实
性能后果的实现细节——嵌套对象意味着对任一会话的写入都会让订阅整个 `sessions` 对象的
消费方失效，除非选择器写得很小心。**保持并列表布局、只改访问方式是更省的路径**，下面的
迁移计划也按这个假设写。`messagesById` 保持扁平且全局，因为它按消息 id 分键，共享反而
有好处。

真正要紧的区别不是存储形状，而是**没有任何组件在无作用域的情况下读会话字段**。

---

## 7. 迁移路径

### 阶段 1 —— 消除活动切片镜像

删掉 `composerInput` 和 `composerSettings` 这两个存储字段。所有读取都走作用域；聚焦会话
的作用域解析为 `composerDrafts[focused]` 和 `composerSettingsBySession[focused]`。

涉及文件：

- `web/lib/session-store/index.ts` —— 删两个字段，重写 `setComposerInput`（`:703`）、
  `setComposerInputFor`（`:712`）、`setComposerSettings`（`:727`），并去掉 `switchChat`
  （`:367`）里负责换镜像的那一半。
- `web/components/chat/composer/composer-session.tsx` —— `:39` 和 `:51` 的 `null` 分支
  收敛成一次聚焦会话查表。
- `web/components/chat/composer/index.tsx` —— `:166`、`:460`、`:480` 的三元变成普通的
  作用域读取。
- `web/lib/tab-transfer-journal.ts:249` 与 `web/lib/desktop-bridge.ts:677`、`:707`、
  `:835` —— 这些在窗口搬移时序列化那份镜像，必须改成只携带按键存的表加一个聚焦键。

风险：快照/恢复路径是尖锐边缘。`snapshotSessionTransfer`
（`web/lib/session-store/index.ts:819`）和 `applySessionTransfer`
（`web/lib/session-store/index.ts:840`）的传输格式里都带着 `composerInput` 和
`composerSettings`，所以传输日志的形状会变，且旧的持久化 blob 必须能优雅降级。第二个
风险是切会话时丢草稿：`switchChat` 现在会把切出去那个会话的活动文本写回表里——没有镜像
之后就没东西可写，但"按键写入表"与"改聚焦键"之间的先后顺序必须核实。

规模：一个 store 文件、三个 composer 文件、两个持久化文件。面很小，但对草稿的杀伤半径
很大——这一阶段值得手工过一遍 输入/刷新/切换/分屏 的序列。

### 阶段 2 —— 剩余 B 类字段逐个入容器

按大致的独立程度排序，逐个来：

1. `fnFormFunction` / `fnFormPrefill` / `fnFormForkOf` / `fnFormClosing` →
   `sessions[sid].panels.fnForm`。涉及
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
3. `runningTask` → 删除；`runningTasks[sid]` 已经是权威，
   `web/lib/session-store/index.ts:678` 的镜像随之消失。需要审计遗留的 `setRunning` 调用方。
4. `composerFocusTick` → 每会话计数器，这样聚焦一个窗格的输入框不会把另一个拽走。
5. `branchInfo`、`statusBadge`、`paused`、`providerInfo` → 每会话。它们喂给顶栏，而顶栏
   显示的是*聚焦*会话，所以顶栏读聚焦作用域，视觉上没有其他变化。
6. `currentSessionId` / `activeChatKey` → 保留为视图层的*全局聚焦指针*。它们不再表示
   "那个会话"，而是表示"聚焦窗格的会话"，这正是 `center-tabs-store` 已经用 `activeId`
   跟踪的东西。这是最后动的一个，也是对 runtime bridge 影响最大的一个。

风险：每一步都要重新核查 `pendingDecisions` 的路由——它在
`web/components/chat/composer/index.tsx:352` 按 `sessionId` 过滤，作用域搞错的 composer
要么吞掉另一个会话的提问，要么把它显示两遍。fn-form 这一组是单项最大的改动，因为有八个
文件消费它。

规模：大约十五到二十个文件，但可拆成六次独立提交，每次都能单独验证。

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

**不重新设计存储形状。** 这次迁移改的是状态的*访问*方式，不一定是它的排布方式。把今天
并列的 `Record<sid, T>` 多张表合成一张嵌套的 `sessions` 对象是可选项，带有订阅粒度的
风险，且对正确性不是必需。

**两个以上窗格不在范围内。** 作用域机制碰巧让 N 个窗格也能工作，但这里没有任何东西是
按 N > 2 设计或验证过的。

---

## 9. 各阶段开销

| 阶段 | 涉及文件 | 主要风险 |
| --- | --- | --- |
| 1 —— 消除活动切片镜像 | 约 6 个（store、3 个 composer、2 个持久化） | 切会话丢草稿；传输日志格式变更 |
| 2 —— B 类入容器 | 约 15–20 个，可拆成 6 次提交 | fn-form 打开目标的语义；`pendingDecisions` 路由 |
| 3 —— `window.*` 退役 | 约 13 个 runtime-bridge 模块加 DOM shell | 静默丢 WebSocket 帧；做一半比两个端点都差 |

阶段 1 和 2 可独立交付，各自都让代码库比动手前更好。阶段 3 不是，应当作为一个独立项目
对待，而不是渐进清理。

---

## 相关

- [`composer-interaction-modes.zh.md`](composer-interaction-modes.zh.md) ——
  composer 如何在 idle、fn-form、question、approval 之间仲裁
- [`invariants.zh.md`](invariants.zh.md) —— 跨模块 UI 不变量
- [`interaction-feedback.md`](interaction-feedback.md) —— 塑造这些写入顺序的
  乐观状态优先规则
