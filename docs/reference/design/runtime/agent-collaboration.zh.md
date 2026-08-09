# Agent 协作：一个分支间通信原语

整套 agent 协作收敛成**一个原语：分支间通信**。一个 agent 能派生别的 agent、能给
别的分支/别的 session 发消息、能把几条分支的内容汇给一个模型综合——这些表面上不同
的操作，**底层是同一件事**：往某条分支投递内容 → 触发那条分支跑一轮 → 结果自动回送
发起方。全部做成工具调用，全部建在已有的事件层上。

> 范围：本文是设计，不含代码。实现状态见末节。

---

## 0. 核心：只有一个原语

整套协作只有一个原语：

> **分支间通信** = 往一条分支（同 session 另一分支 / 跨 session / 当场新建的 /
> 已存在的）**投递内容** → **触发**那条分支跑一轮（模型读到投来的内容）→ 结果
> **自动回送**发起方（追加一条新消息 + 触发发起方跑一轮，发起方醒来读到、继续）。

所有协作操作都是这个原语的**参数化**：

| 操作 | 是通信的哪种用法 | 工具 |
|---|---|---|
| **派生子 agent** | **新建**一条分支 + 投消息 + 自动回 | `agent` |
| **发消息给某 agent** | 往**已存在**分支投消息 + 自动回 | `send_message` |
| **综合多条分支** | 投递时**带多个来源**分支的内容，让目标模型综合 | `send_message(sources=…)` |

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

把原语包成 agent 能调的工具。分工对齐 Claude Code：**`agent` 生、
`send_message` 聊、`list_agents` 看**。

### 2.1 工具

**`agent` — 派生新 agent（唯一会创建分支的工具）：**

```
agent(
    prompt: str,                        # 给被派生 agent 的指令
    description: str = "",              # 简短 label，成为分支名
    agent_id: str = "",                 # agent 档案；默认用本会话的
    context: str = "clean",             # "clean" / "inherit" / "SID:MSG_ID"
    wait: bool = true,                  # true=阻塞等回复；false=返回 task_id
) -> str
```

`context` 决定新分支从哪起：`"clean"`（默认）新根、只见 prompt；
`"inherit"` 从当前轮 fork、带全链；`"SID:MSG_ID"` 从那个节点（任意
session）fork、继承到该节点为止的链。`wait=False` 返回 `task_id`，配套
`task_output(task_id)`（阻塞取结果）和 `task_stop(task_id)`（取消）管理
异步形态。

**`send_message` — 和已存在的 agent 通信：**

```
send_message(
    message: str,                       # 投给目标的内容/指令
    to: str,                            # 见下方 to 取值
    sources: list[str] = [],            # 额外带上这些分支的内容一起投（综合多条时用）
    agent_id: str = "main",             # 目标用哪个 agent
    wait: bool = false,                 # false=异步(默认,瞬间返回)；true=同步等回复
) -> str
```

**`to` 取值——每个取值都指认一条已存在的分支：**

| to | 含义 |
|---|---|
| `"sid:head"` | 往一条已存在分支投 message。节点指认的是分支，不是 fork 点：投递永远落在该分支的当前末端，旧 head（分支后来又跑过 turn）仍是有效地址，不会从历史节点岔出新分支。节点若是多条分支的公共祖先则报歧义，错误里列出候选（名字 + `sid:当前末端`）。要从指定节点 fork 用 `agent(context="sid:msg_id")`。 |
| `"<分支名>"` | 按名投递。不是 `SID:HEAD` 语法时按名字解析：精确匹配优先，唯一前缀次之；多个命中返回错误并列出候选（名字 + `sid:head`），零命中提示用 `list_agents`。`list_agents` 输出里标出每条分支的名字，模型可以直接按名寻址。 |

已删除的 spawn 寻址（`to="new"` / `"new:sid:msg_id"`）直接报错，并指向
`agent` 工具。

每次投递（直投或 §5.4 的排队消费）都会加一个发件人回执头：
`[message from SID:HEAD] To reply, use send_message(to="SID:HEAD"). Replying is
optional …` —— 收件方由此知道谁发的、怎么回、以及不回也是正当的。agent
工具的派生投的是裸 prompt：它们是干活的 worker，不是通信对象。

两种用法：

- **发消息给已有分支/session**：`to="sid:head"` → 往那条分支投 message，触发它
  跑一轮，答完自动回送。跨 session 同一路径（to 是任意 session）。
- **综合多条分支**：`sources=["s1:h1","s2:h2",...]` → 投递时把这几条分支的内容一起
  带上，目标模型读完综合。数量任意。可与任意 to 组合。

两个工具驱动同一个原语：派生就是同一条投递→触发→回送流程，只是目标分支
是当场新建的。

**统一执行流程**（无论哪个工具发起）：
1. 定目标分支：`agent` 当场新建（`context` 定 fork 点）；`send_message`
   把 `to` 解析到已存在分支的当前末端。
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

### 2.3 `list_agents` — 看见对方（通信的前提）

```
list_agents(limit=50, agent_id?, source?) -> str   # db.list_sessions + db.list_branches
```

agent 的对话就存在 session DAG 的分支里，所以"能跟谁说话"="有哪些
session、每个有哪些分支"。一次调用全列出，按 session 分组：session 行带
id、标题、agent、busy/idle 状态（`run_control.is_turn_running`，探测失败
就不标）；分支行带名字（若有）、现成的 `to="SID:HEAD"` 地址、末端预览。
这是"两个 agent 互相看见"的入口。

### 2.4 新建分支要有名字

`agent` 工具每次创建分支，**都必须给分支一个名字**
——否则 web 端只能显示 8 位 hex 短号，一堆分支分不清谁是谁。

- **立刻有名（Stage 1）**：创建时把一个简短 label 传给 `run_agent_turn(... label=…)`
  → `store.set_branch_name`。label 从投递的 prompt 摘一句（截断到 ~24 字），或让
  模型在调用时显式带一个名字（`description`）。这样分支一出生就有可读名，不用等 LLM。
- **后台自动改好名（Stage 2）**：分支正常聊起来后，由 `finalize_turn` 在 `turns`
  命中阈值 `{1,6,16,40}` 时，后台线程用 LLM 依据分支内容生成更贴切的标题，覆盖
  Stage 1 的临时名。规则见 [branch-naming](operations/branch-naming.zh.md)——那里定义
  了命名的分级、锁、触发点；本节只强调：**agent 工具派生的分支和用户手动
  fork 的分支，走同一套命名（都要 Stage 1 占位名 + Stage 2 自动改名），不能漏。**

### 2.5 回送节点落在哪：发起方当前尾部，串行成链

异步回送（`wait=false`）时，`_dispatch_followup` 把目标分支的回复作为一个
**synthetic user-role turn** 喂回投递 session。**关键规则：回送的 `TurnRequest`
不设 `branch_from`（INHERIT_PARENT），dispatcher 解析为投递 session 当前的
HEAD 并推进它。**每个投递 session 有一把回送串行锁
（`TaskRunner._followup_lock`），并发完成被串行化：N 个子任务跑完形成一条串行链
`… → notice₁ → answer₁ → notice₂ → answer₂`，每条回送读到的 HEAD 已包含上一条的回答。

为什么不把回送钉在发起节点（`caller_msg_id`）上：同一轮 fork 出 N 个并行子任务时，
每条回送都会作为同一节点的 sibling 落下，触发派生的那一条用户消息会在 N 条并行分支
上被回答 N 次。锚在 HEAD 让 N 次完成始终走同一条会话主线。

这样锚定不丢回流出处：派生时写下的 **attach 指针**仍然挂
`predecessor = caller_msg_id`，DAG 上照样能看出每条子分支从哪一轮 fork 出去、
每个结果从哪条分支回流。子分支本身是并列的独立一支，**不并回主线**。

---

## 3. 底座：事件层（整个设计，自包含）

通信原语建在事件层上。这里把事件层完整写清——它是框架级的统一事件流
（`openprogram/events/` 包：`bus.py` + `tool_gate.py` + `bridges.py`），通信只是
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

**框架现有的事件类型**：

| 类 | type | 何时 | 来源 |
|---|---|---|---|
| A | `user.prompt_submitted` | 用户发消息 | dispatcher |
| A | `model.response_started`/`.completed` | 模型开始/说完 | agent_loop |
| A | `tool.before` | 工具即将执行（**可拦截**，见 §3.5） | agent_loop |
| A | `tool.after` | 工具执行完 | agent_loop |
| A | `file.changed` | 文件被改 | write/edit/apply_patch |
| A | `subagent.started`/`.ended` | 子任务起止 | TaskRunner |
| B | `credential.cooldown`/`.exhausted`/`.rotated` | 凭据限流/耗尽/轮换 | events/bridges.py←AuthStore |
| B | `context.compaction_recommended`/`.compacted` | 上下文到阈值/已压缩 | context/engine |
| B | `channel.message_inbound` | 外部消息进 | channels |
| B | `memory.ingest_started`/`.ended` | wiki ingest 起止 | memory watcher |
| B | `skills.changed`/`plugins.update_available` | 技能改/插件新版 | webui watcher |

**通信引入的事件**（A 类）：

| type | 何时 | origin | payload 关键字段 |
|---|---|---|---|
| `branch.message_sent` | send_message 投递 | agent | from, to, sources, delivery_id, is_new, chain |
| `branch.message_replied` | 目标答完自动回送 | agent | from, to, delivery_id, is_error |
| `branch.created` / `.started` / `.failed` / `.cancelled` | 分支状态转换 | agent | branch, parent, agent_id, status |
| `sessions.listed` / `branches.listed` | 列举 | agent | count |

`chain`（派生链）走 metadata，用于深度防循环（§5.1）；状态事件支持进度监听/审计/排查。

通信复用已有 `subagent.started`/`.ended`（派生用法时 TaskRunner 照发）。

### 3.5 两种交互：观察 vs 拦截

- **观察型（默认，异步）**：emit 出去，订阅者异步收到，源不等。绝大多数事件走这条。
- **拦截型（仅 `tool.before`，同步）**：工具执行前能让下游说"别执行"。单一入口
  `_execute_tool_calls` 在 `tool.execute()` 前有同步问询点（`openprogram/events/tool_gate.py`
  `register_tool_gate`）。必须快（不许调 LLM）；多方表态取最严；对 subagent 也生效
  （在 approval 包装外，`permission_mode=bypass` 关不掉它）。**通信工具
  `send_message` 走它做值守拦截**（见 §5）。

### 3.6 通信怎么用事件层

- 每个通信动作 `emit_safe(...)`（投递、回送、列举）—— proactive / 审计 / 前端刷新
  都是这条流的订阅者，互不耦合。
- **前端通知统一走 `emit_ws_frame(frame)`**：跨 session 时目标 session 的前端经总线
  收到 `ws.frame` 事件、webui 订阅后原样广播，两边前端实时看到"收到来自 X 的消息"
  "X 回复了"。前端零改协议、通信工具不认识 webui。
- **值守拦截走 `tool.before` 同步点**：投递是副作用，无人值守 + deny 时拦下要求确认。

### 3.7 一条原则

**不是所有调用都是事件，只有"有消费者想响应"的时机才是。** 上表按这条筛选，通信引入的
几个事件都有确定的响应方（前端渲染、proactive、审计）。演进方向是只加不改：新增事件类型、
给 payload 加字段都不影响既有订阅者（它们只读自己关心的字段）。

---

## 4. 端到端：两个 agent 互相看见 + 通信

A、B 同时在跑（同 session 不同分支，或不同 session）：

1. **看见**：A 调 `list_agents` → 看到 B 的 session 和它的活跃分支
   `(B_session, B_head)`。
2. **发**：A 调 `send_message("...", to="B_session:B_head")` → 瞬间返回，
   A 继续。
3. **B 收到**：消息进 B 分支（B 那边一个 △"收到 A 的消息"），B 跑一轮答它（△）。
   两边前端经 ws.frame 实时看到。
4. **回送 A**：B 答完，`_dispatch_followup` 自动把回复追加到 A 末尾（△）+ 触发 A
   跑一轮，A 醒来读到、可继续。
5. **可循环**：A 再 `send_message` 给 B……两条分支各自不阻塞、不串行。

派生（`agent` 工具）和综合（带 sources）是同一流程的另两种参数化，不另列。

---

## 5. 健壮性与安全

通信会创建分支、触发别的分支跑、跨 session 写——这些副作用必须有边界。

### 5.1 递归派生 + 死循环防护

**允许递归派生**（子分支能再 `send_message` 派子分支，做多层任务分解），靠以下防爆：

- **深度上限**：每次投递在 Event metadata 里带一条**派生链**（`chain: [发起分支, …,
  当前]`）。`send_message` 执行时若链长 ≥ `MAX_DEPTH`（默认 8），拒绝并把理由回给
  模型。回送（自动 followup）继承同一条链，不重置——所以 A↔B 互发的来回也算进深度，
  到顶自动停。
- **自发拒绝**：to 指向**发起分支自己**（直接环）立即拒绝。
- 链信息只在 metadata 流转，不进模型可见内容。

### 5.2 并发上限 + 排队

- 派生走 `TaskRunner` 线程池，上限 `OPENPROGRAM_TASK_WORKERS`（默认 4）。一次派几十
  个：超出上限的**排队**，槽位空出再跑，不打爆。
- 可选 **token 预算**：一次协作的总派生数 / 总 token 设上限，到顶拒绝新派生（防一个
  失控分解派出几百个）。文档默认不强制，留参数。

### 5.3 取消传播（级联）

- 取消一个 task 时，**它派出的所有子 task 也被取消**。任何在运行中 task 内部
  发起的派生都会在 Task 实体上记下链条（`parent_task_id`，由 runner 的
  当前 task ContextVar 默认填入）。`TaskRunner.cancel_task` 沿这条链对持久化
  实体做广度优先遍历（visited 集合防环，即使出现畸形环也能终止）：
  pending/queued 的后代直接翻成 cancelled、不再被拾取；running 的后代走与根
  相同的单 task 取消路径——session cancel event + `kill_active_runtime` +
  30 秒强制取消看门狗。不留僵尸线程/子进程。
- session 级取消（用户对某 session 按 Stop）额外清空该 session 的
  send_message 收件箱（`inbox.clear`）：排队消息是还没开始的新工作，用户停掉
  一个 session 就是要它的全部工作都停。每条被丢的消息在其发送方 session 落一条
  系统提示，让发送方知道消息未被投递。

### 5.4 发给"正在跑"的分支（竞态）

A 给 B 发消息时 B 可能正跑一轮。**不打断、不丢弃——排队。**忙判定是
`run_control.is_turn_running(target)`：每个并发 turn 入口（webui chat、task
runner worker）都在 finally 里成对注册/注销 cancel token，token 在场就是进程内
"正有一轮在跑"的权威信号。只有跨 session 投递才做这个检查——同 session 投递本来
就跑在发送方自己的 turn 里，检查看到的就是自己的 token。

- **入队**：目标忙时消息持久化到目标 session 的收件箱
  （`<session-repo>/inbox.json`，`openprogram/agent/inbox.py`，与 `tasks.json`
  同一放置模式），记录投递全文、发送方 `SID:HEAD`、发送方 agent、发送时的
  spawn 深度、入队时间。发送方立刻得到"目标正忙，消息已排队，对方本轮结束后
  处理"。
- **消费**：dispatcher 在 turn 收尾时清空收件箱（`_process_turn_once` →
  `_drain_send_message_inbox`，成功和 error 两个 return 点都挂），每条经正常
  路径投出一轮异步 turn（`run_agent_turn_async` → auto-followup 回流发送方），
  从目标当前 head 继续。先投递后删除：两步之间崩溃可能重复投递（可接受），
  反过来会丢消息（不可接受）。排队触发的 turn 继承入队时记录的 spawn 深度
  （+1），排队跳数照样计入 §5.1 的深度闸。
- **上限**（对齐 Claude Code 跨会话消息的限制）：每个目标最多积压 50 条，满了
  丢最旧并在被丢消息的发送方 session 落一条系统提示；同一发送方 60 秒内与仍在
  队列中的副本内容完全相同的消息按重复拒收，并告知发送方。

B 空闲则立即投递（原有行为）。

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

- `send_message` 走事件层 `tool.before` 同步问询点：无人值守 + deny 策略时拦下要求
  确认（对子分支也生效，在 approval 包装外）。
- 投递前校验 `to` 真实存在（`db.get_session` 非 None），不存在报错、
  不静默新建。沿用三层门控（check_fn / can_use / requires_approval）。

### 5.9 分支可见性

分支标记 **内部（子派生）vs 用户可见**：内部分支只能被 `send_message` 触发，不进
UI 的会话选择列表（但 DAG 照画、能被 list_agents 列出供 agent 寻址）。

### 5.10 明确不做（及理由）

- **parentID 额外字段**：`(session_id, head_id)` + caller/predecessor 已构成树，DAG
  已画，不再加冗余字段。
- **ID 前缀分类**（fork_/msg_）：现有 id + name 足够寻址，不加。
- **重试 / 熔断策略**：失败回送给模型，由模型决定，不内置固定策略（见 §5.5）。
- **内置聚合函数**（投票 / 全部成功等）：综合就是把 sources 喂给模型让它综合（§2.2），
  模型综合比预设聚合灵活，不做固定聚合算子。

---

## 6. 前后端清单

**后端（工具）**
- `openprogram/functions/tools/agent/` — `agent/`（派生 / fork）+
  `task_output/` + `task_stop/`（异步形态管理）
- `openprogram/functions/tools/send_message/` — `send_message/`（投递 +
  触发 + 自动回送 + 多源自我总结）+ `list_agents/`（复用
  db.list_sessions + db.list_branches）
- 各工具 `emit_safe(...)`；跨 session 通知用 `emit_ws_frame`

**后端（复用既有组件）**
- `TaskRunner`（线程池并发、await、cancel、_dispatch_followup 自动回送、attach 连线）
- `SessionStore`（list/append/set_head/commit/get）
- `dispatcher.process_user_turn`
- `branch_summarization`（多源自我总结）
- `openprogram.events`（emit_safe / emit_ws_frame）

**前端（`web/`）**
- session / branch 列表面板（已有 WS handler）+ "选 to → 发消息"交互入口
- 收到 `branch.message_sent` / `branch.message_replied`（经 ws.frame）→ 在对应
  session 的 DAG / 消息流渲染通信节点 + 回流软连接线（hover 显示，dag-live 已定稿）
- 派生进度复用现有 `task_status` 帧 + tasks 面板

---

## 7. 可验证的行为

设计成立时，下列行为各自独立可验证。验证手段是 webui（`cd web && npm run build` +
`openprogram worker restart`）或事件日志（`~/.openprogram/sessions/<sid>/events.jsonl`，常开）。

| 行为 | 表现 |
|---|---|
| 派生（`agent` 工具） | agent 调一次，新建分支跑一轮，结果自动 followup 回发起方；spawn 事件在事件日志可见 |
| 列举 | `list_agents` 列出真实的多 session 及各自的分支 |
| 发给同 session 已有分支 | A 发给同 session 的 B 分支，A 不阻塞，B 跑一轮，回复自动回 A |
| 跨 session | A 发给别的 session 走同一路径；两边前端经 ws.frame 实时更新 |
| 多源综合 | 带 2 条 source 分支，每条先自我总结，目标模型综合出新回答 |
| 健壮性（§5） | A↔B 互发到 MAX_DEPTH 自动停；派 30 个排队不打爆；取消父→子全停（`tests/unit/test_cascade_cancel.py`）；给正跑的 B 发消息落它的收件箱、等它这轮结束投递（`tests/unit/test_send_message_inbox.py`）；子失败父收到 is_error；超大结果截断给文件路径 |
| 安全（§5.7-5.9） | deny 下被 tool.before 拦；不存在的 to 报错；子分支权限不高于父、不进 UI 选择列表 |
| 前端 | webui 里选分支发消息，DAG 出现通信节点 + hover 软连接线 |

---

## 8. 关键文件速查

| 事 | 位置 |
|---|---|
| 子 agent 派生 + 自动回送 | `openprogram/agent/sub_agent_run.py`、`agent/task/runner.py`（spawn_task / _dispatch_followup） |
| 工具范本 + 注册 | `openprogram/functions/tools/agent/agent/agent.py`、`functions/_runtime.py`（@function） |
| session/branch 数据层 | `openprogram/store/session/session_store.py`（list_sessions:658 / list_branches:832 / append_message:706 / set_head:814 / commit_turn:455） |
| 触发某 session 跑一轮 | `openprogram/agent/dispatcher/__init__.py`（process_user_turn:97） |
| 多源自我总结 | `openprogram/agent/compaction/branch_summarization.py` |
| 列表 WS handler | `webui/ws_actions/session.py:825`、`branch.py:221` |
| attach 连线（仅画图） | `openprogram/agent/sub_agent_run.py`（write_attach_pointer_for_spawn） |
| 事件总线 | `openprogram/events/bus.py`（emit_safe / emit_ws_frame） |
| 忙时收件箱（§5.4） | `openprogram/agent/inbox.py`（enqueue / drain），忙判定 `run_control.is_turn_running`，消费挂点 `dispatcher._drain_send_message_inbox` |

> 注："综合多条"由 `send_message(sources=[...])` 提供，不另设独立工具。底层
> `_merge.py` 的多父 ContextCommit 血缘记录被复用来记下"这次综合来自哪几条分支"。

---

## 附：实现状态

本文内容均已实现：事件层（§3）、`TaskRunner`、`SessionStore`、
`process_user_turn`、`branch_summarization`、`agent` 工具族（`agent` /
`task_output` / `task_stop`）、`send_message` 及其列举工具 `list_agents`、
派生分支命名（§2.4——派生时的 Stage 1 label + `finalize_turn` 的 Stage 2
自动改名）、串行化的回送锚定（§2.5——`_dispatch_followup` +
`_followup_lock`）、级联取消（§5.3——`TaskRunner.cancel_task` 沿
`parent_task_id` 遍历，session 级停止时附带 `inbox.clear`；
`tests/unit/test_cascade_cancel.py`），以及忙时收件箱（§5.4——
`tests/unit/test_send_message_inbox.py`）。
