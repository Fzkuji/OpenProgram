# Agent 协作：一个分支间通信原语

整套 agent 协作收敛成**一个原语：分支间通信**。一个 agent 能派生别的 agent、能给
别的分支/别的 session 发消息。这些表面上不同
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
| 计划 | **todo** | `todo_create` / `todo_update` / `todo_list` | 手写的计划清单：条目、状态、负责人、依赖。只记录打算做什么，有条目不代表有东西在跑 |
| 执行 | **task** | `job_output` / `job_stop` / `list_jobs` | 派出去正在跑的任务：job_id、状态、结果 |
| 实体 | **agent** | `agent` / `list_agents` / `archive_agent` | 执行任务的实体：新建一个、给已有的派任务（`to=`）、列出所有 agent、把干完的归档 |
| 通讯 | **message** | `send_message` / `read_conversation` | 发消息和读历史：投一条消息，读任何分支的全文 |

在 todo 清单上写"跑一遍 parser 基准"不会启动任何东西。`agent(…)` 才启动东西，
拿回来的是一个 job_id。清单说的是打算做什么，`list_jobs` 说的是什么正在跑。

一个 agent 的对话就是一条**分支**：session 内的 `(session_id, head_id)` 对。
同 session 两个 head 是一次会话的两条分支，两个 session 是两次会话。agent 的
地址永远是分支：`"SID:HEAD"`，或者分支名。

### 只有派活方能操作任务

派出去的任务上有三件事，三件都只有派活方能做：

| 能做什么 | 含义 |
|---|---|
| 结果必回 | 任务结束时回复自动落进派活方的对话，不管派活方是在等还是已经去做别的 |
| 可取消 | `job_stop` 取消任务；还在排队的直接撤回，一轮都不跑 |
| 级联取消 | 停掉一个任务，它派出去的所有任务跟着停，一路到底 |

`read_conversation` 能读到任何 job_id，所以归属要查而不是默认：
`job_output` 和 `job_stop` 拒绝别的 session 派出的任务（§5.10）。没有 session
上下文的调用（用户、UI）不受这条限制。

`send_message` 这三件事都不带，所以任何 agent 都能发。它只投递一条消息，收件方
回不回都行，不产生 job_id、不能取消、不会级联。发消息不会打断已经在跑的任务。

### `agent` 的两种模式

| 调用 | 发生什么 |
|---|---|
| `agent(prompt=…)` | 新建一个 agent 并跑它。阻塞等回复；`run_in_background=true` 则返回 job_id |
| `agent(prompt=…, to="reviewer")` | 不创建任何东西。prompt 作为受管任务派给已存在的 `reviewer`，作为它的下一轮跑，排在它手上这一轮后面，一次一轮。永远返回 job_id |

两种都产生 task，只有第一种产生 agent。`to` 与 `start_from` 互斥：目标自带
历史，没有 fork 点可选。

一次完整的委派就是这四个词：

```
todo_create("跑一遍 parser 基准")              → 板上 todo #1
todo_update("1", status="in_progress")
agent("跑一遍 parser 基准", "bench",
      run_in_background=true)                  → job_id=t_7f2
list_jobs()                                   → t_7f2 running（bench）
send_message("进展如何？", to="bench")          → agent 回话，不产生任务
job_output("t_7f2")                           → 结果到了就拿到
todo_update("1", status="completed")
archive_agent(to="bench")                      → 从 agent 列表归档
```

### 与 Claude Code 同名的部分

`agent`、`list_agents`、`send_message`、`job_output`、`job_stop` 与
Claude Code 同名同义，这是刻意的：认识那批名字的模型就已经认识这批工具。

有一个名字刻意不同。Claude Code 的 `TaskList` 是 todo 规划清单，不是正在运行的
任务清单。这里的规划清单改用 `todo_*` 前缀，撞不上，`list_jobs` 也就保住了字面
意思：正在跑的任务。

三个工具在 Claude Code 里没有对应：`list_jobs`（那边没有让模型查询后台任务的
工具）、`archive_agent`（把 agent 从 agent 列表里归档，§2.6）、`read_conversation`
（把别的 agent 的历史读成可读文本，而不是直接读原始会话文件）。

---

## 2. 原语的工具形态

把原语包成 agent 能调的工具。分工对齐 Claude Code：**`agent` 新建 agent、
`send_message` 发消息、`list_agents` 列出 agent**。

### 2.1 工具

**`agent` — 派生新 agent（唯一会创建分支的工具）：**

```
agent(
    prompt: str,                        # 给被派生 agent 的指令
    description: str = "",              # 简短 label，成为分支名
    agent_id: str = "",                 # agent 档案；默认用本会话的
    start_from: str = "clean",          # "clean" / "inherit" / "SID:MSG_ID"
    run_in_background: bool = false,    # false=阻塞等回复；true=返回 job_id
    to: str = "",                       # 改为给已有 agent 派活
    archive_when_done: bool = false,    # 派生的 agent 终态即归档（§2.6）
) -> str
```

`start_from` 决定新分支从哪起：`"clean"`（默认）新根、只见 prompt；
`"inherit"` 从当前轮 fork、带全链；`"SID:MSG_ID"` 从那个节点（任意
session）fork、继承到该节点为止的链。`run_in_background=true` 返回 `job_id`，配套
`job_output(job_id)`（阻塞取结果）和 `job_stop(job_id)`（取消）管理
异步形态。

`"SID:MSG_ID"` 是精确 fork 地址。在接收任务前必须同时确认 session 和
message 存在；这个 message 不会被改为分支当前 tip。已归档分支仍可作为
fork 来源，因为该操作只读历史并创建新分支，不是向已归档分支投递任务。

地址指向另一个 session 时，必须分开两种 session 角色。session S 的节点 A
从 `"T:M"` 启动时，新分支和 canonical Job 在目标 session T 执行，M 是精确
predecessor。Job 记录 `parent_session_id=T`、`parent_msg_id=M`、
`caller_session_id=S`、`caller_msg_id=A`。attach 卡片存在源 session S 的 A
旁边，但卡片中的 `attach.session_id` 是 T，终态 `head_id` 是目标分支的
tip。创建或终态化卡片不移动 S 的 HEAD；派生轮使用 `advance_head=false`，
也不替换 T 当前选中的 HEAD。异步完成后，第 2.5 节的普通回送 turn 可以再
推进 S 的 HEAD。

**`to=` — 给已有 agent 派受管任务。** 传了 `to` 就不新建分支：prompt 作为
一件正式任务派给指认的已有分支。寻址与 send_message 完全一致
（`"SID:HEAD"` 归位到分支当前末端；分支名先精确匹配、再唯一前缀；歧义列出
候选）。派活与发消息的区别在任务追踪：

- 创建 **Task 记录**（runner 侧的任务条目）：派活方立即拿到 `job_id`，
  `job_output` 可等，`job_stop` 可撤回或取消，`list_jobs` 可见。
- 投递复用消息机制：目标空闲，任务作为它分支上的下一轮立刻跑；目标忙，任务
  排进它的收件箱（§5.4）。Task 记录以 `pending` 预建，排队期间 id 就存在，
  drain 时跑的是同一个 task。投出的这一轮带任务来源头
  （`[task from SID:HEAD] This is a tracked task …`），目标知道这轮的回复
  就是任务结果，会自动回给派活方。
- 终态后结果回流：往派活方会话投一条followup通知，回复正文内联在通知里。
  派活不创建分支，所以没有attach指针：attach记录的是"这次调用创造了它指向的
  那条分支"，派给一个已经存在的agent不成立。
- `to` 与 `start_from` 互斥（目标分支自带历史，再选 fork 点自相矛盾，直接
  报错）。`to` 必然异步，`run_in_background` 被忽略。派给自己当前分支被
  拒绝（直接继续做）。派活花的是消息预算，不是派生预算（§5.1），因为它不创建
  agent。

**`send_message` — 和已存在的 agent 通信：**

```
send_message(
    message: str,                       # 投给目标的内容/指令
    to: str,                            # 见下方 to 取值
    agent_id: str = "main",             # 目标用哪个 agent
) -> str
```

**`to` 取值，每个取值都指认一条已存在的分支：**

| to | 含义 |
|---|---|
| `"sid:head"` | 往一条已存在分支投 message。节点指认的是分支，不是 fork 点：投递永远落在该分支的当前末端，旧 head（分支后来又跑过 turn）仍是有效地址，不会从历史节点岔出新分支。节点若是多条分支的公共祖先则报歧义，错误里列出候选（名字 + `sid:当前末端`）。归位只作用于活分支：被合并吸收的那条分支，它的 head 解析到自己（§2.6）。要从指定节点 fork 用 `agent(start_from="sid:msg_id")`。 |
| `"<分支名>"` | 按名投递。不是 `SID:HEAD` 语法时按名字解析：精确匹配优先，唯一前缀次之；多个命中返回错误并列出候选（名字 + `sid:head`），零命中提示用 `list_agents`。`list_agents` 输出里标出每条分支的名字，模型可以直接按名寻址。 |

已删除的 spawn 寻址（`to="new"` / `"new:sid:msg_id"`）直接报错，并指向
`agent` 工具。

每次投递（直投或 §5.4 的排队消费）都会加一个发件人回执头：
`[message from SID:HEAD] To reply, use send_message(to="SID:HEAD"). Replying is
optional …`。收件方由此知道谁发的、怎么回、以及不回也是正当的。agent
工具的派生投的是裸 prompt：被派生的 agent 没有需要回信的发送方。

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

`agent` 工具每次创建分支，**都必须给分支一个名字**。
否则 web 端只能显示 8 位 hex 短号，一堆分支分不清谁是谁。

- **立刻有名（Stage 1）**：创建时把一个简短 label 传给 `run_agent_turn(... label=…)`
  → `store.set_branch_name`。label 从投递的 prompt 摘一句（截断到 ~24 字），或让
  模型在调用时显式带一个名字（`description`）。这样分支创建时就有可读名，不用等 LLM。
- **后台自动改好名（Stage 2）**：分支正常聊起来后，由 `finalize_turn` 在 `turns`
  命中阈值 `{1,6,16,40}` 时，后台线程用 LLM 依据分支内容生成更贴切的标题，覆盖
  Stage 1 的临时名。规则见 [branch-naming](operations/branch-naming.zh.md)，那里定义
  了命名的分级、锁、触发点；本节只强调：**agent 工具派生的分支和用户手动
  fork 的分支，走同一套命名（都要 Stage 1 占位名 + Stage 2 自动改名），不能漏。**

### 2.5 回送节点落在哪：发起方当前尾部，串行成链

异步回送时，`_dispatch_followup` 把目标分支的回复作为一个
**synthetic user-role turn** 喂回投递 session。**关键规则：回送的 `TurnRequest`
不设 `branch_from`（INHERIT_PARENT），dispatcher 解析为投递 session 当前的
HEAD 并推进它。**每个投递 session 有一把回送串行锁
（`JobRunner._followup_lock`），并发完成被串行化：N 个子任务跑完形成一条串行链
`… → notice₁ → answer₁ → notice₂ → answer₂`，每条回送读到的 HEAD 已包含上一条的回答。

为什么不把回送钉在发起节点（`caller_msg_id`）上：同一轮 fork 出 N 个并行子任务时，
每条回送都会作为同一节点的 sibling 落下，触发派生的那一条用户消息会在 N 条并行分支
上被回答 N 次。锚在 HEAD 让 N 次完成始终走同一条会话主线。

这样锚定不丢回流出处：派生时写下的 **attach 指针**仍然挂
`predecessor = caller_msg_id`，DAG 上照样能看出每条子分支从哪一轮 fork 出去、
每个结果从哪条分支回流。子分支本身是并列的独立一支，**不并回主线**。

跨会话派生时，指针仍位于发起方 session，但引用目标
`(session_id, head_id)`。终态化从目标 session 读目标分支及其 ContextCommit，
然后更新发起方 session 里的卡片。源侧发起节点标记 `spawn_out`；目标侧第一个
`agent_spawn` user 节点记录 `caller=<source node>` 与
`metadata.spawned_from_session=<source session>`，并投影为 `spawn_remote`。
因为源 session 里有真实 attach 指针，它的异步 followup 通过 attach 展开获取结果，
不再内联一份重复回复。`send_message` 与 `agent(to=...)` 不创建分支和 attach
指针，因此它们的回复仍内联，也不会得到任何 spawn 标记。

### 2.6 归档：把一个 agent 从 agent 列表里移出

分支在 session DAG 里永久存在，fork、回放、`read_conversation` 都依赖这一点，
所以没有归档标记时 `list_agents` 会攒下历史上派生过的每一个 agent，模型还会
继续去找那些任务早就做完的 worker。归档就是这个标记：分支 meta 条目上的
`archived: true`，和分支名同一个 `branches` 条目，用 `set_branch_meta` 写、
`get_branch_meta` 读。和名字共用一个条目是安全的：每个写入方都在索引锁里按字段
合并，Stage-2 自动命名（branch-naming.md）只写 `name` 和它自己的计数器，冲不掉
归档标记；何况已归档的分支根本不进自动命名，活干完的 agent 不需要新名字。

**归档后分支不再接收新的投递，它的历史照常保留。**

| 对已归档分支的操作 | 行为 |
|---|---|
| `list_agents`（`scope="session"` / `"all"`） | 不列 |
| `list_agents(scope="archived")` | 列出每一条已归档分支，包括已被合并吸收的 |
| `send_message(to=…)` | 报错：`agent SID:HEAD is archived` |
| `agent(to=…)` | 同样报错，同一句话 |
| `read_conversation` | 照读 |
| `agent(start_from="SID:MSG_ID")` | 照 fork |

拒收只写在一处：两条投递路径共用的寻址 `resolve_existing_target`（§2.1）在把
地址归位到分支当前末端之后立刻查这个标记，于是每条投递都自带这道守卫，谁也
绕不过去。`archive_agent` 用同一个寻址加 `allow_archived=True` 来指认已归档
分支。

**归档和合并正交。**合并把一条分支吸收进另一条，被吸收的head从`list_branches`
里消失，因为它的内容已经能从吸收它的那条分支读到。这讲的是内容存在哪里，而且它
自动发生：后台派生一旦成功完成，task runner就吸收掉它的分支。归档讲的是分支上
那个agent已经收工，永远是一次显式动作。两者互不蕴含，于是分开存、分开读
（`merged_heads`在session meta上，`archived`在分支条目上）：

- `list_agents(scope="archived")`读的是分支条目上的归档标记
  （`store.list_archived_branches`），不是从活分支末端列表里筛，所以每一条已归档
  分支都会列出，不管有没有被合并吸收。`archive_when_done`在成功派生上看得见效果，
  靠的就是这一点：成功恰好就是合并先发生的那种情况。
- 默认scope和`scope="all"`列的是活分支末端，所以被合并的分支不进这两个视图，
  归不归档都一样。这是合并本来的行为，归档不改它。
- 被合并分支的head仍然指认它自己那条分支。`resolve_existing_target`只把地址归位
  到活分支的当前末端；已退休分支的head不归位到别处，解析到它自己
  （`store.merged_heads`）。没有这条规则，`archive_agent(to="SID:MERGED_HEAD")`
  会解析到吸收了这个节点的那条活分支，归档错的那一条，还报成功。

两种归档方式：

- **`agent(archive_when_done=true)`**：派生时就声明这个 agent 是一次性 worker。
  分支在任务终态（`completed` / `errored` / `cancelled`）被打上标记，时点在
  结果回流之后；同步派生形态则在结果拿到手后打标。这次写入是 best-effort：
  meta 写失败只记日志，结果照常返回。只对派生生效，和 `to=` 同时传会报错，
  因为派活指向的是本次调用没有创建的 agent。
- **`archive_agent(to, reason="")`**：事后归档。`to` 收 `send_message` 那套
  地址（`"SID:HEAD"` 或分支名）。对已归档分支再归档是一句幂等提示，不是错误。

**任何 session 都能归档任何 agent。** 归档不像 `job_stop`（§5.10）那样设门，
因为它做的事和 `job_stop` 不是一回事：它不中断任何在跑的工作，也不删任何数据。
分支上已经在跑的任务照跑到完，`read_conversation` 照读，
`agent(start_from="SID:MSG_ID")` 照 fork。变的只有两件事：这个分支从 `list_agents`
里消失，并且不再接收 `send_message` 和 `agent(to=)`。谁都看得出一个 agent 的活
干完了，那谁都可以说出来。

**归档是单向的，没有反归档。** 这个标记的含义是"这段对话结束了"，而结束的对话若
还有值得复用的记忆，做法是用 `agent(start_from="SID:MSG_ID")` fork 出一条新分支，
它有自己的名字和自己的生命周期，复用记忆本来就需要这样一条新分支。反归档工具只会
是同一件事的第二种写法。

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
  拦住等确认。子 agent 走同一个拦截点，`permission_mode=bypass` 关不掉它。

事件层本身（总线、事件模型、注册表、否决协议）写在
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

通信会创建分支、触发别的分支跑、跨 session 写，这些副作用必须有边界。

### 5.1 每条链有三个预算

允许递归协作（被派生的agent还能再往下派消息做多层分解），三个预算保证它有限。
**链**指一次用户轮次产生的全部调用。其中两个预算跟着链走，各有各的计数器；
第三个数的是一轮之内的兄弟数量。

| 预算 | 配置项 | 默认 | 计数器 | 什么动作花它 |
|---|---|---|---|---|
| **派生深度** | `agent.max_spawn_depth` | 1 | `depth._chain_generations` | 只有创建agent才花：不带`to=`的`agent`。新agent往下走一代 |
| **消息数** | `agent.max_messages` | 8 | `depth._chain_messages` | 每一跳都花：派生、`send_message` 投递、`agent(to=…)` 派活、结果回送 |
| **扇出** | `agent.max_spawn_fanout` | 8 | `agent._fanout_used`，按（会话，轮次）计 | 创建agent，按轮次计而不是按链计 |

**任一项设成 0 就是取消该上限**：既不累积也不拒绝。

**读结果花的是消息，不花代数。**把已完成agent的回复带回来的那一轮是**派发方**
自己的轮次，所以它跑在派发方的代数上（`Task.caller_chain_generations`，由
`JobRunner._dispatch_followup`重新绑定），消息数则往前走一格。多agent最常见
的形态因此保持通畅：派一批活出去，看回来的结果，再派下一批。两个预算共用一个
计数器就会把这条路堵死：协调者的followup轮次继承worker的计数1，那条链里后续
每次调用`agent`都会被拒。真正让这种链停下来的是消息计数：每一波都花消息，
第8条把它停住。

```bash
openprogram config set agent.max_spawn_depth 2   # 允许 worker 再开一代
openprogram config set agent.max_messages 0      # agent 之间随便聊
openprogram config set agent.max_spawn_fanout 16 # 一轮铺得更宽
```

**预算耗尽时的表现**：超额的那次调用被拒绝，理由回给模型，其它工具照常可用。
**消息预算**耗尽后，`agent`、`job_output`、`job_stop`直接从工具清单里消失：
任何一种派发都要交出一条消息，消息用完这三个工具就什么也做不成，而工具摆在清单里
模型就会去调用。代数预算不摘工具，因为代数用完还能把活派给已存在的agent。扇出
预算也不摘：工具清单在轮次开始时冻结，这个数要到轮次里才花掉，所以它只能拒绝。

默认值（派生深度1、消息8、扇出8）下的典型行为：

- 主 agent 派 worker。worker 再想派生会被告知自己动手做。
- 同一个 worker 仍然有 `agent(to=…)` 和 `send_message`：能把任务交给**已存在**的
  agent，能回复给它发消息的 agent。对它关掉的只是"再开一代"。
- 主agent派出一波worker，读回结果，再派下一波。读结果花消息不花代数，所以
  不论派多少波，worker始终只在第一代上。
- A和B来回对话在这条链的第8条消息后停下，不论此刻轮到谁。回送这一跳重新绑定
  已完成task的计数而不是加一，所以一个来回花1，8条够走八个来回。
- 一轮里第9次调用`agent`被拒绝，并被指回它已经有的那8个。下一轮的扇出预算
  重新开始，所以这条上限拦的是失控的那一轮，不是整个会话的配额。

`agent.max_spawn_depth: 2`时worker可以再开一代，第三代被拒。三项都是`0`时
什么都不拒，防失控就只剩并发上限和每轮迭代上限（§5.2）以及用户按停止。

**自发拒绝**不受三个预算影响，永远生效：to指向发起分支自己是直接环，立即拒绝。

**这几个数是怎么定的。**每个默认值都对着
`agent-collab-comparison.html` §05那八个参考实现校准过，理由写在各自常量旁边
（`agent.MAX_SPAWN_DEPTH`、`agent.MAX_SPAWN_FANOUT`、`depth.MAX_MESSAGES`）。

- **派生深度1**是openclaw、codex-cli V1、hermes-agent、opencode共同的选择。
  Claude Code的3不能照搬：它泄露的源码树里根本没有深度计数器，`Agent`工具在
  每个子agent的工具池里都被摘掉，除非`USER_TYPE=ant`，所以外部用户在那边的实际
  深度就是1；它的异步工具白名单里也没有`Agent`，所以后台子agent无论计数器写几
  都不能再派生。深度3只作用于同步嵌套，那条路上父的工具调用会一直阻塞到孩子跑完。
  我们的无人值守路径是`run_in_background=True`，在那条路上Claude Code用的正是1。
- **消息8**锚在openclaw上，八家里只有它数同一样东西：它的agent之间来回循环
  默认5次交替回复，最多20次。8落在两者之间，一个还要为派生和派活买单的计数器
  就该落在这个位置。
- **扇出8**补的是此前没人数的那种失控。派生把计数交给孩子、自己那份不动，所以在这
  条预算之前，一轮可以一直调`agent`，直到50次迭代上限把它停下。八家里只有openclaw
  有真正的扇出上限（每个父最多5个活着的孩子，可配1到20）；hermes的3和pi-mono
  的8校验的是一次调用里那个批量参数的长度，不能照搬，因为`agent`一次只创建一个孩子。
  8是我们四个worker的两倍宽度，一轮可以把池子填满，后面再排一波。

**看过但没有采用的两条防护。**openclaw给父发给子的消息加了2秒限速，
hermes给每个被委派的子任务600秒超时。

- 2秒限速守的是openclaw的steer通道：那条路会中止孩子正在跑的那一轮、清空它的
  队列、再重启一遍，所以两次steer挨太近会在中止过程中互相中止。它那条不中断的
  send通道完全没有限速。我们的`send_message`属于不中断的那种：目标忙就排队
  （§5.4），消息作为它自己的一轮被消费，没有可以打断的东西。
- hermes的600秒是调用方一侧的`Future.result(timeout=…)`，不是杀。到点它只是
  设一个协作式中断标志然后放弃那个线程，孩子如果卡在阻塞I/O上会继续跑。这两半我们
  都有，而且更强：`job_output(timeout=)`就是同样的调用方等待（默认30秒，上限
  600秒），`job_stop`除了协作式取消还会杀掉活动运行时，30秒后强制把实体置终态。
  我们和hermes都没有的是一个没人盯着也会到点触发的死线。要加就是在`JobRunner`
  提交时挂一个定时的`cancel_job`，而让它很少用得上的那条边界是下面每轮50次迭代的上限。

**两个计数怎么传下去**。两个计数各存在一个 ContextVar 里
（`send_message…depth._chain_messages`、`._chain_generations`）。一条链要跨三个
线程边界，每个边界都得显式把它们交接过去，因为 Python 新起的线程里 ContextVar
全是默认值：

| 跨越 | 计数怎么到达 |
|---|---|
| dispatcher → 工具体 | `functions/_runtime.py` 里的 `copy_context()` 把两个都带进执行器线程 |
| 发送方 → task worker | 两个都写在 Task 实体上（`chain_messages` 恒为发送方 + 1；`chain_generations` 派生时 + 1、派活时不动），由 `JobRunner._run_one` 重新绑定 |
| task → 回送 followup | `JobRunner._dispatch_followup` 在自己的线程里绑定这个已完成 task 的 `chain_messages` 和它的 `caller_chain_generations` |

回送这一跳正是两个预算分道的地方，两个方向都要紧。消息从孩子那边接着往下走：
followup轮次正是A读到B回复、写下一条消息的地方，followup若从0开始，A每一轮
都拿到全新预算，8条上限永远走不到。代数则退回派发方的计数：followup不创建
任何agent，继承孩子的计数会让一个刚读完worker回复的agent在这条链里再也创建
不出新agent。

同一个线程还把 `_current_job_id` 绑成这个已完成 task 的 `parent_job_id`，
于是 A 在读回复时派出的 task 落在级联取消要走的同一条谱系上（§5.3）。

这些工具读的 session id（`run_control._current_session_id`）由 `TurnBindings`
在一轮的时长内与 turn id 一并绑定，因此进入 `process_user_turn` 的每条路径上它
都在，而不只是调用方碰巧先绑过的那几条。绑定只在无人绑定时补上：某个入口若把这个
id 持有在比一轮更宽的作用域里（webui 执行线程、task runner worker、channel
adapter），它继续持有，于是一个跑别的 session 的嵌套轮次不会把 cancel hook 或
`runtime.ask` 指向一个没注册 turn token 的 session。

### 5.2 并发上限 + 排队

- 派生走`JobRunner`线程池，上限`OPENPROGRAM_JOB_WORKERS`（默认4）。一次派八
  个：超出上限的**排队**，槽位空出再跑，不会过载。这是个全局池，它限制的是同时在跑
  多少，不是一轮能造出多少工作，后者归扇出预算管（§5.1）。
- 每一轮，包括派生出来的那些，跑到50次内层工具调用就停
  （`agent_loop.MAX_INNER_ITERATIONS`）。调用方给的`max_iterations`只能把它调紧，
  不能调松。事件流干净收尾，这一轮算正常结束，所以一个不停要"再调一次工具"的模型
  仍然会返回。hermes-agent给被委派的子agent定的也是50。

### 5.3 取消传播（级联）

- 取消一个 task 时，**它派出的所有子 task 也被取消**。任何在运行中 task 内部
  发起的派生都会在 Task 实体上记下链条（`parent_job_id`，由 runner 的
  当前 task ContextVar 默认填入）。`JobRunner.cancel_job` 沿这条链对持久化
  实体做广度优先遍历（visited 集合防环，即使出现畸形环也能终止）：
  pending/queued 的后代直接翻成 cancelled、不再被拾取；running 的后代走与根
  相同的单 task 取消路径：session cancel event + `kill_active_runtime` +
  30 秒强制取消看门狗。不留僵尸线程/子进程。
- **后代先于根被取消**。取消根会让它的 worker 退出，空出来的线程池槽位立刻启动
  下一个排队的 future，那正是级联还没走到的后代，于是它为用户已经叫停的工作跑
  完了一整轮。先走链条，捡起这个后代的 worker 看到的实体已经是 `cancelled`，
  直接返回，不会调 `run_agent_turn`。只改顺序：`cancel_job` 仍然返回根更新后的
  实体，task id 解析不到 session 时仍然返回 `None`。
- session 级取消（用户对某 session 按 Stop）额外清空该 session 的
  send_message 收件箱（`inbox.clear`）：排队消息是还没开始的新工作，用户停掉
  一个 session 就是要它的全部工作都停。每条被丢的消息在其发送方 session 落一条
  系统提示，让发送方知道消息未被投递。

### 5.4 发给"正在跑"的分支（竞态）

A 给 B 发消息时 B 可能正跑一轮。**不打断、不丢弃，排队。**忙判定是
`run_control.is_turn_running(target)`：每个并发 turn 入口（webui chat、task
runner worker）都在 finally 里成对注册/注销 cancel token，token 在场就是进程内
"正有一轮在跑"的权威信号。只有跨 session 投递才做这个检查。同 session 投递本来
就跑在发送方自己的 turn 里，检查看到的就是自己的 token。

- **入队**：目标忙时消息持久化到目标 session 的收件箱
  （`<session-repo>/inbox.json`，`openprogram/agent/inbox.py`，与 `jobs.json`
  同一放置模式），记录投递全文、发送方 `SID:HEAD`、发送方 agent、发送时的
  链上的消息数、入队时间。发送方立刻得到"目标正忙，消息已排队，对方本轮结束后
  处理"。
- **消费**：dispatcher 在 turn 收尾时清空收件箱（`_process_turn_once` →
  `_drain_send_message_inbox`，成功和 error 两个 return 点都挂），每条经正常
  路径投出一轮异步 turn（`run_agent_turn_async` → auto-followup 回流发送方），
  从目标当前 head 继续。先投递后删除：两步之间崩溃可能重复投递（可接受），
  反过来会丢消息（不可接受）。排队这一跳和直投一样花消息预算（§5.1）。
- **上限**：每个目标最多积压50条，满了丢最旧并在被丢消息的发送方session落一条
  系统提示；同一发送方60秒内与仍在队列中的副本内容完全相同的消息按重复拒收，
  并告知发送方。50取自Claude Code同一结构的数字，它的跨会话信箱就是一个丢最旧的
  50条环，八家里也只有它有信箱可比。60秒这个窗口没有任何一家可对照：Claude Code
  按消息uuid去重，weclaw按收到的消息id去重，两者都只能拦住同一个消息对象被逐字
  重发，拦不住模型第二次写出同样的文字。这个检查只对仍在队列里的条目生效，所以窗口
  只界定一件事：发送方要等多久，同样的文字才算主动重发而不是重试循环。

B 空闲则立即投递（原有行为）。

### 5.5 失败回送

子/目标分支失败（崩溃 / 超时 / 模型报错）：**也回送**，回送内容带 `is_error` + 原因
（"B 失败了：<原因>"），发起方模型读到后自行决定重发/换路/放弃。**不内置重试/熔断**：
父是模型，由它判断比固定策略好。

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
  一个拦截点，`permission_mode=bypass` 关不掉它。
- `to` 指向不存在的东西就报错，不会静默新建。常规权限门控照常叠加在上面。

### 5.9 分支可见性

分支标记 **内部（子派生）vs 用户可见**：内部分支只能被 `send_message` 触发，不进
UI 的会话选择列表（但 DAG 照画、能被 list_agents 列出供 agent 寻址）。

### 5.10 任务归属（job_output / job_stop）

`read_conversation` 能读任意分支，任何 agent 都能拿到任意 job_id。
不设门槛的话，任何 agent 都能等待或取消别人派出的任务。所以 `job_output` 和
`job_stop` 执行前核对归属：当前 session 必须是该 task 的派活方
（`caller_session_id`，同 session spawn 则是 `parent_session_id`），或在
任务链祖先上（当前 task 经 `parent_job_id` 是它的祖先，或当前 session
派发过它的某个祖先，与级联取消走的是同一条链）。都不是则拒绝：
`[job_stop error] task {id} was not dispatched by this session`。无
session 上下文的调用（用户、UI）不受这条限制。

`job_stop` 对 `to=` 派出的任务按状态分三种：

- **排队中**（目标当时忙，任务在它收件箱里）→ 从收件箱撤回，记录翻
  `cancelled`。不发 session 级取消：目标正在跑的是别人的轮，撤回不能
  终止它。
- **在跑** → 取消目标分支上的那一轮（task 取消事件 + session 取消桥 +
  runtime 终止 + 30 秒看门狗），不终止目标 agent 或它的 session。
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

下面每一条都能独立看到：在 web 界面里，或者在 session 事件日志里。

| 行为 | 表现 |
|---|---|
| 派生（`agent` 工具） | agent 调一次，新建分支跑一轮，结果自动回到发起方；派生过程在事件日志里可见 |
| 列举 | `list_agents` 列出真实的多 session 及各自的分支 |
| 归档（§2.6） | 已归档 agent 从 `list_agents` 消失、在 `scope="archived"` 里出现；`send_message` 与 `agent(to=)` 拒收，`read_conversation` 与 `agent(start_from=…)` 照常；任何 session 都能归档任何 agent，且标记单向；成功完成、已被合并吸收的派生同样出现在 `scope="archived"` 里，它的 head 也仍然指认它自己那条分支 |
| 发给同 session 已有分支 | A 发给同 session 的 B 分支，A 不阻塞，B 跑一轮，回复自动回 A |
| 跨 session | 向另一个 session 投递时两边实时更新。`send_message` / `agent(to=...)` 仍是纯消息投递。`agent(start_from="T:M")` 在 T 创建分支和 canonical Job，卡片留在发起 session，源与目标 DAG 节点分别标记 `spawn_out` / `spawn_remote`，不移动任一选中 HEAD |
| 健壮性（§5） | A↔B 互发到消息预算用完自动停，预算为 0 时不停；一次派 30 个是排队不是过载；取消父→子全停；给正忙的 B 发消息先排队、等它这轮结束再投；子失败父会被告知；超大结果截断并给出文件路径 |
| 安全（§5.7-5.9） | deny 策略下投递被拦下等确认；不存在的 to 报错；子分支权限不高于父、不进 UI 选择列表 |
| 前端 | web 界面里选分支发消息，DAG 出现通信节点，hover 显示回流连线 |

---
