# 上下文压缩 — 节点降级方案

> 状态: **设计完成，待实现**。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/nodes.py`（`render_context`）。
> 关联: [`context-composition.md`](context-composition.md)（三层上下文构建）

---

## 1. 核心思路

上下文压缩 = **节点降级**。

DAG 保持完整不动，渲染时控制每个节点展示多少内容。token 不够了，就把旧节点降级成一行摘要或完全跳过。不用 LLM 做摘要，纯模板拼接。

聊天和函数调用用同一个算法——聊天只是"只有一层"的特例。

---

## 2. 节点的三种展示状态

| 状态 | 渲染行为 | token 开销 | 信息保留 |
|---|---|---|---|
| `full`（默认） | 完整渲染 input + output | 原始大小（几百到几千） | 全部 |
| `degraded` | 一行调用路径 | ~10 tokens | 知道做了什么、结果是什么 |
| `hidden` | 跳过，不参与渲染 | 0 | 无（DAG 中仍保留） |

和现有 `expose` 机制的关系：`expose` 控制函数内部对外部的可见性（函数作者声明，静态）。展示状态控制节点自身在渲染时的详细程度（压缩系统动态设置）。两者正交。

---

## 3. 压缩算法

### 3.1 触发条件

history tokens 占 context window 的比例超过阈值时触发：

| context window 大小 | 触发线 | 说明 |
|---|---|---|
| ≤ 200K | 70% | 小 context 下压缩更频繁 |
| > 200K | 80% | 大 context 下更宽松 |

### 3.2 压缩目标

压到 **20%**。

### 3.3 层级定义

DAG 的层级通过 `called_by` 链的深度自然确定：

```
层级 0（顶层对话）：called_by == ""
  └─ 层级 1（函数调用）：called_by → 层级 0 的节点
      └─ 层级 2（嵌套调用）：called_by → 层级 1 的节点
          └─ ...
```

纯聊天场景只有层级 0，嵌套函数调用有多层。算法不区分，统一处理。

### 3.4 算法步骤

```
输入：
  - nodes: 当前所有可见节点（render_context 输出）
  - tokens[i]: 每个节点的 token 数
  - context_window: 模型的 context window 大小
  - L0_tokens: 系统 prompt 占用（固定，不参与压缩）

输出：
  - 每个节点的展示状态（full / degraded / hidden）

算法：

1. T_current = sum(tokens[i] for all nodes) + L0_tokens
2. T_trigger = context_window * trigger_ratio  (70% 或 80%)
3. 如果 T_current ≤ T_trigger，不压缩，全部 full，返回

4. T_target = context_window * 20%
5. T_history_target = T_target - L0_tokens   # history 部分的目标
6. T_history_current = T_current - L0_tokens  # history 部分当前占用

7. 保留比例 R = T_history_target / T_history_current
   （例：35K / 135K ≈ 26%）

8. 按层级分组节点，每层内按时间排序（seq 从旧到新）

9. 每层保留最近的 ceil(count * R) 个节点为 full
   其余节点降级为 degraded

10. 计算降级后的总 token 数
    如果仍超 T_target，把最旧的 degraded 节点逐个改为 hidden
    直到总数 ≤ T_target
```

关键：**所有层用同一个保留比例 R**。不硬编码"第几层保留多少"。比例由触发时的实际 token 分布动态算出。

### 3.5 聊天 vs 函数调用

同一个算法，不需要分支：

| 场景 | 层级数 | 算法行为 |
|---|---|---|
| 纯聊天 | 1 层（depth=0） | 只有一层，保留最近 N 轮完整，前面降级 |
| 聊天 + 函数调用 | 2 层 | 每层各保留最近 N 个，前面降级 |
| 嵌套函数调用 | 3+ 层 | 每层各保留最近 N 个，前面降级 |

聊天是"只有一层"的特例。算法代码里没有 `if 聊天 else 函数调用` 这种分支。

### 3.6 降级后的展示

全部是模板拼接，不调 LLM。每种节点类型一个模板：

**聊天节点降级：**
```
[turn 3] User: 帮我分析这段代码的性能问题 → Assistant: 发现3个瓶颈，建议优化数据库查询
```

**工具调用节点降级：**
```
[call 5] search_papers("attention mechanism") → 8 results
[call 6] read_file("paper.pdf") → 12 pages
[call 7] analyze(findings) → 3 key insights
```

**函数调用节点降级（整个子树折叠）：**
```
[func] idea_generator(topic="LLM reasoning") → 5 ideas generated (8 sub-calls compacted)
```

模板规则：
- 聊天：`[turn N] User: {input[:50]} → Assistant: {output[:50]}`
- 工具：`[call N] {tool_name}({args_summary}) → {result_summary}`
- 函数：`[func] {func_name}({args_summary}) → {result_summary} ({n} sub-calls compacted)`

截取前 50 个字符作为摘要，超长用 `...` 截断。

---

## 4. 推演验证

### 4.1 场景 A：200K context，3 层嵌套

一个 research_agent 跑了一段时间后的状态：

```
压缩前（70%，140K）：
  L0 系统 prompt:         5K  (固定，不参与压缩)
  层级 0 对话 20 轮:     40K  (每轮 ~2K)
  层级 1 函数 15 轮:     30K  (每轮 ~2K)
  层级 2 嵌套 28 轮:     65K  (每轮 ~2.3K)
  ─────────────────────────
  总计:                 140K (70%)

目标 20%（40K），history 部分目标 = 40K - 5K = 35K
保留比例 R = 35K / 135K ≈ 26%
```

每层降级后：

| 层级 | 总轮数 | 保留 full | 降级 degraded | full tokens | degraded tokens | 合计 |
|---|---|---|---|---|---|---|
| 0 对话 | 20 | 最近 5 轮 | 前 15 轮 | 10K | 0.15K | 10.15K |
| 1 函数 | 15 | 最近 4 轮 | 前 11 轮 | 8K | 0.11K | 8.11K |
| 2 嵌套 | 28 | 最近 7 轮 | 前 21 轮 | 16.1K | 0.21K | 16.31K |
| **合计** | | | | | | **34.57K** |

```
压缩后：
  L0 系统 prompt:   5K
  history:        34.57K
  ─────────────────────
  总计:           39.57K (19.8%) ✅
```

### 4.2 场景 B：200K context，纯聊天 70 轮

```
压缩前（70%，140K）：
  L0 系统 prompt:         5K
  层级 0 对话 70 轮:    135K  (每轮 ~1.9K)
  ─────────────────────────
  总计:                 140K

保留比例 R = 35K / 135K ≈ 26%
保留最近 18 轮 full，前 52 轮 degraded

压缩后：
  L0 系统 prompt:   5K
  18 轮 full:     34.2K
  52 轮 degraded:  0.52K
  ─────────────────────
  总计:           39.72K (19.9%) ✅
```

### 4.3 场景 C：1M context

1M context 下，80% 触发线 = 800K。需要 400+ 轮对话才会触发。极少发生，但机制要有——防止 research_agent 跑几百轮工具调用。

算法相同：`R = 200K / 800K = 25%`，每层保留最近 25% 的节点。

### 4.4 场景 D：函数内部触发

层级 2 的函数内部跑了 50 轮工具调用，自己就超了 70%：

```
只对层级 2 自己的 50 个节点做降级
保留最近 13 轮（26%），前 37 轮降级
```

不需要动层级 0 和层级 1 的节点——它们由上层的压缩管道负责。每层独立管自己的预算。

---

## 5. 渲染管道（改造后）

```
DAG 节点
  │
  ▼
render_context（筛选可见节点：callers / subcalls / expose）  ← 已实现
  │
  ▼
apply_degradation（按比例降级旧节点）                        ← 待实现
  │
  ▼
render_dag_messages（渲染为消息列表）                        ← 已实现，需扩展
  │   full 节点 → 完整渲染
  │   degraded 节点 → 一行模板
  │   hidden 节点 → 跳过
  │
  ▼
文本级兜底（工具老化 / 微压缩）                              ← 已实现
  │
  ▼
LLM 调用
```

节点级控制在粗筛之后、渲染之前插入。文本级作为兜底——如果单个 full 节点的输出特别大（比如一个工具返回了 10K 文本），工具老化会截断它。

---

## 6. 和现有机制的关系

| 现有机制 | 保留/替代 | 说明 |
|---|---|---|
| 预算分配（budget.py） | **保留** | 提供 context_window 和各槽大小，压缩算法用它算触发条件和目标 |
| 自动压缩（engine.py compact） | **替代** | 现有的 LLM 摘要被模板降级替代——零额外 LLM 调用，更快更便宜 |
| 微压缩（microcompact.py） | **保留** | 空闲时清理旧 tool result 的大段输出，和节点降级互补（一个管节点间，一个管节点内） |
| 工具老化（tool_aging/） | **保留** | 截断过长工具输出（节点内部压缩），和节点级降级（节点间压缩）互补 |
| render_context（nodes.py） | **扩展** | 在渲染管道中加入 apply_degradation 步骤 |

---

## 7. Agentic function 内部的压缩

### 7.1 现状

`runtime.exec`（agentic function 的 LLM 调用路径）完全没有压缩。不调 ContextEngine、不检查预算、不做工具老化。超了直接 API 报错（`context_length_exceeded`，标记为不可重试）。

### 7.2 方案

在 `runtime.exec` 调 LLM 前接入同一套压缩管道：

```python
# runtime.exec 每次调 LLM 前
nodes = render_context(graph, frame_node_id, ...)
token_count = estimate_tokens(nodes)

if token_count > context_window * trigger_ratio:
    nodes = apply_degradation(nodes, target=context_window * 0.2)

messages = render_dag_messages(graph, nodes)
# → 调 LLM
```

复用同一套 `render_context` + `apply_degradation` + `render_dag_messages` 管道。和 dispatcher 主循环走完全相同的逻辑，不需要单独写。

---

## 8. 和 Claude Code 的对比

| | Claude Code | OpenProgram |
|---|---|---|
| 数据结构 | 线性消息列表 | DAG（调用树） |
| 压缩触发 | ~83.5% 自动（五步管道） | 70%/80% 自动（单步节点降级） |
| 压缩方式 | LLM 摘要旧消息（需额外 LLM 调用） | 模板降级（纯字符串拼接，零 LLM） |
| 压缩粒度 | 消息文本 | DAG 节点（可折叠整个子树） |
| agentic 路径 | 子 agent 各自独立 context | 统一 render_context 管道 |
| 核心优势 | — | 隐藏一个深层子树一步释放大量 token；零 LLM 开销 |

---

## 9. 实施计划

| 步骤 | 做什么 | 依赖 | 优先级 |
|---|---|---|---|
| 1 | Call 节点加 `visibility` 字段（full/degraded/hidden），默认 full | 无 | 高 |
| 2 | 实现 `apply_degradation(nodes, budget)` —— 等比保留、模板降级 | 步骤 1 | 高 |
| 3 | 实现三种降级模板（聊天/工具/函数） | 无 | 高 |
| 4 | `render_dag_messages` 支持 degraded 节点的模板渲染 | 步骤 3 | 高 |
| 5 | dispatcher 主循环接入：LLM 调用前检查 + 降级 | 步骤 2, 4 | 高 |
| 6 | runtime.exec 接入：同样的检查 + 降级 | 步骤 2, 4 | 高 |
| 7 | 废弃 engine.py 的 LLM compact（被模板降级替代） | 步骤 5, 6 | 低 |
| 8 | 端到端测试：纯聊天 + 函数调用 + 嵌套调用三种场景 | 步骤 5, 6 | 高 |
