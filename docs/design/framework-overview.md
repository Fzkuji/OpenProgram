# OpenProgram 框架总览：一次对话从输入到产出

> 本文所有 `file:line` 直接取自当前代码库（CodeGraph 索引校验 + 逐处核对）。不含推断；未实现/TODO 的部分集中在末尾「已知边界」。
>
> 这是把整个框架**串起来**的一篇——单轮/多轮上下文、事件层、agent 运行、协作、DAG，在「一次对话的时间轴」上怎么咬合。

**贯穿全文的一句话：整个框架靠一个进程级事件总线把各子系统解耦相连。** dispatcher、agent loop、工具执行、存储、协作彼此不直接调用对方的 UI/广播逻辑，而是 `emit` 一个事件，谁关心谁订阅（`event_bus.py:141` `emit` / `:159` `subscribe`）。事件层有两条 lane：**异步旁观**（`EventBus`，谁也拦不住正在发生的事）和**同步问询**（`tool_gate`，全框架唯一能拦住工具执行的点）。

---

## 第一部分　一次对话的生命周期（主线）

以「用户输入一句话」为起点，真实数据流如下。每步标注：发生在哪、emit 什么事件、单轮 vs 多轮差异、分支（branch）如何体现。

### 数据流（时间轴）

```
用户输入一句话
   │
   ▼  [入口] process_user_turn(req)              dispatcher/__init__.py:97（同步，内部起 asyncio loop 跑到完）
   │
   ├─▶ 1. session 建立/加载                       :175 get_session / :177 create_session
   │
   ├─▶ 2. 历史解析（分支在此第一次体现）           :186–198
   │       branch_from=INHERIT_PARENT → db.get_branch() 沿活跃分支父链回走（= 多轮）
   │       branch_from=None           → []（根级 fork，从空起）
   │       branch_from="<node_id>"    → db.get_branch(sid,node)（兄弟 fork，看到分叉点为止）
   │
   ├─▶ 3. 持久化 user 消息（先写后跑，崩了也留痕）   :271–304；写成 DAG Call 节点 caller=ROOT（:293）
   │       emit chat_ack(:305) + user_message(:315)  ← 让 UI 实时显示
   │       emit_safe("user.prompt_submitted")        :337  ← 进事件总线
   │
   ├─▶ 4. 绑定 turn 上下文（ContextVar）            :358–421
   │       _current_turn_id.set(assistant_msg_id) :371   ← turn 内任何协程都读得到同一 turn id
   │       _store.set(GraphStoreShim)            :414   ← 深层 runtime/工具/@agentic_function 写同一 DAG
   │       assistant_msg_id = user_msg_id+"_reply" :164
   │       写 assistant 占位行 + set_head           :431/:435；status="running" :439
   │
   ├─▶ 5. ★ 调模型前：上下文引擎先跑一遍 ★          _run_loop_blocking :738–759
   │       a. ContextEngine.prepare(...)           :859  ← DAG 历史渲染成 LLM messages（TurnPrep）
   │       b. should_auto_compact(prep)?           :870
   │            是 → snip（免费删最老 turn :870–878）不够 → compact（LLM 压缩 :936）→ 重新 prepare
   │       c. 拼出 prompt（loop 内只加一次，避免破坏 prompt 缓存 :447–461）
   │       d. agent_loop([prompt], context, ...)   :1048 → 进核心循环（见下 §核心循环）
   │
   ├─▶ 6. 持久化 assistant 消息                     persist_assistant_message :651
   │
   ├─▶ 7. finalize（收尾，context 子系统在这里做 3 件事）  finalize_turn :686 / finalize.py:34
   │       · head/token 更新
   │       · ContextCommit 回填                      finalize.py:142
   │       · ctx_engine.after_turn(...)             finalize.py:167 → engine.py:437
   │            ↑ 这里才 emit "context.compaction_recommended"（按预算算 pct）
   │       · git commit_turn(:215) + 项目自动提交(:228 commit_turn_changes)
   │       · auto-title、usage 反馈、快照淘汰
   │       db.update_session(status="idle"/"done")  :702/:704
   │
   └─▶ emit chat_response/result(:710) → 返回 TurnResult(:714)
```

### 入口：dispatcher

`process_user_turn(req, *, on_event, cancel_event)` 是全框架**唯一**对话入口（`dispatcher/__init__.py:97`）。它是**同步**的，便于 channel worker 线程直接调用，内部自起 asyncio loop 跑 `agent_loop` 到完。返回 `TurnResult`（`dispatcher/types.py:102`）。

### turn_id 绑 ContextVar——框架解耦的另一根脊柱

`_current_turn_id.set(assistant_msg_id)`（`:371`）是关键：ContextVar 沿 asyncio task 传播，turn 内**任何**协程（工具执行、`@agentic_function`、`message_branch`）都读得到同一 turn id，从而把文件备份、子分支父锚点都归到正确的 assistant 消息上。同时绑 `_store`（`:414`）让深层 runtime 写同一 SQLite DAG，无需层层透传。`finally` 块（`:626–645`）成功/异常/提前 return 都会 `reset`。

### 上下文组装：单轮 / 多轮 / 分支

历史解析集中在 `:186–198`，**分支在数据流里第一次体现**：

| 情况 | branch_from | 取的历史 | 含义 |
|---|---|---|---|
| 普通追加 | `INHERIT_PARENT` | `db.get_branch(sid)` 沿活跃分支父链回走 | **多轮**：看到当前分支全部历史 |
| 根级 fork | `None` | `[]` | LLM 从空起 |
| 兄弟 fork | `"<node_id>"` | `db.get_branch(sid, node_id)` | 看到分叉点为止，不被活跃分支污染 |

**单轮 vs 多轮唯一差别就是 `get_branch` 回走出来的链长度**：单轮链上只有刚写的 user 节点（挂在 ROOT 下），多轮则是一条完整父链。

> **分支怎么"写"出来**（上面讲的是怎么"读"）：fork 的写入侧 = 给 user 节点指一个非 active-tail 的 caller（存储层 `set_head` 改 UI 指针），或 `message_branch` 起新根（见 §⑤）。读=三种 get_branch，写=换 head 指针 / 新建根。

### ★ 调模型前：上下文引擎先跑一遍（每轮自动压缩主路径）★

这是最容易被忽略、但每个 turn 都发生的一层。step 5 的实体是 `_run_loop_blocking`（`dispatcher/__init__.py:738–759`），它在进 `agent_loop` **之前**：

1. `ContextEngine.prepare(agent, session, history, model, tools)`（`:859` → `engine.py:194`）：把 DAG 历史**渲染成 LLM 输入** messages（默认走 DAG 渲染 `_build_messages_from_dag` `engine.py:558`；config `context.render="legacy"` 回退到 commit-chain）。返回 `TurnPrep`（`context/types.py:102`）。
2. `should_auto_compact(prep)`（`:870`）为真时——**这是上下文超预算时真正触发的链路**：先 `snip`（免费删最老 turn，`:870–878`），不够再 `_ctx_engine.compact(...)`（LLM 压缩，`:936`），然后**重新 prepare**。
3. 拼出 prompt（loop 内只加一次，避免重复破坏 prompt 缓存，`:447–461`）。
4. `agent_loop([prompt], context, config, ...)`（`:1048`）进核心循环。

> 这条「turn 开头先压一遍」和末尾「idle-gap microcompact」是两条不同的压缩路径，别混（见 §③）。

### 核心循环：调模型 → 工具 → 回灌 → 循环

`agent_loop`（`agent_loop.py:114`）建 `EventStream`，内部 `_run_loop`（`:205`）的内循环（`:236`）：

1. push `AgentEventTurnStart`（多轮每轮都 push，`:246`）。
2. `_stream_assistant_response` 调模型（`:260`）。emit `model.response_started`（`:442`）/ `model.response_completed`（`:466`）。
3. 抽 `ToolCall`（`:273`）。有工具 → `_execute_tool_calls`（`:278`），结果 append 回 context（**回灌**，`:288–290`）。
4. push `AgentEventTurnEnd` + emit `turn.ended`（`:292–293`）。
5. 无更多工具且无 steering/follow-up → break。

硬上限 `MAX_INNER_ITERATIONS = 50`（`:226`），防无限「再调一个工具」。

### 工具执行：tool.before 拦截

`_execute_tool_calls`（`agent_loop.py:540`）对每个 tool call：

1. push `AgentEventToolStart`（`:561`）+ 插件 hook `TOOL_BEFORE_USE`（`:572`，best-effort）。
2. **事件层 tool.before**：`make_event("tool.before",...)` + `emit`（`:581–584`）——一份事件，异步旁观和同步问询共用。
3. **同步 gate**：`decide_tool_gate(before_ev)`（`:587`）。**全框架唯一能拦住工具执行的点**（`tool_gate.py:53`）：任一 gate 返回 deny 即拦（理由合并），gate 抛错按 allow（fail-open）。被拦 `raise ToolGateDenied`（`:594`），deny 理由作为 error tool result 回模型。**对 subagent 也生效**——gate 在 `permission_mode` approval 包装之外，`bypass` 关不掉它（`tool_gate.py:14–15`）。
4. `tool.execute(...)`（`:605/:616`），前后做 cwd 快照 + 文件 checkpoint。
5. push `AgentEventToolEnd`（`:645`）+ emit `tool.after`（`:651`）。
6. 组 `ToolResultMessage` 回灌（`:671–682`）。每次工具后检查 steering（`:685`），命中则跳过剩余工具、回灌 steering。

### 持久化 + finalize（context 子系统在这步做三件事）

- 出错路径（`:582–625`）：错误折进占位行或独立 error 节点，`head_id` 移到失败 turn（`:597`）。
- 成功路径：`persist_assistant_message`（`:651`）写 assistant 消息 → `finalize_turn`（`:686`，`finalize.py:34`）。**finalize 里 context 子系统做三件事**：① head/token 更新 ② **ContextCommit 回填**（`finalize.py:142`，把这轮压缩决策固化成不可变 per-turn commit）③ **`after_turn`**（`finalize.py:167` → `engine.py:437`，**这里才发 `context.compaction_recommended`**：usage 回灌 + 按预算算 pct）。随后 git `commit_turn`（`finalize.py:215`）+ 项目自动提交（`:228`）。

### DAG 更新——贯穿全程，不是单独一步

user 节点（`:293`）、assistant 占位（`:431`）、每个工具结果、`@agentic_function` 内部节点，都通过 `_store` ContextVar 落入同一 `GraphStoreShim`，turn 末 `commit_turn`（`session_store.py:455`）把整棵工作树作为一次 turn 提交——append-only、无可变"当前态"镜像文件，两个 agent 并发写不会撞同一文件。

---

## 第二部分　分层参考（查细节）

### 事件全表（核心：子系统靠它解耦相连）

实际在发的事件（全仓 `emit_safe` / `emit_ws_frame` / `make_event` 扫描 + 核对）。两类：进总线的 typed 事件（异步旁观 + 同步问询）和透传前端的 `ws.frame`。

| 事件 type | 谁发（file:line） | 谁收 | 备注 |
|---|---|---|---|
| `user.prompt_submitted` | dispatcher `:337` | proactive observer | 用户消息已提交 |
| `tool.before` | agent_loop `:581–584` | **tool_gate（同步）** + 旁观 | 唯一拦截位 |
| `tool.after` | agent_loop `:651` | 旁观 | 携带 `is_error` |
| `turn.ended` | agent_loop `:267/:293` | 旁观 | 每个内循环 turn 结束 |
| `model.response_started` | agent_loop `:442` | 旁观 | 模型流开始 |
| `model.response_completed` | agent_loop `:466` | proactive 收尾策略 | 收尾时机检查 |
| `subagent.started` / `.ended` | task/runner `:115/:121`（origin=`system`，session 显式传参，因 worker 线程 ContextVar 不可靠） | 旁观 | 子 agent 状态漏斗 |
| `branch.message_sent` | message_branch `:266` | 旁观 + `ws.frame` | from/to/is_new/sources |
| `branch.message_replied` | message_branch `:339` | 旁观 + `ws.frame` | 含 is_error |
| `question.asked` | questions `:164`（同时 `emit_ws_frame` `:161` 成前端卡片） | channels question bridge（`_question_bridge.py:43`） | 既进总线又发 ws 帧 |
| `question.replied` | questions `:275`（`resolve_question_and_broadcast`） | 前端 | **只走 ws 帧** |
| `question.rejected` | questions `:173/:276` | 前端按"收回"处理 | **只走 ws 帧** |
| `context.compaction_recommended` | **engine `after_turn` `:437`**（finalize 里调） | UI / proactive | 按预算算 pct |
| `context.compacted` | context 引擎 | UI / 旁观 | 压缩已发生 |
| `file.changed` | functions watcher 等 | 旁观 | 文件变更 |
| `channel.message_inbound` | channels | 旁观 | 入站消息 |
| `memory.ingest_started` / `.ended` | memory | 旁观 | 记忆摄入 |
| `skills.changed` / `plugins.update_available` / `sessions.listed` / `branches.listed` | 各子系统 | UI / 旁观 | 列表与可用更新 |
| `ws.frame`（`event_bus.py:115`） | 外部源 `emit_ws_frame`（`:118`） | `webui/server.py:1153`（原样广播） | 透传信封：外部源不直连 webui `_broadcast` |

**订阅侧实际位点**：proactive 引擎订阅**全部**事件再按 `on` 过滤（`proactive/engine.py:145`）；webui 只订 `ws.frame`（`server.py:1153`）；channels question bridge 只订 `question.asked`（`_question_bridge.py:43`）；auth/session 各有 `subscribe`（`event_bridges.py:75`、`session.py:112`）。

**事件契约**：`emit` 是 fire-and-forget，handler 抛错绝不反噬发射方（`event_bus.py:141–157` + `_call:182–198` 打 stderr）；async handler 无 loop 时跳过；`emit_safe`（`:96`）整体 try/swallow——「事件层绝不破坏调用方代码路径」。

---

### ① 存储层　SessionStore，分支 = (session, head)

**职责**：每 session 一个 git 仓 + 内存索引；append-only、无可变当前态镜像。
**关键文件**：`store/session/session_store.py`、`memory_index.py`。
**关键机制**：
- `_open(sid)`（`:403`）返回 `(GitSession, SessionMemoryIndex)`，LRU 缓存、容量淘汰；索引可从 git 无损重建。
- **分支 = (session, head 指针)**：`get_branch(sid[, head])`（`:751` / `memory_index.py:87`）沿 `predecessor`/`caller` 父链回走；`set_head` 改的是 meta 里单值的 UI 指针。fork 只是从某 node 起再走一条链，互不污染。
- `commit_turn(sid, msg)`（`:455`）：把工作树作为一次 turn 提交。
**对外事件**：`sessions.listed`、`branches.listed`。

### ② 事件层　总线 + tool.before 拦截

**职责**：进程级 fan-out，解耦所有子系统。
**关键文件**：`agent/event_bus.py`、`tool_gate.py`、`questions.py`。
**关键机制**：
- `EventBus`（`:129`）：typed `subscribe(handler, types=...)`（`:159`）+ legacy channel `on`（`:208`）；进程单例 `get_event_bus()`（`:241`，双检锁）。
- **tool.before 同步拦截**：`register_tool_gate`（`tool_gate.py:38`）/ `decide_tool_gate`（`:53`），取最严、fail-open。gate 必须快，不许调 LLM / 慢 IO。
- 问询子系统：`QuestionRegistry`（`questions.py:61`，进程级待答表、claim-once、线程安全）。
**对外事件**：见上表。

### ③ 上下文引擎　组装 / 压缩 / ContextCommit

**职责**：把 DAG 历史渲染成 LLM 输入，并做压缩决策。
**关键文件**：`context/engine.py`、`microcompact.py`、`references.py`、`render.py`、`commit/`。
**关键机制**：
- `ContextEngine.prepare(...)`（`engine.py:194`）返回 `TurnPrep`（`context/types.py:102`）。默认从 DAG 渲染（`_build_messages_from_dag:558`），失败回退 legacy（`:218–220`，免得一个坏 commit 拖垮整个 turn）。
- **两条压缩路径**（别混）：
  - **turn-start auto-compact（主）**：`should_auto_compact` 为真 → `snip`（免费删最老 turn）→ 不够则 `compact`（LLM 压缩）→ re-prepare。在 `_run_loop_blocking` 里，每轮调模型前（见第一部分 step 5）。
  - **idle-gap microcompact（次）**：`microcompact.py:76`，**仅空闲 > 3600s 触发**（`GAP_THRESHOLD_SECONDS:45`，prompt 缓存到期后清理"零额外成本"），保留最近 5 个 tool_result，更老的大结果替占位符。**非破坏性**（返回拷贝，不动 DAG 节点）。
- **引用扫描**：`ReferenceTracker.build`（`references.py:114`）廉价子串扫描，标记被后文引用的 tool_result 避免被压。（注：目前结果仅用于日志，ContextCommit 规则尚未消费它，见已知边界。）
- **ContextCommit**（`commit/types.py:104`）：每 turn 的压缩决策固化成不可变 commit；`finalize_turn` 回填（`finalize.py:142`）。
- **`after_turn`**（`engine.py:437`，finalize 里调 `finalize.py:167`）：usage 回灌 + 发 `context.compaction_recommended`。
**对外事件**：`context.compaction_recommended`、`context.compacted`。

### ④ agent 运行　loop + 工具注册 + 子 agent

**职责**：驱动「模型↔工具」循环，执行工具，派生子 agent。
**关键文件**：`agent/agent_loop.py`、`agent.py`、`sub_agent_run.py`、`providers/utils/event_stream.py`、`agent/management/gating.py`。
**关键机制**：
- `EventStream`（`event_stream.py:15`）：async-iterable + 终值；provider dict 事件归一成 typed。
- `agent_loop` / `_run_loop`（`:114/:205`，硬上限 50）+ `_execute_tool_calls`（`:540`）。
- `Agent` 支持 `steer`（中途插队）/ `follow_up`（收尾追加）/ `prompt`。
- **工具/技能/MCP 静态准入**：`gate(name, disabled, allowed, categories)`（`management/gating.py:38`，fnmatch，解析序 disabled→allowed→categories）。注意：这是**静态准入**（agent.json 声明谁能用），与 ② 的 `tool_gate` 运行时拦截是两回事。
- **子 agent**：`run_agent_turn(sid, prompt, agent_id, branch_from, label)`（`sub_agent_run.py:41`）内部就是再调一次 `process_user_turn`（`:91`），用 `source="agent_spawn"`、`permission_mode="bypass"`（`:88`）。返回 `AgentTurnResult`（`:32`，`head_id`=新分支 tip）；`label` 经 `set_branch_name` 成命名分支（`:104`）。
**对外事件**：`model.response_started/completed`、`turn.ended`、`subagent.started/ended`（后者由 task/runner 发）。

### ⑤ 协作　message_branch + 跨 session + 防护

**职责**：分支/会话之间投递消息、跑分支、把回复带回来。
**关键文件**：`functions/tools/agent_collab/message_branch.py`、`list_branches.py`。
**关键机制**：
- `message_branch(message, target, sources, agent_id, wait)`（`:388` → `_message_branch_impl:186`）。
- target 语义（`_parse_target:167`）：`new`（当前 session 新根）/ `new:SID:MSG_ID`（fork 某节点继承其链）/ `SID:HEAD`（投到已存在分支 = 从其 head 再跑一轮）。
- 父锚点 `_resolve_parent`（`:74`）读 dispatcher 的 session/turn ContextVar，**turn id 缺失时回退到 session head**（修了"no active parent turn"）。
- **sources 综合**：`_gather_sources`（`:128`）把每个源分支 tip 文本包成 `<branch source=...>` 块前置给目标模型综合。
- 异步（默认）交给 task runner（`run_agent_turn_async`），跑完写 attach pointer 并 dispatch followup 回**发起方** session（回复自动回流）。
- **防护**：深度守卫 `MAX_SPAWN_DEPTH=8`（`:35`，子继承 depth+1，A↔B 来回也计入）；自指守卫（投给自己当前 turn 直接拒）；目标 session 必须存在不静默创建；超大回复 `_clip_result`（`:360`，>30000 字存文件返回路径）；tool.before 拦截（值守可拦）。
**对外事件**：`branch.message_sent`（`:266`）、`branch.message_replied`（`:339`）；并 `emit_ws_frame("branch_message",...)`（`_emit_branch_ui:107`）在发起方聊天流显示「已发送/已回复」行。

---

## 已知边界（gaps / TODO，如实记录）

- **tool.before 仅「观察 + deny」，尚不能 mutate/veto-by-plugin**：插件 hook `TOOL_BEFORE_USE` 注释明确写 future-work（`agent_loop.py:567–569`）。
- **gate 的 "ask" 三态尚未完全接 ApprovalRegistry**：`Gate.ask` 注释「接 ApprovalRegistry，后续单元接」（`proactive/actions.py:29`）；"critical fail-closed" 分级也待规则层进场（`tool_gate.py:13`）。
- **引用扫描结果未被 ContextCommit 规则消费**：目前仅用于日志（`engine.py:204–212`）。
- **DAG 渲染回退路径与正常路径并存**：坏 commit 时 fall back 到 legacy（`engine.py:218–220`）。
- **核心入口无覆盖测试**：`process_user_turn` / `agent_loop` 若干路径标注「⚠️ no covering tests found」。
- **worker 线程 ContextVar 不可靠**：`subagent.started/ended` 因此由 session 显式传参（`runner.py:112`）；`message_branch._resolve_parent` 为此加了 head 回退。
- **`ContextEngine.after_turn` 有两层**：抽象基类桩 `engine.py:124` 为 `pass`；**具体引擎实现 `engine.py:437` 才是真正在干活的**（usage 回灌 + 发 compaction_recommended），由 `finalize.py:167` 调用。

---

## 主线锚点速查

dispatcher 入口 `dispatcher/__init__.py:97`；turn_id 绑定 `:371`；历史/分支解析 `:186–198`；user 节点写入 `:293`；**调模型前 prepare/auto-compact** `_run_loop_blocking :859/:870/:1048`；finalize `:686`/`finalize.py:34`（ContextCommit 回填 `:142`、after_turn `:167`→`engine.py:437`）。事件总线 `event_bus.py:141/159/241`；tool.before 拦截 `agent_loop.py:581–587` + `tool_gate.py:53`。上下文 `engine.py:194` + 两条压缩路径（auto-compact `_run_loop_blocking:870` / microcompact `microcompact.py:76`）。agent loop `agent_loop.py:114/205/540`；子 agent `sub_agent_run.py:41`。协作 `message_branch.py:186/388`，深度上限 `:35`。
