# P3 — 三端同步(补全)+ P1/P2 上三端

调研后修正:三端同步的地基比预想完整得多,真空很窄。

## 已有(不用做)

| 能力 | 现状 | file:line |
|---|---|---|
| 单 worker + 共享 git SessionDB | 三端连同一 worker、读同一真值 | worker/lock.py, agent/session_db.py |
| running 状态位 | `_running_tasks` 注册表 + `running_task`/`running_task_clear` 广播(驱动 sidebar 呼吸灯 + composer 状态) | webui/server.py:187-224 |
| 重连补历史 | `handle_sync(session_id, known_seqs)` 把缺的消息帧补发 | ws_actions/runtime.py:436-450 |
| 事件广播 | EventBus + _broadcast,一处 emit 所有 WS 客户端收到 | event_bus.py, server.py `_broadcast` |

→ 一端发起的 turn,其它端**本来就**收到同样的 stream 事件;重连**本来就**补历史 + running 灯。所以"一端做了另一端看不到"在 webui/TUI(都走 worker WS)路径上基本不成立。

## 真空(P3 要做)

### G1 — 重连不补 running_task 状态
`handle_sync` 只补消息帧,不补当前 `running_task` 指示。新连/重连的客户端要等下一次 `_emit_running_task_event` 才看到"正在跑"。
**修**:`handle_sync` 末尾对该 session 调一次 `_emit_running_task_event`(或直接给这个 ws 发当前 running_task 快照)。一行级改动。

### G2 — P1 值守开关上三端(WS action + 会话状态)
attended 现在是进程级标志(CLI 用)。要让 TUI/web 也能切,需:
- WS action `{action:"set_attended", session_id, attended: bool}` → 调 `attended.set_attended` + 广播一个 `attended_changed` 状态帧,三端同步显示当前模式。
- 注意:attended 现在是**进程全局**,不是 per-session。webui 单 worker 多 session 时,全局开关会互相影响。**决策**:P3 把 attended 改成**会话级**(dict[session_id]→bool),CLI 那条传自己的 session。
- 前端:TUI 一个 toggle 键 + 状态显示;web 一个 toggle 按钮。

### G3 — P2 steer 上三端(WS action)
steer 现在只有 CLI 子命令(写文件收件箱)。加:
- WS action `{action:"steer", session_id, message}` → `steering.push(session_id, message)`(已是文件收件箱,worker 写、跑着的进程读)+ 广播一个回执帧。
- 子进程场景:research_agent 在子进程跑,文件收件箱用的是同一个 session_dir,子进程读得到 —— **无需额外 IPC**(文件天然跨进程)。这点比预想简单。
- 前端:TUI/web 一个"发送干预指令"输入框(任务跑着时可用)。

## 顺序
G1(一行)→ G2(attended 改会话级 + WS action + 前端 toggle)→ G3(steer WS action + 前端输入框)。

## 待拍板
- attended 改 per-session:确认要(单 worker 多 session 时全局开关会串)。默认仍 unattended。
- 前端改动:TUI(Ink, cli/src)+ web(web/)两套前端都要加 toggle/输入框。工作量主要在这。先做后端 WS action(三端共享、可测),前端 UI 随后。
