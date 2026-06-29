# Agent 协作：一个分支间通信原语

整套 agent 协作收敛成**一个原语：分支间通信**。一个 agent 能派生别的 agent、能给
别的分支/别的 session 发消息、能把几条分支的内容汇给一个模型综合——这些表面上不同
的操作，**底层是同一件事**：往某条分支投递内容 → 触发那条分支跑一轮 → 结果自动回送
发起方。全部做成工具调用，全部建在已有的事件层上。

> 范围：本文是设计，不含代码。落地顺序见末节。

---

## 0. 核心：只有一个原语

整套协作只有一个原语：

> **分支间通信** = 往一条分支（同 session 另一分支 / 跨 session / 当场新建的 /
> 已存在的）**投递内容** → **触发**那条分支跑一轮（模型读到投来的内容）→ 结果
> **自动回送**发起方（追加一条新消息 + 触发发起方跑一轮，发起方醒来读到、继续）。

所有协作操作都是这个原语的**参数化**：

| 操作 | 是通信的哪种用法 |
|---|---|
| **派生子 agent** | **新建**一条分支 + 投消息 + 自动回 |
| **发消息给某分支** | 往**已存在**分支投消息 + 自动回 |
| **综合多条分支** | 投递时**带多个来源**分支的内容，让目标模型综合 |

投来的内容一定被目标模型读取并使用。数量任意（派生能派 N 个、综合能合 N 条、发消息
也能群发），不是区分维度。三种用法共用一条投递→触发→回送的路径。

`attach` 不是操作，是通信结果在 DAG 上的"回流连线"画法（标记结果从哪条分支回来）。

---

## 1. 名词对齐（沿用现有抽象，不发明）

| 概念 | 定义 | 来源 |
|---|---|---|
| **session** | 一个独立会话，有 `session_id`，对应一个 git 仓库 | `SessionStore` |
| **branch（分支）** | `(session_id, head_id)` 对。同 session 不同 head = 同会话两条分支；不同 session = 跨会话 | `merge.py` 已确立，同/跨 session 走同一路径 |
| **投递** | 往某分支追加一条消息节点 | `append_message`（任意 session_id，无权限限制） |
| **触发** | 让某分支跑一轮 agent | `process_user_turn(TurnRequest(...))` |
| **自动回送** | 目标答完，把回复作为新输入喂回发起方 + 触发它跑 | `TaskRunner._dispatch_followup`（已存在） |
| **attach 连线** | DAG 上标记"结果从哪条分支回流来"的指针节点（只画图） | `write_attach_pointer_for_spawn` |

DAG 画法已在 `dag/dag-live.html` 定稿（分支间通信场景：异步、send 瞬间返回、回复异步
回送、通信点线 hover 显示；派生=子分支服务场景；回流=软连接线）。

---

## 2. 原语的工具形态

把原语包成 agent 能调的工具。**一个核心工具 + 两个列举工具**。

### 2.1 `message_branch` — 分支间通信（核心，唯一的协作原语）

```
message_branch(
    message: str,                       # 投给目标的内容/指令
    target: str = "new",                # 见下方 target 取值
    sources: list[str] = [],            # 额外带上这些分支的内容一起投（综合多条时用）
    agent_id: str = "main",             # 目标用哪个 agent
    wait: bool = false,                 # false=异步(默认,瞬间返回)；true=同步等回复
) -> str
```

**`target` 取值——创建分支和发消息是同一参数的不同取值：**

| target | 含义 |
|---|---|
| `"new"` | 从 ROOT 全新创建一条分支（新 session），投 message 让它跑 |
| `"new:sid:msg_id"` | 从某节点 fork 出一条新分支，投 message 让它跑 |
| `"sid:head"` | 往一条已存在分支投 message |

**创建分支不是独立操作，就是 `target` 取 `new` / `new:…`**。三种用法：

- **创建并跑（派生 / 开新会话 / fork）**：`target="new"` 或 `"new:sid:msg_id"` →
  新建分支 + 投 message，它跑完自动回流。（想建几条，就调几次，各自异步并行。）
- **发消息给已有分支/session**：`target="sid:head"` → 往那条分支投 message，触发它
  跑一轮，答完自动回送。跨 session 同一路径（target 是任意 session）。
- **综合多条分支**：`sources=["s1:h1","s2:h2",...]` → 投递时把这几条分支的内容一起
  带上，目标模型读完综合。数量任意。可与任意 target 组合。

**统一执行流程**（无论哪种用法）：
1. 解析 `target`：`new` → 新建 session + 空 `branch_from`；`new:sid:msg_id` →
   在 sid 里 `branch_from=msg_id` fork；`sid:head` → `set_head` 切到该分支。
2. 组装投递内容：`message` +（若有 `sources`）把每条来源分支的内容附上。
3. 投递 + 触发：`process_user_turn(TurnRequest(session_id=目标, user_text=投递内容,
   branch_from=fork 起点))` → 目标分支跑一轮，**模型读到投来的全部内容**。
4. **回送**：
   - `wait=false`（默认）：瞬间返回"已投递 + delivery_id"，发起方不阻塞继续；目标
     答完，`_dispatch_followup` **自动**把回复作为新消息喂回发起方 session + 触发它
     跑一轮，发起方醒来读到。
   - `wait=true`：阻塞等目标答完，直接返回回复文本。
- 事件：投递 emit `branch.message_sent`；回送 emit `branch.message_replied`（见 §3）。

### 2.2 多条来源喂多少内容（综合的关键）

`sources` 里每条分支怎么喂给目标模型——**先让每条分支自我总结，再汇集总结**：

1. 对每条 source 分支，先让它产出一个**面向本次通信的总结**（"把你这条分支的结论
   浓缩成要点"），复用 `branch_summarization`。
2. 把这些总结拼成 `<branch label="...">总结</branch>` 块，连同 `message` 一起投给
   目标模型综合。

这样不爆 context、能带很多条、交给模型的是浓缩要点而非原始长对话。统一走"自我总结"，
不设"喂全文/喂摘要"的参数选择。

### 2.3 `list_sessions` / `list_branches` — 看见对方（通信的前提）

```
list_sessions(limit=50, agent_id?, source?) -> str      # db.list_sessions
list_branches(session_id?) -> str                        # db.list_branches
```

通信前要能指定 target/sources，所以得先列出有哪些 session、每个有哪些分支
（`(session_id, head_id)` + name）。这是"两个 agent 互相看见"的入口。数据层 + WS
handler 已有（`handle_list_sessions` / `handle_list_branches`），只缺包成工具。

---

## 3. 底座：事件层（整个设计，自包含）

通信原语建在事件层上。这里把事件层完整写清——它是框架级的统一事件流，**已落地**
（`openprogram/agent/event_bus.py` + `tool_gate.py` + `event_bridges.py`），通信只是
它的又一组源 + 消费者。

### 3.1 为什么有事件层

框架里"某件事发生了"的信号分散在多套机制里（agent loop 的 AgentEvent、auth 的
`_emit`、context 的 on_event、channels 的 WS 广播、memory 的 poll、store 的日志）。
事件层把它们统一成**一条总线：源往里 emit，消费者从里 subscribe，源和消费者互不
认识**——想"在某时机做某事"，订阅对应类型即可。

### 3.2 Event 模型

核心三样（是什么事 + 内容 + 时间）固定；关联信息放进开放的 metadata 口袋，不写死。

```python
@dataclass(frozen=True)
class Event:
    id: str          # 唯一编号
    ts: float        # 发生时间
    type: str        # 是什么事（见 §3.4）
    origin: str      # 谁引起的：user / agent / tool / system / proactive
    payload: dict    # 这件事的内容（命令、文件路径、哪条分支收到消息……）
    metadata: dict   # 开放口袋：{"session":..., "turn":..., "lane":...}，需要才塞
```

session/turn/lane 进口袋不做固定字段：它们是外加关联、对一半事件（auth/channel）没
意义；开放 dict 让以后加关联维度不改模型。`make_event(type, origin, payload, metadata)`
会自动从 ContextVar 填上当前 session/turn。

### 3.3 进程级单例总线

所有组件（webui、agent loop、channels、memory、auth、task runner、通信工具）都在
**同一个 worker 进程**里（各是 daemon 线程），所以总线是**进程级单例** `get_event_bus()`。
同进程所有线程拿同一实例，直接 emit/subscribe，不跨进程桥接。

```python
bus.emit(event)                              # 广播，fire-and-forget，不阻塞调用方
bus.subscribe(handler, types={...})          # 按类型订阅，返回 unsubscribe
emit_safe(type, origin, payload, metadata)   # 源用：构建+emit，吞掉一切异常
emit_ws_frame(frame)                         # 源用：把现成 WS 帧经总线送前端（解耦 webui）
```

### 3.4 两类事件源 + 现有全部事件类型

| | A 类：agent 活动（带 turn） | B 类：系统状态（可能没 agent 在跑） |
|---|---|---|
| 例子 | 用户消息、模型回复、工具前后、文件改、turn 结束、子任务起止 | 凭据限流、上下文溢出、外部消息进、技能变 |

**已落地的事件类型**（第一版，✅=在发）：

| 类 | type | 何时 | 来源 |
|---|---|---|---|
| A | `user.prompt_submitted` | 用户发消息 | dispatcher ✅ |
| A | `model.response_started`/`.completed` | 模型开始/说完 | agent_loop ✅ |
| A | `tool.before` | 工具即将执行（**可拦截**，见 §3.5） | agent_loop ✅ |
| A | `tool.after` | 工具执行完 | agent_loop ✅ |
| A | `file.changed` | 文件被改 | write/edit/apply_patch ✅ |
| A | `turn.ended` | 一轮结束 | agent_loop ✅ |
| A | `subagent.started`/`.ended` | 子任务起止 | TaskRunner ✅ |
| B | `credential.cooldown`/`.exhausted`/`.rotated` | 凭据限流/耗尽/轮换 | event_bridges←AuthStore ✅ |
| B | `context.compaction_recommended`/`.compacted` | 上下文到阈值/已压缩 | context/engine ✅ |
| B | `channel.message_inbound` | 外部消息进 | channels ✅ |
| B | `memory.ingest_started`/`.ended` | wiki ingest 起止 | memory watcher ✅ |
| B | `skills.changed`/`plugins.update_available` | 技能改/插件新版 | webui watcher ✅ |

**本设计新增的通信事件**（A 类）：

| type | 何时 | origin | payload 关键字段 |
|---|---|---|---|
| `branch.message_sent` | message_branch 投递 | agent | from, to, sources, delivery_id, is_new, chain |
| `branch.message_replied` | 目标答完自动回送 | agent | from, to, delivery_id, is_error |
| `branch.created` / `.started` / `.failed` / `.cancelled` | 分支状态转换 | agent | branch, parent, agent_id, status |
| `sessions.listed` / `branches.listed` | 列举 | agent | count |

`chain`（派生链）走 metadata，用于深度防循环（§5.1）；状态事件支持进度监听/审计/排查。

通信复用已有 `subagent.started`/`.ended`（派生用法时 TaskRunner 照发）。

### 3.5 两种交互：观察 vs 拦截

- **观察型（默认，异步）**：emit 出去，订阅者异步收到，源不等。绝大多数事件走这条。
- **拦截型（仅 `tool.before`，同步）**：工具执行前能让下游说"别执行"。单一入口
  `_execute_tool_calls` 在 `tool.execute()` 前有同步问询点（`tool_gate.py`
  `register_tool_gate`）。必须快（不许调 LLM）；多方表态取最严；对 subagent 也生效
  （在 approval 包装外，`permission_mode=bypass` 关不掉它）。**通信工具
  `message_branch` 走它做值守拦截**（见 §5）。

### 3.6 通信怎么用事件层

- 每个通信动作 `emit_safe(...)`（投递、回送、列举）—— proactive / 审计 / 前端刷新
  都是这条流的订阅者，互不耦合。
- **前端通知统一走 `emit_ws_frame(frame)`**：跨 session 时目标 session 的前端经总线
  收到 `ws.frame` 事件、webui 订阅后原样广播，两边前端实时看到"收到来自 X 的消息"
  "X 回复了"。前端零改协议、通信工具不认识 webui。
- **值守拦截走 `tool.before` 同步点**：投递是副作用，无人值守 + deny 时拦下要求确认。

### 3.7 一条原则

**不是所有调用都是事件，只有"有消费者想响应"的时机才是。** 上表是精挑的。通信新增的
几个事件都是真有人响应（前端渲染、proactive、审计）才加。演进只加不改：加事件类型、
给 payload 加字段零风险（老订阅者只读自己关心的）。

---

## 4. 端到端：两个 agent 互相看见 + 通信

A、B 同时在跑（同 session 不同分支，或不同 session）：

1. **看见**：A 调 `list_sessions` → 看到 B；`list_branches` → 看到 B 的活跃分支
   `(B_session, B_head)`。
2. **发**：A 调 `message_branch("...", target="B_session:B_head")` → 瞬间返回，
   A 继续。
3. **B 收到**：消息进 B 分支（B 那边一个 △"收到 A 的消息"），B 跑一轮答它（△）。
   两边前端经 ws.frame 实时看到。
4. **回送 A**：B 答完，`_dispatch_followup` 自动把回复追加到 A 末尾（△）+ 触发 A
   跑一轮，A 醒来读到、可继续。
5. **可循环**：A 再 `message_branch` 给 B……两条分支各自不阻塞、不串行。

派生（target="new"）和综合（带 sources）是同一流程的另两种参数，不另列。

---

## 5. 健壮性与安全

通信会创建分支、触发别的分支跑、跨 session 写——这些副作用必须有边界。

### 5.1 递归派生 + 死循环防护

**允许递归派生**（子分支能再 `message_branch` 派子分支，做多层任务分解），靠以下防爆：

- **深度上限**：每次投递在 Event metadata 里带一条**派生链**（`chain: [发起分支, …,
  当前]`）。`message_branch` 执行时若链长 ≥ `MAX_DEPTH`（默认 8），拒绝并把理由回给
  模型。回送（自动 followup）继承同一条链，不重置——所以 A↔B 互发的来回也算进深度，
  到顶自动停。
- **自发拒绝**：target 指向**发起分支自己**（直接环）立即拒绝。
- 链信息只在 metadata 流转，不进模型可见内容。

### 5.2 并发上限 + 排队

- 派生走 `TaskRunner` 线程池，上限 `OPENPROGRAM_TASK_WORKERS`（默认 4）。一次派几十
  个：超出上限的**排队**，槽位空出再跑，不打爆。
- 可选 **token 预算**：一次协作的总派生数 / 总 token 设上限，到顶拒绝新派生（防一个
  失控分解派出几百个）。文档默认不强制，留参数。

### 5.3 取消传播（级联）

- 取消一个分支时，**它派出的所有子分支也被中断**：维护"活跃子分支"列表（线程锁保护），
  取消时遍历 `child.interrupt`，复用 `TaskRunner` 现有 cancel + `kill_active_runtime`。
- 子分支优雅关闭（cleanup），不留僵尸线程/子进程。

### 5.4 发给"正在跑"的分支（竞态）

A 给 B 发消息时 B 可能正跑一轮。**不打断、不丢弃——排队**：消息追加到 B 分支后，等 B
当前这轮结束再触发处理它（OpenCode 的 pendingWake 思路）。B 空闲则立即触发。

### 5.5 失败回送

子/目标分支失败（崩溃 / 超时 / 模型报错）：**也回送**，回送内容带 `is_error` + 原因
（"B 失败了：<原因>"），发起方模型读到后自行决定重发/换路/放弃。**不内置重试/熔断**
——父是模型，由它判断比固定策略好。

### 5.6 结果截断

回送内容超过 `max_result_chars`（复用 `@function` 的 30k 默认）就**截断头尾 + 存完整
文件**，回送里给文件路径。巨量中间结果不撑爆发起方 context、不阻塞主流程。

### 5.7 子分支的身份 / 最小权限

- `agent_id` 指定子分支用哪个 agent（不同 agent = 不同 system + 工具集 + 模型）。
- model 支持 `inherit`（继承发起方模型），也可显式指定更弱的。
- **默认子分支权限不高于发起方**（最小权限）；危险工具（删文件等）仍走 §5.8 拦截，
  `permission_mode=bypass` 关不掉拦截点。
- 子分支**只看到投递消息及其后的响应**，不继承发起方完整历史（省 context + 隔离）。

### 5.8 值守拦截 + 校验

- `message_branch` 走事件层 `tool.before` 同步问询点：无人值守 + deny 策略时拦下要求
  确认（对子分支也生效，在 approval 包装外）。
- 投递前校验 `target`（非 "new"）真实存在（`db.get_session` 非 None），不存在报错、
  不静默新建。沿用三层门控（check_fn / can_use / requires_approval）。

### 5.9 分支可见性

分支标记 **内部（子派生）vs 用户可见**：内部分支只能被 `message_branch` 触发，不进
UI 的会话选择列表（但 DAG 照画、能被 list_branches 列出供 agent 寻址）。

### 5.10 明确不做（及理由）

- **parentID 额外字段**：`(session_id, head_id)` + caller/predecessor 已构成树，DAG
  已画，不再加冗余字段。
- **ID 前缀分类**（fork_/msg_）：现有 id + name 足够寻址，不加。
- **重试 / 熔断策略**：失败回送给模型，由模型决定，不内置固定策略（见 §5.5）。
- **内置聚合函数**（投票 / 全部成功等）：综合就是把 sources 喂给模型让它综合（§2.2），
  模型综合比预设聚合灵活，不做固定聚合算子。

---

## 6. 前后端清单

**后端（工具，`openprogram/functions/tools/agent_collab/`）**
- `message_branch.py` — 唯一核心：投递 + 触发 + 自动回送 + 多源自我总结
- `list_sessions.py` / `list_branches.py` — 复用 db.list_*
- 各工具 `emit_safe(...)`；跨 session 通知用 `emit_ws_frame`

**后端（已有，直接复用，不重写）**
- `TaskRunner`（线程池并发、await、cancel、_dispatch_followup 自动回送、attach 连线）
- `SessionStore`（list/append/set_head/commit/get）
- `dispatcher.process_user_turn`
- `branch_summarization`（多源自我总结）
- `event_bus`（emit_safe / emit_ws_frame）

**前端（`web/`）**
- session / branch 列表面板（已有 WS handler）+ "选 target → 发消息"交互入口
- 收到 `branch.message_sent` / `branch.message_replied`（经 ws.frame）→ 在对应
  session 的 DAG / 消息流渲染通信节点 + 回流软连接线（hover 显示，dag-live 已定稿）
- 派生进度复用现有 `task_status` 帧 + tasks 面板

---

## 7. 落地顺序（每步独立可验证）

| 步 | 做什么 | 验证 |
|---|---|---|
| **C1** | `message_branch` 核心：target="new"（派生）→ 投递 + 触发 + 自动回送 | agent 调一次，新建分支跑一轮，结果自动 followup 回发起方；事件 message_sent/replied 在事件日志可见 |
| **C2** | `list_sessions` / `list_branches` 工具 | agent 列出真实多 session / 多分支 |
| **C3** | `message_branch` target=已存在分支（同 session） | A 发给同 session 的 B 分支，A 不阻塞，B 跑一轮，回复自动回 A |
| **C4** | 跨 session：target=别的 session | A 发给别的 session，同一路径跑通；两边前端经 ws.frame 实时更新 |
| **C5** | `sources` 多源综合（自我总结 → 汇集 → 综合） | 带 2 条 source 分支，每条先自我总结，目标模型综合出新回答 |
| **C6** | 健壮性（§5）：深度上限 + 自发拒绝、并发排队、取消级联、发给忙碌分支排队、失败回送、结果截断 | A↔B 互发到 MAX_DEPTH 自动停；派 30 个排队不炸；取消父→子全停；给正跑的 B 发消息排队等它结束；子崩溃父收到 is_error；超大结果截断给文件路径 |
| **C7** | 值守拦截 + target 校验 + 最小权限 + 分支可见性 | deny 下被 tool.before 拦；不存在 target 报错；子分支权限不高于父、不进 UI 选择列表 |
| **C8** | 前端交互：列表选 target → 发消息 + 通信节点渲染 | webui 里选分支发消息，DAG 出现通信节点 + hover 软连接线 |

C1 核心原语（派生）；C3/C4 推广到已有分支/跨 session；C5 综合；**C6 健壮性（防爆，最该
先有）**；C7 安全；C8 前端。每步在 webui（`cd web && npm run build` + `openprogram
worker restart`）或事件日志（`OPENPROGRAM_EVENT_LOG=1`）验证。

---

## 8. 关键文件速查（落地时碰这些）

| 事 | 位置 |
|---|---|
| 子 agent 派生 + 自动回送 | `openprogram/agent/sub_agent_run.py`、`agent/task/runner.py`（spawn_task / _dispatch_followup） |
| 工具范本 + 注册 | `openprogram/functions/tools/task/task.py`、`functions/_runtime.py`（@function） |
| session/branch 数据层 | `openprogram/store/session/session_store.py`（list_sessions:658 / list_branches:832 / append_message:706 / set_head:814 / commit_turn:455） |
| 触发某 session 跑一轮 | `openprogram/agent/dispatcher/__init__.py`（process_user_turn:97） |
| 多源自我总结 | `openprogram/agent/compaction/branch_summarization.py` |
| 列表 WS handler | `webui/ws_actions/session.py:825`、`branch.py:221` |
| attach 连线（仅画图） | `openprogram/agent/sub_agent_run.py`（write_attach_pointer_for_spawn） |
| 事件总线 | `openprogram/agent/event_bus.py`（emit_safe / emit_ws_frame） |

> 注："综合多条"由 `message_branch(sources=[...])` 提供，不另设独立工具。底层
> `_merge.py` 的多父 ContextCommit 血缘记录被复用来记下"这次综合来自哪几条分支"。
