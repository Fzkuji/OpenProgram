# 上下文压缩 — 节点降级方案

> 状态: **设计完成，待实现**。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/nodes.py`（`render_context`）。
> 关联: [`context-composition.md`](context-composition.md)（三层上下文构建）

---

## 1. 核心思路

上下文压缩 = **节点降级**。

DAG 保持完整不动，渲染时控制每个节点展示多少内容。token 不够了，就把旧节点降级。

**两类节点用不同的降级策略**：

- **聊天节点**（user + llm 对）：语义信息，降级成 LLM 生成的摘要（约 50-100 tokens/轮）
- **工具/函数调用节点**（tool_call / func_call）：结构化操作，降级成模板拼接的调用路径（约 10 tokens/个）

降级顺序：**先砍工具/函数节点（便宜，纯模板），不够再砍聊天节点（需要摘要）**。

## 1.1 DAG 的实际结构

```
root（根节点，系统创建）
├── user_1（用户消息 "你好"）                              ← 聊天节点
│   └── llm_1（大模型回复 "你好！有什么..."）                ← 聊天节点
├── user_2（用户消息 "帮我分析代码"）                       ← 聊天节点
│   └── llm_2（大模型回复）                                ← 聊天节点
│       ├── tool_call_1（bash "grep ..."）                 ← 工具节点
│       ├── tool_call_2（read_file "main.py"）             ← 工具节点
│       └── func_call_1（research_agent）                  ← 函数节点
│           └── llm_3（函数内部的 LLM 调用）                ← 函数内部聊天
│               ├── tool_call_3（search_papers）           ← 工具节点
│               └── tool_call_4（write_note）              ← 工具节点
├── user_3（用户消息 "继续"）                              ← 聊天节点
│   └── llm_4（大模型回复）                                ← 聊天节点
...
```

关键区分：
- **user + llm 对**（called_by 链：root → user → llm）= 一轮语义交互，压缩时要保留语义
- **llm 下的 tool_call / func_call** = 结构化操作，压缩时只需保留调用路径

---

## 2. 节点的三种展示状态

| 状态 | 渲染行为 | token 开销 | 信息保留 |
|---|---|---|---|
| `full`（默认） | 完整渲染 input + output | 原始大小（几百到几千） | 全部 |
| `degraded` | 降级展示（见下方两种策略） | 聊天 ~50-100；工具 ~10 | 摘要或调用路径 |
| `hidden` | 跳过，不参与渲染 | 0 | 无（DAG 中仍保留） |

**degraded 的两种策略：**

| 节点类型 | 降级方式 | 生成方式 | token 开销 |
|---|---|---|---|
| **聊天节点**（user + llm 对） | LLM 摘要：保留用户意图 + 模型关键结论 | Turn 结束时异步预生成，存 metadata | ~50-100 / 轮 |
| **工具/函数节点** | 模板路径：`{tool}({args}) → {result}` | 纯模板拼接，零 LLM | ~10-15 / 个 |

聊天节点的摘要示例：
```
[turn 3] 用户要求分析代码性能 → 模型发现3个瓶颈：数据库查询慢、循环冗余、缓存缺失
```

工具节点的路径示例：
```
[call 5] search_papers("attention mechanism") → 8 results
```

函数节点折叠示例（整个子树变一行）：
```
[func] idea_generator(topic="LLM reasoning") → 5 ideas generated (8 sub-calls compacted)
```

### 2.1 聊天摘要的生成时机

最佳方案：**每轮 turn 结束时异步生成**，存在节点 `metadata.summary` 中。

- 和 shadow git 的 turn-end commit 类似——turn 结束时顺手做
- 压缩触发时直接读缓存，不需要额外 LLM 调用
- 如果缓存不存在（旧节点没预生成），fallback 到截取前 50 字符

### 2.2 和 expose 机制的关系

`expose` 控制函数内部对外部的可见性（函数作者声明，静态）。展示状态控制节点自身在渲染时的详细程度（压缩系统动态设置）。两者正交。

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

DAG 的层级通过 `called_by` 链的深度自然确定，但需要区分两类节点：

```
root（根节点）
├── user_1 ─┐
│   └── llm_1 ─┘ 聊天对（depth=1，一轮语义交互）
├── user_2 ─┐
│   └── llm_2 ─┘ 聊天对（depth=1）
│       ├── tool_call_1    工具节点（depth=2）
│       ├── tool_call_2    工具节点（depth=2）
│       └── func_call_1    函数节点（depth=2）
│           └── llm_3      函数内部聊天（depth=3）
│               ├── tool_3 工具节点（depth=4）
│               └── tool_4 工具节点（depth=4）
├── user_3 ─┐
│   └── llm_3 ─┘ 聊天对（depth=1）
```

纯聊天场景只有 depth=1 的聊天对，嵌套函数调用有更多层。

### 3.4 算法步骤（两阶段降级）

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
5. T_history_target = T_target - L0_tokens
6. T_history_current = T_current - L0_tokens

────── 第一阶段：砍工具/函数节点（便宜，纯模板）──────

7. 收集所有工具/函数节点，按 (depth DESC, seq ASC) 排序
   （最深最旧的先砍）

8. 逐个将工具/函数节点从 full 降为 degraded（模板路径）
   每降一个，重新计算 T_current
   如果 T_current ≤ T_target，停止，返回

9. 如果函数节点的所有子节点都已 degraded/hidden，
   将整个子树折叠成一行：
   [func] {name}({args}) → {result} ({n} sub-calls compacted)

────── 第二阶段：砍聊天节点（需要摘要）──────

10. 如果第一阶段后仍超 T_target：
    收集所有聊天对（user + llm），按 seq ASC 排序（最旧的先砍）

11. 逐对将聊天节点从 full 降为 degraded（LLM 摘要）
    读 metadata.summary（turn 结束时预生成的缓存）
    无缓存则 fallback 到截取前 50 字符
    每降一对，重新计算 T_current
    如果 T_current ≤ T_target，停止，返回

────── 兜底：hidden ──────

12. 如果仍超 T_target，把最旧的 degraded 节点逐个改为 hidden
    直到 T_current ≤ T_target
```

关键设计决策：
- **先砍工具再砍聊天**：工具降级零成本（模板拼接），聊天降级需要摘要（可能触发 LLM）
- **不硬编码层级比例**：按 (depth, seq) 排序自然决定降级顺序
- **聊天对作为整体降级**：user + llm 一起降，不拆开

### 3.5 聊天 vs 函数调用

两类节点用不同策略，但在同一个算法内：

| 节点类型 | 降级策略 | 降级成本 | 降级后大小 | 降级顺序 |
|---|---|---|---|---|
| 工具/函数节点 | 模板路径 | 零（纯字符串拼接） | ~10-15 tokens | 第一阶段，优先砍 |
| 聊天节点（user+llm 对） | LLM 摘要（预生成缓存） | 零（读缓存）或一次 LLM 调用 | ~50-100 tokens | 第二阶段，工具砍完不够才砍 |

纯聊天场景没有工具节点，第一阶段跳过，直接进第二阶段砍聊天对。
纯函数调用场景（agentic function 内部）大多是工具节点，第一阶段通常就够了。

---

## 4. 推演验证

### 4.1 场景 A：200K context，3 层嵌套（两阶段降级）

一个 research_agent 跑了一段时间后的状态：

```
压缩前（70%，140K）：
  L0 系统 prompt:              5K  (固定)
  聊天对 20 轮:               40K  (每轮 ~2K)
  工具调用 43 个:             95K  (每个 ~2.2K)
    ├── 层级 1 函数内 15 个:  30K
    └── 层级 2 嵌套内 28 个:  65K
  ─────────────────────────────
  总计:                      140K (70%)

目标 20%（40K），history 目标 = 40K - 5K = 35K
需要释放 100K
```

**第一阶段：砍工具/函数节点**

按 (depth DESC, seq ASC) 顺序降级：先砍层级 2 最旧的，再砍层级 1 最旧的。

| 降级的节点 | 原 tokens | 降级后 tokens | 释放 |
|---|---|---|---|
| 层级 2 前 21 个工具 | 48.3K | 0.21K（模板路径） | 48.1K |
| 层级 1 前 11 个工具 | 22K | 0.11K（模板路径） | 21.9K |
| **第一阶段合计释放** | | | **70K** |

第一阶段后：140K - 70K = 70K，仍超 40K 目标。

**第二阶段：砍聊天节点**

按 seq ASC 顺序降级聊天对（最旧的先砍）：

| 降级的节点 | 原 tokens | 降级后 tokens | 释放 |
|---|---|---|---|
| 前 15 轮聊天对 | 30K | 1.2K（LLM 摘要，~80 tokens/轮） | 28.8K |
| **第二阶段合计释放** | | | **28.8K** |

```
压缩后：
  L0 系统 prompt:              5K
  5 轮聊天 full:              10K
  15 轮聊天 degraded (摘要):   1.2K
  11 个工具 full:             22K  (层级 1 最近 4 + 层级 2 最近 7)
  32 个工具 degraded (路径):   0.32K
  ───────────────────────────────
  总计:                      ~38.5K (19.3%) ✅
```

### 4.2 场景 B：200K context，纯聊天 70 轮

没有工具节点，第一阶段跳过，直接砍聊天对：

```
压缩前（70%，140K）：
  L0 系统 prompt:         5K
  聊天对 70 轮:         135K  (每轮 ~1.9K)

目标 20%（40K），history 目标 35K
需要释放 100K

第一阶段：无工具节点，跳过
第二阶段：砍最旧的聊天对

  前 52 轮降级为摘要：135K × 52/70 = 100.3K → 4.2K（80 tokens × 52）
  释放：96.1K

压缩后：
  L0 系统 prompt:         5K
  18 轮 full:           34.2K
  52 轮 degraded (摘要):  4.2K
  ─────────────────────────
  总计:                ~43.4K (21.7%) ≈ ✅
```

注意：聊天摘要（~80 tokens/轮）比工具路径（~10 tokens）大，所以纯聊天场景的压缩后占比略高于有工具的场景。如需更激进，可降低摘要长度或把最旧的摘要改为 hidden。

### 4.3 场景 C：1M context

80% 触发线 = 800K。需要 400+ 轮对话才会触发。极少发生，但机制要有。

算法相同，两阶段降级。1M context 下工具/函数节点通常足够多，第一阶段就能释放足够 token，聊天对很少需要降级。

### 4.4 场景 D：函数内部触发

层级 2 的函数内部跑了 50 轮工具调用，自己就超了 70%。

只对自己的 50 个工具节点做降级（第一阶段），不动聊天对和上层节点。工具节点降级成模板路径，通常一次就够。

```
50 个工具 × 2.2K = 110K
保留最近 13 个 full（28.6K），前 37 个降级（0.37K）
降级后：28.97K ✅
```

函数内部压缩几乎不需要第二阶段——内部节点绝大多数是工具调用，模板降级极其高效。

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
| 自动压缩（engine.py compact） | **改造** | 现有的 LLM 摘要改为 turn-end 预生成聊天摘要缓存（存 metadata.summary），压缩时直接读；不再在压缩触发时同步调 LLM |
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
| 压缩方式 | LLM 摘要旧消息（需额外 LLM 调用） | 两阶段：工具模板降级（零 LLM）+ 聊天预生成摘要（turn-end 异步） |
| 压缩粒度 | 消息文本 | DAG 节点（可折叠整个子树） |
| agentic 路径 | 子 agent 各自独立 context | 统一 render_context 管道 |
| 核心优势 | — | 隐藏一个深层子树一步释放大量 token；零 LLM 开销 |

---

### 8.1 参考来源

Claude Code 五层压缩管道的信息来自第三方源码逆向分析（非 Anthropic 官方文档）：

- 论文：[Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems](https://arxiv.org/html/2604.14228v1)（arXiv 2604.14228，VILA-Lab）
- 博客：[How Claude Code Compresses Context — The 5-Level Pipeline](https://harrisonsec.com/blog/claude-code-context-engineering-compression-pipeline/)
- 对比分析：[Inside Claude Code - Context Compaction](https://y-agent.github.io/inside-claude-code/04-context-compaction.html)

---

## 9. 实施计划

| 步骤 | 做什么 | 依赖 | 优先级 |
|---|---|---|---|
| 1 | Call 节点加 `visibility` 字段（full/degraded/hidden），默认 full | 无 | 高 |
| 2 | Turn-end 聊天摘要预生成：每轮结束异步生成 1-2 句摘要，存 `metadata.summary` | 无 | 高 |
| 3 | 实现工具/函数降级模板（纯字符串拼接） | 无 | 高 |
| 4 | 实现 `apply_degradation(nodes, budget)` —— 两阶段降级（先工具后聊天） | 步骤 1, 2, 3 | 高 |
| 5 | `render_dag_messages` 支持 degraded 节点的模板/摘要渲染 | 步骤 3 | 高 |
| 6 | dispatcher 主循环接入：LLM 调用前检查 + 降级 | 步骤 4, 5 | 高 |
| 7 | runtime.exec 接入：同样的检查 + 降级 | 步骤 4, 5 | 高 |
| 8 | 改造 engine.py compact：从同步 LLM 摘要改为读 turn-end 预生成的 summary 缓存 | 步骤 2, 6 | 中 |
| 9 | 端到端测试：纯聊天 + 函数调用 + 嵌套调用 + 混合场景 | 步骤 6, 7 | 高 |
