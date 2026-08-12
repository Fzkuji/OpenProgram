# 承诺与心跳

本文记录已经实现的边界和剩余工作，不定义通用任务系统。

## 1. OpenProgram 当前现状

### 已实现的数据与写入路径

现有 memory writer 在处理 Runtime 已选定的会话批次时识别义务，并调用
`record_commitments`，没有第二套提取服务。LLM 提供义务句子、语义截止值、
Source ref 和一行精确引用；Runtime 验证该引用是当前批次可信 Source 的精确
片段，校验绝对 `YYYY-MM-DD` 日期或 `null`，并派生承诺 ID 和发言人。

tool schema 与 Runtime 同时执行 writer 限额：一个批次的创建与转移合计最多 64
项，规范化承诺文本最多 2,048 字符，精确引用最多 8,192 字符，Source ref 最多
512 字符。批次数量沿用现有 transaction 每次最多 64 个 Source 的先例；即使调用
方绕过 schema，Runtime 校验仍然生效。owner Web route 每次只执行一项显式转移，
也不接受 writer 语义文本或引用，因此这些 writer batch 限额不会形成另一套 owner
限制。

`commitments.jsonl` 与 Topic 变更一起通过同一个 staged memory transaction
安装。每条记录包含 `id`、`text`、`due`、`speaker_id`、`source`、
`source_quote`、`status`、状态转移 provenance、转移时间和
`notification_steps`。ID 仅由 Source ref 和精确引用确定性生成，不依赖 LLM
措辞、截止日期解释或 item 顺序。同一 Source 的不同引用可表示多条承诺；同一
证据被改写后重复提取不会新增或重新打开记录。只支持 `open`、`done`、
`dismissed`。

该文件是增量 workspace 能力：既有 workspace 没有该文件时按空集合读取，
因此本次发布不需要一次性 migration。写入复用现有原子文件替换、staged
install、workspace lock 和 memory Git 候选清单。状态读取和 heartbeat 隔离
非法行、计入 invalid，并在更新合法记录的通知状态时原样保留非法行；upsert
和 transition 使用严格读取，发现任意非法行即拒绝变更且不修改文件。非法数据
不会被静默删除或解释成另一套 schema。

后续可信 writer 批次只有引用当前批次可信 Source 及其中的精确关闭证据，才可
在语义上关闭记录。Runtime 持久化该 Source、引用和 Runtime 时间戳作为转移
provenance。显式转移使用现有 `memory_update` 事务并携带当前 memory revision；
只有持久化的 owner authority 可以请求 `done` 或 `dismissed`，Runtime 固定记录
`owner/manual`，不接受客户端指定 provenance。

### 已实现的确定性心跳

心跳是现有 `cron-worker` 里的内置检查，不是新 scheduler，也不是 agent turn。
每次普通 cron tick 读取 live config：

- `proactive.heartbeat`：本地时间 09:00 执行 `daily`（默认），每小时 00 分
  执行 `hourly`，或设为 `off`。
- `proactive.quiet_hours`：默认本地时间 `23:00-08:00`。

手工写入的非法配置会跳过，不终止 worker。心跳只读取 `open` 且
有日期的承诺：到期时提醒一次，达到当前七天逾期档时再提醒一次。无日期记录
保持可见但不推送；安静时段抑制不会消耗通知档位。

确定性 ID 负责重复提取去重；持久化的 `due` 与 `overdue:7` 把当前策略下每条
记录的成功提醒限制为两次。发送失败不消耗档位，后续合格 heartbeat 会重试。
并发 heartbeat pass 由 OS 文件锁 claim 串行化，因此正常运行中每个档位只发送并
记录一次；发送失败时释放 claim，owner 进程退出时由 OS 自动释放。仅异常 owner
退出保留 at-least-once：如果 channel 已接受消息，而进程在原子写入通知状态前
退出，可能出现一次重复。当前 outbound API 没有跨 channel idempotency
key，因此不能声称 exactly-once；另加 delivery journal 也不能消除外部不确定性。

送达目标来自证据，不来自模型输出。对于
`openprogram/<session>/<message>` Source，Runtime 从 `SessionDB` 读取来源会话，
使用该会话已有的 channel、account 和 peer binding。没有目标的记录保持可见。
同一目标的到期记录合成一条消息，经 `channels.outbound` 发送；只有发送返回
成功后才写入通知档位。发送是带重试和退避预算的网络 I/O，因此在 workspace
写锁之外执行：读取到期记录时取一次锁，写入已送达档位时再取一次，第二次写入
只把档位合并到当时磁盘上的记录，所以提醒发送期间提交的状态变更不会被覆盖。
测试替换 outbound send，不使用真实渠道或凭据。

### 已实现的 owner 与 model 界面

`memory_status`、`openprogram memory status` 和 `GET /api/memory/status` 提供
承诺计数和字段受限的记录投影。
model-facing 响应仍不包含宿主 workspace 路径，也不包含 channel binding、账号、
peer 数据、Source 精确引用或 JSONL 中的任意额外字段；它只暴露可审计的 Source
ref 和状态转移 Source ref，不重复消息原文。owner-only web route 保留现有
`workspace_path` 行为。

现有 Memory Web 增加 Commitments tab，展示计数和记录，并为 `open` 记录提供
带确认的 `done` 与 `dismissed` 控件。owner-authenticated route 把界面显示的
revision 传入 `memory_update` 底层同一个 `MemoryWorkspace.update` transition；
stale revision 返回 conflict，非法或非 object JSON 返回结构化
`INVALID_ARGUMENT`，没有增加第二个状态 writer 或 authority model。

### 当前边界

该功能只有带引用的扁平清单和确定性提醒检查。没有优先级、子任务、项目、
自主执行、独立 task manager，也没有独立 scheduling/storage 子系统。它要求
`cron-worker` 正在运行；常驻 web worker 不提供 fallback timer。

## 2. 其他项目如何设计

- **采用并修改：** [OpenClaw Heartbeat](https://docs.openclaw.ai/gateway/heartbeat) 当前把
  heartbeat 设计为由 Automations scheduler 管理的 main-session 定时轮次，
  具有显式送达目标和 active hours。OpenProgram 采用可配置非活跃时段并实现为
  quiet hours，但从可信 Source/session 派生 target，只做确定性筛选；拒绝另建
  scheduler 和 heartbeat LLM turn。
- **采用谨慎结论、修改 provenance、拒绝已退役机制：** [OpenClaw Inferred commitments](https://docs.openclaw.ai/concepts/commitments)
  明确说明 inferred-commitments 实验已经退役：不再提取或送达新的 follow-up，
  只保留 legacy 记录的查看与 dismiss。OpenProgram 采用 inspect/dismiss
  lifecycle 可见性和评测要求，但要求可信且带引用的 Source，由 Runtime 确定
  字段；拒绝基于置信度扩张自主 follow-up。
- **采用现有 memory lifecycle，拒绝第二个 manager：** [LangMem](https://github.com/langchain-ai/langmem) 提供 hot-path memory tool
  和自动提取、合并、更新知识的 background memory manager。OpenProgram 保留
  现有 writer 提取，并加入 Source batch 校验和承诺原子 staging；不增加另一项
  memory service。
- **复用调度原则，拒绝另一套 scheduling API：** [LangSmith Deployment cron jobs](https://docs.langchain.com/langsmith/cron-jobs)
  按计划在指定 thread 或新 thread 上运行 assistant 和输入。OpenProgram 复用
  现有 cron-worker 和来源 session binding，不增加 scheduled assistant turn 或
  第二个 thread scheduler。

这些来源只支持逐项机制比较，不能据此声称各系统的信任、存储、状态转移或
送达语义等价。

## 3. OpenProgram 后续计划

1. 在会话 replay 上测量提取精度、遗漏义务、错误日期、过早关闭、重复提醒和
   提醒有效性。OpenClaw 退役该实验使这项评测成为验收门槛，而不是可选优化。
2. 把已经实现的 fake-transport 矩阵扩展为长时间 soak test，重点覆盖 writer 与
   heartbeat 并发，以及 post-send/pre-state-write 窗口内的进程终止。
3. 只有全部 outbound adapter 能遵守同一契约时才增加 transport idempotency key；
   此前明确记录和观测 at-least-once，不增加无法证明外部去重的 journal。
4. 只有实际使用提出需求时，才考虑可配置逾期间隔或 timezone；此前保持
   daily/hourly/off、七天逾期档和宿主本地时间。
5. 后续不加入优先级、子任务、项目、自主执行，也不新增
   scheduler/storage/task-manager 子系统。
