# Composer interaction modes — 输入框作为"用户决定"的统一承接点

Status: **已落地**（2026-06-14）。五步全部实现并浏览器自验，逐步提交。
as-built：modes 框架骨架（26685949）→ question mode + 删浮窗（bc144b8c）→
后端审批合流（73a094be）→ approval mode + 拒绝附理由（27c05faa）→ 冲突排队 +
超时收回（c0c8956e）。唯一与原设计的偏差：fn-form 第一版仍由 composer 内联
渲染（它本就是"输入框变形"的范本），未正式塞进 modes 注册表——question /
approval 走注册表式的 mode 组件；fn-form 纳入注册表作为后续收尾（非阻塞）。

## 一句话

把聊天输入框（composer）从"只能打字"升级成一个**能切换多种形态的容器**。
每一种形态是一个"变换"（mode）：填函数表单是一种、回答 runtime.ask 的问题
是一种、批准一个工具是一种。所有"需要用户做决定"的交互都**就地在输入框里
变形呈现**，而不是各弹各的浮窗。每种 mode 各自一个文件夹、走同一套接口，
后续新交互要么直接复用一种 mode，要么在已有 mode 上做衍生。

## 为什么这么做

现在前端有两套互不一致的"要用户操作"的呈现：

| 交互 | 现在怎么呈现 | 范式 |
|---|---|---|
| 跑一个 @agentic_function | 输入框**就地变成参数表单**（fn-form），Send 变"运行" | 输入框变形 ✅ |
| runtime.ask 问问题 | 屏幕中间一个**独立浮窗卡片**（question-prompt.tsx，portal 到 body） | 浮窗 ❌ |
| 工具批准（permission ask） | **没有 UI**（生产里是死的，只有测试在后台 resolve） | 无 ❌ |

三种"该用户了"的交互，一种就地变形、一种浮窗、一种没有。用户的注意力被
扯到不同地方，代码也各写各的。**统一成一处**：所有"该用户了"都发生在输入框，
用户视线不离开输入区；前端只有一个"用户决定"承接点。

这也跟事件层对齐：事件层是统一事件流，任何"需要用户决定"的事件（question.asked、
将来的 approval.asked / form.asked）在前端都该落到**同一个出口**——输入框的
mode 容器，由它选一种变换来呈现。一个后端 registry（QuestionRegistry）、一个
前端承接点（composer），一条事件路径。

## 现状：fn-form 已经是"变换"的范本

读 `web/components/chat/composer/` 得到的事实——fn-form 已经把"输入框变形"
做对了，新框架是把它的隐式约定显式化、再容纳更多 mode：

* **触发态在 store**：`session-store.ts` 的 `fnFormFunction`（+ `fnFormClosing`），
  `openFnForm(fn)` / `closeFnForm()`。非空 = 当前处于 fn-form 形态。
* **字段态在 hook**：`use-fn-form-state.ts`（values / workdir / error / closing），
  fn 变了就重新播种默认值。
* **视觉在 module.css**：`inputWrapper` 加 `fnFormMode` 切形；`outgoingLayer`
  做 fn→fn 切换时的交叉淡出动画。
* **Send 按钮行为跟着切**：`onSendButtonClick = fnFormActive ? submitFnForm : submit`，
  disabled / title 也随当前形态变。
* **组件**：`fn-form/fn-form.tsx`（外形）+ `fn-form-fields.tsx`（字段渲染）。

问题（runtime.ask）现在没走这套，是独立浮窗（`web/components/ui/question-prompt.tsx`，
监听 `op:question-asked` window 事件、发 `question_reply`/`question_reject`）。
本设计让它退役，改成一种 mode。

## 模型：容器 + 变换（mode）

### 容器（composer）

composer 任一时刻处于**一种** mode：

* `idle` —— 普通打字（默认）。
* `fn-form` —— 填函数参数表单。
* `question` —— 回答 runtime.ask（选项 / 多选 / 自由文本）。
* `approval` —— 批准/拒绝一个工具执行（question 的衍生：固定两个选项 +
  危险动作摘要）。
* 将来：`form`（runtime.form 多字段）、`diff-approve`（带 diff 预览的批准）……

同一时刻只有一种 mode 占据输入区（互斥）。mode 切换走容器的状态机，进入/退出
都有就地变形动画（沿用 `outgoingLayer` 交叉淡出）。

### 一种 mode 的统一接口

每种 mode 是一个**自包含单元**，对容器暴露同一套契约（草案，实现时定型）：

```ts
interface ComposerMode<TState> {
  id: string;                       // "fn-form" | "question" | "approval" | …
  // 进入这种 mode 需要的数据（fn 定义 / 问题 envelope / 批准请求）
  // 由触发源塞进 store，容器读出来传给 mode。
  useModeState(input): TState;      // 这种 mode 的局部状态 hook（如 use-fn-form-state）
  Body: React.FC<{ state: TState; ... }>;   // 输入区里渲染的主体
  // 主操作按钮（占住 composer 的 Send 位）的行为/文案/可用性
  primaryAction(state): { label; disabled; run: () => void };
  // 次操作（取消/拒绝），退出 mode
  secondaryAction(state): { label; run: () => void } | null;
  onExit?(): void;                  // 收尾（清状态、发未答信号等）
}
```

容器只认这个接口；加一种 mode = 加一个实现了它的文件夹，**不改容器主体**。

### 文件组织

```
web/components/chat/composer/
  modes/
    index.ts            # mode 注册表（id → ComposerMode），容器据此查表
    types.ts            # ComposerMode 接口
    fn-form/            # 现有 fn-form 迁进来，作为第一种 mode
    question/           # runtime.ask（吸收 question-prompt 的逻辑，去掉浮窗）
    approval/           # 工具批准（question 的衍生）
  index.tsx             # 容器：读当前 mode、查表、渲染 Body + 接管 Send
```

后续衍生：`approval/` 直接 import `question/` 的 Body 再包一层（加危险摘要），
就是"在已有变换上做衍生"。

## 事件如何路由进来

后端不变地经事件层发"要用户决定"的帧（`question.asked`，审批合流后也走它，
见下）。前端：

1. `use-ws.ts` 收到 `question.asked` → 现在转成 `op:question-asked` window 事件
   给浮窗。改成：写入 store 的 `pendingDecision`（envelope）。
2. composer 容器订阅 `pendingDecision`：非空就根据 `kind` 选 mode
   （`ask`/`confirm` → question，`approval` → approval）进入该形态。
3. 用户在输入区操作 → mode 的 primary/secondary 发 `question_reply` /
   `question_reject`（沿用现有 WS action，后端 `_resolve_question` 收口不变）。
4. 别处先答了 / stop → 后端广播 `question.replied`/`rejected` → 前端清
   `pendingDecision`、退出 mode（沿用现有"收回"逻辑）。

**互斥与优先级（已定，2026-06-13）**：一次只呈现一个 mode，规则两条——

* **系统决定之间排队**：两个"系统要用户决定"的事件（如先来 question 再来
  approval）→ FIFO 排队，一次一个。答完前一个，自动呈现下一个。不叠加、
  不并排。
* **系统决定 vs 用户主动开的 mode**：用户自己点开的 fn-form 撞上一个系统
  决定 → 直接**取消** fn-form（用户主动开的，丢弃无所谓），让系统决定占住
  输入区。不暂存、不恢复。

即：`pendingDecision` 是一个 FIFO 队列；队首非空时占据输入区。新系统决定入队；
若此刻是用户主动 mode（fn-form），清掉它再显示队首。实现简单，无栈、无快照。

## 后端：审批合流到 QuestionRegistry

为了让"审批"也走同一条事件路径 → 落到同一个 composer 承接点，后端把
`_approval.py` 合流到 `QuestionRegistry`（user-input-requests.md 点 6）：

* `await_user_approval` 不再用独立的 `ApprovalRegistry` + 自定义
  `approval_request` 信封，而是注册一个 `kind="approval"` 的 PendingQuestion
  （prompt = "允许执行 {tool}?"，options = ["允许","拒绝"]，detail = 参数摘要），
  经事件层发 `question.asked`。
* 异步等待沿用 `asyncio.to_thread(ev.wait, timeout)`（工具 execute 是协程，
  不能同步阻塞 loop）。
* 布尔结果从问题三态映射：answered「允许」→ True；declined / timeout → False。
* `ApprovalRegistry` 退役；`approval_registry()` 访问器保留为薄垫片或迁移
  调用方；两个 dispatcher 批准测试改写成走 QuestionRegistry。

合流后：一个 registry、一种事件（question.asked）、一个前端承接点。审批顺带
**复活**（之前没 UI）。

## 退役（已完成）

* `web/components/ui/question-prompt.tsx` 浮窗 + app-shell 挂载 → 已删。
* `_approval.py` 的 `ApprovalRegistry` / `approval_request` 信封 → 已删；
  `approval_registry()` 返回统一 QuestionRegistry。

## 落地顺序（每步独立验证，全部完成）

1. ✅ **框架骨架**（26685949）：`modes/types.ts`（ComposerMode 接口）+
   `modes/index.ts`（注册表）。fn-form 第一版仍内联（见 Status 偏差说明）。
2. ✅ **question mode**（bc144b8c）：`modes/question/`，runtime.ask 在输入框
   就地呈现；删浮窗；use-ws → store 的 pendingDecisions 队列。真子进程端到端验证。
3. ✅ **后端审批合流**（73a094be）：`await_user_approval` 走 QuestionRegistry
   （kind=approval），经事件层发 question.asked；ApprovalRegistry 退役；两个
   dispatcher 批准测试迁到统一 registry + 事件总线契约。
4. ✅ **approval mode**（27c05faa）：`modes/approval/`，question 的衍生——
   危险摘要（工具名+参数）+ 允许/拒绝 + 拒绝附理由（理由变工具错误文本）。
   真子进程端到端验证（含子进程审批桥）。
5. ✅ **冲突排队 + 超时收回**（c0c8956e）：FIFO 队列一次一个；用户主动 fn-form
   撞系统决定则取消；超时经 transport 广播 question.rejected 收回卡片
   （修了"超时卡片挂死"的真 bug）。浏览器验证抢占。

每步都能在浏览器自验、独立提交。

## 决策（已定，2026-06-13）

* **同屏冲突**：FIFO 队列；系统决定排队、一次一个；用户主动开的 fn-form 撞上
  系统决定直接取消（见上）。
* **approval 危险摘要**：显示命令/参数全文，超长截断（首尾保留）；不做危险
  token 高亮（第一版从简）。
* **拒绝并附理由**：approval 的 secondary 允许附文本理由，理由变成工具错误
  文本回给模型（opencode 做法）。
* **timeout**：mode 占输入区等用户，超时按 declined 收尾、自动退出 mode 并在
  输入区给一行提示（"超时未响应"）。

## 关联

* user-input registry / runtime.ask：[../runtime/user-input-requests.md](../runtime/user-input-requests.md)
* 事件层（统一事件流，这是它在前端的对齐落点）：
  [../proactive/event-reference.html](../proactive/event-reference.html)
