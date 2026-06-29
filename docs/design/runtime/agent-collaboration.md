# Agent 协作：一个分支间通信原语

整套 agent 协作收敛成**一个原语：分支间通信**。一个 agent 能派生别的 agent、能给
别的分支/别的 session 发消息、能把几条分支的内容汇给一个模型综合——这些表面上不同
的操作，**底层是同一件事**：往某条分支投递内容 → 触发那条分支跑一轮 → 结果自动回送
发起方。全部做成工具调用，全部建在已有的事件层上。

> 范围：本文是设计，不含代码。落地顺序见末节。

---

## 0. 核心：只有一个原语

之前纠结过 派生 / send / attach / merge 是不是四五个并列操作——**不是**。把它们拆开
看，本质都是同一个动作：

> **分支间通信** = 往一条分支（同 session 另一分支 / 跨 session / 当场新建的 /
> 已存在的）**投递内容** → **触发**那条分支跑一轮（模型读到投来的内容）→ 结果
> **自动回送**发起方（追加一条新消息 + 触发发起方跑一轮，发起方醒来读到、继续）。

所有协作操作都是这个原语的**参数化**：

| 表面操作 | 其实是通信的哪种用法 |
|---|---|
| **派生子 agent**（旧 task/spawn） | 通信：**新建**一条分支 + 投消息 + 自动回 |
| **发消息给某分支**（send_to_branch） | 通信：往**已存在**分支投消息 + 自动回 |
| **综合多条分支**（旧 merge） | 通信：投递时**带多个来源**分支的内容，让目标模型综合 |
| **attach** | **不是操作**，是通信结果在 DAG 上的"回流连线"画法 |

**没有独立的 merge，没有"贴 vs 综合"的区分**（投来的内容一定被模型读取使用，不存在
"光放着不读"）。**数量不是区分点**（派生能派 N 个、综合能合 N 条、send 也能群发）。
唯一的原语就是上面那条。

为什么能这么收敛（和 Claude Code 一致）：Claude Code 的异步子 agent 跑完，结果靠
"完成通知作为新输入唤醒父"回去；它没有 merge，多个结果靠主 agent 自己读了综合。我们
把这个推广——不分父子、不分同跨 session，**任意分支之间都能投递+自动回**，于是派生、
send、综合都成了它的用法。

---

## 1. 名词对齐（沿用现有抽象，不发明）

| 概念 | 定义 | 来源 |
|---|---|---|
| **session** | 一个独立会话，有 `session_id`，对应一个 git 仓库 | `SessionStore` |
| **branch（分支）** | `(session_id, head_id)` 对。同 session 不同 head = 同会话两条分支；不同 session = 跨会话 | `merge.py` 已确立，同/跨 session 走同一路径 |
| **投递** | 往某分支追加一条消息节点 | `append_message`（任意 session_id，无权限限制） |
| **触发** | 让某分支跑一轮 agent | `process_user_turn(TurnRequest(...))` |
| **自动回送** | 目标答完，把回复作为新输入喂回发起方 + 触发它跑 | `TaskRunner._dispatch_followup`（已存在） |
| **attach 连线** | DAG 上标记"结果从哪条分支回流来"的指针节点 | `write_attach_pointer_for_spawn`（只画图，不是操作） |

DAG 画法已在 `dag/dag-live.html` 定稿（分支间通信场景：异步、send 瞬间返回、回复异步
回送、通信点线 hover 显示；派生=子分支服务场景；回流=软连接线）。

---

## 2. 原语的工具形态

把原语包成 agent 能调的工具。**一个核心工具 + 两个列举工具**。

### 2.1 `talk_to_branch` — 分支间通信（核心，唯一的协作原语）

```
talk_to_branch(
    message: str,                       # 投给目标的内容/指令
    target: str = "new",                # "new"=当场新建分支(派生)；"sid"或"sid:head"=已存在分支
    sources: list[str] = [],            # 额外带上这些分支的内容一起投（综合多条时用）
    agent_id: str = "main",             # 目标用哪个 agent
    wait: bool = false,                 # false=异步(默认,瞬间返回)；true=同步等回复
) -> str
```

一个工具覆盖三种用法（靠参数，不是三个工具）：

- **派生子 agent**：`target="new"` → 当场新建一条分支，把 message 投给它，它跑完
  自动回流。（想派几个，就调几次，各自异步并行。）
- **发消息给已有分支/session**：`target="sid:head"` → 往那条已存在分支投 message，
  触发它跑一轮，答完自动回送。跨 session 同一路径（target 是任意 session）。
- **综合多条分支**：`sources=["s1:h1","s2:h2",...]` → 投递时把这几条分支的内容
  一起带上，目标模型读完综合。（替代旧 merge；数量任意。）

**统一执行流程**（无论哪种用法）：
1. 若 `target="new"`：新建分支；否则 `set_head` 切到 `target` 那条分支。
2. 组装投递内容：`message` +（若有 `sources`）把每条来源分支的内容附上。
3. 投递 + 触发：`process_user_turn(TurnRequest(session_id=target, user_text=投递内容,
   branch_from=target_head))` → 目标分支跑一轮，**模型读到投来的全部内容**。
4. **回送**：
   - `wait=false`（默认）：瞬间返回"已投递 + delivery_id"，发起方不阻塞继续；目标
     答完，`_dispatch_followup` **自动**把回复作为新消息喂回发起方 session + 触发它
     跑一轮，发起方醒来读到。
   - `wait=true`：阻塞等目标答完，直接返回回复文本。
- 事件：投递 emit `branch.message_sent`；回送 emit `branch.message_replied`（见 §3）。

### 2.2 多条来源喂多少内容（综合的关键，照 Claude Code）

`sources` 里每条分支怎么喂给目标模型——**照 Claude Code：先让每条分支自我总结，再
汇集总结**。Claude Code 子 agent 的 final message 本就是"给父看的总结"；我们的来源
分支不一定以总结收尾，所以多一步：

1. 对每条 source 分支，先让它产出一个**面向本次通信的总结**（"把你这条分支的结论
   浓缩成要点"），复用 `branch_summarization`。
2. 把这些总结拼成 `<branch label="...">总结</branch>` 块，连同 `message` 一起投给
   目标模型。

这样不爆 context、能带很多条、且每条交给模型的是浓缩要点而非原始长对话——和 Claude
Code 主 agent 拿 N 段子 agent 总结再综合是同一思路。不需要"喂全文/喂摘要"的参数选择，
统一走"自我总结"。

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

框架里"某件事发生了"的信号原本散在六套互不相通的机制里（agent loop 的 AgentEvent、
auth 的 `_emit`、context 的 on_event、channels 的 WS 广播、memory 的定时 poll、store
的纯日志）。想"在某时机做某事"得先搞清那时机归哪套、怎么接。事件层把它们统一成
**一条总线：源往里 emit，消费者从里 subscribe，源和消费者互不认识**。

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
| `branch.message_sent` | talk_to_branch 投递 | agent | from, to, sources, delivery_id, is_new |
| `branch.message_replied` | 目标答完自动回送 | agent | from, to, delivery_id |
| `sessions.listed` / `branches.listed` | 列举 | agent | count |

通信复用已有 `subagent.started`/`.ended`（派生用法时 TaskRunner 照发）。

### 3.5 两种交互：观察 vs 拦截

- **观察型（默认，异步）**：emit 出去，订阅者异步收到，源不等。绝大多数事件走这条。
- **拦截型（仅 `tool.before`，同步）**：工具执行前能让下游说"别执行"。单一入口
  `_execute_tool_calls` 在 `tool.execute()` 前有同步问询点（`tool_gate.py`
  `register_tool_gate`）。必须快（不许调 LLM）；多方表态取最严；对 subagent 也生效
  （在 approval 包装外，`permission_mode=bypass` 关不掉它）。**通信工具
  `talk_to_branch` 走它做值守拦截**（见 §5）。

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
2. **发**：A 调 `talk_to_branch("...", target="B_session:B_head")` → 瞬间返回，
   A 继续。
3. **B 收到**：消息进 B 分支（B 那边一个 △"收到 A 的消息"），B 跑一轮答它（△）。
   两边前端经 ws.frame 实时看到。
4. **回送 A**：B 答完，`_dispatch_followup` 自动把回复追加到 A 末尾（△）+ 触发 A
   跑一轮，A 醒来读到、可继续。
5. **可循环**：A 再 `talk_to_branch` 给 B……两条分支各自不阻塞、不串行。

派生（target="new"）和综合（带 sources）是同一流程的另两种参数，不另列。

---

## 5. 安全 / 值守

投递是副作用（往别人会话写、触发别人跑），必须可拦：

- 走事件层已有的 `tool.before` 同步问询点：无人值守 + deny 策略时拦下 `talk_to_branch`
  要求确认。
- 投递前校验 `target`（非 "new" 时）真实存在（`db.get_session` 非 None），不存在报错、
  不静默新建。
- 沿用三层门控（check_fn / can_use / requires_approval），不在工具里自造放行。

---

## 6. 前后端清单

**后端（工具，`openprogram/functions/tools/agent_collab/`）**
- `talk_to_branch.py` — 唯一核心：投递 + 触发 + 自动回送 + 多源自我总结
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
| **C1** | `talk_to_branch` 核心：target="new"（派生）→ 投递 + 触发 + 自动回送 | agent 调一次，新建分支跑一轮，结果自动 followup 回发起方；事件 message_sent/replied 在事件日志可见 |
| **C2** | `list_sessions` / `list_branches` 工具 | agent 列出真实多 session / 多分支 |
| **C3** | `talk_to_branch` target=已存在分支（同 session） | A 发给同 session 的 B 分支，A 不阻塞，B 跑一轮，回复自动回 A |
| **C4** | 跨 session：target=别的 session | A 发给别的 session，同一路径跑通；两边前端经 ws.frame 实时更新 |
| **C5** | `sources` 多源综合（自我总结 → 汇集 → 综合） | 带 2 条 source 分支，每条先自我总结，目标模型综合出新回答 |
| **C6** | 值守拦截 + target 存在性校验 | deny 下被 tool.before 拦；不存在的 target 报错不新建 |
| **C7** | 前端交互：列表选 target → 发消息 + 通信节点渲染 | webui 里选分支发消息，DAG 出现通信节点 + hover 软连接线 |

C1 是核心原语（派生用法）；C3/C4 把 target 推广到已有分支/跨 session；C5 是综合用法；
C6 安全；C7 前端。每步在 webui（`cd web && npm run build` + `openprogram worker
restart`）或事件日志（`OPENPROGRAM_EVENT_LOG=1`）验证。

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

> 注：旧的 `_merge.py` / `process_merge_turn` 不再作为独立"操作"暴露给 agent——
> "综合多条"由 `talk_to_branch(sources=[...])` 覆盖。底层 merge 代码可保留（多父
> ContextCommit 血缘记录仍有用），但不再是面向 agent 的独立工具。
