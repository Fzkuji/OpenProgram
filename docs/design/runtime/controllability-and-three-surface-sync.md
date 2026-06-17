# 长跑 agent 任务的可控性 + 三端一致性

用户需求(原话整理):
1. **值守开关**:无人值守时(我睡觉了)别问我;观察时可以问我。做法已定 —— 无人值守就**不把"询问用户"工具给 agent**,它拿不到就不问,顶多生成点不确定内容,靠后续模型思考自解。
2. **中途干预/切换方向**:任务跑一半发现路线错了,我能插一条新指令让它调整。做法方向已定 —— **基于事件层**,程序时刻监控外部事件,有用户额外消息就注入进去。
3. **中途提问显示**:agent 要问我时,三端都能看到、能答。
4. **三端会话同步**:一端后台跑的会话,CLI/TUI/网页端三端同步显示,不能一端做了另一端看不到。

## 地基盘点(已有,可复用)

| 能力 | 现状 | file:line |
|---|---|---|
| steering 注入点 | agent_loop 已有 3 个检查点(turn 头、每个工具后、follow-up 前)可插消息 | `agent/agent_loop.py:218-310,620-629` |
| 提问框架 | QuestionRegistry(进程级、线程安全、claim-once)+ 两种 Transport(EventLayer / Queue)+ 子进程桥 | `agent/questions.py:34-198`、`agent/process_runner.py` 的 answer_queue/QueueTransport |
| 事件广播 | EventBus + emit_ws_frame:一处 emit,所有 WS 客户端收到 | `agent/event_bus.py:115-125`、`webui/server.py` `_broadcast` |
| 共享存储 | git-backed SessionDB,三端共享同一真值;worker 单例(WorkerLock) | `agent/session_db.py`、`worker/lock.py` |
| 工具目录策略 | apply_tool_policy 有 deny/allow;toolset 分级(default 不含 ask_user_question,full 含) | `functions/__init__.py` apply_tool_policy |
| 取消/优雅停 | cancel_event 全链路 + 子进程 graceful stop IPC(本轮新加) | `agent/process_runner.py` request_graceful_stop |

**关键结论**:三端共享 worker+DB+EventBus,同步根基已成立;steering 和提问的管道都现成。真正的真空是「闸门」和「协调层」。

## 重要发现:值守开关的真正落点

research_harness 的自主 loop **已经事实上是无人值守**:
- `oversight="interactive"` 已把 socratic_plan 等排除出 catalog(`registry.py:357`)。
- 实验子函数用 `toolset="default"`,而 default **不含** `ask_user_question`。

所以"无人值守不给 ask 工具"这条,在 research_harness 自主路径上**已经天然成立**。值守开关真正有意义的是 **OpenProgram 通用 chat/agent 路径**(那里 `full` toolset 含 `ask_user_question` + clarify 工具)。

→ P1 落点 = dispatcher 给 turn 选工具时,按会话级 `attended` 标志决定是否 deny 掉 `ask_user_question` / clarify 类工具。

## 分阶段计划(每步独立可验证、独立交付)

### P1 — 值守开关(attended/unattended)
- **会话级标志** `attended`(默认 attended=True,即可问)。存哪:会话 metadata(SessionDB)+ 内存态,三端可读可改。
- **闸门**:dispatcher 组装 turn 工具时,`attended=False` → `apply_tool_policy(deny=["ask_user_question","clarify",...问询类])`。agent 拿不到工具 = 不会问。
- **三端设开关**:CLI flag(`--unattended`)、TUI 一个快捷键/状态、网页端一个 toggle。三端改的是同一个会话标志(走 worker)。
- **验证**:unattended 下跑一个本会去问的任务,确认 catalog 无 ask 工具、agent 不弹问题、照样产出。

### P2 — 中途干预(事件注入 steering)
- **机制**:把"用户中途消息"做成一个事件,research_agent / agent_loop 在循环检查点(就是 max_runtime_s/stop_event 那两个点)poll 一个**会话级 steering 队列**,有就把消息作为新指令注入下一步的 context / 重新 _pick_stage。
- **入口**:三端发"干预消息" → WS/CLI → 投进该会话的 steering 队列(复用 server 的 `_follow_up_queues` 或新建)。
- **注入语义**:不打断当前 step(优雅),当前 step 完成后,把用户的新指令喂给下一轮 stage 决策("用户中途说:改成 X,据此调整")。
- **复用**:agent_loop 已有 steering_messages 管道;research_agent 循环加一个 `_steering_pending()` 检查,和 `_stop_requested()` 并列。
- **验证**:任务跑到 stage 2 时发"别写实验了,先补文献",确认下一轮 stage 决策吃到了这条、转向了。

### P3 — 三端同步补全
- **running 状态位**:会话加"正在计算"标志(谁在跑、跑到哪),写进可广播的会话状态,三端能看到"这个会话正忙"。
- **新连接回放**:新 WS 客户端 join 时,若该会话有正在 streaming 的 turn,补发已发生的中间事件(或至少发"正在跑+当前阶段")。现在新连接只能看 final。
- **验证**:A 端发起任务,B 端中途连上,B 能看到正在跑 + 进度,不是空白等 final。

### P4 — 提问显示对齐
- CLI / TUI 把 `question.asked` 事件渲染成可答的卡片(网页端已有)。CLI 走 stdin 提示,TUI 走 follow_up 卡片组件。
- **验证**:attended 下 agent 提问,三端都弹得出、答得了,答案回到同一个 registry。

## 跨切面:三端一致性是贯穿原则
P1 的开关、P2 的干预、P4 的提问 —— 状态都存在**会话级**(worker 内 + DB),三端只是这同一状态的不同视图。任何一端的操作走 worker → 广播 → 三端同步。绝不做某端本地、不同步的状态。

## 待用户拍板的设计点
- P1 默认值:attended=True(默认可问)还是 unattended=True(默认别打扰)?
- P2 注入时机:只在 step 边界优雅注入(推荐),还是要支持"立即打断当前 step"?
- 顺序:是否就按 P1→P2→P3→P4。
