# Proactive Layer 接入代码 — 设计

> Proactive 本身的设计在 [`../proactive/`](../proactive/README.md)。本文只讲那套设计
> 与现有 OpenProgram 代码的交界：复用了哪些机制、事件从哪里发出、gate 挂在哪里、
> 它的强制力实际覆盖到什么程度。有冲突以设计文档为准。

## 1. 代码落点

事件层是 `openprogram/events/bus.py` 的就地升级：`Event`、按事件类型订阅、
进程级单例。taps 加在各个本来就知道「刚才发生了什么」的源文件里，而不是集中到一个
收集器。`openprogram/proactive/` 包装规则层，等第一个规则消费者出现时才建；在那之前
事件层本身已经有用。

事件模型是核心三样加一个开放的 `metadata` 口袋（设计 `event-layer.md` §1）。
`turn` 和 `session` 走 metadata 而不是固定字段，这样没有 turn 概念的源不必硬造一个。

## 2. 复用而非重建的机制

设计里描述的每个角色，代码库里都已经有一份能用的实现：

| 设计中的角色 | 现有机制 | 位置 |
|---|---|---|
| 进程内事件扇出 | `EventBus`——已实现但闲置，dispatcher 和 agent_loop 用直接回调绕过了它 | `openprogram/events/bus.py` |
| gate 的 `ask` 路径 | `ApprovalRegistry` + `_wrap_with_approval`：发起请求、阻塞等待、批准或拒绝，拒绝时回一个 is_error 的 tool result | `openprogram/agent/permissions/approval.py` |
| observer 的 `Prepare` 后台 task | `JobRunner.spawn_job`——ThreadPoolExecutor、状态机、job_status 广播 | `openprogram/agent/job/runner.py` |
| `Inject` 的落地槽位 | 注入 system prompt 的 memory prefetch，以及 steering messages | `openprogram/agent/agent_loop.py` |
| 事件因果、rewind、分支 | session git DAG，节点带 parent_id / caller | `openprogram/context/git/` |
| gate 的强制点 | 所有 chat tool 调用都要过的那一个点 | `agent_loop.py` `_execute_tool_calls` |

## 3. 事件 tap

事件从本来就能察觉到相应事实的位置发出。多数 tap 是把现有回调或内部事件转成规范事件，
而不是新增一套探测：

| Event | 来源 | tap 的性质 |
|---|---|---|
| `user.prompt_submitted` | `dispatcher/__init__.py`，持久化用户消息处 | 加在已有的 chat_ack / chat_response 广播旁；放在持久化分支之外，两条路径都会发 |
| `model.response_started` | `agent_loop.py` 的 AgentEventMessageStart | 转换现有事件 |
| `model.response_completed` | `agent_loop.py` 的 AgentEventMessageEnd | 转换现有事件 |
| `tool.before` | `agent_loop.py`，每次 `tool.execute()` 之前 | 一份事件同时喂 notify emit 与 gate 问询 |
| `tool.after` | `agent_loop.py`，每次工具调用结束之后 | notify emit，附结果文本通道 |
| `subagent.started` / `completed` | `task/runner.py` 的 job_status 广播 | 转换，经 `_broadcast_job_status` 汇总 |
| `permission.requested` | `_approval.py` 的 approval_request 信封 | 新增 tap |
| `artifact.file.changed` | `file_backup.backup_before_edit` 与 `project_commit` | 写成功后新增发送 |

## 4. gate 的挂载点与如实声明的覆盖率

gate 挂在两个地方，两处的保证强度确实不同，这个差别是明说的，不掩盖。

**chat 路径——强制。** gate 串在 `agent_loop.py` 的 `_execute_tool_calls` 里、
`tool.execute` 之前。所有 chat tool 调用都过这一个点，绕不过去。

**agentic 嵌套路径——可选挂载。** `function.py` 的 `_pre_invocation_hooks`，
cancel 检查已经挂在这里。它是一个挂载点而非咽喉点；覆盖率如实声明，不对嵌套调用
宣称全覆盖。

gate 对 subagent turn 生效，且**独立于 `permission_mode`**。特别地，它不会被
`sub_agent_run.py` 里设的 `permission_mode="bypass"` 关掉——那个 bypass 是本设计
要堵的现有漏洞（设计 `invariants.md` 与 `execution-model.md` §2）。

## 5. Prepare 的执行

`Prepare` 复用 `JobRunner.spawn_job`，但注入一个不含 bash、write、network 的受限
tool allowlist。它跑在一个独立小池里，并发 1–2，可被用户任务抢占，遇 429 让路
（设计 `execution-model.md` §3）。

## 6. 一个已知缺口，但不阻塞

`@function` 的 tool 执行不写 DAG 节点，只有 `@agentic_function` 写，因此 DAG 树作为
因果记录是不完整的。如果审计必须靠 DAG 做因果回溯，就得先补上这一块。本设计改为在
`events.jsonl` 里独立记录全量事件，于是 DAG 的缺口成了一个已知项，而不是前置条件。

## 7. 验证方式

每一处接线改动都按同一套查：`py_compile`、相关单测、`openprogram worker restart`、
`/healthz` 正常，以及经 web UI 发一条真实消息（前端改动要先 `npm --prefix apps/web run build`）。

事件顺序的验证方式是跑一个带工具调用的 turn，读该会话的 `events.jsonl`
（事件日志常开）。日志必须依序出现 `user.prompt_submitted → model.response_started →
tool.before → tool.after → model.response_completed`，且每条的
metadata 里带 session 和 turn。

## 附录：实现状态

迁移分五步，已落地四步。先启用总线并接入 agent 内部的事件源；接着是 `file.changed`
与 `tool.before` 同步问询点；然后是外部源桥接（一座真的 auth 桥，加上 context、
channels、memory、web UI 的源头 tap）；再然后把 web UI 从发送方改成订阅方，外部源
发 `ws.frame` 信封、web UI 原样转发。前四步中除第四步改动了旧路径外都是纯加法。
第五步——`openprogram/proactive/` 规则层及其 Policy、挡路、旁观行为——尚未落地，
验收标准是 proactive 不碰任何子系统内部，纯靠订阅工作。

已落地的各部分位置：

| 件 | 位置 |
|---|---|
| `Event` / `make_event` / `emit_safe` / `subscribe(types=)` / `get_event_bus` / 事件日志订阅者 | `openprogram/events/bus.py` |
| 同步问询点：`register_tool_gate` / `decide_tool_gate` / `ToolGateDenied` | `openprogram/events/tool_gate.py` |
| `tool.before` 观察与问询、`tool.after`、`model.*` taps | `openprogram/agent/agent_loop.py` |
| `user.prompt_submitted` | `openprogram/agent/dispatcher/__init__.py` |
| `subagent.started` / `ended` | `openprogram/agent/job/runner.py` `_broadcast_job_status` |
| `file.changed`，写成功后经懒 import 发出 | write / edit / apply_patch 三个工具中的五处 |
| 外部源桥，worker 启动时幂等安装 | `openprogram/events/bridges.py` + `worker/runner.py` |
| 外部源头 taps | `context/engine.py`（compaction ×2）、`channels/_conversation.py`、`memory/session_watcher.py`（×2）、`webui/server.py`（skills / plugins） |
| `emit_ws_frame` 透传信封 + `_subscribe_event_bus` 订阅转发 | `openprogram/events/bus.py`、`webui/server.py` |
| 外部源不再 import web UI | `task/runner.py`、`sub_agent_run.py`、`worktree/manager.py`、`functions/watcher.py`、`channels/_broadcast.py` |
| 单测（30 个） | `tests/agent/test_event_bus.py`、`test_tool_gate.py`、`test_event_bridges.py` |

实测验证覆盖了真实 turn 上的完整事件序列、`file.changed`、gate 的端到端测试、
`skills.changed`，以及一次 WebSocket 探针——确认 job_status 的四个状态都经新链路
到达前端。有一条环境注意事项要带下去：worker 的工作目录是 home，因此项目 skills
目录解析成 `~/skills`。
