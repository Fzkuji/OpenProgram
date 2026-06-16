# Usage Metering 子系统设计

状态：实施中（2026-06）
作者：设计 + 实现一体推进

## 1. 目标

让框架内**每一次 LLM 调用**的 token / 模型 / 成本被无遗漏地记录，带来源标签、时间序列、按模型/来源/会话分组的聚合能力，支撑可视化面板与成本核算，并为未来的配额、限流、预算告警、导出预留扩展口。

明确不做的妥协：不为旧的累积快照式记账保留兼容代码；旧职责该拆该删则拆删。

## 2. 现状诊断（实施前）

LLM 调用最终都过 `providers/stream.py` 的 `stream_simple()`（流式）/ `complete_simple()`（非流式，内部跑 stream_simple）。返回的 `AssistantMessage` 带 `usage`（`Usage` 对象：input/output/cache_read/cache_write/total_tokens/cost）。

记账只覆盖「走 engine 的主路径」，两层：
- 消息级：`dispatcher/persistence.py` 把 token 写进 assistant 消息历史列（单条消息 pill 用）。
- 会话累积级：`context/usage.py` 的 `UsageTracker.record_turn()` 写进 git session meta 的 `_usage`（只有累积总数，无时间序列、无 per-model、无 per-source、无成本）。

三个根本问题：

1. **收口点不统一**。`stream.py` 本身不记账，记账在更上层的 dispatcher；而 `memory/llm_bridge.py` 直接调 `api_provider.stream_simple()` 连 stream.py 都绕过。"过了 stream_simple" ≠ "被记账"。

2. **存储是累积快照不是流水**。session meta `_usage` 无法跨 session 聚合、无法按时间桶查询、无 per-model 索引。

3. **职责耦合**。`UsageTracker` 同时干记账、compaction 阈值估算（`estimated_input`/`record_compaction`）、热路径 budget 缓存——三者生命周期与消费者完全不同。

**漏记路径清单**（直接调 complete_simple/stream_simple 绕过 engine）：
`context/summarize.py`、`agent/compaction/compaction.py`、`agent/compaction/branch_summarization.py`、`functions/tools/mixture_of_agents`、`memory/llm_bridge.py`，以及 `@agentic_function` 子进程（`process_runner.py`，进程内单例 tracker 主进程收不到，返回结果不含 usage）。

一个有利事实：`providers/models.py:calculate_cost(model, usage)` 已能从 `Model.cost` 算成本。metering 层只需在收口点确保它被调用，不必新建定价逻辑。

## 3. 分层架构

```
消费层  webui 面板 / CLI / 导出 / 未来配额引擎
          │ query(filters, group_by, time_bucket)
存储层  UsageLedger  —  SQLite 单库 usage_events 表 (append-only)
          │ record(UsageEvent)
记账层  UsageRecorder  —  唯一收口逻辑：usage + model + 来源上下文 → UsageEvent
          │ 读 call-context
上下文层 UsageContext  —  contextvar + usage_scope() 上下文管理器

(独立保留) context budget 估算 — compaction 阈值，从 UsageTracker 拆出
```

新增模块 `openprogram/metering/`：
- `event.py` — `UsageEvent` schema
- `context.py` — contextvar + `usage_scope()` / `current_usage_context()` / `snapshot()` / `apply_snapshot()`
- `ledger.py` — `UsageLedger`（SQLite 后端 + 聚合查询）
- `recorder.py` — `UsageRecorder`（收口，best-effort）
- `subprocess.py` — 子进程账目桥
- `__init__.py` — 门面

放顶层 `metering/` 而非 `context/` 之下：metering 是横切关注点（providers/agent/memory/functions 都依赖），放 context 会制造 `providers → context` 反向依赖。`metering/` 只依赖 `providers/types`（纯数据），无环。

## 4. UsageEvent Schema

一条 event = 一次 LLM 调用的完整记账记录（`metering/event.py`，pydantic frozen）：

身份：`event_id`(uuid 幂等键)、`ts`(unix epoch float)。
归属：`session_id`、`parent_session_id`(子 agent 归属父)、`agent_id`、`call_kind`(核心来源标签)、`call_label`(自由文本细分)、`origin_pid`(主进程 vs 子进程)。
模型：`provider`、`api`、`model_id`。
tokens（provider 权威值，缺失为 0）：`input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_write_tokens`、`total_tokens`。
成本（USD，拍平便于 SUM）：`cost_input/output/cache_read/cache_write/total`、`cost_source`("model_catalog"|"provider_reported"|"unknown")。
溯源：`token_source`("provider_usage"|"anthropic_count_api"|"estimate")、`schema_version`。

`call_kind` 用字符串而非 Enum（可扩展，新增调用方不改底层）：
`chat` / `exec` / `compaction` / `summarize` / `memory` / `subagent` / `tool` / `title` / `unknown`。

取舍：cost 拍平而非嵌套 UsageCost → SQLite 列化、SUM 无需 JSON 解析；token_source 单列 → 面板可标注"估算"避免误导成本。

## 5. 来源标签传递：contextvar 为主，metadata 显式覆盖兜底

底层 `stream_simple` 不知道自己被谁调。三种传法：

| 方案 | 优 | 劣 |
|---|---|---|
| 显式参数 `options.call_kind` | 显式 | 每个调用方都改，穿透多层签名，违反"加调用方不改底层" |
| `SimpleStreamOptions.metadata` | 字段已有 | 深层调用易漏；memory 绕过 stream.py 时也到不了 |
| **contextvar** | 一行 `with usage_scope(...)`，async Task 自动继承 | 跨进程/线程不自动传播（需显式快照） |

采用 contextvar 为主 + metadata 显式覆盖。`metering/context.py`：
`usage_scope(call_kind, call_label, parent_session_id, agent_id)` 上下文管理器，set/reset contextvar，支持嵌套 merge。`current_usage_context()` 读。`snapshot()`/`apply_snapshot()` 跨进程序列化。

边界说明：asyncio 建 Task 默认 `copy_context()`，stream_simple 在 create_task 下游能读对 scope。线程边界（run_in_executor/裸 Thread）不继承 → 入口 apply_snapshot。进程边界 fork 拷贝 contextvar 当前值（对 process_runner fork 有利），但 spawn 不行 → snapshot/apply_snapshot 作可靠路径。

recorder 合并优先级：`metadata.usage` > contextvar > 默认 `unknown`。

## 6. 收口点：stream.py 包装 + 把 memory 拉回

在 `stream.py` 的 `stream()`/`stream_simple()` 包记账装饰器，并把 `memory/llm_bridge.py` 拉回 stream.py。

理由：
1. stream.py 是"一次逻辑 LLM 调用"的语义边界，已做 api_key 解析/provider 查找，能拿到 model(含 cost)+最终 AssistantMessage.usage。
2. stream() 是 generator，包装方式 = 消费 done/error 时提取 final_message.usage → `calculate_cost` → 读 contextvar → 组 UsageEvent → recorder。流式不阻塞，记账只在终止 event 触发一次。
3. 不选 api_registry 层：ApiProvider 是 Protocol，每实现各自 stream_simple，收口要么改 Protocol（侵入所有 provider）要么包 registry（包装点分散）。stream.py 是唯一单函数收口。
4. memory 必须拉回：现 `llm_bridge.py` 直连 api_provider = 漏账。改调 `providers.stream_simple` + `usage_scope("memory")`，顺带修复 stream.py 注释担心的 header 不一致。

不变量：记账失败必须 best-effort（try/except 吞），绝不打断 LLM 响应。

dispatcher 现有 `persist_assistant_message`（messages 列）**保留不动**——那是单条消息 pill 的数据。ledger 是独立第二份权威账：消息列="这条消息花多少"，ledger="可聚合全局流水"。

## 7. 存储：独立全局 SQLite ledger

废弃 session meta 塞累积 dict。新建 `~/.openprogram/usage.db` 一张 append-only 表：

```sql
CREATE TABLE usage_events (
    event_id TEXT PRIMARY KEY, ts REAL NOT NULL,
    session_id TEXT, parent_session_id TEXT, agent_id TEXT,
    call_kind TEXT NOT NULL, call_label TEXT, origin_pid INTEGER,
    provider TEXT NOT NULL, api TEXT, model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cost_total REAL NOT NULL DEFAULT 0, cost_input REAL, cost_output REAL,
    cost_cache_read REAL, cost_cache_write REAL, cost_source TEXT, token_source TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_usage_ts ON usage_events(ts);
CREATE INDEX ix_usage_model_ts ON usage_events(model_id, ts);
CREATE INDEX ix_usage_session ON usage_events(session_id);
CREATE INDEX ix_usage_kind_ts ON usage_events(call_kind, ts);
```

WAL 模式，支持子进程并发追加。SQLite 用 stdlib `sqlite3`，零外部依赖。

为什么不扩展 session meta：那是 git session 的 idx.meta JSON，无法跨 session 聚合/按时间桶查/per-model 索引；子进程写它还争 git 锁。

聚合 API `UsageLedger.query(since, until, group_by=[...], filters={...}, time_bucket="day"|"hour"|None)`，供面板（趋势=bucket，柱状=group_by）与 CLI 同源消费。

后端抽象为接口（append/query），默认 SQLite，留 JSONL/远程实现口子。

## 8. 子进程边界

`@agentic_function` 子进程：fork 跑 tool body，子进程内 LLM 调用（gui_agent 等）的 default_tracker 是进程内单例，主进程收不到。

方案：**子进程直接写共享 SQLite ledger（账本真相）+ result pickle 回传 usage_summary（仅 UI 即时显示）**。
- 子进程继承 home root，打开同一 usage.db（WAL 多进程安全），自己 recorder 直接 append，`origin_pid` 标记来源。contextvar 经 fork 继承到 `call_kind=exec`。无需主进程二次入账（避免双计）。
- result dict 加 `usage_summary`（total_tokens/cost_total/by_model）仅供父进程立即显示，不写库。
- SIGKILL 风险：已 append 的 event 在库中（WAL 落盘）是正确的——那些 token 确实花了；被杀前未完成调用没收到 done event，不入账，符合"绝不编造"。

`metering/subprocess.py`：`child_init()`（apply_snapshot + 指向同一 db）、`collect_summary()`。

## 9. 可扩展留口（不实现）

- per-user：UsageEvent 加 `user_id`（默认单用户，多租户由 usage_scope 注入），query(group_by=["user_id"])。
- 限流/告警：UsageRecorder.record() 暴露 post-record hook 列表（register_usage_hook），事件驱动不阻塞热路径。
- 导出：ledger 后端接口加 export(format, filters) 或 JSONL 镜像后端。
- 远程聚合：后端换 push OTLP/collector 实现，event schema 不变。

## 10. 删除 / 重构清单

`UsageTracker`（context/usage.py）拆分——现混 3 职责：
- `record_turn` 记账（cumulative→session meta `_usage`）：**删**，被 UsageRecorder+UsageLedger 取代。engine.after_turn 的 record_turn 调用改为只更新 compaction budget 状态、不记账。
- `estimated_input`/`record_compaction`/last_prompt_tokens 热缓存：**保留搬家**到 `context/budget_state.py`，承载 compaction 需要的"最近一次真实 input_tokens"。它消费数据不再是记账主体。
- `_persist`/`_load_from_db`：**删**（不再写 session meta `_usage`）。

`UsageState`（context/types.py）：保留（compaction 需 last_prompt_tokens/cache_hit_rate），`cumulative_*` 标记 deprecated 后续移除。

session meta `_usage`：废弃写入。**不主动迁移历史**（旧快照无 per-model/per-source/时间序列，回填会造假数据）——ledger 从启用起记账，启用前历史用 `session_store.py` 扫消息历史的兜底聚合（保留作 legacy/单 session 详情用途）。

不动（metering 的输入，复用）：`models.py:calculate_cost`、`_event_parsing.py:extract_usage`、`dispatcher/persistence.py` 消息列、`types.py:Usage/UsageCost`。

## 11. 分阶段实施

- **Phase 0**：metering 地基（event/context/ledger/recorder + 单测），无行为变更。
- **Phase 1**：stream.py 收口；dispatcher chat 包 usage_scope；验证 messages 列与 ledger 一致。
- **Phase 2**：summarize/compaction/branch/moa 包 usage_scope；memory 拉回 stream.py。
- **Phase 3**：metering/subprocess.py；process_runner result 加 usage_summary；验证子进程入库无双计。
- **Phase 4**：抽 budget_state；engine.after_turn 不写 _usage；删 UsageTracker 记账职责。
- **Phase 5**：/api/usage/summary 改查 ledger；前端趋势曲线+per-model 柱状+per-source 分解+成本卡；CLI op usage。
