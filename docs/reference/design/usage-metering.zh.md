# Usage Metering 子系统设计

## 1. 目标

框架内**每一次 LLM 调用**的 token、模型、成本都被无遗漏地记录，带来源标签、时间序列、按模型/来源/会话分组的聚合能力，支撑可视化面板与成本核算，并为配额、限流、预算告警、导出预留扩展口。

记账不为累积快照式的旧记录保留兼容路径；该拆该删的职责就拆就删。

## 2. 记账需要覆盖什么

LLM 调用最终都过 `providers/stream.py` 的 `stream_simple()`（流式）/ `complete_simple()`（非流式，内部跑 stream_simple）。返回的 `AssistantMessage` 带 `usage`（`Usage` 对象：input/output/cache_read/cache_write/total_tokens/cost）。这使 `stream.py` 成为唯一能拿到一次调用完整记录的位置，metering 就在这里收口。

已有的两层记账保留，各自回答不同问题：

- 消息级：`dispatcher/persistence.py` 把 token 写进 assistant 消息历史列——单条消息 pill 的数据。
- compaction 预算：`context/usage.py` 的 `UsageTracker.record_turn()` 写进 git session meta 的 `_usage`，是压缩决策读取的状态。

两者都不是计费账。session meta `_usage` 是累积快照：无法跨 session 聚合、无法按时间桶查询、无 per-model / per-source / 成本分解。所以 metering 新增第三份独立记录——一条 append-only 事件流——而不是扩展这两者中的任何一个。

三条性质决定设计：

1. **单一收口点**。记账在 `stream.py` 内部，不在上层调用方。任何以其它途径抵达 provider 的路径（例如 `memory/llm_bridge.py` 曾直调 `api_provider.stream_simple()`）都被拉回经 `stream.py`，而不是单独记账，因此"过了 stream_simple"与"被记账"是同一件事。

2. **流水而非快照**。一次调用一行、只追加，任何聚合都是一次查询而非一次 schema 变更。

3. **职责分离**。计费记账、compaction 阈值估算、热路径 budget 缓存的生命周期与消费者各不相同，分属不同对象。

聊天主循环之外抵达 provider 的路径——`context/summarize.py`、`functions/tools/mixture_of_agents`、`memory/llm_bridge.py`，以及 `@agentic_function` 子进程（`process_runner.py`）——都由同一个收口点加一个显式来源 scope 覆盖。

`providers/models.py:calculate_cost(model, usage)` 已能从 `Model.cost` 算成本。metering 层在收口点调用它，不新建定价逻辑。

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

## 5. 来源标签传递

底层 `stream_simple` 不知道自己被谁调。三种传法：

| 方案 | 优 | 劣 |
|---|---|---|
| 显式参数 `options.call_kind` | 显式 | 每个调用方都改，穿透多层签名，违反"加调用方不改底层" |
| `SimpleStreamOptions.metadata` | 字段已有 | 深层调用易漏；memory 绕过 stream.py 时也到不了 |
| **contextvar** | 一行 `with usage_scope(...)`，async Task 自动继承 | 跨进程/线程不自动传播（需显式快照） |

contextvar 为主，metadata 显式覆盖兜底。`metering/context.py`：
`usage_scope(call_kind, call_label, parent_session_id, agent_id)` 上下文管理器，set/reset contextvar，支持嵌套 merge。`current_usage_context()` 读。`snapshot()`/`apply_snapshot()` 跨进程序列化。

边界说明：asyncio 建 Task 默认 `copy_context()`，stream_simple 在 create_task 下游能读对 scope。线程边界（run_in_executor/裸 Thread）不继承 → 入口 apply_snapshot。进程边界 fork 拷贝 contextvar 当前值（对 process_runner fork 有利），但 spawn 不行 → snapshot/apply_snapshot 作可靠路径。

recorder 合并优先级：`metadata.usage` > contextvar > 默认 `unknown`。

## 6. 收口点

记账装饰器包在 `stream.py` 的 `stream()`/`stream_simple()` 上，`memory/llm_bridge.py` 经 stream.py 而非绕过它。

为什么收在这里：
1. stream.py 是"一次逻辑 LLM 调用"的语义边界，已做 api_key 解析/provider 查找，能拿到 model(含 cost)+最终 AssistantMessage.usage。
2. stream() 是 generator，包装方式 = 消费 done/error 时提取 final_message.usage → `calculate_cost` → 读 contextvar → 组 UsageEvent → recorder。流式不阻塞，记账只在终止 event 触发一次。
3. 不选 api_registry 层：ApiProvider 是 Protocol，每实现各自 stream_simple，收口要么改 Protocol（侵入所有 provider）要么包 registry（包装点分散）。stream.py 是唯一单函数收口。
4. memory 必须拉回：现 `llm_bridge.py` 直连 api_provider = 漏账。改调 `providers.stream_simple` + `usage_scope("memory")`，顺带修复 stream.py 注释担心的 header 不一致。

不变量：记账失败必须 best-effort（try/except 吞），绝不打断 LLM 响应。

dispatcher 现有 `persist_assistant_message`（messages 列）**保留不动**——那是单条消息 pill 的数据。ledger 是独立第二份权威账：消息列="这条消息花多少"，ledger="可聚合全局流水"。

## 7. 存储：独立全局 SQLite ledger

ledger 是独立的 `~/.openprogram/usage.db`，而不是 session meta 里的累积 dict，一张 append-only 表：

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

`@agentic_function` 在子进程跑函数体，子进程内 LLM 调用（gui_agent 等）的 default_tracker 是进程内单例，主进程收不到。

**子进程直接写共享 SQLite ledger，ledger 即真相来源。**
- 子进程打开同一 usage.db（WAL 多进程安全），自己 recorder 直接 append，`origin_pid` 标记来源。无需主进程二次入账（避免双计）。
- SIGKILL 风险：已 append 的 event 在库中（WAL 落盘）是正确的——那些 token 确实花了；被杀前未完成调用没收到 done event，不入账，符合"绝不编造"。

`process_runner.py` 用的是 **spawn 而非 fork**：父 worker 已加载 PyTorch/libomp/Cocoa，fork 后这些库处于不安全状态会 SIGSEGV。spawn 不复制 contextvar，所以 scope 显式传递——父侧 `run_agentic_in_subprocess` 调 `metering.context.snapshot()` 把当前 UsageContext 序列化为 dict，作为 `_child_entry` 的 `usage_ctx_snapshot` 参数传入；子侧入口（`os.setpgrp()` 之后）调 `apply_snapshot()` 还原。ledger 的 `_connect()` 检测到 `os.getpid()` 变化会重开 sqlite 连接（fork/spawn 后旧 handle 不可用），子进程因此拿到自己的 handle 写共享 WAL db。

快照/还原复用 `context.py` 的 `snapshot()`/`apply_snapshot()`，process_runner 增加两处调用；这条边界没有单独的 `metering/subprocess.py` 模块。result pickle 不回传 usage_summary——面板直接从 ledger 查子进程写入的 event，回传没有额外价值。

## 9. 可扩展留口（已设计，未实现）

- per-user：UsageEvent 加 `user_id`（默认单用户，多租户由 usage_scope 注入），query(group_by=["user_id"])。
- 限流/告警：UsageRecorder.record() 暴露 post-record hook 列表（register_usage_hook），事件驱动不阻塞热路径。
- 导出：ledger 后端接口加 export(format, filters) 或 JSONL 镜像后端。
- 远程聚合：后端换 push OTLP/collector 实现，event schema 不变。

## 10. 与 UsageTracker 的边界

`UsageTracker`（`context/usage.py`）与 `UsageLedger` 职责分离，并存且互不读写。

Tracker 是 **compaction 预算状态机**：给 `ContextEngine.prepare/compact` 回答上一轮真实 input_tokens 是多少、cache 命中率多少、是否该压缩——热路径 sub-μs 读、按 session 缓存、持久化到 session meta `_usage`。那份持久化是压缩决策的状态，不是计费账本。

Ledger 是**计费记账**：append-only、跨进程、带 per-model / per-source / 时间序列。

两者消费者、生命周期、数据形状不同，合并只会造成耦合而非简化。`UsageState` 与 session meta `_usage` 原样保留，因为 compaction 在读。

metering 复用但不修改：`models.py:calculate_cost`、`_event_parsing.py:extract_usage`、`dispatcher/persistence.py` 消息列、`types.py:Usage/UsageCost`。

## 附录：实现状态

第 1–8 节与第 10 节已实现。`webui/routes/usage.py` 查 ledger，提供 `/api/usage/summary` 与 `/api/usage/trend`（day/hour bucket 时序，加 by_kind 分解，入参支持 since/until）；前端面板绘制趋势折线、by_source 横条、per-model 表与成本卡。

两点值得记下，因为仅看设计推不出代码形状：

- 记账在**终止 event 处、yield 之前**触发，由 `recorded` flag 防双计。async generator 的消费者（`agent_loop`）在收到 done event 时直接 return，把生成器挂起在 `yield` 处——放在循环之后的代码永不执行。
- 原 `agent/compaction/` 目录（约 1180 行）是死代码，无任何 import、动态引用或副作用加载，已删除；其中没有任何路径需要 usage scope。

已设计未实现：第 9 节的扩展留口，以及 CLI `op usage` 命令。
