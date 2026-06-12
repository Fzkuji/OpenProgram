# 落地规划：Proactive Layer 接入现有代码

> 设计见 [`../design/proactive/`](../design/proactive/README.md)。本文只讲**怎么把那套设计
> 接进现有 OpenProgram 代码**：接线点（file:line）、复用哪些现成机制、分几阶段、验证方式。
> 设计的"是什么/为什么"不在这里，冲突以设计文档为准。

代码落点：新建 `openprogram/proactive/` 包。

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
  `permission_mode="bypass"` 关掉（堵现有漏洞，见设计 `invariants.md` 不变式 2/4 与
  `execution-model.md` §2）。

## Prepare 接入

复用 `TaskRunner.spawn_task`，但注入受限 tool allowlist（不含 bash/write/network）。独立小池
并发 1-2，可被用户任务抢占，429 时让路（见设计 `execution-model.md` §3）。

## 附带要修的洞

`@function` tool 执行目前**不写 DAG 节点**（只有 `@agentic_function` 写），DAG 树不完整。
如果审计要靠 DAG 做因果回溯，需先补；本设计改为 `events.jsonl` 独立记全量，DAG 洞列为已知项、
不阻塞 proactive 落地。

## 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0** | 设计文档（`docs/design/proactive/`） | 完成 |
| **P1** | 事件层 + 单写者落盘 + 消费者 + replay(strict)。**纯可观测，无决策** | 普通 chat turn 产出完整可回放轨迹；并发 subagent 按 root_id 正确分片 |
| **P2** | 双通道引擎 + 三条 policy + budget/熔断 + replay(augmented) | 四条不变式回归测试、故障域降级、gate p99 自身 CPU ≤10ms、observer 对 turn 零延迟 |
| **P3** | 前端 notice（折叠/展开、明确 system 来源、accept/snooze/dismiss/mute）+ 设置页 + 反馈统计 + 隐式 accept fold + `ApprovalRegistry` 泛化 | 隐式 accept fold 生效；mute 可恢复 |
| **P4（future）** | 外部框架 adapter（Claude Code hooks HTTP 桥）+ policy-portability 矩阵 | 同一 policy 跑两个框架 + 降级对照 |

P1 把 replay 工具放在决策引擎**之前**交付——评估优先于功能。

## 包结构（建议）

```
openprogram/proactive/
  events.py      Event schema + AgentEvent/信封 → Event 转换；单写者 appender（带锁/截断坏行/secret redaction）
  bus.py         复用 agent/event_bus.py 进程内扇出；独立消费者线程 + 有界队列 + 持久化消费位点
  state.py       三层 fold；root_id 分片；lazy L2 + derived event 回写
  policy/        Policy 基类 + 注册；MVP 三条
  gate.py        同步 gate：critical 独立预算/最先求值/超时归因；冲突取最严
  observer.py    异步 observer：Inject/Notify/Prepare/Remember 落地 + staleness
  budget.py      任务段预算 + dedup/cooldown（内存+持久）+ 滑窗熔断 + 隐式 accept
  replay.py      strict/augmented 回放 + 冷却闭环 + 时钟注入 + 按 node_id DAG 路径折叠
  audit.py       决策审计
```

## 验证

- **P1**：跑一个真实 chat turn（webui，需 `cd web && npm run build` + `openprogram worker
  restart`），确认 `events.jsonl` 产出完整轨迹且 `replay --policy noop` 能无损重放；并发 spawn
  两个 subagent，断言事件按 root_id 分片不交叉污染。
- **P2**：单测覆盖——proactive 链深度 ≤2 不变式、critical 独立预算超时归因、故障域降级（注入
  坏行后仍只跑无状态 critical）、gate 对 bypass subagent 生效、滑窗熔断 + 隐式 accept。回放历史
  会话抽样人工标注 precision。
- **gate 性能**：构造长/对抗 bash 命令测 p99 自身 CPU 时间 ≤10ms，且不泄漏超时线程。
