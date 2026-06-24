# 上下文压缩 — 行业对比与改进方案

> 状态: **部分实现**。dispatcher 主循环已完整；agentic function 内部缺失。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/summarize.py`、`context/persistence.py`。

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
