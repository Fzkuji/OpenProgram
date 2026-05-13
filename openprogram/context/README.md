# openprogram/context — 上下文管理

OpenProgram 每个 turn 喂给 LLM 的内容由这个包决定：系统提示怎么拼、历史保留哪些、token 预算怎么分、什么时候触发 compaction、compaction 之后历史怎么持久化。

整个包由 10 个单一职责的文件组成，由 `DefaultContextEngine` 编排。设计目标是把 Claude Code、Hermes、OpenClaw 三个参考系统各自的强项拿过来，再加上 OpenProgram 独有的 DAG 持久化层。

## 总体流程

每个 turn 进入 dispatcher 后：

1. `engine.on_session_start(session_id)` — 预热 UsageTracker 缓存
2. `engine.prepare(agent, session, history, model, tools)` — 同步走 6 步组装出 `TurnPrep`
3. dispatcher 检查 `prep.budget_pct`
   - `≥ 0.85` → 内联跑 `engine.compact(user_initiated=False)`，把 LLM summary 写进 DAG，再次 `prepare`
   - 否则继续
4. dispatcher 把 `prep.agent_messages` 喂给 agent_loop，跑 LLM
5. `engine.after_turn(session_id, usage, prep, on_event)` — 真实 provider usage 喂回 UsageTracker；越过 0.70 阈值时发 `compaction_recommended` 事件
6. 用户手动点 `/compact` → `trigger_compaction()` 走 `engine.compact(user_initiated=True)`

下面按职责分层逐层展开。

---

## 1. 系统提示装配

文件：`system_prompt.py::build_system_prompt(agent)`

把 5 类信息按固定顺序拼成一段，包在 `── Agent prompt ──` 和 `── End of agent prompt ──` 之间，模型一眼能看到边界。

**装配顺序**

```
[1] 身份 banner    "You are <name> (agent_id=<id>). Users may address you via: <mentions>."
[2] 工作区文件      AGENTS.md  → SOUL.md  → USER.md  （按顺序读三个，空的跳过）
[3] 内联 prompt    agent.system_prompt  （用户在 agents show 里编辑的）
[4] Skill 索引     "Skills available on demand:" + 一行一个启用的 skill 的 name+一句话描述（前 20 条）
[5] Memory 块      BuiltinMemoryProvider().system_prompt_block()  （持久化 memory 快照）
```

**为什么是这个顺序**：越靠前的越稳定，prefix cache 命中率越高。身份和工作区文件是几乎不变的；inline prompt 偶尔改；skill 列表会随启用/禁用变；memory 每天都在长。把不变的放前面，整段 system prompt 的 cache 命中率最高。

**实现细节**

- 接受 AgentSpec 对象或 dict，内部用 `_attr(obj, name, default)` 统一访问，webui 传 profile dict、CLI 传 AgentSpec 都能用
- 任何一层抛异常都被吞掉，最差情况退化到只返回 `agent.system_prompt`，绝不让系统提示装配失败拖崩整个 turn
- 工作区文件读取走 `openprogram.agents.workspace`，命中文件系统但有内部缓存
- Skill 索引限 20 条，超出显示 `... (+N more)`，避免 skill 多的 agent 把 system prompt 撑爆

## 2. Token 预算

三个文件分工：

```
tokens.py        估算单条消息和整段历史的 token 数；提供 real_context_window
budget.py        把 context_window 切成 system / history / tools_schema / output_reserve 四段
usage.py         缓存 provider 返回的真实 usage，下一轮 prepare 时用真实数代替估算
```

### 2.1 真实 context window

`real_context_window(model)` 读 `model.context_window`（输入+输出的总容量），**不是** `model.max_tokens`（这是输出上限，通常只占总窗口的 10–30%）。这点容易踩坑——之前的实现读 `max_tokens`，导致在 32K 的 max_tokens 上估算预算，但实际窗口是 200K，触发 compaction 时利用率只到 16%。

### 2.2 Token 估算

`estimate_message_tokens(msg)` 和 `estimate_history_tokens(history)`：

- 优先 tiktoken（OpenAI 系模型精确）
- 回退到字符比例估算：`_is_cjk(text)` 判断主体是不是 CJK，CJK 用 1.3 char/tok，ASCII 用 3.8 char/tok。一段中英混排会按 CJK 字符占比加权。
- 估算的是输入 token，不包含 prompt template overhead，所以会略低于 provider 报数，hybrid 模式（见 2.3）会修正这一点。

### 2.3 BudgetAllocator

`BudgetAllocator.allocate(context_window, system_prompt, history, tools)` 返回 `BudgetAllocation`：

```
context_window      模型真实总窗口
system_prompt       系统提示 token（estimate_message_tokens）
history             历史 token（estimate_history_tokens）
tools_schema        工具 JSON schema 的 token（json.dumps + 每工具 5 tok 描述 overhead）
output_reserve      预留给 assistant 输出，默认 16384，最低 25% 总窗口
input_used          system + history + tools_schema
input_budget        context_window - output_reserve
input_used_pct      input_used / input_budget
headroom            input_budget - input_used
```

`output_reserve` 的 25% 下限是必须的：模型生成时会一口气吐到 reserve 上限，如果 reserve 太小，长回答会被截断；太大，输入端可用 token 不够。25% 是 Claude Code 和 Hermes 实测下来的折中点。

### 2.4 UsageTracker

`UsageTracker` 维护一个线程安全的 in-memory 缓存，持久化到 `SessionDB.extra_meta._usage`：

```
last_prompt_tokens          provider 上一轮报的 input_tokens
last_cache_read_tokens      provider 上一轮报的 cache_read_tokens
cumulative_prompt_tokens    会话累计
cumulative_completion       会话累计
turn_count                  这个会话总 turn 数
compaction_count            这个会话总 compact 次数
source                      "estimate" | "provider" | "cached"
```

**Hybrid 估算**：`prepare()` 里如果 `usage.source == "provider"`，说明上一轮拿到了真实数。我们信 provider 报的 `last_prompt_tokens`（作为已固定前缀的 token 数），再加上本轮新增消息的本地估算，得到一个"信前半、估后半"的混合数。比纯估算准很多，比每次重新估全部历史也快很多。

`on_session_end(session_id)` 会把 in-memory cache flush 回 SessionDB。

**对比**

```
                       OpenProgram   Claude Code   OpenClaw   Hermes
真实 context_window     有            有            无         部分
Tools schema 计入预算   有            有            无         部分
Provider usage 反馈     有            部分          无         无
CJK-aware 字符估算      有            无            无         无
```

## 3. 历史瘦身（aging，不调 LLM）

文件：`aging.py::TurnAger` + `references.py::ReferenceTracker`

每个 turn 都跑一次，把旧的、大的、没人再用的 tool_result 块替换成占位符，省 token。**不调 LLM、不动消息结构、不动 SessionDB**——只在 prepare 的内存副本里替换 extra.blocks 里的 content 字段。

### 3.1 三闸门

一条 tool_result 块要被 redact，必须**同时**通过三个闸门：

```
turn 距离闸门       消息所在 turn 离当前 ≥ keep_recent_turns（默认 4 个 assistant turn）
wall-clock 闸门     消息 timestamp 离现在 ≥ keep_recent_seconds（默认 60 秒）
引用闸门             ReferenceTracker 没有把这条消息标记为 cited
```

每条单独失败都不动。turn 距离闸门防止 agent 还在用刚抓的数据；wall-clock 闸门防止 agent 在 10 秒内连发 8 个 tool_call 时把刚来的输出也 redact 掉；引用闸门保护 agent 还在分析的旧输出。

通过三闸门后，再看 tool_result 的 content token 数 ≥ 800 才动手——更小的 redact 收益太低（占位符本身也占字数）。

替换后的内容：`[Old tool result content cleared (was N tokens)]`，并在 block 上加 `_redacted: true` 标记，下次 prepare 时遇到这个标记就跳过（避免反复处理）。

### 3.2 保护前 N

`protect_first_n` 默认 2。前 2 条消息（通常是用户的初始任务描述）永远不被 aging 触碰，即使过了几小时也保留原文。否则在长会话里 LLM 会忘了一开始要干嘛。

### 3.3 ReferenceTracker

判断"旧消息是不是还在被后续消息引用"。算法不追求精确，只要 catch 90% 的真实引用：

**Distinctive 子串提取**（regex）：

```
路径          [A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+         长度 ≥ 8
hex/数字 id    [a-f0-9]{6,} | [0-9]{6,}
反引号片段     `(.{3,40})`
CamelCase     [A-Z][A-Za-z0-9]{3,}                     长度 ≥ 4
```

去停用词（the / and / True / False / TextContent / AssistantMessage / ...），每条消息最多保留 32 个 distinctive 子串。

**引用判定**：扫历史一遍，对每对 (i, j) i<j，检查消息 i 的 distinctive 子串是否出现在消息 j 的 text 里。命中一次就把 i 标为 cited，标完就跳过后续检查。O(messages²) 但每条只比 32 个短串、有 early-exit，几百条消息毫秒级。

ReferenceMap 在 prepare 开头构建一次，aging 和 summarisation 都共享。

**对比**

```
                       OpenProgram   Claude Code   OpenClaw   Hermes
Tool-result aging       有            有            无         无
wall-clock 闸门         有            有            无         无
turn 距离闸门           有            有            无         无
引用追踪保护            有            无            无         有
保护前 N 条             有            无            无         有
800-tok 大小门槛        有            无            无         无
```

## 4. 历史压缩（compaction，调 LLM）

文件：`summarize.py::Summarizer` + `prompts.py` + `persistence.py::Persister`

**触发条件**

```
自动     prep.budget_pct ≥ 0.85   dispatcher 在 LLM 调用前内联跑
手动     /compact 或 trigger_compaction(keep_recent_tokens=N)
推荐     prep.budget_pct ≥ 0.70   只发事件不动手，UI 显示提示
```

### 4.1 找切点 `find_cut_index`

从末尾倒走累计 token，越过 `keep_recent_tokens`（默认 20000）后再向前 snap 到下一个 user 消息边界——保证 kept tail 一定从 user 消息开始，否则 LLM 看到一上来就是 assistant 回复会困惑。

切点 ≤ `protect_first_n` 时返回 0（不切），避免把保护区切掉。

`keep_recent_tokens` 可以通过 `engine.compact(keep_recent_tokens=...)` 临时覆盖，用户手动 `/compact 200` 时走的就是这条路。

### 4.2 链式 summary

不是每次都从头总结，而是基于上一次的 summary 增量更新。

```
prepare 时        从 session.extra_meta._last_summary_text 读出上次 summary
                  Summarizer.summarise(previous_summary=...)
                  → 命中走 UPDATE_PROMPT，没有走 FRESH_PROMPT
compact 完成后    把新 summary 写回 session.extra_meta._last_summary_text
```

**FRESH_PROMPT** 让 LLM 按 5 段结构产出第一次的 summary：

```
1. User intent       用户总目标（1-2 句）
2. Decisions         用户表达的每一条具体指令/约束（接近原话）
3. Work completed    动过的文件、跑过的命令、得出的结论（带路径和 id）
4. Outstanding       用户还在等什么、有什么 dangling 问题
5. Active context    需要知道的活动状态（"连着 db X，开着文件 Y，env Z 是 W"）
```

**UPDATE_PROMPT** 包一个 `<previous-summary>` 标签把上一版扔给 LLM，让它"合并新消息、删过期细节、刷进度"，同样输出 5 段结构。冲突时新消息为准。

**SYSTEM_PROMPT** 框定 summariser 的工作风格：要具体（路径、id、命令名、错误信息），不要 hedging、不要前言"Here is a summary..."、不要 moralising。

### 4.3 LLM 失败兜底

`Summarizer.summarise` 包了一层 try/except：

```python
try:
    text = await self._llm_summary(...)        # 调 provider
    fell_back = False
except Exception as e:
    text = self._structural_summary(prefix)    # deterministic 兜底
    fell_back = True
    err = f"{type(e).__name__}: {e}"
```

`_structural_summary` 按 role + 60 字符 head 列出每条要折叠的消息：

```
[user] explain the database schema in section 4
[assistant] The schema has three tables: users, sessions, messages...
[user] now refactor the message table to add...
```

这样即使 provider 401 / 网络挂 / token 超限，summary 也能产出**某种**总结，agent loop 不会因为 compaction 失败而崩溃。CompactResult 里带 `fell_back_to_structural=true` 标记和 `error` 字段，调用方可以日志告警。

### 4.4 可取消

`summarise(cancel_event=threading.Event)`。LLM 调用前后都检查 `cancel_event.is_set()`，set 了就 raise CancelledError 走 structural 兜底。用户在长 summary 跑到一半中断时，agent 不会卡死。

### 4.5 持久化到 DAG

`Persister.insert_summary_node(session_id, summary_text, cut_idx, history)`：

1. 生成 `summary_id = "summary_" + uuid4()[:10]`
2. 写一条 `compactionSummary` 行：

```
id            summary_xxx
role          system
content       "[Previous conversation summary]\n<summary_text>"
parent_id     None                  这是关键——summary 作为新链的根
timestamp     first_kept_ts - 1e-6  必须早于第一条 kept 消息
type          compactionSummary
source        compaction
extra         {"compaction": true}
```

3. Re-parent kept tail：`for original in history[cut_idx:]`，每条复制一份，分配 `k_<uuid>` 新 id，`parent_id` 指向链上前一节点。原行不删。
4. `db.set_head(session_id, prev)` 推进到新链尾。

**timestamp 的微妙之处**：`get_branch` 走 parent_id 找到所有节点后用 `ORDER BY timestamp ASC` 排序返回。如果 summary 用 `time.time()`（=now）作 timestamp，而 kept tail 保留了原始 timestamp（小得多），summary 会排在 kept tail 后面——branch[0] 不是 summary 而是某条 kept 消息，模型看到的第一条就成了 user 的"turn 18"，完全失序。

解决方案：summary 的 timestamp 设为 `history[cut_idx].timestamp - 1e-6`，比第一条 kept 消息早一微秒。这是 SQL `ORDER BY ASC` 唯一能保证 summary 排第一的方式。

**为什么 parent_id = None**：如果 summary 的 parent_id 指向被折叠的最后一条原消息，`get_branch` 会从 head 沿 parent_id 走回去，一路走到 summary，然后继续走到 summary.parent，再继续走到 m0——把整段被折叠的历史又走出来了，compaction 等于白做。设成 None 才是真切断。

### 4.6 原始历史不丢

原历史行（m0..m_{cut-1}）还在 messages 表里，只是不在当前活动分支上。`get_descendants(m0)` 还能拿到。这意味着：

- 用户可以"checkout"到压缩前的状态对比
- 调试时能看出"LLM 是基于哪段原始上下文产生了这个 summary"
- 误压缩可以撤销（理论上）

代价：DB 体积不会因为 compact 而缩小。但 SQLite 不在乎几千条消息，活动分支变短才是 LLM 在乎的。

**对比**

```
                            OpenProgram   Claude Code   OpenClaw   Hermes
自动 compact（阈值触发）     有            有            无         有
手动 /compact                有            有            无         有
增量 summary 链              有            有            无         有
LLM 失败 structural 兜底     有            无            无         无
可取消的 summarisation       有            有            无         无
DAG re-parent 保留原始分支   有            无            无         无
压缩后可回放调试             有            无            无         无
```

## 5. 生命周期与插件化

文件：`engine.py::ContextEngine`（ABC）+ `DefaultContextEngine`（默认实现）

### 5.1 ABC 暴露的 hook

```
on_session_start(session_id)          会话载入/创建时调用一次，预热缓存
ingest(session_id, message)            新消息落 DB 时调用，默认 no-op；自定义引擎可以维护索引
prepare(agent, session, history,       每个 LLM 调用前跑，返回 TurnPrep
        model, tools)
should_recommend(prep)                 budget_pct ≥ 0.70？
should_auto_compact(prep)              budget_pct ≥ 0.85？
compact(agent, session_id, model,      触发 compaction，返回 CompactResult
        on_event, previous_summary,
        user_initiated, cancel_event,
        keep_recent_tokens)
after_turn(session_id, usage, prep,    LLM 返回后调用，喂真实 usage、发推荐事件
           on_event)
on_session_end(session_id)             会话关闭时调用，flush in-memory state
```

每个 hook 都可以单独 override。`DefaultContextEngine` 把这些 hook 接到 6 个组件单例（usage / budgets / ager / summarizer / persister / references）上，子类只要换一个组件就能改一个维度的行为。

### 5.2 组件注入

`DefaultContextEngine.__init__` 全部走 keyword-only 注入：

```python
DefaultContextEngine(
    usage_tracker=...,         # 换 UsageTracker 子类
    budget_allocator=...,      # 换 BudgetAllocator 子类
    ager=...,                  # 改 aging 策略
    summarizer=...,            # 换 summary 模型/prompt
    persister=...,             # 换持久化层
    references=...,            # 换引用追踪算法
    recommend_pct=0.65,        # 阈值微调
    auto_compact_pct=0.80,
)
```

测试里这是主要的 stub 路径——传一个 fake summarizer 就能跑 compact 流程而不调 provider。

### 5.3 注册表与按 agent 选引擎

```python
CONTEXT_ENGINE_REGISTRY: dict[str, ContextEngine] = {}

register_engine(engine)                  把引擎按 engine.name 注册
get_engine(name)                         按名取，找不到 fallback default
resolve_engine_for(agent)                按优先级取引擎
```

`resolve_engine_for(agent)` 的优先级：

```
1. agent.context_engine 字段        per-agent 显式指定
2. config.context.engine             全局配置
3. default_engine                    兜底
```

dispatcher 每个 turn 调一次 `resolve_engine_for(agent_profile)`，所以同一进程不同 agent 走不同引擎是免费的。

**对比**

```
                              OpenProgram   Claude Code   OpenClaw   Hermes
ContextEngine ABC 插件化       有            无            有         部分
Per-agent engine override      有            无            有         有
完整生命周期 hooks             有            无            有         部分
组件级注入                     有            无            有         无
```

## 6. 文件清单

```
__init__.py        公开 API：default_engine / resolve_engine_for / TurnPrep / CompactResult / ...
types.py           纯 dataclass：UsageSnapshot / BudgetAllocation / TurnPrep / CompactResult / ReferenceMap
tokens.py          token 估算 + real_context_window + CJK 比例
usage.py           UsageTracker：provider usage 缓存 + hybrid 估算
budget.py          BudgetAllocator：context_window → 四段切分
references.py      ReferenceTracker：distinctive substring 引用图
aging.py           TurnAger：三闸门 + 保护前 N 的 tool_result redact
prompts.py         SYSTEM / FRESH / UPDATE 三个 summariser 提示
summarize.py       Summarizer：找切点 + LLM 调用 + structural 兜底
persistence.py     Persister：写 compactionSummary 节点 + re-parent kept tail
system_prompt.py   build_system_prompt：5 段分层装配
engine.py          ContextEngine ABC + DefaultContextEngine + 注册表
```

## 7. 数据类型

```
UsageSnapshot          last_prompt_tokens / last_cache_read_tokens
                       cumulative_prompt_tokens / cumulative_completion_tokens
                       turn_count / compaction_count
                       source: "estimate" | "provider" | "cached"

BudgetAllocation       context_window / system_prompt / history / tools_schema / output_reserve
                       input_used / input_budget / input_used_pct / headroom

TurnPrep               system_prompt: str
                       agent_messages: list[Message]      给 agent_loop 用
                       history_dicts: list[dict]          aging 后的 dict 视图
                       budget: BudgetAllocation
                       usage: UsageSnapshot
                       tool_results_redacted: int
                       tokens_freed_by_aging: int
                       references_protected: int
                       summary_id: str | None             当前活动 summary id
                       decision_path: list[str]           遥测：这一轮做了哪些动作
                       budget_pct: float                  budget.input_used_pct
                       context_window: int                budget.context_window

CompactResult          ok: bool
                       summary_text / summary_id
                       summarised_count / summarised_tokens
                       tokens_before / tokens_after
                       duration_ms
                       used_previous_summary: bool
                       reason: "auto" | "manual" | "recovered"
                       error: str | None
                       fell_back_to_structural: bool

ReferenceMap           cited_tool_use_ids: set[str]
                       quoted_snippets_by_msg: dict[str, set[str]]
                       last_built_at: float
```

## 8. 与其他平台的整体对比

下面是把上面所有维度合并起来的总表。"有" = 原生支持，"部分" = 有但不完整，"无" = 不支持。

```
维度                              OpenProgram   Claude Code   OpenClaw   Hermes
─────────────────────────────────  ───────────   ───────────   ────────   ──────
分层 system prompt 装配            有            有            有         有
工作区文件 (AGENTS/SOUL/USER.md)   有            有            有         无
Skill 索引                         有            有            无         部分
持久化 memory 块                   有            有            无         无

真实 context_window 预算           有            有            无         部分
Tools schema 计入预算              有            有            无         部分
Provider usage 反馈                有            部分          无         无
CJK-aware token 估算               有            无            无         无
Hybrid 估算 (信前缀 + 估增量)      有            无            无         无

Tool-result aging（保留结构）      有            有            无         无
wall-clock 时间闸门                有            有            无         无
turn 距离闸门                      有            有            无         无
800-tok 大小门槛                   有            无            无         无
引用追踪保护                       有            无            无         有
保护前 N 条                        有            无            无         有

自动 compact（阈值触发）           有            有            无         有
手动 /compact                      有            有            无         有
推荐事件（达到 70% 提示）          有            部分          无         无
增量 summary 链                    有            有            无         有
LLM 失败 structural 兜底           有            无            无         无
可取消的 summarisation             有            有            无         无

DAG re-parent 保留原始分支         有            无            无         无
压缩后可回放调试                   有            无            无         无

ContextEngine ABC 插件化           有            无            有         部分
Per-agent engine override          有            无            有         有
完整生命周期 hooks                 有            无            有         部分
组件级注入                         有            无            有         无
```

**OpenProgram 独有的设计点**

- DAG re-parent 持久化（compact 后原始分支不丢，可回放）
- LLM summary 失败 structural 兜底（agent loop 永远不因 compaction 崩溃）
- 三闸门 + 保护前 N + 引用追踪同时启用
- CJK 字符比例独立估算
- 组件级注入（不仅是 engine 整体替换，单个组件也能换）
- Hybrid 估算（信 provider 的前缀 + 估本轮增量）

**主要借鉴**

- Claude Code 的三层 compaction（aging + auto + manual）和 wall-clock 闸门
- Hermes 的引用追踪 + 保护前 N + 增量 summary 链
- OpenClaw 的 ContextEngine ABC + 完整生命周期 hooks
