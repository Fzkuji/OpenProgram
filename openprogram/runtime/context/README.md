# openprogram/runtime/context — 上下文管理

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

节点 schema（`Call`、`Graph`、role 常量）由会话存储持有，定义在 `openprogram/state/store/session/nodes.py`；本目录的 `nodes.py` 只放基于它的上下文计算（`render_context`、spine、fold history 等）。

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
                                called_by = 调起这次 LLM 调用的 Agent method id（如果有）
role="code"   ─ 一次函数调用    input  = arguments dict
                                output = 函数返回值
                                called_by = 调起这个函数的 LLM 或父函数 id
```

**DAG 上的"边"有三套**（权威定义见
[`docs/reference/design/runtime/dag/overview.html`](https://github.com/FuzzyKuji/OpenProgram/blob/main/docs/reference/design/runtime/dag/overview.html)
§3，本文其余段落里出现的旧名 `called_by` 一律读作 `caller`）：

```
caller       调用关系图   "谁调起我"           形成 fan-out 的嵌套树
predecessor  对话链       "我在聊天里接在谁后面"  分支靠它区分（同一前驱多个孩子 = fork）
reads        上下文引用    "我看了哪些节点"      fan-in 的 LLM prompt 组成
```

**时间顺序由 `seq` 表达，不是图的边**。`seq` 是节点 append 到 DB 时单调递增的整数，按 seq 升序排就是发生顺序。`predecessor` 表达的是对话顺序而非时间顺序：两者的区别正是分支能存在的原因——retry 出来的兄弟节点 seq 更大，但 `predecessor` 与被替换的节点相同。

`caller` 与 `predecessor` 都是 `Call` 的顶层字段，metadata 里没有镜像。

`Graph` 是一个轻量容器：`nodes: dict[id → Call]` + `_next_seq` 整数。`add(node)` 自动分配 seq；`update(node_id, **fields)` 用于"入口 append 占位、出口 update output"的 Agent method 生命周期（这是 DAG 中**唯一**违反 append-only 的操作，专门支持实时观察）。

**算法 helper**：

```
last_user_message(graph)             找最近一条 user-role Call
linear_back_to(graph, target)        seq ≥ target.seq 的所有节点，按 seq 升序
branch_terminals(spawn_id, graph)    沿 called_by 链找每个分支的末端
branch_internal(spawn_id, term, g)   单个分支从 spawn 到 terminal 的内部节点链
fold_history(current_id, graph)      把历史按 turn 折叠：每个 prior turn 留 (user, final-llm) 对
compute_reads(graph,                 按 expose / render_range 算出
              head_seq=...,           Agent method 下次 LLM 调用的 reads
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

`append(node)` 是单调 append-only——重复 append 同 id 报错。`append` 时自动给 `node.seq` 赋值（如果 caller 没指定）。`update(node_id, **fields)` 用于 Agent method 出口填占位节点的 output，同时刷 FTS。`load()` 把整个 session 重建成内存 Graph。`search(query, limit)` 走 FTS5 在 session 内搜，模块级 `search_across_sessions(db, query)` 跨 session 搜。

### 0.3 Session 抽象 `session.py`

`DagSession` = `SessionMeta` + `Graph` + `GraphStore` + `DagRuntime`，一个对象包一整个会话的所有状态。

`DagSessionManager(db_path)` 是多会话管理器：`create()` / `load()` / `list()` / `rename()` / `delete()` / `exists()`，一个 SQLite 文件可以存任意多个 session，按 `updated_at` 排序返回。

### 0.4 SessionDB 适配器 `session_db.py`

`DagSessionDB` 提供**跟老 `SessionDB` 同名同签名的 API**——`create_session` / `append_message` / `get_messages` / `get_branch` / `set_head` / `list_branches` / `get_branch_token_stats` / `search_messages` 等 14 个方法，但底下走 DAG schema。

外部 `from openprogram.runtime.context.session_db import SessionDB` 其实是 `SessionDB = DagSessionDB` 的别名（`agent/session_db.py` 是个 16 行 re-export）。dispatcher / channels / webui 代码一行没改、全部跑在 DAG 后端上。

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

`DagRuntime` 是个"给定 graph + reads，调一次 LLM"的纯函数 wrapper，不感知 `Agent method`，`reads` 谁来算由调用方负责——`chat.py` 的 chat 循环用它。跟它并列的是 `agent/runtime.py` 里的 `Runtime`（provider 基类，`Agent method` 注入的就是它）：`Runtime.exec` 自己感知 `Agent method`——从 ContextVar 取当前 graph/frame、调 `compute_reads` 算 reads、把 llm 节点写回 DAG（见 §0.7）。两者都不再有任何内存里的 tree Context。

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

### 0.7 Agent method ↔ DAG 集成

集成靠两个 ContextVar，没有粘合层（旧的 `bridge.py` 已删）：

- `_store`（`context/storage.py`）—— 本回合的 `GraphStore`。
- `_call_id`（`context/scope.py`）—— 当前正在执行的 `Agent method` 的 code 节点 id；写在它内部的任何节点都拿这个做 `called_by`。

`context/scope.py` 存放调用核心设置、`Context` 读取的任务局部状态：`_call_id`、`_forced_predecessor`、`default_expose()`，以及 workflow 调用点（`CallSite`、`current_site()`、`current_entry()`）。`agent/call_state.py` 和 `agent/workflow_site.py` 从这里导入同一批对象，所以 `context` 不导入 `agent`，每个 ContextVar 也只有一份。

**turn 入口**（`dispatcher.process_user_turn`，以及 webui 的 `run` 路径）：
- `_store.set(GraphStore(db_path, session_id))` —— 装上本会话的 DAG store
- `run` 路径还会 `_call_id.set(命令消息 id)`，把整个 program 执行子树挂到那条命令下
- `finally` 里 reset 这两个 token

**`Agent method` 装饰器**（`function.py`）：
- 入口生成 `pending_id`，`_append_function_call_entry` → 若 `_store` 已装，`store.append(Call(role=code, output=None, status="running", called_by=_call_id.get()))` ——webui / 调试器能实时看到正在跑的函数。函数 docstring 一并写进该节点的 `metadata.doc`，`render_dag_messages` 渲染 code 节点时把它拼在 `函数名(参数)` 前面,所以函数自己的 LLM 调用能在上下文里看到"这个函数是干什么的"
- 入口 `_call_id.set(pending_id)` ——内部的 `runtime.exec` / 嵌套 `Agent method` 据此做 `called_by` 标记
- 装饰器的 `system=` 在调用期间盖到注入的 runtime 上(`_apply_system` / `_restore_system`,save/restore),`runtime.exec` 读 `runtime.system` 才拿得到——所以 `Agent method(system=...)` 是经由 runtime 生效的,不进 DAG 节点
- 出口 `_update_function_call_exit` → `store.update(pending_id, output=..., status=...)` ——填回同一个节点的 output / status，**不写第二个节点**；异常路径写 `output={"error": ...}` + `status="error"`
- `_store` 没装时全部 no-op（standalone 跑 Agent method 不依赖持久化）

**`Runtime.exec`**（`agent/runtime.py`）：
- `store = _store.get()`；为 `None` 时退化成普通 LLM 调用，完全不碰 DAG
- 装了 store：从 `_call_id` 拿当前 frame 节点 → `compute_reads(...)` 算出本次要读哪些节点 → `render.py::render_dag_messages` 渲染成 messages → 调 provider
- 成功后 append 一个 `Call(role=llm, name=model, output=reply, called_by=_call_id.get())` 到 store；顶层（无 frame）时 `called_by=""`

**`compute_reads(graph, head_seq, frame_entry_seq, render_range)`**（在 `nodes.py`）—— 纯函数，按 DAG 结构算下一次 LLM 调用要读哪些节点：

```
顶层聊天                       seq ≤ head_seq 的所有节点，按 seq 升序
Agent method 内部          frame_entry_seq 之前 + frame 内部新增节点
expose='io' 的 code Call        把它内部 llm Call（called_by == this）从 reads 里去掉
expose='full'                  保留内部所有 llm Call
render_range['callers']         pre-frame 节点最多保留多少（默认 None 不限，0 = 完全隔离）
render_range['subcalls']        in-frame 节点最多保留多少（默认 -1 不限；frame 自然看见自己进度，子函数内部由各自 expose 负责藏）
```

所有节点**始终写进 DAG**（不影响存储），expose 只影响算 reads 时的可见性过滤。`compute_reads` + `render_dag_messages` 是 `runtime.exec` 的 prompt 拼接路径。

---

## 总体流程

每个 turn 进入 dispatcher 后：

1. `engine.on_session_start(session_id)` — 预热 UsageTracker 缓存
2. `engine.prepare(agent, session, history, model, tools)` — 同步走 6 步组装出 `TurnPrep`
3. dispatcher 检查 `prep.budget_pct`
   - `≥ 0.80`（`AUTO_COMPACT_PCT`）→ 内联跑 `engine.compact(user_initiated=False)`，把 LLM summary 写进 DAG，再次 `prepare`
   - 否则继续
4. dispatcher 把 `prep.agent_messages` 喂给 agent_loop，跑 LLM
5. `engine.after_turn(session_id, usage, prep, on_event)` — 真实 provider usage 喂回 UsageTracker
6. 用户手动点 `/compact` → `trigger_compaction()` 走 `engine.compact(user_initiated=True)`

下面按职责分层逐层展开。

---

## 1. 系统提示装配

文件：`components.py::build_system_prompt(agent)`

把 5 类信息按固定顺序拼成一段，包在 `── Agent prompt ──` 和 `── End of agent prompt ──` 之间，模型一眼能看到边界。

**装配顺序**

```
[1] 身份 banner    "You are <name> (agent_id=<id>). Users may address you via: <mentions>."
[2] AGENTS.md 链    全局 ~/.openprogram/AGENTS.md → 项目根 … → 工作目录（共用 32 KiB）
[3] 内联 prompt    agent.system_prompt  （用户在 agents show 里编辑的）
[4] Skill 索引     "<available_skills>" + 每份启用 skill 的 name + 描述首行（截 250 字符）+ SKILL.md 绝对路径
[5] Memory 块      get_backend().system_prompt()  （持久化 memory 快照）
```

**为什么是这个顺序**：越靠前的越稳定，prefix cache 命中率越高。身份和 AGENTS.md 是几乎不变的；inline prompt 偶尔改；skill 列表会随启用/禁用变；memory 每天都在长。把不变的放前面，整段 system prompt 的 cache 命中率最高。

**实现细节**

- 接受 AgentSpec 对象或 dict，内部用 `_attr(obj, name, default)` 统一访问，webui 传 profile dict、CLI 传 AgentSpec 都能用
- 任何一层抛异常都被吞掉，最差情况退化到只返回 `agent.system_prompt`，绝不让系统提示装配失败拖崩整个 turn
- AGENTS.md 由 `openprogram.runtime.context.agents_md` 读取；工作目录以下的 AGENTS.md 在文件工具触及时附在工具结果后（每会话一次）
- Skill 索引限 20 条，超出显示 `... (+N more)`，避免 skill 多的 agent 把 system prompt 撑爆

**注册的段落**：`components.py` 按层（L0/L1/L2）和 `order` 装配所有 `register(ContextComponent(...))` 注册的段落。本包只注册不依赖会话功能的段落；会话包导入时由 `conversation/context_registration.py` 注册 `open_work` 段（L1，order 35：本会话还欠用户的事），并通过 `session_stats.set_agent_profile_loader()` 提供 `/context` 拆分读取 Agent 配置的方式。`context` 不导入 `conversation`。

## 2. Token 预算

三个文件分工（文本的可加权重在 `state/store/session/weight.py`：中日韩和全角记 1，其余记 0.25，用来决定截断点，存储层的 `spill.py` 也用它）：

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

## 3. 旧工具结果（不老化）

旧的工具结果按本轮发出的样子原样再发，渲染从不把它们换成存根。逐轮老化每移动一次边界就改写一次已缓存的前缀；Codex、Claude Code、Pi 和试过又删掉的 OpenCode 1.19 都没有它，所以这里也不做。历史太长时由压缩（第 4 节）统一处理。设计和对比见 `docs/reference/design/context/overview.html` 的“Old tool results”。

## 4. 历史压缩（compaction，调 LLM）

文件：`summarize.py::Summarizer` + `prompts.py` + `persistence.py::Persister`

策略综合了 Claude Code、Hermes Agent、OpenClaw 三家的实战经验，每个参数都有明确出处：

```
参数                  默认值        来源
────────────────────  ────────────  ────────────────────────────────────────
trigger_pct           0.80          Hermes 50% 太激进 cache 失效频繁
                                    Claude Code 93% 太晚 compact 时已紧张
emergency_pct         0.95          紧急 fallback，保证下一轮不会爆窗
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

### 4.1 触发判定

```python
if prep.budget_pct >= AUTO_COMPACT_PCT:     # 0.80
    # 主动 compact — 仍有 budget 留给 summary call
    fire auto-compact
```

80% 时主动 compact 就享受充足的 summary 预算（call 本身要占 8-15K tokens），不必等到窗口逼近上限——那时连做摘要的余量都没有了。

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

不是每次都从头总结，而是基于这条链上生效的 summary 增量更新。

```
compact 输入      load_compaction_view(db, session_id, head_id) 渲染调用方指定的链：
                  生效的 summary（nodes.active_summary_for_chain）在前，未覆盖的轮次在后
                  → 生效的 summary 作为被覆盖前缀的第一条，和新轮次一起交给摘要器
compact 完成后    新 summary 节点一次写入（covers_ids 展开成真实轮次，并带 trigger、
                  model、前后占用、覆盖数、时间、render_role、restored_state）；
                  每条链用 active_summary_for_chain 选自己的 summary，图构建器同一规则
```

**SUMMARY_PROMPT** 一份分段提示词，每次（首次和滚动）都用，生效的旧 summary 作为被覆盖前缀的第一条交给它。要求输出这些 `## ` 段：

```
User intent / All user messages / Decisions and constraints / Completed work /
Current work / Next step / Relevant files
```

缺段时带 **NUDGE** 再问一次，仍缺段就失败（不写节点，计入 hold）；输出上限 20K，碰到上限也算失败。新 summary 作为带前缀的 user 消息渲染，旧节点按原样渲染。

**SYSTEM_PROMPT** 框定 summariser 的工作风格：要具体（路径、id、命令名、错误信息），不要 hedging、不要前言"Here is a summary..."、不要 moralising。它带上 **SUMMARY_RULES**（请求期压缩也用）：用户取消或替换的请求要标明，不算待办；助手和工具说的话要注明出处，不写成事实。

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
content       "The earlier part of this conversation was compacted into this summary:\n<summary_text>"
              （render_role=user；之前写的节点仍是 "[Previous conversation summary]" 前缀的 assistant）
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
should_auto_compact(prep)              budget_pct ≥ 0.80？
compact(agent, session_id, model,      触发 compaction，返回 CompactResult
        on_event, previous_summary,
        user_initiated, cancel_event,
        keep_recent_tokens)
after_turn(session_id, usage, prep,    LLM 返回后调用，喂真实 usage
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
nodes.py           基于 state/store/session/nodes.py 的 helpers (last_user_message /
                    linear_back_to / branch_terminals / branch_internal /
                    fold_history / render_context)
storage.py         GraphStore：SQLite 三表（sessions/nodes/nodes_fts），
                    append + update + FTS5
session.py         DagSession + DagSessionManager：一个 SQLite 文件多 session
session_db.py      DagSessionDB：跟老 SessionDB 同 API 的适配器（chat msg ↔ Call 映射）
runtime.py         DagRuntime：provider_call wrapper，exec → 写 llm-role Call
render.py          render_dag_messages：把 reads 节点列表渲染成 provider messages
chat.py            chat_turn：LLM↔tool 循环 + parse_tool_call

─── 上下文编排 ─────────────────────────────────────────────
__init__.py        公开 API：default_engine / resolve_engine_for / TurnPrep / CompactResult / ...
types.py           编排 dataclass：UsageState / BudgetAllocation / TurnPrep / CompactResult
tokens.py          token 估算 + real_context_window + CJK 比例
usage.py           UsageTracker：provider usage 缓存 + hybrid 估算
budget.py          BudgetAllocator：context_window → 四段切分
aging.py           TurnAger：三闸门 + 保护前 N 的 tool_result redact
prompts.py         SYSTEM / FRESH / UPDATE 三个 summariser 提示
summarize.py       Summarizer：找切点 + LLM 调用 + structural 兜底
persistence.py     Persister：写 compactionSummary 节点 + re-parent kept tail
engine.py          ContextEngine ABC + DefaultContextEngine + 注册表
components.py      系统提示段落注册表与装配（其他包可注册段落）
scope.py           调用核心写、Context 读的任务局部状态（_call_id、workflow 调用点）
```

## 7. 数据类型

```
UsageState          last_prompt_tokens / last_cache_read_tokens
                       cumulative_prompt_tokens / cumulative_completion_tokens
                       turn_count / compaction_count
                       source: "estimate" | "provider" | "cached"

BudgetAllocation       context_window / system_prompt / history / tools_schema / output_reserve
                       input_used / input_budget / input_used_pct / headroom

TurnPrep               system_prompt: str
                       agent_messages: list[Message]      给 agent_loop 用
                       history_dicts: list[dict]          aging 后的 dict 视图
                       budget: BudgetAllocation
                       usage: UsageState
                       tool_results_redacted: int
                       tokens_freed_by_aging: int
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

```

**DAG 节点类型**（底座，`openprogram/state/store/session/nodes.py`）：

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
Agent method 入口 placeholder      有        无            无         无
+ 出口 in-place update（实时观察）
FTS5 节点级全文搜索                    有        无            无         无
SessionDB 兼容适配器                   有        —             —          —

分层 system prompt 装配            有            有            有         有
分层 AGENTS.md                     有            有            有         无
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
- 聊天对话 + `Agent method` 内部调用统一落在同一张 DAG，按 seq 自然交错，没有"主聊天 DAG vs agent 内部 DAG"的切分
- `Agent method` 入口 append placeholder + 出口 update output 模式——函数运行期间 DAG 上就能看到 `status="running"` 节点，方便实时观察 / 调试 / webui 进度展示
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
