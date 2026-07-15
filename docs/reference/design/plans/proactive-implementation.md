# 落地规划：Proactive Layer 接入现有代码

> 设计见 [`../design/proactive/`](../design/proactive/README.md)。本文只讲**怎么把那套设计
> 接进现有 OpenProgram 代码**：接线点（file:line）、复用哪些现成机制、分几阶段、验证方式。
> 设计的"是什么/为什么"不在这里，冲突以设计文档为准。

代码落点：事件层就地升级 `openprogram/agent/event_bus.py`（Event + 类型订阅 + 进程级单例），
taps 加在各源文件里；`openprogram/proactive/` 包到"新消费者进场"那步（规则层）才新建。
Event 模型 = 核心三样 + metadata 开放口袋（见设计 `event-layer.md` §1，turn/session 不是固定字段）。

## 复用的现有机制

设计里反复提到的"现有可复用件"，对应的真实位置：

| 设计中的角色 | 现有机制 | 位置 |
|---|---|---|
| 进程内事件扇出 | `EventBus`（已实现但闲置，dispatcher/agent_loop 直接用回调绕过了它） | `openprogram/agent/event_bus.py:14-60` |
| gate 的 `ask` 路径 | `ApprovalRegistry` + `_wrap_with_approval`（请求→阻塞等待→批准/拒绝，deny 回 is_error tool result） | `openprogram/agent/_approval.py:77-174` |
| observer 的 `Prepare` 后台 task | `TaskRunner.spawn_task`（ThreadPoolExecutor，状态机，task_status 广播） | `openprogram/agent/task/runner.py` |
| `Inject` 落地槽位 | memory prefetch 注入 system prompt + steering messages | `openprogram/agent/agent_loop.py:343-354` |
| 事件因果 / rewind / 分支 | session git DAG（节点带 parent_id / caller） | `openprogram/contextgit/` |
| gate 的 hard enforcement 点 | 所有 chat tool 调用的单点 | `openprogram/agent/agent_loop.py` `_execute_tool_calls` |

## 事件 tap 接线点

把设计里的 Event 从现有代码的这些位置发出（多数是把现有回调/事件转成 CanonicalEvent）：

| Event | 接线点 | 现状 |
|---|---|---|
| `user.prompt_submitted` | `dispatcher/__init__.py` phase 2（persist user message） | 已有 chat_ack/chat_response 广播，加 tap |
| `model.response_started` | `agent_loop.py:429`（AgentEventMessageStart） | 已有事件，转 CanonicalEvent |
| `model.response_completed` | `agent_loop.py:452`（AgentEventMessageEnd） | 同上 |
| `tool.before` | `agent_loop.py:495`（现有 no-op `dispatch_hook(TOOL_BEFORE_USE)`） | 把 observe-only hook 升级成 PRL gate tap |
| `tool.after` | `agent_loop.py:564`（`dispatch_hook(TOOL_AFTER_USE)`） | 同上 |
| `subagent.started/completed` | `task/runner.py:96-113`（task_status 广播） | 转 CanonicalEvent |
| `permission.requested` | `_approval.py`（approval_request 信封） | 加 tap |
| `artifact.file.changed` | `file_backup.backup_before_edit` + `project_commit` | 新增 emit |

## gate 接入点

- **chat 路径（hard）**：`agent_loop.py` 的 `_execute_tool_calls`，gate 串在 `tool.execute`
  之前。所有 chat tool 过这一点，是 hard enforcement。
- **agentic 嵌套路径**：`function.py:50-89` 的 `_pre_invocation_hooks`（cancel 检查已在用此
  挂载点）。这是可选挂载点，覆盖率如实声明，不假装全覆盖。
- gate 对 subagent turn 生效，**独立于 `permission_mode`**，不被 `sub_agent_run.py:88` 的
  `permission_mode="bypass"` 关掉（堵现有漏洞，见设计 `invariants.md` 与 `execution-model.md` §2）。

## Prepare 接入

复用 `TaskRunner.spawn_task`，但注入受限 tool allowlist（不含 bash/write/network）。独立小池
并发 1-2，可被用户任务抢占，429 时让路（见设计 `execution-model.md` §3）。

## 附带要修的洞

`@function` tool 执行目前**不写 DAG 节点**（只有 `@agentic_function` 写），DAG 树不完整。
如果审计要靠 DAG 做因果回溯，需先补；本设计改为 `events.jsonl` 独立记全量，DAG 洞列为已知项、
不阻塞 proactive 落地。

## 分阶段（对应 `framework-evolution.md` §4 的五步迁移）

| 步 | 内容 | 性质 | 状态 |
|---|---|---|---|
| **1** | 总线启用 + A 类源接入 | 纯加法 | ✅ 2026-06-13 落地（commits e06b2db6 / 2915849b / 9b15ccac / dd8cb843），真实 turn 验收过完整序列 |
| **2** | `file.changed` + `tool.before` 同步问询点 | 纯加法 | ✅ 2026-06-13 落地（commit 89e16a10），file.changed live 验证、gate 端到端测试 |
| **3** | B 类源桥接：auth 真桥 + context/channels/memory/webui 源头 tap | 纯加法 | ✅ 2026-06-13 落地（commit 5cc967df），skills.changed live 验证，auth 桥 5 单测。注意 worker cwd=home，project skills 目录是 ~/skills |
| **4** | webui 切换为订阅者：外部源 emit `ws.frame`、webui 订阅原样广播 | 动旧路 | ✅ 2026-06-13 落地（commits 99678165 + 4c5a5ede），WS 探针验证 task_status 四态经新链路到前端 |
| **5** | 新消费者进场：`openprogram/proactive/` 规则层（Policy/挡路/旁观） | 纯加法 | ⏳ 验收：proactive 不碰子系统内部，仅靠订阅工作 |

## 已落地的实现（步 1–2 as-built）

| 件 | 位置 |
|---|---|
| Event / make_event / emit_safe / subscribe(types=) / get_event_bus / 事件日志订阅者 | `openprogram/agent/event_bus.py` |
| 同步问询点（register_tool_gate / decide_tool_gate / ToolGateDenied） | `openprogram/agent/tool_gate.py` |
| tool.before 观察+问询、tool.after、model.\*、turn.ended taps | `openprogram/agent/agent_loop.py` |
| user.prompt_submitted（持久化分支外，两条路径都发） | `openprogram/agent/dispatcher/__init__.py` |
| subagent.started/ended（状态漏斗） | `openprogram/agent/task/runner.py` `_broadcast_task_status` |
| file.changed（写成功后，懒 import） | write / edit / apply_patch 三工具五处 |
| B 类桥（auth 订阅翻译，幂等安装于 worker 启动） | `openprogram/agent/event_bridges.py` + `worker/runner.py` |
| B 类源头 taps | `context/engine.py`(compaction ×2)、`channels/_conversation.py`、`memory/session_watcher.py`(×2)、`webui/server.py`(skills/plugins) |
| 步4 透传信封 emit_ws_frame + webui `_subscribe_event_bus` 订阅转发 | `agent/event_bus.py`、`webui/server.py` |
| 步4 外部源解耦（不再 import webui） | `task/runner.py`、`sub_agent_run.py`、`worktree/manager.py`、`functions/watcher.py`、`channels/_broadcast.py` |
| 单测（30 个） | `tests/agent/test_event_bus.py`、`test_tool_gate.py`、`test_event_bridges.py` |

## 验证

每步通用：`py_compile` + 相关单测 + `openprogram worker restart` + `/healthz` 正常 +
webui 发一条真实消息（前端改动需 `cd web && npm run build`）。
步 1 专属：`OPENPROGRAM_EVENT_LOG=1` 重启 worker，跑一个带工具调用的 turn，确认日志里
依序出现 `user.prompt_submitted → model.response_started → tool.before → tool.after →
model.response_completed → turn.ended`，且 metadata 带 session/turn。
