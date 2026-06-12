# User input requests — research notes (2026-06)

Raw multi-agent research output backing [user-input-requests.md](user-input-requests.md).
Two reports: OpenProgram current-state audit, human-in-the-loop reference study (Claude Code / opencode / openclaw / MCP elicitation).

---

# OpenProgram「运行中途等待用户输入」现状审计报告

## 0. 结论速览

框架里**已经存在三套相关机制**，但没有一条在当前主执行路径（统一 dispatcher 聊天路径）上端到端工作：

| 机制 | 位置 | 状态 |
|---|---|---|
| `ask_user` / `set_ask_user` / `FollowUp`（函数中途提问原语） | `openprogram/functions/agentics/ask_user/__init__.py` | 原语完整、DAG 占位完整；但 worker 内**无人注册 handler**，且 agentic 子进程边界单向，实际返回 `None` |
| webui follow-up round-trip（`follow_up_question` 包络 + WS `follow_up_answer` action + 前端渲染） | `webui/server.py:234-270`、`ws_actions/session.py:499-507`、`web/lib/runtime-bridge/chat-handlers.ts:429,622-672` | 三段都在，但发起端 `_web_follow_up` 在 `/run` 路径被砍（commit `b39347fb`）后**成为死代码，零调用方** |
| 工具审批门 `approval_request`/`ApprovalRegistry` | `openprogram/agent/_approval.py` | 后端等待机制完整且在 dispatcher 上接线（`dispatcher/__init__.py:662`），但 `resolve()` **只有测试调用**（`tests/unit/test_dispatcher_tools.py:438,490`），web/TUI 均无响应 UI，permission_mode 默认 bypass 绕过 |

也就是说：**"暂停-提问-等待-恢复"的后端骨架（阻塞队列、WS action、前端 handler、DAG awaiting 占位、stop 解锁）全部已有**，缺的是三处接线：(1) worker 进程注册全局 ask_user handler；(2) agentic 子进程的"答案回传"通道（当前 mp.Queue 只有 child→parent 单向）；(3) 前端 React 化的提问 UI（现存的是 legacy DOM 注入，依赖只在 streaming 时存在的 `#runtime_pending` 元素）。

---

## 1. 现状：已有机制逐个盘点

### 1.1 `ask_user` 原语（最核心资产）

`openprogram/functions/agentics/ask_user/__init__.py`：

- `set_ask_user(handler)`（L32-41）：模块级全局 handler，线程安全（`_ask_user_lock`），进程内唯一。
- `ask_user(question)`（L64-100）：查找顺序 = 全局 handler → TTY `input()` → 返回 `None`。
- **DAG 集成已做完**（L108-155）：调用入口即向 GraphStore append 一个 `role=USER` 的占位 Call（`metadata.status="awaiting"`，`called_by` 指向 enclosing @agentic_function），handler 返回后 update 为 `answered/unanswered`。即"问题作为 DAG 节点持久化"这一层已存在。
- `FollowUp` + `run_with_follow_up(func, ...)`（L162-256）：把阻塞式 ask_user 转成非阻塞返回值——函数在后台线程跑，调 `ask_user` 那刻调用方拿到 `FollowUp` 对象，`.answer(text)` 恢复执行（局部变量、调用栈、Context 树全保留）。**生产路径上无人使用**。
- `has_ask_user_handler()`（L44-61）：供函数自检"提问有没有地方送"。

### 1.2 `clarify` 内置工具（LLM 可直接调用的提问工具）

`openprogram/functions/tools/clarify/clarify.py:50-76`：`@function(toolset=["core"])`，包装 `ask_user`。**LLM 今天就能调它，但 worker 内无 handler、stdin 非 TTY，永远返回错误串** `"Error: no ask_user handler is registered and stdin is not a TTY..."`（L70-75）。docstring 里"webui does this automatically"已过期。

### 1.3 webui follow-up round-trip（半死）

- 发起端 `_web_follow_up`（`webui/server.py:234-270`）：contextmanager，注册全局 handler——把 `{"type":"follow_up_question", question, function}` 通过 `_broadcast_chat_response` 广播到所有 WS 客户端，然后阻塞在 per-session `queue.Queue`（`_follow_up_queues`，server.py:181-185），300s 超时返回空串。**`grep` 全仓库无调用方**——`/run` 手动执行路径在 commit `b39347fb`（unify @agentic_function path on dispatch_forced_tool_call）中移除后变成孤儿。
- 应答端 `handle_follow_up_answer`（`ws_actions/session.py:499-507`，注册于 ACTIONS L729）：WS action `follow_up_answer` → `_follow_up_queues[session_id].put(answer)`。**活着，可直接复用**。
- stop 解锁：`handle_stop`（`ws_actions/runtime.py:186-192`）会向 pending 队列 put `{"_cancelled": True}`，防止 stop 后卡死等待。`routes/lifecycle.py:52-53` 同样。
- 前端 `handleFollowUpQuestion`（`web/lib/runtime-bridge/chat-handlers.ts:429-432, 622-652`）：收到 `chat_response.data.type=="follow_up_question"` 时往 `#runtime_pending` 块里注入 legacy DOM（黄色 ⚠ 卡片 + input + Submit），`submitFollowUp`（L656-672）经 WS 发回 `follow_up_answer`。问题：`#runtime_pending` 只在 RuntimeBlock streaming 时存在（`web/components/chat/messages/runtime-block.tsx:177`），且这是 DOM 注入不是 React 组件，纯聊天（非 runtime block）turn 中无落点。
- TUI：`cli/src/ws/client.ts:62` 的类型 union 里列了 `'follow_up_question'`，但 `cli/src` 中**没有任何处理逻辑**。

### 1.4 CLI 文件 IPC 版（独立、可用但限 CLI 场景）

`openprogram/agentic_programming/session.py`：`run_with_session(func)` 注册 handler——函数调 `ask_user` 时输出 `{"type":"follow_up","question",...,"session":id}` JSON 到 stdout，然后**轮询 `~/.openprogram/sessions/<id>/answer` 文件**（0.3s 间隔，300s 超时，L82-96）。`openprogram sessions resume <id> "answer"`（`_cli_cmds/sessions.py:8-19`）写 answer 文件恢复。这是为"外部 agent 调 CLI"设计的，与 web 链路无关，但证明了**跨进程文件轮询恢复**模式可行。

### 1.5 工具审批门（approval gate）——结构上最接近"中途等用户"的活机制

`openprogram/agent/_approval.py`：

- `ApprovalRegistry`（L34-67）：进程级 `request_id → threading.Event` 表 + 答案表。
- `wrap_with_approval`（L77-146）：dispatcher 对**每个工具**包一层（`dispatcher/__init__.py:662`），在工具自己的 coroutine 内 gate（注释 L87-92 解释了为什么不能从外面 gate）。`permission_mode=="bypass"` 跳过；`"auto"` 下仅 risky 工具（bash/exec/shell/execute_code/process）和 `_requires_approval` 工具触发；`exit_plan_mode` 永远强制（L104）。
- `await_user_approval`（L149-177）：mint request_id → 注册 Event → `on_event({"type":"approval_request", data:{request_id, session_id, tool, args}})` → `asyncio.to_thread(waiter.wait, 300)` 等待 → 超时即拒。
- **断点**：`approval_registry().resolve(request_id, approved)` 全仓库只有测试调用。web 前端、TUI 都不渲染 `approval_request`（TUI 注释明说："shift+tab cycles the modes that are usable without a separate approval prompt UI"，`cli/src/screens/REPL.tsx:232-236`，permission 只在 bypass/auto 间切换，REPL.tsx:134 默认 bypass）。web 聊天路径的 `_on_dispatcher_event` 只转发 `type=="chat_response"` 包络（`webui/_execute/chat.py:142-144`），`approval_request` 直接丢弃。channels 路径反而会把它原样 broadcast 到 webui WS（`channels/_conversation.py:177-184`），但前端无 handler。spawn 的 sub-agent 因此强制 bypass（`agent/sub_agent_run.py:74-81` 注释说明：否则每个工具挂 300s 然后 denied）。
- web 聊天默认 `permission_from_config(run_cfg, default="bypass")`（`webui/_execute/__init__.py:556`），channels 默认 `"auto"`（`channels/_conversation.py:218`）。

### 1.6 其他相邻机制

- `tool_gate`（`openprogram/agent/tool_gate.py`）：`tool.before` 同步问询点，gate 必须快、**不许等待**，只能 allow/deny——不是用户交互点，但展示了"事件 + 同步判定"的接缝位置（`agent_loop.py:517-531`）。
- steering / follow-up 消息队列（`agent/agent.py:234-272`、`AgentLoopConfig.get_steering_messages/get_follow_up_messages`，`agent/types.py:80-83`）：agent_loop 支持每个工具执行完后注入 steering 消息并跳过剩余工具（`agent_loop.py:619-628`）。**dispatcher 路径没有接**（`_run_loop_blocking` 构造的 `AgentLoopConfig` 不传这两个回调，`dispatcher/__init__.py:803-826`），目前仅 `Agent` 类自用。
- plan mode 的 `exit_plan_mode`：唯一一个"必须等用户表态"的现役流程，走的就是 approval gate（`_approval.py:101-104`），同样受困于无 UI。

---

## 2. 执行模型：一次用户消息的完整链路

### 2.1 Web 路径

```
浏览器 composer（web/components/chat/composer/index.tsx）
  └─ wsSend {action:"chat", text, session_id, ...}        ← 唯一一条全双工 WS (/ws)
worker 进程（FastAPI, webui/server.py:_websocket_handler:1017）
  └─ WS_ACTIONS["chat"] = handle_chat (ws_actions/chat.py:296)
      ├─ 持久化 user msg + 回 chat_ack
      └─ threading.Thread(execute_in_context, daemon=True).start()   (chat.py:549-559)
          └─ run_query (webui/_execute/chat.py:18)
              └─ process_user_turn (agent/dispatcher/__init__.py:96)   ← 同步函数
                  └─ _run_loop_blocking (L614)：新建独立 asyncio loop，
                     loop.run_until_complete(_drain()) (L1003-1008)
                      └─ agent_loop（agent/agent_loop.py）
                          └─ 工具按顺序 await（agent_loop.py:495-552，非并行）
                              ├─ 普通 @function 工具：同步函数体 → run_in_executor
                              │   线程池执行（functions/_runtime.py:807-818），in-process
                              └─ @agentic_function 工具：_wrap_agentic_runtime_block
                                  （dispatcher/runtime_attach.py:57）
                                  └─ run_agentic_in_subprocess（agent/process_runner.py:210）
                                      mp spawn 子进程 + mp.Queue 单向事件桥（child→parent）
```

关键事实：

- **每个 turn = worker 内一个独立线程 + 该线程内一个新建 asyncio loop**。`process_user_turn` 故意做成同步（注释：callable from channel worker threads，dispatcher/__init__.py:102-106）。
- **前后端连接 = 单条 WebSocket `/ws`**（`web/lib/use-ws.ts:325`），全双工。SSE 只用于 provider 登录事件（`web/lib/provider-auth-events.ts`）。无任务轮询；刷新恢复靠 `_running_tasks` 重放（server.py:187-224）。
- **后端任意时刻可推自定义事件**：`_s._broadcast(json)`（任意 envelope）或 `_broadcast_chat_response`（`chat_response` 包络，server.py:968-997）。前端 dispatch 在 `use-ws.ts:83+`（已迁移类型）→ 兜底 legacy `window.handleMessage`。注意 `_broadcast_chat_response` 有 **cancel gag**：session 被 stop 后除 `stopped` 状态帧外全部丢弃（L983-990）——"等待用户输入"的事件如果走这个函数，stop 之后会被吞，行为正确。
- **运行中发新消息**：前端 composer 把发送按钮换成 Stop 并直接 return（`composer/index.tsx:268, 472, 1224`）——同一 session 运行中**不能发新消息**（没有排队，也没有 steering）。后端 `handle_chat` 本身无锁，理论上并发 turn 可被打进来（不同 session 并发是正常路径）。
- **@agentic_function 的子进程隔离是最硬的边界**：`process_runner.py` 用 `mp.get_context("spawn")`（L232，注释解释为何不用 fork：PyTorch/libomp/Cocoa fork 不安全），事件经 `event_queue.put`（child，L149-153）→ parent drain 线程 → `on_event` 转发。**没有 parent→child 的任何通道**。子进程是全新解释器：worker 里 `set_ask_user` 注册的 handler 不存在，stdin 是 devnull → 子进程内 `ask_user()` 必然返回 `None`。

### 2.2 Channel 路径

`channels/_conversation.py:168-233`：channel worker 线程同步调 `process_user_turn(req, on_event=_on_event)`；`_on_event` 把**所有** envelope 原样 `srv._broadcast` 到 webui WS（让 web/TUI 看见 streaming），并按 tool 边界节流编辑 channel 占位消息。回复经 `channels/outbound.send` 推回外部用户（`webui/_execute/chat.py:370-384` 也有 web 侧回推）。channel 没有任何 ask_user/approval 接线；channel 用户的下一条消息永远被当成**新 turn**。

---

## 3. 中断 / 取消现状

- **AgentTool.execute 签名自带 cancel**：`execute(call_id, args, cancel: asyncio.Event | None, on_update)`（`agent/types.py:99-110`，agent_loop.py:552 传入）。
- **前端 Stop 按钮** → WS `{action:"stop"}` → `handle_stop`（`ws_actions/runtime.py:158-223`）：
  1. `mark_cancelled(session_id)`（`webui/_pause_stop.py:71-79`，flag + threading.Event）；
  2. `kill_active_subprocess` —— **SIGKILL 整个进程组**，毫秒级杀掉 agentic 子进程（`process_runner.py:309-329`）；
  3. `kill_active_runtime`（杀 CLI provider 子进程）；
  4. 给 pending follow-up 队列 put 哨兵解锁（L186-192）；
  5. 把 DB 里 `status=running` 的行改成 `cancelled`；广播 `stopped` 状态帧。
- **协作式取消**：`@agentic_function` 与 `Runtime.exec` 每次进入都跑 pre-invocation hooks（`agentic_programming/function.py:753,825`、`runtime.py:713-714`），`_pause_stop._cancel_hook` 在其中抛 `CancelledError`（**BaseException**，函数体 `except Exception` 吞不掉，agent_loop.py:559-578 专门处理）。`check_cancelled()` 供长任务手动埋点。
- **dispatcher cancel 桥**：thread 侧 `cancel_event` → watch 线程 → `call_soon_threadsafe` 翻 asyncio Event（dispatcher/__init__.py:830-841）。
- 还有一套全局 pause/resume（`_pause_stop.py:25-41`），`handle_stop` 里顺手 `resume_execution()` 防止 pause 卡 stop。

对"中途提问"有直接意义的点：**stop 与 pending question 的交互已经被处理过一次**（follow-up 队列哨兵），新机制照抄即可。

---

## 4. 事件系统

`openprogram/agent/event_bus.py`（注意：按要求只读；当前 git status 显示该文件无未提交改动，未提交改动在 web/ 下）：

- **typed Event**（L39-49）：frozen dataclass，`id/ts/type/origin/payload/metadata`，`origin ∈ {user, agent, tool, system, proactive}`；`make_event` 自动从 ContextVar 填 session/turn 关联（L52-93）；`emit_safe` 吞错。
- **EventBus**（L112-211）：进程级单例（`get_event_bus`，双检锁），typed `subscribe(handler, types={...})` + legacy channel `on()`。**fire-and-forget fan-out，无请求-响应原语**；async handler 在无 loop 线程上直接跳过（L165-176）——worker 的 turn 线程各自有自己的 loop，这点对设计有影响。
- 现役 typed 事件类型：`tool.before`（agent_loop.py:518，兼做 tool_gate 的输入）、`tool.after`（L586）、credential 冷却类（docstring 提到 `credential.cooldown`）。
- **AgentEvent**（agent loop 流内事件，`agent/types.py:143-210`）：`agent_start/agent_end/turn_start/turn_end/message_start/message_update/message_end/tool_execution_start/tool_execution_update/tool_execution_end`。
- **WS envelope 类型**（前后端实际协议）：顶层 `chat_ack/chat_response/status/session_reload/session_updated/running_task/running_task_clear/approval_request/browser_result/...`；`chat_response.data.type ∈ {status, stream_event, result, error, tree_update, context_stats, follow_up_question, compaction_failed, ...}`；`stream_event.event.type ∈ {text, thinking, tool_use, tool_result}`（`agent/_event_parsing.py:30-74`）。

**能否新增"等待用户输入"事件并由前端响应？** 协议层完全可以——WS 是全双工的，前端 dispatch 是开放的 switch；后端 `_broadcast`/`_broadcast_chat_response` 随处可调。事件总线本身不提供"等回包"，但 `ApprovalRegistry`（事件单向发出 + 进程级 registry + `threading.Event` 阻塞 + WS action 回填）就是现成的请求-响应模板。真正的难点不在事件层，在**子进程边界**。

---

## 5. 缺口逐层对账

数据要走的路：**函数体 → (子进程边界) → dispatcher/worker → WS → 前端 → (用户) → WS action → worker → (子进程边界) → 函数体恢复**。

| 层 | 已有 | 缺 |
|---|---|---|
| 函数体 API | `ask_user()` / `clarify` 工具 / DAG awaiting 占位 | 无 |
| in-process 工具（@function，executor 线程） | 阻塞等待安全（不堵 asyncio loop） | worker 启动时没人 `set_ask_user` |
| @agentic_function 子进程 | child→parent 事件桥（mp.Queue） | **parent→child 答案通道完全没有**；child 内无 handler |
| worker 等待/路由 | `_follow_up_queues` + `handle_follow_up_answer` + stop 哨兵 | 发起端死了；question 无 id（按 session 路由，单问题假设） |
| WS 协议 | `follow_up_question` 包络、`follow_up_answer` action 都在 | 无 request_id、无超时/重连恢复语义（`_running_tasks` 重放不含 pending question） |
| web 前端 | `handleFollowUpQuestion` + `submitFollowUp` | legacy DOM 注入，落点 `#runtime_pending` 仅 runtime block streaming 时存在；纯聊天 turn 无落点；composer 在 isRunning 时锁死，不能用主输入框作答 |
| TUI | ws client 类型里有 `follow_up_question` | 无处理、无渲染（approval 同样缺 UI） |
| channel | outbound send 可达用户 | inbound 回复=新 turn，无"这是答案"路由；`process_user_turn` 同步阻塞期间 channel 还能收消息但进不来 |

---

## 6. 候选实现路径

### 路径 A（推荐起点）：复活 ask_user 全局链路 + 补子进程双向队列

沿用既有协议，改动最小、与 stop/cancel 交互已验证。

1. **in-process 段（quick win，~30 行）**：worker 启动（或 dispatcher 每 turn 入口）注册全局 ask_user handler——逻辑即 `_web_follow_up` 的 handler 体（广播 `follow_up_question` + 阻塞 `_follow_up_queues[session_id]`），但改为常驻而非 contextmanager，并带上 request_id。这一步就让 LLM 调 `clarify` 在 web 聊天里直接活过来（clarify 是 in-process @function 工具，在 executor 线程阻塞不影响 loop）。
2. **子进程段**：`process_runner.run_agentic_in_subprocess` 增加第二条 `mp.Queue`（parent→child，answer queue）；`_child_entry` 里 `set_ask_user(handler)`，handler = 把 `follow_up_question` envelope put 进现有 event_queue → 阻塞 `answer_queue.get(timeout)`；parent 的 drain 线程识别该 envelope 注册 pending 后照常转发；`handle_follow_up_answer` 路由：先查子进程 pending（per-session registry），再查 in-process 队列。
3. **前端**：把 `follow_up_question` 从 legacy DOM 注入改为 React——消息流里渲染一个提问卡片（或复用 composer：isRunning 且有 pendingQuestion 时解锁输入框、把输入发成 `follow_up_answer` 而非 `chat`）。
4. **TUI/channel（可后置）**：TUI 在 useWsEvents 加一个分支渲染 + 输入；channel 把"session 有 pending question 时收到的下一条 inbound 消息"路由成答案而不是新 turn。

改动面：`process_runner.py`、`runtime_attach.py`（透传）、`webui/server.py`（handler 注册 + pending registry 加 request_id）、`ws_actions/session.py`（路由）、前端 1-2 个组件。风险：并发多问题（一 turn 多工具/并行 session）需要 request_id 而非 session_id 做 key；超时语义（300s 后函数拿空串继续 vs 失败）；mp.Queue 在 SIGKILL 时的清理（stop 哨兵模式照搬）。

### 路径 B：统一为"用户输入请求"事件 + UserInputRegistry（approval 与 ask_user 合流）

仿 `_approval.py` 建一个泛化的 `UserInputRegistry`：`request(kind="question"|"approval"|"choice", payload, timeout)` → 发 `user_input_request` envelope（同时 `emit_safe("user_input.request", ...)` 进 typed bus 供 proactive 层观察）→ 阻塞 threading.Event → WS action `user_input_response` 按 request_id resolve。approval gate 改为该 registry 的一个 kind，顺便把现在"approval 永远等满 300s 被拒"的死分支修活，TUI 的 ask 模式也才有意义。

优点：一个协议管 approval / question / plan-approve / 未来的 choice 菜单；事件总线留观察钩子。缺点：改动面大（_approval.py 迁移 + 前端两种 UI + TUI），且**子进程边界问题与路径 A 完全相同**（子进程里的 registry 是另一个进程的，仍需 queue 桥）——B 不替代 A 的第 2 步，只是把 A 的第 1、3 步做得更通用。适合作为 A 之后的归并重构。

### 路径 C：pending-question 持久化为 DAG/消息节点 + "回复即答案"

不走内存 registry，把问题写成 SessionDB 消息行（ask_user 已经写 `metadata.status="awaiting"` 的 DAG 节点，`ask_user/__init__.py:108-137`，只差 broadcast 和消息流渲染）；函数侧用 `agentic_programming/session.py` 的文件/DB 轮询模式等答案（子进程直接读写同一 SQLite/git store，**天然跨进程**，spawn 边界免费解决）；用户在前端正常发消息，`handle_chat` 检测"该 session 有 awaiting 节点"时把消息写成答案节点而不是新 turn——channel 也自动获得同语义。

优点：跨进程、跨重启可恢复（worker 重启后 pending question 还在，`_running_tasks` 重放修不到的场景它能修）；channel 免费支持；composer 不需要特殊模式。缺点：轮询延迟（0.3-1s 级）；"回复即答案 vs 新 turn"的判定是产品语义坑（用户想中途说别的怎么办）；阻塞期间 stop 的清理要自己做（把 awaiting 节点翻成 cancelled——handle_stop 已有把 running 行翻 cancelled 的先例，runtime.py:197-217）。

### 推荐组合

A 为主干（机制对、改动小、复用率最高），其中持久化部分吸收 C（question 同时落 DAG awaiting 节点——本来就落了——并在 answer 后 update，保证刷新/重启可恢复）；B 作为后续重构方向，把 approval 死分支一并救活。无论哪条路，**子进程 answer 通道（mp.Queue 反向）是必做且独立的一块**，可以先单独落。
---

# Human-in-the-Loop 机制调研报告：Claude Code / opencode / openclaw / MCP elicitation → OpenProgram 设计建议

调研对象均在本地 `references/` 下。每节按统一框架展开：(a) 触发方、(b) 控制流（挂起/恢复）、(c) 传输协议、(d) UI 形态、(e) 状态持久性。

---

## 1. Claude Code（泄露版，`references/claude-code-leaked/src`）

### 1a. 触发方：框架拦截为主，工具可"借道"

- 框架拦截：`query.ts:1382` 的 `runTools(toolUseBlocks, assistantMessages, canUseTool, toolUseContext)` 在每个 tool 执行前调用 `canUseTool`（类型 `CanUseToolFn`，定义于 `hooks/useCanUseTool.tsx:27`）。工具的 `call()` 签名也接收 `canUseTool`（`Tool.ts:380-385`），子代理（AgentTool）借此把许可检查递归传下去。
- 工具借道：工具可以通过 `checkPermissions()` 永远返回 `behavior: 'ask'` 来强制弹对话框。**`AskUserQuestionTool` 正是这么做的**（`tools/AskUserQuestionTool/AskUserQuestionTool.tsx:181-187`）：提问工具完全复用 permission 管道，对话框收集到的答案通过 `onAllow(updatedInput)` 注入回工具输入（schema 里专门留了 `answers` 字段，见同文件 `commonFields`，注释 "User answers collected by the permission component"），随后 `call()` 只是把 answers 原样回显为 tool result。

### 1b. 控制流：进程内 Promise 挂起 + 回调队列 + 多 racer claim-once

核心在 `hooks/useCanUseTool.tsx:32`：`canUseTool` 返回 `new Promise(resolve => ...)`，整条 tool 执行链 await 在这个 Promise 上。流程：

1. `hasPermissionsToUseTool`（配置/规则判定）→ `allow` 直接 resolve、`deny` resolve 拒绝；
2. `ask` → `handleInteractivePermission(params, resolve)`（`hooks/toolPermission/handlers/interactiveHandler.ts:58`）。该函数**不返回 Promise**——它把一个 `ToolUseConfirm` 对象 push 进 React state 队列（`ctx.pushToQueue`，对应 `REPL.tsx:1101` 的 `toolUseConfirmQueue` state），对象上带 `onAllow / onReject / onAbort / recheckPermission` 回调，全部包着外层的 `resolve`（`interactiveHandler.ts:93-232`）；
3. REPL 渲染队首为 `PermissionRequest` 对话框（`components/permissions/PermissionRequest.tsx:103-125` 定义 `ToolUseConfirm` 类型）；用户选择 → 回调 → `resolve(decision)` → tool 执行点恢复。

关键工程细节：`createResolveOnce` + `claim()` 原子标记（`interactiveHandler.ts:71`），因为同一个请求有**多个并发 racer**，先到先得：

- 本地 TUI 对话框；
- PermissionRequest hooks 与 bash classifier（后台异步跑，赢了就自动放行）；
- **bridge**（claude.ai/CCR 远程 UI）：`bridgeCallbacks.sendRequest(bridgeRequestId, ...)` 发出，`onResponse` 订阅回包，远端先答则本地撤框（`interactiveHandler.ts:240-295`）；
- **channel relay**（Telegram/iMessage 等）：向每个 channel MCP client 发 `CHANNEL_PERMISSION_REQUEST_METHOD` 通知，用户在手机上回 "yes abc123"，在 notification handler 里被拦截消费、不进对话（`interactiveHandler.ts:300-410`）。

### 1c. 传输协议

- 交互模式：纯进程内（React state + Promise resolve）。
- SDK / headless 模式（即 **Claude Agent SDK 的 canUseTool 回调**的底层）：`cli/structuredIO.ts`。CLI 把许可请求序列化为 NDJSON 的 `control_request`（`subtype: 'can_use_tool'`，带 `tool_name / input / permission_suggestions / blocked_path / decision_reason / tool_use_id`，`structuredIO.ts:588-600`）写到 stdout；宿主（SDK 进程，如 VS Code）回 `control_response`；CLI 端用 `pendingRequests: Map<requestId, {resolve, reject}>`（`structuredIO.ts:137,510-530`）挂起，支持 `control_cancel_request` 取消。`createCanUseTool`（`structuredIO.ts:533`）里 SDK 请求与 hooks `Promise.race`，谁先决定谁赢。所以 SDK 的 `canUseTool` 回调 = 跨进程版的同一个 Promise 挂起协议。
- 重复投递防御：`resolvedToolUseIds` 有界 Set 去重迟到的 `control_response`（`structuredIO.ts:133-155`）。

### 1d. UI 形态

- Permission：每类工具有专用渲染组件（`components/permissions/` 下 20+ 个），通用三选（allow once / allow always+规则 / reject+自由文本 feedback），feedback 会变成拒绝的 tool_result 文本（`onReject(feedback, contentBlocks)`）。
- **AskUserQuestion**：1-4 个问题，每题 2-4 个选项（`label/description/preview`），`multiSelect`，header chip ≤12 字符，自动追加 "Other" 自由输入项，用户还可加 notes/annotations；结果以 `"问题"="答案"` 拼接为 tool_result 文本（`AskUserQuestionTool.tsx:225-240`）。
- 无超时——一直等；Esc/abort 走 `onAbort` → 合成"用户中断"拒绝。
- 值得注意的防挂死设计：`requiresUserInteraction()` 工具（AskUserQuestion/ExitPlanMode/ReviewArtifact）在 channels 模式下直接 `isEnabled() === false`（`AskUserQuestionTool.tsx:138-145`）——人不在键盘前时干脆不给模型这个工具，而不是让它挂 30 分钟。

### 1e. 状态持久性

- 本地：pending Promise 不持久。进程死亡后，transcript 里没有 result 的 tool_use 在 resume 时合成 `[Request interrupted by user for tool use]`（`utils/messages.ts:207-209`），会话能继续但问题作废。
- 远程：会话状态机 `idle | running | requires_action`，`requires_action` 时把 `pending_action`（tool_name、description、tool_use_id、request_id、原始 input）写进 session 的 `external_metadata`（`utils/sessionState.ts:1-44`），供 CCR/移动端**重连后重绘 pending 问题**、驱动 push notification。这是"等待中状态可恢复展示"的关键做法——持久化的是请求快照，不是执行栈。

---

## 2. opencode（`references/opencode/packages/opencode/src`）

### 2a. 触发方：工具内部主动 ask

与 Claude Code 相反，opencode 的许可由**工具自己**在 `execute` 内调用 `ctx.ask({permission, patterns, always, metadata})`（接口 `tool/tool.ts:43`；调用点如 `tool/shell.ts:267-287`、`tool/read.ts:227`、`tool/webfetch.ts:39`）。`session/tools.ts:64-73` 把 `ctx.ask` 绑到 `Permission.ask`，并合并 agent 与 session 的 ruleset。框架层也有一处拦截：doom-loop 检测（同一工具同参数连刷 N 次）强制 ask（`session/processor.ts:441-448`）。

### 2b. 控制流：服务端 Effect Deferred 挂起

`permission/index.ts:171-211`：`Permission.ask` 先对 patterns 逐个 `evaluate`（deny → 抛 `DeniedError`；全 allow → 直接返回）；需要问则 `Deferred.make()` 存入 `pending: Map<PermissionID, {info, deferred}>`，`bus.publish(Event.Asked, info)`，然后 `Deferred.await(deferred)` —— **工具 fiber 原地挂起**。

`Permission.reply`（`permission/index.ts:213-269`）三态 `once | always | reject`：

- `reject` → `Deferred.fail(RejectedError)`（带用户 feedback 时是 `CorrectedError`，message 会作为 tool error 文本回给模型，`permission/index.ts:87-93`），并**级联拒绝同 session 所有 pending**；
- `always` → 把 `always` patterns 追加进 approved 规则，并自动放行其他已满足规则的 pending。

### 2c. 传输协议：SSE 事件下行 + REST 回复上行

服务端是 TS（Bun）HTTP server；客户端（TUI——当前仓库已是 TS/SolidJS 实现，旧版为 Go，**协议形态相同**：独立进程经 HTTP 连 server）：

- 下行：`GET /event`（SSE，`server/routes/instance/httpapi/public.ts:155-163`）广播 Bus 事件 `permission.asked / permission.replied / question.asked / question.replied / question.rejected`；TUI 订阅后维护本地 pending store（`cli/cmd/tui/context/sync.tsx:138-211`）。
- 上行：`POST /permission/:requestID/reply`（`groups/permission.ts`、`handlers/permission.ts:16-37`），`POST /question/:requestID/reply | /reject`（`groups/question.ts`）。
- 关键补充：`GET /permission`、`GET /question` **list 端点**列出全部 pending（`handlers/question.ts:12-14`）——新连上/重连的客户端靠它补齐当前等待项，不依赖错过的事件。

### 2d. UI 形态 + 通用提问工具

opencode 有独立于 permission 的**通用 Question 服务 + question 工具**：

- 服务 `question/index.ts`：`Question.ask({sessionID, questions, tool}) -> Effect<Answer[]>`，同样的 Deferred/pending-Map/bus 模式；`reply(answers: string[][])` / `reject`。
- 工具 `tool/question.ts`：参数为 `questions[]`（每题 `question / header(≤30 字符) / options[{label, description}] / multiple`），输出文本与 Claude Code 几乎逐字一致："User has answered your questions: ... You can now continue with the user's answers in mind."
- TUI 表单（`cli/cmd/tui/routes/session/question.tsx`）：单选/多选、`custom !== false` 时尾部追加自由输入 "other" 项（`question.tsx:38-40`）、Esc = reject（`question.tsx:150`）。Permission UI 是 once/always/reject 三键。
- 无超时，一直等。

### 2e. 状态持久性

pending 全在内存（`InstanceState`）；instance dispose 时 finalizer 把所有 pending `Deferred.fail(RejectedError)` 清场（`permission/index.ts:158-165`、`question/index.ts:142-149`）。持久的只有 approved 规则（DB `PermissionTable`，`permission/index.ts:150-152`）。server 重启 = 挂起的工具 fiber 死、问题作废；会话本身可恢复，但不带 pending 问题。

---

## 3. openclaw（`references/openclaw/src`）——与 OpenProgram 架构最像

### 3a. 触发方：exec 工具执行前的策略检查（框架内置于工具实现）

bash/exec 工具执行前算 `requiresExecApproval({ask, security, analysisOk, allowlistSatisfied, durableApprovalSatisfied})`（`src/agents/bash-tools.exec-host-gateway.ts:484-496`；策略函数 `src/infra/exec-approvals.ts:997`）。策略维度：`security = deny | allowlist | full`，`ask = off | on-miss | always`（`exec-approvals.ts:24-25`），另有 heredoc / inline-eval / 安全审计抑制等强制审批分支。没有通用"向用户提问"工具——openclaw 是消息驱动框架，agent 直接发一条消息、用户的下一条消息就是新 turn，提问天然走对话回路。

### 3b. 控制流：gateway 内 Promise + 定时过期；**两种等待模式**（这是 openclaw 最独特的设计）

- 挂起核心 `src/gateway/exec-approval-manager.ts`：`register(record, timeoutMs)` 创建 Promise 存入 `pending: Map`，附 `setTimeout` 到期 `expire()` → resolve(null)（`exec-approval-manager.ts:74-106, 143-162`）；`resolve(id, decision)` 由用户决定触发。`consumeAllowOnce` 原子消费一次性批准、防 15s 宽限窗内重放（`:175-189`）。
- **inline 模式**：turn 来自内部 channel（本地 TUI/Web 控制台）时，工具内 `await` 决定后继续/拒绝（`bash-tools.exec-host-gateway.ts:352-360, 671-689`）。
- **非阻塞 pending 模式**：turn 来自外部聊天渠道（Telegram 等）时，工具**不等**——立即返回 `pendingResult`（"approval pending, id=..."）结束本 turn；同时起一个 detached async 闭包后台 `await` 决定，批准后在后台 `runExecProcess` 执行命令，把结果用 `sendExecApprovalFollowupResult` 作为**后续消息注入会话**、触发新 turn（`bash-tools.exec-host-gateway.ts:692-792`）。即：渠道场景下"挂起恢复"被改写成"先返回 pending、批准后结果回灌"，模型不持锁等待。

### 3c. 传输协议

- gateway WebSocket JSON-RPC：方法 `exec.approval.request / waitDecision / resolve / get / list`（`src/gateway/server-aux-methods.ts:2-6`），广播事件 `exec.approval.requested / resolved`（scope 限 `approvals`，`src/gateway/server-broadcast.ts:27-28`）。
- sandbox/node 远端执行经 unix socket `~/.openclaw/exec-approvals.sock` + token 问 gateway（`exec-approvals.ts:217-223`）。
- iOS 推送走 APNS（`src/gateway/exec-approval-ios-push.ts`）。

### 3d. UI 形态：按钮即命令

渠道消息带原生按钮 **Allow Once / Allow Always / Deny**（style success/primary/danger），按钮的 value 就是文本命令 `/approve <id> <decision>`（`src/infra/exec-approval-reply.ts:106-180`）——所以**纯文本渠道用户手打同一条命令也能批**，按钮只是糖。无按钮渠道还有降级文案指引去 Web UI/TUI 批（`exec-approval-reply.ts:80-88`）。多 surface（Web/TUI/macOS app/各渠道）同时收到，先答先得。

### 3e. 状态持久性与超时

- 超时默认 **30 分钟**（`DEFAULT_EXEC_APPROVAL_TIMEOUT_MS = 1_800_000`，`exec-approvals.ts:203`）；超时 decision=null → `askFallback`（默认 `full`=放行，可配 deny/allowlist 二次判定，`bash-tools.exec-host-gateway.ts:620-628`）。**超时必须有明确 fallback 语义**是 openclaw 的教训式设计。
- pending 在 gateway 内存，重启丢失；持久的是 allowlist / durable approvals（`~/.openclaw/exec-approvals.json`，`exec-approvals.ts:684-744`）。已 resolve 条目保留 15s 宽限供迟到的 `waitDecision`（`exec-approval-manager.ts:9`）。

---

## 4. MCP elicitation 协议（2025-06-18 spec）

行业标准形态，值得对齐：

- 方向与方法：**server → client** 发 `elicitation/create`，params 为 `{message: string, requestedSchema: JSONSchema}`；client 需在 initialize 时声明 `capabilities.elicitation`。
- schema 约束：**只允许 flat object + 原始类型**——string（minLength/maxLength/format∈{email,uri,date,date-time}）、number/integer（min/max）、boolean（default）、enum（`enum` + 显示名 `enumNames`），外加 `required`。明确禁止嵌套对象/对象数组，为的是 client 能机械地渲染成表单。
- 响应三态：`action: "accept"`（带 `content`，匹配 schema）/ `"decline"`（用户明确拒绝）/ `"cancel"`（关窗、Esc 等未做选择的消散）。spec 要求 server 对三态分别处理（decline → 给替代方案；cancel → 可稍后重问）。
- 安全要求：不得用 elicitation 要敏感信息；UI 必须标明是哪个 server 在问；用户可随时 decline。
- 旁证：Claude Code 自己就实现了 elicitation 客户端（`services/mcp/elicitationHandler.ts`、`components/mcp/ElicitationDialog.tsx`，REPL 里 `elicitation.queue` 与 permission 队列并列，`screens/REPL.tsx:1689`），还扩展了 `mode: 'url'` 变体。

**decline 与 cancel 分开、答案受 schema 约束**，是四家里最干净的协议形态。

---

## 5. OpenProgram 现状（设计起点）

OpenProgram 已有一个雏形，问题在于它停在"单字符串问答"且有并发缺陷：

- `openprogram/functions/agentics/ask_user/__init__.py`：`ask_user(question) -> Optional[str]`；handler 经 `set_ask_user(handler)` 注册到**模块级全局变量**（带锁），fallback 到 TTY `input()`；DAG 记账完整——入口 append 一个 user-role Call 占位（`metadata.status="awaiting"`），答完 update 为 `answered/unanswered`（`_begin/_finish_ask_user_node`）。还有 `FollowUp` / `run_with_follow_up` 非阻塞包装（threading queue，函数线程卡在 ask_user 里、局部状态全保留）。
- WebUI handler：`openprogram/webui/server.py:182-260` `_web_follow_up` —— 问题以 `follow_up_question` 帧走 WS broadcast，handler 阻塞在 per-session `queue.Queue`，**超时硬编码 300s**。
- 注册点只有 webui（`server.py:264`）和 CLI session（`agentic_programming/session.py:154-177`）；**channels（telegram/discord/...）完全没接**。
- 已知缺陷：handler 是全局单例，两个会话并发执行时后注册者覆盖前者（`_web_follow_up` 的 closure 绑定了 session_id，但 `set_ask_user` 槽位只有一个）；问题不落库，刷新/重连后 pending 问题无处可查（对比 opencode 的 list 端点和 claude-code 的 `pending_action` metadata）；只支持自由文本，没有 options/confirm/三态。

基础设施方面：worker 单进程、函数跑在线程里、已有 WS broadcast（`webui/messages.py:231` 附近的 listener 机制）、FastAPI REST、SessionDB（SQL）、cooperative pause/cancel（`webui/_pause_stop.py`）。这与 opencode 的"server 内 Deferred + 事件下行 + REST 上行"一一同构。

---

## 6. 对 OpenProgram 的形态建议

### 6.1 五家机制速览

| | 触发方 | 挂起实现 | 传输 | UI | 持久性/超时 |
|---|---|---|---|---|---|
| Claude Code | 框架拦截（工具可借 `behavior:'ask'`） | 进程内 Promise + 回调队列 + claim-once 多 racer | 进程内；SDK 模式 NDJSON `control_request/response` | 选项+Other+notes；permission 三选 | 不持久；远端写 `pending_action` metadata 供重绘；无超时 |
| opencode | 工具内 `ctx.ask` | server 端 Effect Deferred + pending Map | SSE `/event` 下行 + REST reply 上行 + list 端点 | once/always/reject；question 工具单/多选+custom | 内存，dispose 全拒；规则入库；无超时 |
| openclaw | exec 前策略检查 | gateway Promise + 定时过期；渠道场景**非阻塞 pending + 结果回灌** | WS JSON-RPC + 广播 + unix socket + APNS | 渠道按钮=文本命令 `/approve id decision` | 内存；30min 超时 + askFallback；allowlist 入库 |
| MCP elicitation | server（工具/任意点）主动 | client 实现自由 | JSON-RPC `elicitation/create` | schema 约束的 flat 表单 | accept/decline/cancel 三态 |
| OpenProgram 现状 | 函数内 `ask_user()` | 线程阻塞 queue.Queue | WS 帧 + queue | 自由文本 | 不落库；300s 静默超时；全局 handler 并发缺陷 |

### 6.2 API 草案

OpenProgram 的定位（函数是用户写的 Python，运行中需要问人）决定了正确形态是 **opencode 的"工具内主动 ask"+ MCP 的三态/选项约束**，而不是 claude-code 的框架拦截（那是 per-tool permission 问题，OpenProgram 没有不可信工具层）。建议挂在已有的 `runtime` 上（与 `runtime.exec` 同级，`openprogram/agentic_programming/runtime.py:607`）：

```python
# 在 @agentic_function 内
answer = runtime.ask(
    "用哪个库做日期格式化？",
    options=["dayjs", "date-fns", "luxon"],   # 可选；None = 纯自由文本
    multi=False,                               # 多选 → 返回 list[str]
    allow_custom=True,                         # 选项之外允许自由输入（对齐 opencode custom / CC "Other"）
    timeout_s=1800,                            # 默认 30min（学 openclaw），可配
    default=None,                              # 超时返回值；None + 无 default → 抛 AskTimeout
)
# 返回 str（或 multi 时 list[str]）；用户明确拒绝 → 抛 UserDeclined
# （decline 抛异常、timeout 走 default，对应 MCP 的三态拆分）

ok = runtime.confirm("要把 87 封邮件全部归档吗？", detail=preview_text,
                     timeout_s=600, default=False)   # -> bool，超时即 default，永不抛

# 进阶（可后置）：MCP elicitation 对齐的 flat 表单
data = runtime.form("填写部署参数", fields={
    "region":   {"type": "string", "enum": ["us-east", "ap-sg"], "title": "区域"},
    "replicas": {"type": "integer", "minimum": 1, "maximum": 10, "default": 2},
})  # -> dict，schema 限 flat object + 原始类型，将来暴露成 MCP server 时直接映射 elicitation/create
```

`ask_user(question)` 保留为 `runtime.ask(question)` 的薄别名，向后兼容。

### 6.3 实现要点（按依赖顺序）

1. **内核：per-session pending registry，替换全局 `set_ask_user` 槽位。** worker 进程内 `PendingQuestion {id(ULID), session_id, prompt, options, multi, allow_custom, created_at, expires_at}` + per-request `threading.Event`/queue（函数已跑在线程里，opencode 的 Deferred 在这里就是一个 Event）。修掉现状的并发覆盖缺陷。
2. **协议：事件下行 + REST 上行 + list 补齐**（照抄 opencode 的三件套，OpenProgram 基建完全对应）：WS 广播 `question.asked / question.replied / question.rejected`；`POST /api/questions/{id}/reply {answers}`、`POST /api/questions/{id}/reject`；`GET /api/questions?session_id=` 给刷新/重连的前端补 pending——这是现状缺的关键一块（claude-code 用 `pending_action` metadata、opencode 用 list 端点解决同一问题）。
3. **三态语义**：answered（带答案恢复执行）/ declined（用户点拒绝 → 函数内抛 `UserDeclined`，函数作者可 catch 走备选路径——对齐 opencode `RejectedError` 进 tool result 和 MCP decline）/ timeout（默认 30min，到期走 `default`，无 default 抛 `AskTimeout`）。**禁止现状的"300s 静默返回 None"**——超时必须显式可见（openclaw 的 askFallback 教训）。
4. **多 surface 竞争 claim-once**：web 与 channels 同时收到 `question.asked`，先答者赢；registry 的 resolve 做原子 check-and-mark（claude-code `createResolveOnce` / openclaw `claim()`），并广播 `question.replied` 让其他 surface 撤 UI。
5. **channels 接入，按钮即命令**：telegram/discord 渲染 inline buttons，按钮 value 是 `/answer <id> <label>`；纯文本渠道（wechat）直接提示用户回 `1` / `2` / 自由文本。openclaw 验证过这是渠道兼容性最好的形态（`exec-approval-reply.ts` 的 descriptors 模式可直接借鉴）。
6. **持久性：持久化请求快照，不持久化执行栈。** pending question 写 SessionDB（DAG 已有 awaiting 节点，再加一张 pending 表或复用节点查询即可）；worker 重启后函数线程已死，启动时把遗留 pending 标记 expired、DAG 节点置 `unanswered`。**不要做"重启后从 ask 处恢复执行"**——那需要 durable execution（序列化 Python 局部状态），四家全都没做，claude-code/opencode/openclaw 一致选择"等待态内存化 + 快照可查 + 死了作废"，性价比定论已经很清楚。
7. **渠道长等待的替代形态（二期）**：对 channels 发起的执行，借鉴 openclaw 的非阻塞模式——OpenProgram 已有同构载体 `FollowUp/run_with_follow_up`：channel worker 收到 `FollowUp` 时结束本条回复（"我有个问题：…"），用户下一条消息路由回 `followup.answer(text)` 恢复函数。这比让函数线程挂 30 分钟更适合异步渠道。
8. **headless 防呆**：保留并推广 `has_ask_user_handler()`（`ask_user/__init__.py:43-61`）→ `runtime.can_ask()`，无人可问时函数作者可跳过交互步骤——对应 claude-code 在 channels 模式下直接禁用 AskUserQuestion 工具的思路。

### 6.4 为什么是这个形态

- **挂起用进程内阻塞原语**（Event/queue）而非任何花哨的 generator/协程改造：OpenProgram 函数本来就在线程里跑，claude-code（Promise）、opencode（Deferred）、openclaw（Promise）证明"执行点天然 await/block，UI 侧回调 resolve"就是全部所需。
- **`ask`（开放/选项问答）和 `confirm`（布尔审批）分开**：返回类型和超时语义不同（confirm 超时必须有安全默认值且不抛；ask 超时通常该让函数知道）。openclaw 的 approval 三按钮和 MCP 的三态都说明审批与提问是两个收敛形态。
- **对齐 MCP elicitation 的 schema 约束与三态**：flat-object 表单是渲染成本和表达力的最佳平衡点，且未来 OpenProgram 函数暴露为 MCP server 时 `runtime.form` 可零损耗映射 `elicitation/create`。
- **复用现有 WS+REST+SessionDB**，不引入新传输层：opencode 证明 SSE/WS 下行 + REST 上行 + list 端点这三件套足以支撑独立进程客户端（连 Go TUI 时代也是同一协议）。

### 关键文件索引

Claude Code：`hooks/useCanUseTool.tsx`、`hooks/toolPermission/handlers/interactiveHandler.ts`、`tools/AskUserQuestionTool/AskUserQuestionTool.tsx`、`cli/structuredIO.ts`、`utils/sessionState.ts`。opencode：`permission/index.ts`、`question/index.ts`、`tool/question.ts`、`session/tools.ts`、`server/routes/instance/httpapi/{groups,handlers}/{permission,question}.ts`、`cli/cmd/tui/context/sync.tsx`、`cli/cmd/tui/routes/session/question.tsx`。openclaw：`src/gateway/exec-approval-manager.ts`、`src/agents/bash-tools.exec-host-gateway.ts`、`src/infra/exec-approvals.ts`、`src/infra/exec-approval-reply.ts`、`src/gateway/server-aux-methods.ts`。OpenProgram 现状：`openprogram/functions/agentics/ask_user/__init__.py`、`openprogram/webui/server.py:182-268`、`openprogram/agentic_programming/session.py:154-177`。