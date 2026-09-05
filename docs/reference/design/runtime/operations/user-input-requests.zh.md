# 用户输入请求：暂停运行以向用户提问

> 本文描述运行中的函数如何暂停下来向用户提问、再带着答案继续：API 形态、待答
> 问题的 registry、把问题送出子进程的传输通道，以及答案如何回流。
> 配套文档：[../cli/tui-upgrade.md](../../cli/tui-upgrade.md)（TUI 界面）。

## 问题

某个函数（尤其是 `@agentic_function`）有时需要在运行中途求助用户：确认一个
有破坏性的步骤、在多个备选项之间做选择、补上一个缺失的值。这要求能暂停执行、
把问题呈现到 web/TUI/channels，并带着答案恢复运行。

## 已有机制

代码库里存在三套相关机制，但没有一套在主聊天路径上端到端跑通：

| 机制 | 状态 |
|---|---|
| `ask_user` / `set_ask_user` / `FollowUp`（`openprogram/programs/workflow/ask_user/`） | 原语完整，DAG awaiting-node 簿记完整；但 worker 里没注册 handler，且 agentic 子进程桥是单向的——实际上返回 `None` |
| webui follow-up 往返（`webui/server.py:234-270`、WS `follow_up_answer` action、web `handleFollowUpQuestion`） | 三段都在；发起方 `_web_follow_up` 已经没有调用者——死代码。Web UI 那侧是往 `#runtime_pending` 做的旧式 DOM 注入（只在 runtime 块流式输出期间存在）。TUI 给信封定了类型却从不处理它 |
| 审批门（`openprogram/agent/permissions/approval.py`，已接进 dispatcher） | 等待机制完整且活着，但 `resolve()` 只在测试里被调用；没有 web/TUI UI；默认 `bypass` 把它遮住了；子 agent 强制 bypass 以避免 300s 挂起 |

所以骨架（阻塞队列、WS action、stop 哨兵解除阻塞、DAG awaiting 节点）已经具备。
本设计补上的是 registry 的形态（按请求一份，而非一个全局 handler 槽位）、子进程
答案通道、真正的前端 UI，以及显式的超时语义。

关键约束是：`@agentic_function` 函数体在一个 **spawn 出来的子进程**
（`agent/process_runner.py`）里运行，其 mp.Queue 是 child→parent 单向的。任何
设计都必须加一条 parent→child 的答案队列；再多的 worker 侧接线也绕不开这一点。

## 参考设计（我们借鉴的部分）

- **opencode**：工具调用 `ctx.ask(...)` → 服务端 Deferred + pending map →
  事件 `permission.asked` 下行、REST reply 上行，**外加一个列表 endpoint**，
  让重连的客户端能恢复 pending 问题。Reject 可以带一条消息，成为模型看到的
  tool-error 文本。
- **Claude Code**：AskUserQuestion 搭在权限管线上；选项 + 始终存在的 "Other"
  自由文本；pending 请求的*快照*持久化在 session metadata 里，好让远端 UI
  重绘它（执行栈从不持久化）；当没有人接入时，需要交互的工具被禁用。
- **openclaw**：30 分钟超时，带一个显式回退（绝不静默）；channel 按钮的值是
  一条纯文本命令（`/approve <id> …`），这样纯文本 channel 也能一样工作；对于
  channel 发起的运行，工具立即返回 "pending"，结果稍后再注回（非阻塞模式）。
- **MCP elicitation**：三结果协议——accept / decline / cancel——以及面向
  表单式提问的扁平对象 schema 约束。

这四者都实现了"执行点在一个原语上阻塞，UI 来解决它"——没有
generator/coroutine 那套花活。我们的做法是阻塞一个线程（函数本就跑在
线程/子进程里）。

## API

在 `runtime` 上，紧挨着 `runtime.exec` / `decision`：

```python
# 在任意 @agentic_function / @function 函数体内
answer = runtime.ask(
    "Which library for date formatting?",
    options=["dayjs", "date-fns", "luxon"],  # 可选；None = 自由文本
    multi=False,                # True -> 返回 list[str]
    allow_custom=True,          # 除选项外还允许自由文本
    timeout=1800,               # 秒，默认 30 分钟
    default=None,               # 超时时返回；无 default -> AskTimeout
)
# -> str（或 list[str]）；用户按下 Decline 抛 UserDeclined

ok = runtime.confirm("Archive all 87 emails?", detail=preview,
                     timeout=600, default=False)  # -> bool，超时永不抛异常

runtime.can_ask()  # -> bool；无头运行时为 False，作者可据此分支
```

- `ask_user(question)` 是 `runtime.ask(question)` 的一层薄别名。无全局 handler
  时回退到 `runtime.ask`，UserDeclined/AskTimeout 归一为 `None` 以保持旧语义；
  CLI 的 `set_ask_user` 路径不受影响。
- `clarify` 内置工具（LLM 可调用）走同一条路径。
- 三种显式结果：answered / declined / timeout。不存在 300 秒后静默返回 `None`
  的行为。
- `runtime.form(...)`（MCP-elicitation 风格的扁平 schema）暂缓。

## 机制

1. **Registry**（worker 进程）：`PendingQuestion {id, session_id, kind,
   prompt, options, multi, allow_custom, created_at, expires_at}` + 一个
   按请求一份的 `threading.Event`。它取代全局 `set_ask_user` handler 槽位——
   两个并发 session 会互相覆盖那个槽位。Resolve 是原子的认领一次；
   `handle_stop` 像现有 follow-up 队列那样放入 cancel 哨兵。
2. **协议**：WS 广播 `question.asked / question.replied /
   question.rejected`；REST `GET /api/questions?session_id=` + `POST
   /api/questions/{id}/reply` / `.../reject` 用于重连恢复
   （`webui/routes/questions.py`）。`handle_load_session` 在(重)连时重放
   still-pending 的 `question.asked`。这里复用现有的
   `_broadcast_chat_response` 管路，它在 stop 后的静默正是我们想要的行为。
3. **子进程桥**："提问往哪条通道送"做成 `QuestionTransport`，形态对齐
   Python logging 的 Handler（`publish` 对应 `Handler.emit`）：
   `EventLayerTransport`（默认，事件层→前端卡片+总线，worker 用）与
   `QueueTransport`（经 mp.Queue 送回父进程，子进程用）。通道由 runtime
   显式持有（`runtime._question_transport`），不是模块级全局开关。
   `run_agentic_in_subprocess` 加 parent→child `answer_queue`；`_child_entry`
   给子进程 runtime 装 `QueueTransport`（问题经 event_queue 上行、带
   `__op_question__` 标记）并起 answer-pump 线程（从 answer_queue 取答案
   resolve 子进程本地 registry）。父进程 `_drain` 拦截该 envelope，
   `_bridge_question_to_parent` 在父 registry 注册同一 qid、发前端卡片、
   起 waiter；WS reply 经既有 `_resolve_question` resolve 父 registry，
   waiter 把答案推回 answer_queue。子进程退出或被 stop 时，父侧把残留待答按
   declined 收尾并撤回卡片（claim-once，重复 resolve 无害）。
4. **持久化**：持久化请求快照，而非执行栈。DAG 已经写入
   `status="awaiting"` 的 user 角色节点；worker 重启后，残留的 pending 被标记
   为 expired，DAG 节点标为 `unanswered`。不做持久化执行恢复（四个参考都
   有意跳过它）。
5. **前端**：web 在消息流里得到一张 React 问题卡片（取代旧式 DOM 注入），
   问题待答期间输入框兼作答案框；TUI 把问题渲染在输入槽位里
   （tui-upgrade.md P2）。跨界面以第一个答案为准；`question.replied` 会撤回
   其他界面上的 UI。
6. **审批合流**：`permissions/approval.py` 迁移到同一个 registry，`kind="approval"`，
   给原本无从触达的 `ask` 权限模式一个真 UI，采用 opencode 的 reply 形态
   （allow once / always / reject 并带一条会成为 tool error 文本的反馈）。
7. **Channels**：按钮即文本命令（`/answer <id> <choice>`）；对于 channel
   发起的运行，优先采用非阻塞的 `FollowUp` 形态（回复结束本轮，用户的下一条
   消息恢复该函数），而不是把一个线程攥住 30 分钟。

## 待解问题

- 超时默认值：30 分钟（openclaw）还是为 web 优先的用法取更短。
- 当决策对象是人而非模型时，`decision.make` 是否最终也应路由经同一个
  registry（此处不在范围内，已记入 function-calling unification 文档）。

## 附录：实现状态

已实现：registry、`runtime.ask` / `confirm` / `can_ask`、三种显式结果
（answered / UserDeclined / AskTimeout）、WS question_reply/reject 协议、web
question card，以及 stop 时经 cancel_session 解除待答——分别位于
`agent/questions.py`、`agentic_programming/runtime.py`、
`webui/ws_actions/session.py`、`webui/ws_actions/runtime.py` 与
`apps/web/components/ui/question-prompt.tsx`。

重连恢复已实现。问题卡片只靠活的 `question.asked` 帧驱动，刷新或断线后那帧已
成过去；`handle_load_session` 在(重)连某 session 时把该 session 所有
still-pending 的问题按同一个 `question.asked` 帧重放，前端零改动即可重绘。
REST `GET /api/questions` + `POST /api/questions/{id}/reply|reject`
（`webui/routes/questions.py`）给同一 registry 提供 API 侧对等入口，
reply/reject 走与 WS 同一收口 `_resolve_question`。

子进程桥已实现，`@agentic_function` 函数体因此可以提问：`QuestionTransport`
（EventLayerTransport / QueueTransport）加上 `process_runner` 的 parent↔child
桥（问题经 event_queue 上行、答案经 answer_queue 回流）——分别位于
`agent/questions.py`（三类 transport + `emit_question_asked`）、
`agentic_programming/runtime.py`（`set_question_transport`、`_ask_raw` 走
`self._question_transport`）与 `agent/process_runner.py`（`answer_queue`、
answer-pump、`_bridge_question_to_parent`、`_decline_bridged_question`）。
由 `tests/component/agent/test_questions_subprocess_bridge.py`（8 个单测）与一次真 spawn
子进程的端到端验证覆盖。

尚未落地：TUI 界面（question/approval 提示放在输入槽位里，在 tui-upgrade.md
中跟踪），以及上文描述的审批合流、channels 与 `runtime.form`。
