# Agent 协作：一个分支间通信原语

整套 agent 协作收敛成**一个原语：分支间通信**。一个 agent 能派生别的 agent、能给
别的分支/别的 session 发消息——这些表面上不同
的操作，**底层是同一件事**：往某条分支投递内容 → 触发那条分支跑一轮 → 结果自动回送
发起方。全部做成工具调用，全部建在已有的事件层上。

> §1 是整个工具面的词汇总纲，先读它。

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
| **给已有 agent 派活** | 往**已存在**分支投**受管任务** + 自动回 | `agent(to=…)` |
| **发消息给某 agent** | 往**已存在**分支投消息 + 自动回 | `send_message` |

投来的内容一定被目标模型读取并使用。数量任意（派生能派 N 个、发消息
也能群发），不是区分维度。两种用法共用一条投递→触发→回送的路径。

`attach` 不是操作，是通信结果在 DAG 上的"回流连线"画法（标记结果从哪条分支回来）。

---

## 1. 四个域，一词一义

协作分四个域。每个域有一个名词、一套工具，域之间不重叠：同一个词在哪里
出现都是同一个意思。

| 域 | 名词 | 工具 | 是什么 |
|---|---|---|---|
| 计划 | **todo** | `todo_create` / `todo_update` / `todo_list` | 手写的计划清单：条目、状态、负责人、依赖。纸面上的"打算做"，有条目不代表有东西在跑 |
| 执行 | **task** | `task_output` / `task_stop` / `list_tasks` | 派出去正在跑的活：单号、状态、结果 |
| 实体 | **agent** | `agent` / `list_agents` / `archive_agent` | 干活的实体：生新的、给已有的派活（`to=`）、查通讯录、把干完的退役 |
| 通讯 | **message** | `send_message` / `read_conversation` | 说话和读历史：捎一句话，读任何分支的全文 |

在 todo 板上写"跑一遍 parser 基准"不会启动任何东西。`agent(…)` 才启动东西，
拿回来的是一个 task_id。板子说的是打算做什么，`list_tasks` 说的是什么正在跑。

一个 agent 的对话就是一条**分支**：session 内的 `(session_id, head_id)` 对。
同 session 两个 head 是一次会话的两条分支，两个 session 是两次会话。agent 的
地址永远是分支：`"SID:HEAD"`，或者分支名。

### 权力跟创造走

派出去的活上有三样权力，三样都只归派活方：

| 权力 | 含义 |
|---|---|
| 结果必回 | 任务结束时回复自动落进派活方的对话，不管派活方是在等还是走开了 |
| 可叫停 | `task_stop` 取消任务；还在排队的直接撤单，一轮都不跑 |
| 级联取消 | 停掉一个任务，它派出去的所有活跟着停，一路到底 |

`read_conversation` 能读到任何 task_id，所以归属要查而不是默认：
`task_output` 和 `task_stop` 拒绝别的 session 派出的任务（§5.10）。只有人不受限。

`send_message` 一样权力都没有，所以人人可发不会乱。它只投递一条消息，收件方
回不回都行，不产生单号、不能取消、不会级联。发消息扰不动已经在跑的活。

### `agent` 的两种模式

| 调用 | 发生什么 |
|---|---|
| `agent(prompt=…)` | 生一个新 agent 并跑它。阻塞等回复；`run_in_background=true` 则返回 task_id |
| `agent(prompt=…, to="reviewer")` | 不创建任何东西。prompt 作为受管任务派给已存在的 `reviewer`，作为它的下一轮跑，排在它手上这一轮后面，一次一轮。永远返回 task_id |

两种都产生 task，只有第一种产生 agent。`to` 与 `start_from` 互斥：目标自带
历史，没有 fork 点可选。

一次完整的委派就是这四个词：

```
todo_create("跑一遍 parser 基准")              → 板上 todo #1
todo_update("1", status="in_progress")
agent("跑一遍 parser 基准", "bench",
      run_in_background=true)                  → task_id=t_7f2
list_tasks()                                   → t_7f2 running（bench）
send_message("进展如何？", to="bench")          → agent 回话，不产生任务
task_output("t_7f2")                           → 结果到了就拿到
todo_update("1", status="completed")
archive_agent(to="bench")                      → 从通讯录退役
```

### 与 Claude Code 同名的部分

`agent`、`list_agents`、`send_message`、`task_output`、`task_stop` 与
Claude Code 同名同义，这是刻意的：认识那批名字的模型就已经认识这批工具。

有一个名字刻意不同。Claude Code 的 `TaskList` 是 todo 规划板，不是在跑的活的
清单。这里的规划板改用 `todo_*` 前缀，撞不上，`list_tasks` 也就保住了字面
意思：正在跑的任务。

三个工具在 Claude Code 里没有对应：`list_tasks`（那边没给模型开查后台任务的
口子）、`archive_agent`（把 agent 从通讯录里退役，§2.6）、`read_conversation`
（把别的 agent 的历史读成可读文本，而不是直接读原始会话文件）。

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
    start_from: str = "clean",          # "clean" / "inherit" / "SID:MSG_ID"
    run_in_background: bool = false,    # false=阻塞等回复；true=返回 task_id
    to: str = "",                       # 改为给已有 agent 派活
    archive_when_done: bool = false,    # 派生的 agent 终态即归档（§2.6）
) -> str
```

`start_from` 决定新分支从哪起：`"clean"`（默认）新根、只见 prompt；
`"inherit"` 从当前轮 fork、带全链；`"SID:MSG_ID"` 从那个节点（任意
session）fork、继承到该节点为止的链。`run_in_background=true` 返回 `task_id`，配套
`task_output(task_id)`（阻塞取结果）和 `task_stop(task_id)`（取消）管理
异步形态。

**`to=` — 给已有 agent 派受管任务。** 传了 `to` 就不新建分支：prompt 作为
一件正式任务派给指认的已有分支。寻址与 send_message 完全一致
（`"SID:HEAD"` 归位到分支当前末端；分支名先精确匹配、再唯一前缀；歧义列出
候选）。派活与发消息的区别在任务追踪：

- 创建 **Task 记录**（runner 的台账）：派活方立即拿到 `task_id`，
  `task_output` 可等，`task_stop` 可撤单或取消，`list_tasks` 可见。
- 投递复用消息机制：目标空闲，任务作为它分支上的下一轮立刻跑；目标忙，任务
  排进它的收件箱（§5.4）——Task 记录以 `pending` 预建，排队期间 id 就存在，
  drain 时跑的是同一个 task。投出的这一轮带任务来源头
  （`[task from SID:HEAD] This is a tracked task …`），目标知道这轮的回复
  就是任务结果，会自动回给派活方。
- 结果回流和 spawn 任务一致：终态后 attach + followup 通知回派活方会话。
- `to` 与 `start_from` 互斥（目标分支自带历史，再选 fork 点自相矛盾，直接
  报错）。`to` 必然异步，`run_in_background` 被忽略。派给自己当前分支被
  拒绝（直接继续干）。派活花的是消息预算，不是派生预算（§5.1）——它不创建
  agent。

**`send_message` — 和已存在的 agent 通信：**

```
send_message(
    message: str,                       # 投给目标的内容/指令
    to: str,                            # 见下方 to 取值
    agent_id: str = "main",             # 目标用哪个 agent
) -> str
```

**`to` 取值——每个取值都指认一条已存在的分支：**

| to | 含义 |
|---|---|
| `"sid:head"` | 往一条已存在分支投 message。节点指认的是分支，不是 fork 点：投递永远落在该分支的当前末端，旧 head（分支后来又跑过 turn）仍是有效地址，不会从历史节点岔出新分支。节点若是多条分支的公共祖先则报歧义，错误里列出候选（名字 + `sid:当前末端`）。要从指定节点 fork 用 `agent(start_from="sid:msg_id")`。 |
| `"<分支名>"` | 按名投递。不是 `SID:HEAD` 语法时按名字解析：精确匹配优先，唯一前缀次之；多个命中返回错误并列出候选（名字 + `sid:head`），零命中提示用 `list_agents`。`list_agents` 输出里标出每条分支的名字，模型可以直接按名寻址。 |

已删除的 spawn 寻址（`to="new"` / `"new:sid:msg_id"`）直接报错，并指向
`agent` 工具。

每次投递（直投或 §5.4 的排队消费）都会加一个发件人回执头：
`[message from SID:HEAD] To reply, use send_message(to="SID:HEAD"). Replying is
optional …` —— 收件方由此知道谁发的、怎么回、以及不回也是正当的。agent
工具的派生投的是裸 prompt：它们是干活的 worker，不是通信对象。

一种用法：

- **发消息给已有分支/session**：`to="sid:head"` → 往那条分支投 message，触发它
  跑一轮，答完自动回送。跨 session 同一路径（to 是任意 session）。

两个工具驱动同一个原语：派生就是同一条投递→触发→回送流程，只是目标分支
是当场新建的。

**一条流程，谁发起都一样：**
1. 定目标分支：`agent` 当场新建（`start_from` 定起点）；`send_message` 把
   `to` 解析到已存在分支的当前末端。
2. 发件人回执头加上消息，一起投过去。
3. 目标跑一轮，模型读到投来的全部内容。
4. 回复自己回来。发送瞬间就返回了，发起方全程不阻塞；目标答完，答案追加到
   发起方的对话里，发起方跑一轮读到它。

### 2.2 引用别的分支

message 就是纯文本，跟用户消息一样。要让目标参考别的分支，发送方直接写进
`message`：结论已经通过回送回到发送方手里，直接引用；或者点名分支
（`SID:HEAD` 或分支名），目标自己用 `read_conversation` 去读。读多少由目标
模型自己决定，context 天然有界，不需要专门的聚合参数。

### 2.3 `list_agents` — 看见对方（通信的前提）

```
list_agents(scope="session", limit=20, agent_id?, source?) -> str   # db.list_sessions + db.list_branches
```

`scope` 决定看哪一片：`"session"`（默认）列当前 session 的分支，也就是在这里
派生出来的 agent；`"all"` 放宽到所有 session，最近活跃优先，不带预览；
`"archived"` 列当前 session 已归档的分支（§2.6），前两种视图会把它们藏起来。

agent 的对话就存在 session DAG 的分支里，所以"能跟谁说话"="有哪些
session、每个有哪些分支"。一次调用全列出，按 session 分组：session 行带
id、标题、agent、busy/idle 状态（`run_control.is_turn_running`，探测失败
就不标）；分支行带名字（若有）、现成的 `to="SID:HEAD"` 地址、轮数与近似字符量
（`— 3 turns, ~2k chars`，不足1000字符显示`<1k chars`）、末端预览。
模型可据此在调用`read_conversation`前选合适的`max_chars`。
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

异步回送时，`_dispatch_followup` 把目标分支的回复作为一个
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

### 2.6 归档：把一个 agent 从通讯录里退役

分支在 session DAG 里永久存在，fork、回放、`read_conversation` 都依赖这一点，
所以没有退役标记时 `list_agents` 会攒下历史上派生过的每一个 agent，模型还会
继续去找那些活早就干完的 worker。归档就是这个标记：分支 meta 条目上的
`archived: true`，和分支名同一个 `branches` 条目，用 `set_branch_meta` 写、
`get_branch_meta` 读。

**归档砍掉的是分支被打扰的权利，不是它的历史。**

| 对已归档分支的操作 | 行为 |
|---|---|
| `list_agents`（`scope="session"` / `"all"`） | 不列 |
| `list_agents(scope="archived")` | 列出，恰好就是这些退役分支 |
| `send_message(to=…)` | 报错：`agent SID:HEAD is archived` |
| `agent(to=…)` | 同样报错，同一句话 |
| `read_conversation` | 照读 |
| `agent(start_from="SID:MSG_ID")` | 照 fork |

拒收只写在一处：两条投递路径共用的寻址 `resolve_existing_target`（§2.1）在把
地址归位到分支当前末端之后立刻查这个标记，于是每条投递都自带这道守卫，谁也
绕不过去。`archive_agent` 用同一个寻址加 `allow_archived=True` 来指认已归档
分支。

两种归档方式：

- **`agent(archive_when_done=true)`**：派生时就声明这个 agent 是一次性 worker。
  分支在任务终态（`completed` / `errored` / `cancelled`）被打上标记，时点在
  结果回流之后；同步派生形态则在结果拿到手后打标。这次写入是 best-effort：
  meta 写失败只记日志，结果照常返回。只对派生生效，和 `to=` 同时传会报错，
  因为派活指向的是本次调用没有创建的 agent。
- **`archive_agent(to, reason="")`**：事后退役。`to` 收 `send_message` 那套
  地址（`"SID:HEAD"` 或分支名）。对已归档分支再归档是一句幂等提示，不是错误。

**只有创建者能归档。** 每次派生都会把创建者记在新分支上
（`spawner_session_id`，在终态和归档标记一起写），`archive_agent` 拒绝其他
session。没记创建者的分支属于顶层，归它自己的 session 所有：那个 session 可以
归档它，别的 session 不行。没有 session 上下文的调用（用户、UI）不设门——人拥有
一切，和 §5.10 的任务归属同一立场。

**没有反归档。** 这个标记的含义是"这段对话结束了"，而结束的对话若还有值得复用的
记忆，做法是用 `agent(start_from="SID:MSG_ID")` fork 出一条新分支，它有自己的名字和
自己的生命周期。反归档工具只会是同一件事的第二种写法。

---

## 3. 协作进行时是什么样

协作跑在框架统一的事件层上，所以过程是实时可见的，不是事后才知道。由此有三
个效果，用户和 agent 需要知道的就是这三条：

- **两边实时更新。** 投给另一个 session 的消息，落地那一刻就出现在那个
  session 的界面上；回复回来时出现在发送方的界面上。两边都不用刷新。
- **全程留痕。** 投递、分支状态变化、列表查询都写进 session 的事件日志
  （`~/.openprogram/sessions/<sid>/events.jsonl`，始终开启），一次协作事后可
  以回放、可以审计。
- **投递可以被拦下确认。** 值守策略拒绝副作用时，`send_message` 在投递前被
  拦住等确认。子 agent 走同一道闸，`permission_mode=bypass` 关不掉它。

事件层本身——总线、事件模型、注册表、否决协议——写在
[proactive/event-layer](../proactive/event-layer.zh.md)。

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

派生（`agent` 工具）是同一流程的另一种参数化，不另列。

---

## 5. 健壮性与安全

通信会创建分支、触发别的分支跑、跨 session 写——这些副作用必须有边界。

### 5.1 每条链有两个预算

允许递归协作——被派生的 agent 还能再往下派消息做多层分解——两个预算保证它有限。
**链**指一次用户轮次长出来的全部东西，每一跳都花同一对计数，回送也算。

| 预算 | 配置项 | 默认 | 计什么 |
|---|---|---|---|
| **派生深度** | `agent.max_spawn_depth` | 1 | 这条链能创建几代**新** agent |
| **消息数** | `agent.max_messages` | 8 | 这条链总共能传几条消息：派生、`send_message` 投递、`agent(to=…)` 派活都计入 |

**任一项设成 0 就是取消该上限**：既不累积也不拒绝。

```bash
openprogram config set agent.max_spawn_depth 2   # 允许 worker 再开一代
openprogram config set agent.max_messages 0      # agent 之间随便聊
```

**预算耗尽时的表现**：超额的那次调用被拒绝，理由回给模型，其它工具照常可用。
**两个预算都耗尽**后，`agent`、`task_output`、`task_stop` 直接从工具清单里消失
——工具摆在清单里模型就会想用，先给再拒是浪费一轮。

默认值（派生深度 1、消息 8）下的典型行为：

- 主 agent 派 worker。worker 再想派生会被告知自己动手干。
- 同一个 worker 仍然有 `agent(to=…)` 和 `send_message`：能把活交给**已存在**的
  agent，能回复给它写信的人。对它关掉的只是"再开一代"。
- A 和 B 来回对话在这条链的第 8 条消息后停下，不论此刻轮到谁。

`agent.max_spawn_depth: 2` 时 worker 可以再开一代，第三代被拒。两项都是 `0` 时
什么都不拒，防失控就只剩并发上限（§5.2）和用户按停止。

**自发拒绝**不受两个预算影响，永远生效：to 指向发起分支自己是直接环，立即拒绝。

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
  链上的消息数、入队时间。发送方立刻得到"目标正忙，消息已排队，对方本轮结束后
  处理"。
- **消费**：dispatcher 在 turn 收尾时清空收件箱（`_process_turn_once` →
  `_drain_send_message_inbox`，成功和 error 两个 return 点都挂），每条经正常
  路径投出一轮异步 turn（`run_agent_turn_async` → auto-followup 回流发送方），
  从目标当前 head 继续。先投递后删除：两步之间崩溃可能重复投递（可接受），
  反过来会丢消息（不可接受）。排队这一跳和直投一样花消息预算（§5.1）。
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

- 值守策略拒绝副作用时，`send_message` 在投递前被拦下等确认（§3）。子分支走同
  一道闸，`permission_mode=bypass` 关不掉它。
- `to` 指向不存在的东西就报错，不会静默新建。常规权限门控照常叠加在上面。

### 5.9 分支可见性

分支标记 **内部（子派生）vs 用户可见**：内部分支只能被 `send_message` 触发，不进
UI 的会话选择列表（但 DAG 照画、能被 list_agents 列出供 agent 寻址）。

### 5.10 任务归属（task_output / task_stop）

`read_conversation` 能读任意分支，任何 agent 都能拿到任意 task_id——
不设门槛，任何 agent 都能等待或杀掉别人派的活。所以 `task_output` 和
`task_stop` 执行前核对归属：当前 session 必须是该 task 的派活方
（`caller_session_id`，同 session spawn 则是 `parent_session_id`），或在
任务链祖先上（当前 task 经 `parent_task_id` 是它的祖先，或当前 session
派发过它的某个祖先——与级联取消走的是同一条链）。都不是则拒绝：
`[task_stop error] task {id} was not dispatched by this session`。无
session 上下文的调用（用户、UI）不设限——人拥有一切。

`task_stop` 对 `to=` 派出的任务按状态分三种：

- **排队中**（目标当时忙，任务在它收件箱里）→ 从收件箱撤单，记录翻
  `cancelled`。不发 session 级取消：目标正在跑的是别人的轮，撤单不能
  杀它。
- **在跑** → 取消目标分支上的那一轮（task 取消事件 + session 取消桥 +
  runtime 终止 + 30 秒看门狗），不杀目标 agent 或它的 session。
- **已终态** → 幂等 no-op。

### 5.11 明确不做（及理由）

- **parentID 额外字段**：`(session_id, head_id)` + caller/predecessor 已构成树，DAG
  已画，不再加冗余字段。
- **ID 前缀分类**（fork_/msg_）：现有 id + name 足够寻址，不加。
- **重试 / 熔断策略**：失败回送给模型，由模型决定，不内置固定策略（见 §5.5）。
- **内置聚合函数**（投票 / 全部成功等）：综合就是在 `message` 里点名分支、让目标
  模型自己读完综合（§2.2），模型综合比预设聚合灵活，不做固定聚合算子。

---

## 6. 可以核对的行为

下面每一条都能独立看到——在 web 界面里，或者在 session 事件日志里。

| 行为 | 表现 |
|---|---|
| 派生（`agent` 工具） | agent 调一次，新建分支跑一轮，结果自动回到发起方；派生过程在事件日志里可见 |
| 列举 | `list_agents` 列出真实的多 session 及各自的分支 |
| 归档（§2.6） | 已归档 agent 从 `list_agents` 消失、在 `scope="archived"` 里出现；`send_message` 与 `agent(to=)` 拒收，`read_conversation` 与 `agent(start_from=…)` 照常；只有创建它的 session 能归档 |
| 发给同 session 已有分支 | A 发给同 session 的 B 分支，A 不阻塞，B 跑一轮，回复自动回 A |
| 跨 session | A 发给别的 session 走同一路径；两边实时更新 |
| 健壮性（§5） | A↔B 互发到消息预算用完自动停，预算为 0 时不停；一次派 30 个是排队不是打爆；取消父→子全停；给正忙的 B 发消息先排队、等它这轮结束再投；子失败父会被告知；超大结果截断并给出文件路径 |
| 安全（§5.7-5.9） | deny 策略下投递被拦下等确认；不存在的 to 报错；子分支权限不高于父、不进 UI 选择列表 |
| 前端 | web 界面里选分支发消息，DAG 出现通信节点，hover 显示回流连线 |

---
