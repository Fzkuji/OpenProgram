# openprogram/context — 上下文管理

OpenProgram 每个 turn 喂给 LLM 的内容由这个包决定：对话历史怎么存、节点怎么组织、系统提示怎么拼、token 预算怎么分、什么时候触发 compaction、compaction 之后历史怎么持久化。

整个包**分两层**：

```
数据底座（DAG）           节点定义、SQLite 存储、Session 管理、Runtime
                              ↑
上下文编排                 system_prompt / budget / aging / summarize / persistence
```

**底座**定义"对话历史长什么样、存哪里、怎么读写"——所有内容是一张有向无环图（DAG），只有一种节点类型 `Call`，靠 `role` 字段（`user` / `llm` / `code`）区分用户消息、LLM 调用、代码调用。时间顺序由节点的 `seq` 整数表达；两套真正的"边"是 `called_by`（谁调起我，嵌套关系）和 `reads`（这次 LLM 调用读了哪些节点，prompt 引用）。

**编排层**定义"每个 turn 给 LLM 看什么"——拿底座里的历史，按 token 预算切片、把旧 tool_result 脱水、必要时调 LLM 做增量总结，最后把折叠结果写回底座成为新节点。

设计目标：把 Claude Code、Hermes、OpenClaw 三个参考系统各自的强项拿过来，叠加 OpenProgram 独有的扁平 DAG 模型 + DAG re-parent 持久化。

---

## 0. 数据底座（DAG）

### 0.1 节点模型 `nodes.py`

**只有一种节点类型 `Call`**：

```python
@dataclass
class Call:
    id: str
    seq: int                    # append 时单调递增 — 唯一的时间排序依据
    created_at: float
    role: str                   # "user" | "llm" | "code"
    name: str                   # 模型 id / 函数名 / 用户名
    input: Any                  # 这次调用收到的输入
    output: Any                 # 这次调用产出的输出
    called_by: str              # 调起我的节点 id（嵌套边）
    reads: list[str]            # 这次 LLM 调用读了哪些节点（上下文边）
    metadata: dict              # passthrough 字段袋
```

三种 role 共用同一个数据结构：

```
role="user"   ─ 用户消息       output = 消息文本
role="llm"    ─ 一次 LLM 调用   input = {system: "..."}
                                output = 模型回复
                                reads  = prompt 中包含的节点 ids
                                called_by = 调起这次 LLM 调用的 @agentic_function id（如果有）
role="code"   ─ 一次函数调用    input  = arguments dict
                                output = 函数返回值
                                called_by = 调起这个函数的 LLM 或父函数 id
```

**DAG 上的"边"有两套**：

```
called_by    调用关系图   "谁调起我"           形成 fan-out 的嵌套树
reads        上下文引用    "我看了哪些节点"      fan-in 的 LLM prompt 组成
```

**时间顺序由 `seq` 表达，不是图的边**。`seq` 是节点 append 到 DB 时单调递增的整数，按 seq 升序排就是发生顺序。没有 `predecessor` 字段——那是把"时间链"和"逻辑边"混在一起的过度建模。

`Graph` 是一个轻量容器：`nodes: dict[id → Call]` + `_next_seq` 整数。`add(node)` 自动分配 seq；`update(node_id, **fields)` 用于"入口 append 占位、出口 update output"的 @agentic_function 生命周期（这是 DAG 中**唯一**违反 append-only 的操作，专门支持实时观察）。

**算法 helper**：

```
last_user_message(graph)             找最近一条 user-role Call
linear_back_to(graph, target)        seq ≥ target.seq 的所有节点，按 seq 升序
branch_terminals(spawn_id, graph)    沿 called_by 链找每个分支的末端
branch_internal(spawn_id, term, g)   单个分支从 spawn 到 terminal 的内部节点链
fold_history(current_id, graph)      把历史按 turn 折叠：每个 prior turn 留 (user, final-llm) 对
compute_reads(graph,                 按 expose / render_range 算出
              head_seq=...,           @agentic_function 下次 LLM 调用的 reads
              frame_entry_seq=...,
              render_range=...)
```

**Backward-compat 工厂**：保留 `UserMessage(content=...)` / `ModelCall(model=..., reads=..., output=...)` / `FunctionCall(function_name=..., arguments=..., result=..., called_by=...)` 三个名字作为工厂函数，内部都返回 `Call` 实例。让老代码改动最小。但 `isinstance(node, UserMessage)` 不再可用——必须用 `node.is_user()` / `is_llm()` / `is_code()` 检查 role。

`Call` 类上还有几个 property 别名：`.content` → `.output`、`.model` / `.function_name` → `.name`、`.arguments` → `.input`、`.result` → `.output`、`.system_prompt` → `.input["system"]`。

### 0.2 SQLite 存储 `storage.py`

`GraphStore(db_path, session_id)` 是一个 session 对应一个 GraphStore 实例。三张表：

```
sessions      id / title / created_at / updated_at / model / agent_id /
              source / extra_json / last_node_id
nodes         id / session_id / type / predecessor / created_at / seq / data_json
              ↑                  ↑     ↑
              所有节点 id        role   存 metadata.parent_id（legacy 消息树边）
nodes_fts     FTS5 虚表  text / session_id / node_id  全文搜索
```

`nodes.type` 列存 role 字符串（`user` / `llm` / `code`）；`nodes.seq` 是时间排序；`data_json` 把非列字段（input / output / reads / called_by / metadata）打包成 JSON。FTS5 索引节点的可搜索文本（output + name + 等）。

`nodes.predecessor` 列保留但**装的是 `metadata.parent_id`**——legacy 消息树的父 id。dispatcher / channels / webui 对消息树的依赖（fork / rewind / list_branches 等）通过这个 SQL 索引继续工作，不影响新 DAG 模型的纯净性。

`append(node)` 是单调 append-only——重复 append 同 id 报错。`append` 时自动给 `node.seq` 赋值（如果 caller 没指定）。`update(node_id, **fields)` 用于 @agentic_function 出口填占位节点的 output，同时刷 FTS。`load()` 把整个 session 重建成内存 Graph。`search(query, limit)` 走 FTS5 在 session 内搜，模块级 `search_across_sessions(db, query)` 跨 session 搜。

### 0.3 Session 抽象 `session.py`

`DagSession` = `SessionMeta` + `Graph` + `GraphStore` + `DagRuntime`，一个对象包一整个会话的所有状态。

`DagSessionManager(db_path)` 是多会话管理器：`create()` / `load()` / `list()` / `rename()` / `delete()` / `exists()`，一个 SQLite 文件可以存任意多个 session，按 `updated_at` 排序返回。

### 0.4 SessionDB 适配器 `session_db.py`

`DagSessionDB` 提供**跟老 `SessionDB` 同名同签名的 API**——`create_session` / `append_message` / `get_messages` / `get_branch` / `set_head` / `list_branches` / `get_branch_token_stats` / `search_messages` 等 14 个方法，但底下走 DAG schema。

外部 `from openprogram.agent.session_db import SessionDB` 其实是 `SessionDB = DagSessionDB` 的别名（`agent/session_db.py` 是个 16 行 re-export）。dispatcher / channels / webui 代码一行没改、全部跑在 DAG 后端上。

message dict ↔ Call 的双向映射在这一层：
- legacy `role="user"` ↔ `Call(role="user", output=content)`
- legacy `role="assistant"` ↔ `Call(role="llm", name=model, output=content)`
- legacy `role="tool"` ↔ `Call(role="code", name=function, input=arguments, output=result)`
- legacy `role="system"` ↔ `Call(role="llm", metadata.role="system")`（roundtrip 保留原 role）
- 非核心字段（source / attachments / `parent_id` / `_titled` flag / ...）一律塞 `node.metadata`，读出时 hoist 回 dict 顶层

`get_branch(session_id, head_msg_id)` 沿 `metadata.parent_id` 链回溯——保留 chat 消息树的 fork/rewind 语义；默认 head 读 sessions 表的 `last_node_id`。

### 0.5 LLM Runtime `runtime.py`

`DagRuntime(provider_call, graph, store, default_model)` — **干净的 DAG 原生 runtime**：

- `exec(content, reads, model, system, tools)` — 拿 `reads` 列表的节点 id 渲染成 messages，调 `provider_call`，把回复写成一个新的 llm-role `Call` 节点 append 到 graph，**同时持久化到 store**
- `add_user_message(content)` — 在 graph 上加一条 user-role Call
- `record_function_call(name, arguments, called_by, result)` — 调用方自己执行完函数后调这个登记结果

跟老的 `agentic_programming.Runtime` 区别：DagRuntime 不维护 tree Context、不操作 `_current_ctx` thread-local、不感知 `@agentic_function`。它就是一个"给定 graph + reads，调一次 LLM"的纯函数 wrapper。`reads` 谁来算，由调用方负责。

### 0.6 Chat 循环 `chat.py`

`chat_turn(user_input, runtime, tools, max_iterations)` — 一个 LLM↔tool 循环：

```
循环：
  1. fold_history 折叠之前 turn 的细节，加上本轮用户输入
  2. runtime.exec → 拿到 LLM 回复
  3. parse_tool_call(reply) → 看 LLM 要不要调工具
     - 调 → 执行 + 写 code Call + 继续循环
     - 不调（纯文本）→ 返回回复，结束
  4. 最多 max_iterations 轮，超过强制停
```

`parse_tool_call(text)` 容忍 LLM 用 bare JSON、围栏 JSON、或者文本里夹 JSON 多种格式表达"调用工具"。

### 0.7 @agentic_function ↔ DAG 集成

旧设计有一个粘合层 `bridge.py`；新设计把它的职责拆回各自归属，文件已删。

**`@agentic_function` 装饰器**（在 `openprogram/agentic_programming/function.py`）：
- 入口生成 `pending_id` + 立即 `runtime.append_node(Call(role=code, output=None, metadata.status="running"))` ——webui / 调试器可以实时看到正在跑的函数
- 入口 `_current_function_frame.set(pending_id)` ——单 slot ContextVar，给 `runtime.exec` 拿当前函数 id 做 `called_by` 标记
- 出口 `runtime.update_node(pending_id, output=..., status="success")` ——同一个节点的 output 字段被填上，**不写第二个节点**
- 异常路径：`output={"error": ...}` + `status="error"` 在原节点上更新

**`Runtime.exec` / `async_exec`** 调 LLM 成功后调内部 `_record_llm_call()`：
- 读 `_current_function_frame.get()` 拿到 caller_id
- append 一个 `Call(role=llm, name=model, output=reply, called_by=caller_id)` 到 store
- 没有 frame 时（顶层 chat）`called_by=""`
- 没有 store 时全部 no-op（standalone 跑 @agentic_function 不依赖持久化）

**`dispatcher.process_user_turn`** 入口：
- 创建一个 `Runtime`，调 `attach_store(GraphStore(...), head_id=user_msg_id)`
- `_current_runtime.set(runtime)` ——后续 `@agentic_function._inject_runtime` 通过 ContextVar 拿到这个 attached runtime
- finally：`runtime.detach_store()` + `_current_runtime.reset(token)`

**`compute_reads(graph, head_seq, frame_entry_seq, render_range)`**（在 `nodes.py`）—— 纯函数，按 DAG 结构算下一次 LLM 调用要读哪些节点：

```
顶层聊天                       seq ≤ head_seq 的所有节点，按 seq 升序
@agentic_function 内部          frame_entry_seq 之前 + frame 内部新增节点
expose='io' 的 code Call        把它内部 llm Call（called_by == this）从 reads 里去掉
expose='full'                  保留内部所有 llm Call
render_range['depth']           pre-frame 节点最多保留多少（0 = 完全隔离）
render_range['siblings']        in-frame 节点最多保留多少（最近 N 个）
```

所有节点**始终写进 DAG**（不影响存储），expose 只影响算 reads 时的可见性过滤。`compute_reads` 目前还没真正接入 `runtime.exec`——prompt 拼接还走 legacy `render_context`；这是有意保守的渐进，等下一阶段再切。

---

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

策略综合了 Claude Code、Hermes Agent、OpenClaw 三家的实战经验，每个参数都有明确出处：

```
参数                  默认值        来源
────────────────────  ────────────  ────────────────────────────────────────
trigger_pct           0.80          Hermes 50% 太激进 cache 失效频繁
                                    Claude Code 93% 太晚 compact 时已紧张
emergency_pct         0.95          紧急 fallback，保证下一轮不会爆窗
recommend_pct         0.70          只发 UI 事件不动手
keep_min_tokens       8_000         Claude Code 的 minTokens 思路
keep_max_tokens       40_000        Claude Code 的 maxTokens 直接采用
keep_ratio            0.10          按 window × 比例算 tail
keep_min_messages     5             Claude Code 的 minTextBlockMessages
                                    防止 tail 全是大 tool_result 没真对话
protect_first_n       3             Hermes（任务描述+首回复+澄清）
protect_last_n        20            Hermes，作为 tail-budget 之外的兜底
min_prompt_budget     8_000         OpenClaw 的 small-window cap
min_prompt_ratio      0.25          head 至少留 25% 窗口给 system+新轮
```

### 4.1 触发判定（两段式）

```python
if prep.budget_pct >= EMERGENCY_PCT:        # 0.95
    # 紧急 — 必须 compact，下一轮会爆
    fire auto-compact
elif prep.budget_pct >= AUTO_COMPACT_PCT:   # 0.80
    # 主动 compact — 仍有 budget 留给 summary call
    fire auto-compact
elif prep.budget_pct >= RECOMMEND_PCT:      # 0.70
    # 只发事件 UI 提示
    emit compaction_recommended
```

两段触发的意义：80% 时主动 compact 享受充足的 summary 预算（call 本身要占 8-15K tokens），95% 兜底保证不会真爆窗。

### 4.2 找切点 `find_cut_index`

六步算法：

```python
# 1. 算理想 tail 大小：按比例 + 上下界 clamp
desired = clamp(window × keep_ratio, keep_min_tokens, keep_max_tokens)

# 2. 小窗口 cap：head 必须留出 min_prompt 空间
min_prompt = min(min_prompt_budget, window × min_prompt_ratio)
effective_keep = min(desired, window - min_prompt)

# 3. 从尾巴往前累积 token + 数 text-block 消息
#    双闸门：token 数够 AND text-block 消息数够
for i from end downto protect_first_n:
    tail_tokens += estimate(messages[i])
    if has_text_block(messages[i]):
        tail_text_msgs += 1
    if tail_tokens >= effective_keep and tail_text_msgs >= keep_min_messages:
        cut = i; break

# 4. protect_last_n 兜底：cut 不能晚于 len - protect_last_n
cut = min(cut, len - protect_last_n)

# 5. protect_first_n 兜底：cut 不能早于 protect_first_n
cut = max(cut, protect_first_n)

# 6. snap 到 user 消息边界（tail 必须从 user 起）
while messages[cut].role != "user":
    cut += 1
```

**双闸门**（token 数 + text-block 消息数）是 Claude Code 的关键设计。没有 min_messages 时一个 50K 的 tool_result 会单独满足 token budget，结果 tail 里实际对话只剩 1-2 条。加上 ≥5 条 text-block 这个第二闸门后，tail 一定有最少 5 轮真对话原文。

**`has_text_block` 判定**：role 是 user/assistant/system 且 content 非空。纯 tool_result wrapper（content="" + extra.blocks 里有 tool_result）不算。

**不同窗口下的实际行为**：

```
context_window  desired_keep    max_safe_keep   effective_keep   tail 占比
──────────────  ──────────────  ──────────────  ──────────────  ─────────
8K              800 → 8K min    ~6K (cap)       6K              75%
32K             3.2K → 8K min   24K             8K              25%
64K             6.4K → 8K min   56K             8K              12.5%
200K            20K             192K            20K             10%
272K (GPT-5)    27.2K           264K            27.2K           10%
1M              100K → 40K max  ~992K           40K             4%
```

小窗口（8K-32K）自动 cap，大窗口（>400K）自动 ceiling。中间区间按比例伸缩。

### 4.3 legacy `keep_recent_tokens` 覆盖

`engine.compact(keep_recent_tokens=N)` 调用方可以传一个固定数字强制覆盖整套自适应逻辑，跳过 ratio/clamp/cap 直接用 N。webui `/compact <N>` 按钮走这条路，给用户手动微调的逃生通道。

### 4.4 链式 summary

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

### 4.5 LLM 失败兜底

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

### 4.6 可取消

`summarise(cancel_event=threading.Event)`。LLM 调用前后都检查 `cancel_event.is_set()`，set 了就 raise CancelledError 走 structural 兜底。用户在长 summary 跑到一半中断时，agent 不会卡死。

### 4.7 持久化到 DAG

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

### 4.8 原始历史不丢

原历史行（m0..m_{cut-1}）还在 messages 表里，只是不在当前活动分支上。`get_descendants(m0)` 还能拿到。这意味着：

- 用户可以"checkout"到压缩前的状态对比
- 调试时能看出"LLM 是基于哪段原始上下文产生了这个 summary"
- 误压缩可以撤销（理论上）

代价：DB 体积不会因为 compact 而缩小。但 SQLite 不在乎几千条消息，活动分支变短才是 LLM 在乎的。

**对比**

```
                              OpenProgram   Claude Code   OpenClaw   Hermes
两段式触发 (auto + emergency)  有            无            无         无
按 window 比例自适应 tail       有            部分          无         有
绝对值上下界 clamp              有            有            无         无
小窗口 cap（防死循环）          有            无            有         无
text-block 消息数双闸门         有            有            无         无
protect_first_n 保护任务描述    有            无            无         有
protect_last_n 保护最近 N 条    有            无            无         有
增量 summary 链                有            有            无         有
LLM 失败 structural 兜底       有            无            无         无
可取消的 summarisation         有            有            无         无
DAG re-parent 保留原始分支     有            无            无         无
压缩后可回放调试               有            无            无         无
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
─── 数据底座（DAG） ─────────────────────────────────────────
nodes.py           Call 节点类型 + Graph 容器 + helpers (last_user_message /
                    linear_back_to / branch_terminals / branch_internal /
                    fold_history / compute_reads) + backward-compat 工厂
                    (UserMessage / ModelCall / FunctionCall)
storage.py         GraphStore：SQLite 三表（sessions/nodes/nodes_fts），
                    append + update + FTS5
session.py         DagSession + DagSessionManager：一个 SQLite 文件多 session
session_db.py      DagSessionDB：跟老 SessionDB 同 API 的适配器（chat msg ↔ Call 映射）
runtime.py         DagRuntime：provider_call wrapper，exec → 写 llm-role Call
chat.py            chat_turn：LLM↔tool 循环 + parse_tool_call

─── 上下文编排 ─────────────────────────────────────────────
__init__.py        公开 API：default_engine / resolve_engine_for / TurnPrep / CompactResult / ...
types.py           编排 dataclass：UsageSnapshot / BudgetAllocation / TurnPrep / CompactResult / ReferenceMap
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

**DAG 节点类型**（底座，`nodes.py`）：

```
Call                   id: str
                       seq: int                    时间排序，append 时单调递增（-1 = 未存）
                       created_at: float
                       role: str                   "user" | "llm" | "code"
                       name: str                   model id / 函数名 / 用户名
                       input: Any                  prompt 信息 / arguments dict
                       output: Any                 reply text / result / None
                       called_by: str              调起我的节点 id（嵌套关系边）
                       reads: list[str]            这次 LLM 调用 prompt 包含的节点 id
                       metadata: dict              passthrough 字段袋（source / expose / status / parent_id / ...）

# backward-compat factory functions（返回 Call）：
UserMessage(content)         → Call(role="user", output=content)
ModelCall(model, reads,      → Call(role="llm", name=model, reads=reads, output=output,
        output, system_prompt)         input={"system": system_prompt})
FunctionCall(function_name,  → Call(role="code", name=function_name, input=arguments,
            arguments, result,           output=result, called_by=called_by)
            called_by)
```

**Backward-compat property accessors on Call**：

```
.content       → .output         (UserMessage 风格)
.model         → .name           (ModelCall 风格)
.system_prompt → .input["system"]
.function_name → .name           (FunctionCall 风格)
.arguments     → .input
.result        → .output
```

## 8. 与其他平台的整体对比

下面是把上面所有维度合并起来的总表。"有" = 原生支持，"部分" = 有但不完整，"无" = 不支持。

```
维度                              OpenProgram   Claude Code   OpenClaw   Hermes
─────────────────────────────────  ───────────   ───────────   ────────   ──────
扁平 DAG (单一 Call 类型 + role 区分)  有        无            无         无
called_by / reads 双套边               有        无            无         无
seq 时间排序（不混入图结构）           有        无            无         无
统一持久化（chat + agent 同 DAG）      有        部分          无         无
@agentic_function 入口 placeholder      有        无            无         无
+ 出口 in-place update（实时观察）
FTS5 节点级全文搜索                    有        无            无         无
SessionDB 兼容适配器                   有        —             —          —

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

- 扁平 DAG：所有事件都是同一种 `Call` 节点，靠 `role` 字段区分（user / llm / code）——一种数据结构表达全部历史，分支/嵌套/合并都靠 `called_by` 和 `reads` 两套边表达
- `seq` 整数承担时间排序，跟"图的边"完全解耦——纯净 DAG 模型
- 聊天对话 + `@agentic_function` 内部调用统一落在同一张 DAG，按 seq 自然交错，没有"主聊天 DAG vs agent 内部 DAG"的切分
- `@agentic_function` 入口 append placeholder + 出口 update output 模式——函数运行期间 DAG 上就能看到 `status="running"` 节点，方便实时观察 / 调试 / webui 进度展示
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
