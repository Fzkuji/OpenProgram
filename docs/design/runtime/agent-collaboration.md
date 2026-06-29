# Agent 协作：派生、列举、互发消息（跨 session）

一个 agent 能派生别的 agent（=新分支，想派几个派几个）、能列出系统里所有
session / branch、能给别的分支或别的 session 发消息；两个 agent 同时跑时能互相
看见、互相发消息。全部做成**工具调用**，全部建在**已有的事件层**上。

> 范围：本文是设计，不含代码。落地顺序见末节。

---

## 0. 一句话定位

**不是从零造，是补四个工具 + 一个广播能力。** 底层基础设施已经齐全：

- **派生子 agent**：`task` 工具 + `TaskRunner` 线程池已能异步派生、后台跑、完成
  回流。缺的只是"一次派 N 个"。
- **跨 session 读写**：`SessionStore` 的 `list_sessions / list_branches /
  append_message / set_head / commit_turn / process_user_turn /
  process_merge_turn` **全部接受任意 session_id，无权限限制**。缺的只是把它们
  封成 agent 能调的工具。
- **事件 + 前端通知**：`get_event_bus()` 单例总线 + `emit_safe(...)` +
  `emit_ws_frame(frame)`（外部源经总线把 WS 帧推前端）已就绪。新功能每个动作
  emit 事件，前端订阅。

所以本设计 = 复用 Task 子系统 + 复用 SessionStore + 在事件层上补四个工具和它们
的前端。

---

## 1. 名词对齐（沿用现有抽象，不发明）

| 概念 | 定义 | 来源 |
|---|---|---|
| **session** | 一个独立会话，有 `session_id`，对应一个 git 仓库 | `SessionStore` |
| **branch（分支）** | `(session_id, head_id)` 对。同 session 不同 head = 同会话的两条分支；不同 session = 跨会话 | `merge.py` 已确立，同/跨 session 走同一路径 |
| **派生子 agent** | 在某 session 里 fork 一条新分支跑一轮 agent | `run_agent_turn(session_id, prompt, branch_from=...)` |
| **attach 回流** | 子分支跑完，结果作为 `function=attach` 指针嵌回父序列 | `write_attach_pointer_for_spawn` |

DAG 画法已在 `dag/dag-live.html` 定稿：派生 = 子分支服务（spawn 点划线，从 spawn
往下长，attach 回流软连接）；互发消息 = 分支间通信（异步，send 瞬间返回，回复异步
送回，通信点线 hover 显示）。本文只管后端 + 工具 + 前端列表/交互，画法不再赘述。

---

## 2. 四个工具（agent 可调用）

全部用 `@function(name=..., description=..., toolset=["core"])` 注册，和 `task`
工具同款。每个工具执行时 `emit_safe(...)` 一个事件。

### 2.1 `spawn_agents` — 一次派生 N 个子 agent

```
spawn_agents(
    tasks: list[{prompt: str, label?: str, agent_id?: str, context?: "clean"|"inherit"}],
    wait: bool = false,     # false=全异步(默认), true=全部等完返回
) -> str
```

- 对 `tasks` 里每一项调一次现有的 `run_agent_turn_async`（异步）/
  `run_agent_turn`（同步），即每项 = 一条新分支。**想派几个派几个**（1 个、几十个）。
- `wait=false`（默认，对应你"派 agent + 还能继续聊"）：立刻返回每个的 `task_id`
  列表；各子 agent 在 `TaskRunner` 线程池后台跑（池上限 `OPENPROGRAM_TASK_WORKERS`，
  超出排队）；每个完成时各自 attach 回流 + 自动 followup 父 session。
- `wait=true`：全部跑完，返回每个的最终文本汇总。
- 事件：每派一个 emit `agent.spawned`；复用现有 `subagent.started/.ended`。
- 单个 `task` 工具保留（一次派一个的快捷方式），`spawn_agents` 是它的批量版。
- **并发**：N 个子 agent 真并行（线程池），这就是"两个/多个 agent 同时做事"的来源。

### 2.2 `list_sessions` — 列出所有 session（跨 session 可见的基础）

```
list_sessions(limit: int = 50, agent_id?: str, source?: str) -> str
```

- 直接调 `db.list_sessions(...)`，返回每个 session 的 `id / title / agent_id /
  updated_at / head_id`，格式化成 agent 易读的列表（id + 标题 + 活跃时间）。
- 这是"agent 互相看见对方"的入口：A 想找 B，先 list 看有哪些 session/agent。
- 事件：emit `sessions.listed`（轻量，主要给审计/前端刷新用）。

### 2.3 `list_branches` — 列出某 session 的所有分支

```
list_branches(session_id?: str) -> str
```

- 调 `db.list_branches(session_id)`（默认当前 session），返回每条分支的
  `head_id / name / created_at / updated_at`，标出活跃分支。
- 配合 `list_sessions`：先列 session，再列某 session 的 branch，定位到要发消息的
  那条 `(session_id, head_id)`。
- 事件：emit `branches.listed`。

### 2.4 `send_to_branch` — 给一条分支/一个 session 发消息（异步）

```
send_to_branch(
    target_session_id: str,
    message: str,
    target_head_id?: str,      # 指定分支尖；省略=该 session 活跃分支
    agent_id: str = "main",
    wait: bool = false,        # false=投递即返回(默认), true=等对方答完拿回复
) -> str
```

这是核心，**异步语义和 DAG 通信场景一致**：

1. **投递**：往 `target_session_id` 追加一条消息节点（`append_message`），标记
   来源是另一个 agent（DAG 里画成 △，不是真人 ○）。`target_head_id` 给定就先
   `set_head` 切到那条分支再追加。
2. **触发对方跑**：`process_user_turn(TurnRequest(session_id=target, agent_id,
   user_text=message, branch_from=target_head_id))` —— 让目标分支跑一轮。
3. **瞬间返回（`wait=false`，默认）**：发起方**不阻塞**，立刻拿到"已投递"的
   确认 + 一个 `delivery_id`，继续干自己的事。目标 agent 在自己节奏处理。
   答完后，**结果异步送回**发起方 session 末尾（追加一条"来自 B 的回复"节点，
   △），发起方下一轮自然看到。
4. **同步（`wait=true`）**：阻塞等目标答完，直接返回回复文本（像 Claude Code 的
   同步 Task；仅当发起方明确要等结果时用，注意可能阻塞/死锁，默认不用）。
- **跨 session 天然支持**：`target_session_id` 是任意 session id，数据层无限制。
  同 session 另一分支、别的 session，同一个工具、同一条路径。
- 事件：投递 emit `message.sent`（payload: from_session, to_session, to_head,
  delivery_id）；对方答完回送 emit `message.replied`。两者都经 `emit_ws_frame`
  让两边前端实时更新。

> `send_to_branch` 和 `task`/`spawn_agents` 的区别：后者是**新建**子分支（派活），
> 前者是**给已存在**的分支/ session 发消息（通信）。DAG 里前者画"分支间通信"
> （平行已存在的 B），后者画"子分支服务"（spawn 当场新建的 B）。

---

## 3. 全部建在事件层上

每个工具动作 → 一条 `Event`（type/origin/payload/metadata，metadata 自动带
session/turn）。事件类型（新增）：

| type | 何时 | origin | payload 关键字段 |
|---|---|---|---|
| `agent.spawned` | spawn_agents 派出一个子 agent | agent | task_id, label, target_session |
| `sessions.listed` | 调 list_sessions | agent | count |
| `branches.listed` | 调 list_branches | agent | session, count |
| `message.sent` | send_to_branch 投递 | agent | from_session, to_session, to_head, delivery_id |
| `message.replied` | 目标答完异步回送 | agent | from_session, to_session, delivery_id |

复用已有：`subagent.started` / `subagent.ended`（Task runner 已 emit）。

**前端通知统一走 `emit_ws_frame(frame)`**：跨 session 时，目标 session 的前端
可能没订阅发起方——经总线 emit 一个 `ws.frame` 事件，webui 订阅后原样广播，两边
前端都能收到"你收到一条来自 X 的消息""X 回复了"。前端零改动协议、外部源不认识
webui（沿用事件层步 4 的解耦）。

这样：**proactive / 审计 / 前端刷新 全是这条流的订阅者**，互不耦合。

---

## 4. 互相看见 + 互发消息（端到端）

两个 agent A、B 同时在跑（可能同 session 不同分支，也可能不同 session）：

1. **看见**：A 调 `list_sessions` → 看到 B 的 session；调 `list_branches` →
   看到 B 的活跃分支 `(B_session, B_head)`。
2. **发消息**：A 调 `send_to_branch(B_session, "...", target_head_id=B_head)`
   → 瞬间返回，A 继续。
3. **B 收到**：消息进 B 分支（B 那边一个 △"收到 A 的消息"），B 跑一轮答它
   （△"B 应答"）。两边前端经 `ws.frame` 实时看到。
4. **回送 A**：B 答完，结果异步追加到 A 末尾（△"A 收到 B 的回复"），A 下一轮
   看到并可继续回应。
5. **可循环**：A 再 `send_to_branch` 给 B……两条分支各自不阻塞、不串行。

这就是 DAG"分支间通信"场景的真实后端。

---

## 5. 安全 / 值守（接事件层的拦截点）

跨 session 写是副作用（往别人的会话写、触发别人跑），必须可拦：

- 事件层已有 `tool.before` 同步问询点（值守模式拦工具）。`send_to_branch` /
  `spawn_agents` 走它：无人值守 + deny 策略时可拦下，要求确认。
- `send_to_branch` 投递前校验 `target_session_id` 真实存在（`db.get_session`
  非 None），不存在则报错，不静默新建。
- 不在工具里做权限放行；沿用三层门控（check_fn / can_use / requires_approval）。

---

## 6. 前后端清单

**后端（工具，`openprogram/functions/tools/`）**
- `agent_collab/spawn_agents.py` — 批量派生（复用 run_agent_turn_async）
- `agent_collab/list_sessions.py`、`list_branches.py` — 复用 db.list_*
- `agent_collab/send_to_branch.py` — append_message + process_user_turn + 异步回送
- 各工具 `emit_safe(...)` 对应事件；跨 session 通知用 `emit_ws_frame`

**后端（已有，直接用，不重写）**
- `TaskRunner`（线程池、并发、await、cancel、attach 回流、followup）
- `SessionStore`（list/append/set_head/commit/get）
- `dispatcher.process_user_turn`、`_merge.process_merge_turn`
- `event_bus`（总线 + emit_safe + emit_ws_frame）

**前端（`web/`）**
- session 列表 / branch 列表面板：已有 WS `handle_list_sessions` /
  `handle_list_branches` 返回 `sessions_list` / `branches_list` 帧——前端加一个
  "选择 session/分支 → 发消息"的交互入口（复用现有侧栏列表）。
- 收到 `message.sent` / `message.replied`（经 ws.frame）→ 在对应 session 的
  DAG / 消息流里渲染通信节点 + 软连接线（hover 显示，已在 dag-live 定稿）。
- spawn / 多 agent 进度：复用现有 `task_status` 帧 + tasks 面板。

---

## 7. 落地顺序（每步独立可验证）

| 步 | 做什么 | 验证 |
|---|---|---|
| **C1** | `spawn_agents` 工具（批量包 run_agent_turn_async） | agent 一次派 3 个子任务，3 个 task_id 返回、3 个后台并行跑、各自 attach 回流 |
| **C2** | `list_sessions` / `list_branches` 工具 | agent 调用，列出真实的多 session / 多分支 |
| **C3** | `send_to_branch`（异步投递 + 触发 + 回送），同 session 两分支 | A 发给 B 分支，A 不阻塞；B 跑一轮；回复异步回到 A 末尾。事件 message.sent/replied 在事件日志可见 |
| **C4** | `send_to_branch` 跨 session | A 的 session 发给 B 的另一个 session，同一条路径跑通；两边前端经 ws.frame 实时更新 |
| **C5** | 值守拦截 + target 存在性校验 | deny 策略下 send_to_branch 被 tool.before 拦下要求确认；不存在的 target 报错不新建 |
| **C6** | 前端交互：列表选择 → 发消息 + 通信节点渲染 | webui 里选一个 session/分支发消息，DAG 出现通信节点 + hover 软连接线 |

C1–C2 是地基（派生 + 看见）；C3 是核心（异步互发 + 回送）；C4 把它推广到跨
session；C5 补安全；C6 接前端。每步都能在 webui（`cd web && npm run build` +
`openprogram worker restart`）或事件日志（`OPENPROGRAM_EVENT_LOG=1`）验证。

---

## 8. 关键文件速查（落地时碰这些）

| 事 | 位置 |
|---|---|
| 子 agent 同步/异步派生 | `openprogram/agent/sub_agent_run.py`（run_agent_turn / _async / write_attach_pointer_for_spawn） |
| Task 工具范本 + 注册 | `openprogram/functions/tools/task/task.py`、`functions/_runtime.py`（@function / _build_and_register_tool） |
| 线程池 / 并发 / await / cancel | `openprogram/agent/task/runner.py`（TaskRunner.spawn_task / await_task / _run_one） |
| session/branch 数据层 | `openprogram/store/session/session_store.py`（list_sessions:658 / list_branches:832 / append_message:706 / set_head:814 / commit_turn:455） |
| 触发某 session 跑一轮 | `openprogram/agent/dispatcher/__init__.py`（process_user_turn:97） |
| 跨 session 合并范本 | `openprogram/agent/internals/_merge.py`（process_merge_turn）、`webui/ws_actions/merge.py`（分支抽象定义） |
| 列表 WS handler | `webui/ws_actions/session.py:825`（list_sessions）、`branch.py:221`（list_branches） |
| 事件总线 | `openprogram/agent/event_bus.py`（emit_safe / emit_ws_frame / make_event / Event） |
| Task 状态广播 + 事件 tap | `webui/ws_actions/task.py`（_broadcast_task_status → emit_safe subagent.*） |
