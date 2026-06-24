# 上下文压缩 — 行业对比、文本级管道与 DAG 节点级控制

> 状态: **部分实现**。文本级管道（§1-§4）dispatcher 主循环已完整，agentic function 内部缺失。DAG 节点级控制（§5-§8）待实现。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/summarize.py`、`context/persistence.py`、`context/nodes.py`（`compute_reads`）。

---

## 1. 行业对比

### 1.1 各框架压缩机制

| 框架 | 触发策略 | 压缩方式 | 多层管道 | agentic/子任务压缩 |
|---|---|---|---|---|
| **Claude Code** | 83.5% 自动 + `/compact` 手动 | 五层管道：budget reduction → snip → microcompact → context collapse → auto-compact | 是（五层，每层针对不同压力源） | 子 agent 有独立 context，自动压缩 |
| **Hermes** | 两级：50% agent compressor + 85% gateway safety net | 结构化摘要模板（Goal/Progress/Decisions/Files/Next Steps），增量更新 | 是（两级） | 有，gateway 层兜底所有路径 |
| **Cursor** | 动态上下文发现 | 不压缩，改为按需拉取（写文件 + tail/grep/jq 读） | 否（避免压缩，走按需检索） | N/A |

### 1.2 关键设计差异

| 维度 | Claude Code | Hermes | 我们 |
|---|---|---|---|
| **层数** | 5 层（每层针对一种压力） | 2 层（50% + 85%） | 4 层（预算 + 自动压缩 + 微压缩 + 工具老化） |
| **微压缩** | 有（idle-gap 触发，无 LLM） | 有（tool output pruning，在 compressor 之前） | 有（idle-gap 触发，和 Claude Code 同源） |
| **摘要格式** | 自由格式 + 可自定义指令 | 结构化模板 + 增量更新 | 自由格式 |
| **agentic 路径** | 子 agent 各自独立压缩 | gateway 层兜底 | **缺失** |

---

## 2. 我们的现状

### 2.1 四层压缩管道（dispatcher 主循环）

每次 LLM 调用前，按顺序执行：

```
历史消息 → ④ 工具老化 → ③ 微压缩 → ② 预算检查 → ① 自动压缩（如需）→ 发给 LLM
```

| 层 | 组件 | 触发条件 | 做什么 | 用 LLM 吗 |
|---|---|---|---|---|
| ④ 工具老化 | `tool_aging/` | 每轮，距当前 >3 turn 的工具结果 | 截断过长输出（>4000 字符中间截断）；旧结果替换为 `[aged]` 一行 stub | 否 |
| ③ 微压缩 | `microcompact.py` | 空闲 >1 小时 | 清理旧 tool_result 大段输出，保留最近 5 条 | 否 |
| ② 预算检查 | `budget.py` | 每轮 | 把 context window 切成 4 个槽（system_prompt / tools / history / output_reserve），计算 `budget_pct` | 否 |
| ① 自动压缩 | `engine.py` + `summarize.py` | `budget_pct` > 80% | 用 LLM 把旧消息摘要成结构化文本，写入 DAG 作为 `compactionSummary` 节点 | **是** |

### 2.2 阈值

| 阈值 | 百分比 | 行为 |
|---|---|---|
| `RECOMMEND_PCT` | 70% | 发 `compaction_recommended` 事件通知前端 |
| `AUTO_COMPACT_PCT` | 80% | 自动触发 `compact()`，用 LLM 摘要 |
| `EMERGENCY_PCT` | 95% | 最后手段压缩 |

### 2.3 缺口：agentic function 内部

`runtime.exec`（agentic function 的 LLM 调用路径）**完全没有压缩**：

- 不调 ContextEngine
- 不检查预算
- 不做工具老化
- 不做微压缩
- 超了直接 API 报错（`retry.py` 把 context overflow 标记为不可重试）

当 agentic function 内部的工具循环（`max_iterations=20`）积累大量历史时，会直接撞墙。

---

## 3. 改进方案

### 3.1 runtime.exec 路径加 compact-and-retry

在 `runtime.exec` 的 LLM 调用处加压缩-重试机制：

```python
# runtime.py — exec 内部（伪代码）
try:
    response = call_llm(messages, ...)
except ContextOverflowError:
    # 1. 对 DAG 内历史做工具老化（截断旧 tool result）
    messages = age_tool_results(messages)
    try:
        response = call_llm(messages, ...)
    except ContextOverflowError:
        # 2. 用 LLM 摘要旧历史（保留最近 N 轮）
        messages = summarize_old_turns(messages, keep_recent=3)
        response = call_llm(messages, ...)  # 还不行就真的报错
```

**两步降级**：

| 步骤 | 做什么 | 用 LLM 吗 | 延迟 |
|---|---|---|---|
| 1. 工具老化 | 截断旧 tool result 到 stub | 否 | 毫秒级 |
| 2. 历史摘要 | LLM 摘要旧 turn，保留最近 3 轮 | 是 | 秒级 |

### 3.2 预防性检查（更好）

不等报错再处理，而是在每次 `runtime.exec` 调 LLM 前主动检查：

```python
# 每次调 LLM 前
token_count = estimate_tokens(messages)
if token_count > context_window * 0.8:
    messages = age_tool_results(messages)
if token_count > context_window * 0.9:
    messages = summarize_old_turns(messages, keep_recent=3)
```

这和 dispatcher 的逻辑一致（80% 自动压缩），只是在 runtime.exec 路径也加上。

### 3.3 复用 vs 新写

现有的四层组件可以直接复用：

| 组件 | 能否复用 | 说明 |
|---|---|---|
| `tool_aging/` | **是** | 工具老化是纯消息变换，不依赖 dispatcher |
| `microcompact.py` | **是** | 同上 |
| `budget.py` | **部分** | `BudgetAllocator.allocate()` 是无状态的，可直接调用；但需要知道 context_window 大小 |
| `summarize.py` | **是** | `Summarizer.summarize()` 是独立的 LLM 调用 |
| `engine.py` | **否** | `ContextEngine` 绑定了 session/dispatcher 概念，runtime.exec 不经过它 |

**方案：在 runtime.py 中直接调用底层组件**（tool_aging + summarize），不经过 ContextEngine。保持 runtime 的独立性。

---

## 4. 实施计划

| 步骤 | 做什么 | 优先级 |
|---|---|---|
| 1 | runtime.exec 加预防性 token 检查 + 工具老化（无 LLM，零延迟） | 高 |
| 2 | runtime.exec 加 LLM 摘要降级（超阈值时用 summarize 压缩旧 turn） | 高 |
| 3 | runtime.exec 加 compact-and-retry（捕获 ContextOverflowError 后压缩重试） | 中 |
| 4 | 把 Hermes 的结构化摘要模板引入 summarize.py（Goal/Progress/Decisions/Files/Next Steps） | 低 |
| 5 | 把 Cursor 的按需检索策略作为备选（长文件不塞 context，写 tmp 文件再 grep） | 低 |

---

## 5. DAG 节点级上下文控制（新设计）

§1-§4 的文本级压缩在**消息文本**上操作——截断、摘要、替换。有效但粗糙：不感知 DAG 结构，无法利用调用树的层级关系做精准裁剪。

本节设计一套**节点级控制**：上下文压缩 = 控制哪些 DAG 节点参与渲染。节点不删除（DAG 完整保留），渲染时选择性跳过。

### 5.1 为什么需要节点级控制

Claude Code 是线性对话，压缩只有一个维度：时间（旧的消息摘要掉）。

我们是 DAG（通过 `called_by` 形成调用树），有两个维度：
- **时间**（seq 序号）：越旧的节点越不重要
- **深度**（called_by 链的层级）：越深的子调用越不重要

两种场景的可见性也不同：
- **会话级**（顶层对话）：模型看到同辈节点（user→assistant→user→assistant，线性序列）
- **函数调用级**（agentic function 内部）：模型只看到自己和父辈的调用（纵向），同辈函数的内部不可见（通过 `compute_reads` 的 `expose` 机制隐藏）

线性摘要无法利用这种结构。但节点级控制可以：隐藏整个子树比摘要每条消息更高效、更精准。

### 5.2 节点可见性状态

每个 Call 节点在渲染时有三种可见性（通过 `metadata.visibility` 控制，不改 Call dataclass）：

| 状态 | 渲染行为 | 信息保留 |
|---|---|---|
| `full`（默认） | 完整渲染 input + output | 全部 |
| `summary` | 渲染为一行摘要（`[aged] function_name(args...) → result_summary`） | 关键信息 |
| `hidden` | 完全跳过，不参与渲染 | 无（DAG 中仍保留） |

和现有 `expose` 机制的关系：`expose` 控制**函数的内部对外部的可见性**（io/llm/full/hidden）——是函数作者在定义时声明的，静态的。`visibility` 控制**节点自身在渲染时的详细程度**——是压缩系统动态设置的。两者正交。

### 5.3 层级精简策略

DAG 的层级通过 `called_by` 链的深度定义：

```
层级 0（顶层对话）：called_by == ""（root 节点）
  └─ 层级 1（函数调用）：called_by 指向层级 0 的节点
      └─ 层级 2（嵌套调用）：called_by 指向层级 1 的节点
          └─ ...
```

精简按"越旧越深越先隐藏"的原则，每个层级有独立的保留窗口：

| 层级 | 保留窗口（最近 N 轮完整） | 窗口外处理 | 原因 |
|---|---|---|---|
| 0（顶层对话） | 最近 N₀ 轮（如 10 轮） | `summary`（用户输入摘要 + 模型回复摘要） | 顶层是主线，需要更多上下文 |
| 1（函数调用） | 最近 N₁ 轮（如 5 轮） | 当前函数保留，同辈函数降为 `summary`，更早的 `hidden` | 函数调用有 expose 机制，同辈本就受限 |
| 2+（嵌套调用） | 最近 N₂ 轮（如 3 轮） | `hidden` | 越深越不重要，激进裁剪 |

参数 N₀、N₁、N₂ 不是固定值——由 token 预算动态决定（见 §5.5）。

### 5.4 精简算法

在 `compute_reads` 返回节点列表之后、`render_dag_messages` 渲染之前，插入一个精简步骤：

```python
def apply_visibility(
    graph: Graph,
    read_ids: list[str],
    token_budget: int,
) -> list[tuple[str, str]]:
    """对 read_ids 中的节点分配 visibility 状态。
    
    返回 [(node_id, visibility)] 列表，visibility 为 "full"/"summary"/"hidden"。
    
    算法：
    1. 计算每个节点的层级（called_by 链深度）
    2. 从最深、最旧的节点开始，逐步降级：
       full → summary → hidden
    3. 每次降级后重新估算总 token 数，直到 <= token_budget
    """
    # 第一遍：按 (depth DESC, seq ASC) 排序 — 最深最旧的排前面
    # 第二遍：从前往后，逐个降级，直到 fit
```

精简的顺序：
1. **层级 2+ 的旧节点** → hidden（最深最旧，信息价值最低）
2. **层级 1 的同辈函数**（不是当前正在执行的函数）→ summary
3. **层级 1 的旧工具调用** → summary
4. **层级 0 的旧轮次** → summary
5. **层级 0 的旧摘要** → hidden（摘要的摘要没意义，直接丢）

### 5.5 预算驱动的动态窗口

不固定 N₀/N₁/N₂，而是让 token 预算倒推窗口大小：

```python
def compute_windows(token_budget: int, node_stats: dict) -> dict:
    """根据 token 预算和节点统计，计算每个层级的保留窗口。
    
    node_stats: {depth: [(node_id, estimated_tokens), ...]} 按层级分组
    
    分配策略：
    - 层级 0 拿 60% 预算（主线对话）
    - 层级 1 拿 30% 预算（函数调用）
    - 层级 2+ 拿 10% 预算（嵌套调用）
    
    每个层级内，从最新的节点开始往前填充，直到用完该层的预算。
    """
```

这样总长度永远不会超过 context window——预算用完就停止填充，更旧的自动变 hidden。

### 5.6 和现有机制的关系

```
DAG 节点 ─┬─ compute_reads（expose 过滤，render_range 裁剪）← 已有
           │
           ├─ apply_visibility（节点级精简，本设计）          ← 新增
           │
           ├─ render_dag_messages（节点→消息文本）           ← 已有
           │
           └─ 文本级压缩管道（工具老化/微压缩/LLM 摘要）     ← 已有
```

节点级控制在 `compute_reads` **之后**、`render_dag_messages` **之前**插入：

1. `compute_reads` 先按 expose/render_range 做粗筛（这步决定哪些节点"有资格"出现）
2. `apply_visibility` 再按预算做精筛（这步决定有资格的节点以什么详细程度出现）
3. `render_dag_messages` 按 visibility 渲染（full → 完整文本，summary → 一行摘要，hidden → 跳过）

文本级压缩（工具老化/微压缩/LLM 摘要）作为**兜底**——如果节点级控制之后仍然超预算（比如单个 full 节点本身太大），再用文本级手段处理。

### 5.7 和 Claude Code 的差异

| | Claude Code | 我们 |
|---|---|---|
| 数据结构 | 线性消息列表 | DAG（调用树） |
| 压缩维度 | 时间（旧消息摘要） | 时间 + 深度（旧+深的节点优先隐藏） |
| 压缩粒度 | 消息文本（截断/摘要） | 节点（整体隐藏/摘要/完整） |
| 子树处理 | N/A（无子树） | 隐藏整个子树 = 一步去掉大量 token |
| 预算驱动 | 是（83.5% 阈值） | 是（按层级分配预算，倒推窗口） |

核心优势：**隐藏一个深层子树可能立刻释放几千 token，而文本级摘要需要逐条处理**。DAG 结构天然支持粗粒度裁剪。

---

## 6. 会话与函数调用的统一视角

会话（用户发消息→模型回复）和函数调用（agentic function 内部 runtime.exec）在 DAG 中都是节点。压缩策略应该统一，但需要处理可见性差异：

### 6.1 两种场景的可见性规则

| | 会话级（顶层） | 函数调用级（agentic function 内部） |
|---|---|---|
| 模型看到什么 | 所有同层节点（user→assistant 线性序列） | 自己和父辈的调用链（纵向），同辈函数通过 expose 控制 |
| 渲染入口 | `compute_reads(frame_entry_seq=-1)` → 全部节点 | `compute_reads(frame_entry_seq=N)` → N 之后的节点 + N 之前受 callers_cap 限制的 |
| 压缩时机 | dispatcher 每轮调用前 | runtime.exec 每次调 LLM 前（§3 的改进） |

### 6.2 统一精简策略

不管在哪个场景，`apply_visibility` 的输入都是 `compute_reads` 的输出（一个节点 id 列表）。精简逻辑相同：

1. 对输入列表中的节点计算层级
2. 按预算分配 visibility
3. 返回 `(node_id, visibility)` 列表

差异由 `compute_reads` 处理（它已经根据 `frame_entry_seq` 和 `render_range` 做了场景区分）。`apply_visibility` 不需要知道自己在会话级还是函数调用级。

---

## 7. summary 节点的生成

节点被标记为 `summary` 后，需要一行摘要文本。两种方式：

| 方式 | 适用 | 做法 |
|---|---|---|
| **模板摘要**（无 LLM） | 所有节点 | 从节点字段提取：`[{role}] {name}: {truncate(output, 80)}` |
| **LLM 摘要** | token 大的节点（>500 token） | 用 LLM 生成一句话摘要，缓存到 `metadata.summary_text` |

**默认用模板摘要**（零延迟）。LLM 摘要作为可选增强——只在空闲时预生成并缓存。

模板摘要示例：
- `[user] 请帮我分析一下这段代码的性能问题`
- `[llm] claude-opus-4-8: 代码有三个性能瓶颈：1) N+1 查询...`（截断到 80 字符）
- `[code] research_agent(topic="LLM agent"): {"papers": 5, "ideas": 3}`（截断输出）

---

## 8. 实施计划（DAG 节点级）

| 步骤 | 做什么 | 依赖 | 优先级 |
|---|---|---|---|
| D1 | 实现 `apply_visibility(graph, read_ids, token_budget)` 函数 | 无 | 高 |
| D2 | 在 `_render_history_messages` 中插入 apply_visibility 调用 | D1 | 高 |
| D3 | 修改 `render_dag_messages` 支持 visibility 参数（summary → 一行摘要，hidden → 跳过） | D1 | 高 |
| D4 | 模板摘要生成（从节点字段提取） | D3 | 高 |
| D5 | 预算驱动的动态窗口计算（`compute_windows`） | D1 | 中 |
| D6 | LLM 摘要预生成 + 缓存到 `metadata.summary_text` | D4 | 低 |
| D7 | 会话级 + 函数调用级统一验证（end-to-end 测试） | D1-D4 | 高 |
