# 上下文压缩 — 节点降级方案

> 状态: **设计完成，待实现**。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/nodes.py`（`render_context`）。
> 关联: [`context-composition.md`](context-composition.md)（三层上下文构建）

---

## 1. 核心思路

上下文压缩 = **节点降级**。

DAG 保持完整不动，渲染时控制每个节点展示多少内容。token 不够了，就把旧节点降级。

关键简化：**函数调用结束后，自动折叠成一行（输入→输出），内部细节存磁盘。折叠后的节点和普通对话节点一样处理，不需要单独的压缩策略。**

## 1.1 函数节点的生命周期

函数节点有两种状态，压缩策略完全不同：

### 运行中的函数

正在跑的函数，内部的工具调用历史需要在上下文里——模型要看到之前做了什么才能决定下一步。

这部分由 `render_context` 管道处理（callers / subcalls / expose 机制），不需要额外的压缩逻辑。如果运行中内部历史太长，按正常的节点降级算法处理。

### 已完成的函数

函数运行结束后：

1. **自动折叠**：整个子树折叠成一行，只保留输入和输出
   ```
   [func] research_agent("LLM reasoning") → 5 ideas generated
   ```

2. **内部细节存磁盘**：函数内部的工具调用历史存到磁盘，给一个检索路径
   ```
   (详见 ~/.openprogram/sessions/<sid>/func_history/<node_id>.jsonl)
   ```
   类似 Claude Code 的 Microcompact（旧工具输出存磁盘，留 "stored on disk, retrievable by path"）。我们有 DAG 结构天然支持——函数节点的子树整体序列化到磁盘。

3. **当普通节点处理**：折叠后的函数节点和普通对话节点一样，后续按时间远近降级，不需要单独的策略。

4. **大模型可检索**：模型如果需要查看函数运行的具体过程，可以通过路径读取磁盘上的历史记录。

### 不同调用方式的处理

| 调用方式 | 函数结束后保留什么 |
|---|---|
| 大模型调用的函数 | 输入 + 输出（一行），内部存磁盘 |
| 嵌套函数（A 调 B 调 C） | 同上，每层各自折叠 |
| 用户手动调用的函数 | 只保留输入 + 输出 |

## 1.2 DAG 的实际结构

函数结束前（运行中）：
```
root
├── user_1 → llm_1                                        ← 对话节点
├── user_2 → llm_2                                        ← 对话节点
│       ├── tool_call_1（bash "grep ..."）                 ← 工具节点
│       ├── tool_call_2（read_file "main.py"）             ← 工具节点
│       └── func_call_1（research_agent, 运行中）           ← 函数节点（展开）
│           └── llm_3（函数内部 LLM 调用）
│               ├── tool_call_3（search_papers）
│               └── tool_call_4（write_note）
```

函数结束后（自动折叠）：
```
root
├── user_1 → llm_1                                        ← 对话节点
├── user_2 → llm_2                                        ← 对话节点
│       ├── tool_call_1（bash "grep ..."）                 ← 工具节点
│       ├── tool_call_2（read_file "main.py"）             ← 工具节点
│       └── [func] research_agent("topic") → 5 ideas      ← 折叠后的一行（内部存磁盘）
├── user_3 → llm_4                                        ← 对话节点
```

折叠后所有节点都是"扁平"的，压缩算法不需要区分类型。

---

## 2. 节点的三种展示状态

| 状态 | 渲染行为 | token 开销 | 信息保留 |
|---|---|---|---|
| `full`（默认） | 完整渲染 input + output | 原始大小（几百到几千） | 全部 |
| `degraded` | 降级展示（一行摘要） | ~50-100 tokens | 关键信息 |
| `hidden` | 跳过，不参与渲染 | 0 | 无（DAG 中仍保留） |

由于已完成的函数已自动折叠成一行（§1.1），压缩时面对的都是"扁平"节点（对话轮次 + 折叠后的函数调用 + 工具调用），不需要区分类型。

**degraded 统一用一行摘要**：

对话节点示例：
```
[turn 3] 用户要求分析代码性能 → 模型发现3个瓶颈：数据库查询慢、循环冗余、缓存缺失
```

工具节点示例：
```
[call 5] search_papers("attention mechanism") → 8 results
```

折叠后的函数节点示例（已经是一行了，进一步降级可 hidden）：
```
[func] research_agent("LLM reasoning") → 5 ideas (详见 ~/.openprogram/sessions/xxx/func_history/node_id)
```

### 2.1 摘要的生成时机

**每轮 turn 结束时异步预生成**，存在节点 `metadata.summary` 中。

- 和 shadow git 的 turn-end commit 类似——turn 结束时顺手做
- 压缩触发时直接读缓存，不需要额外 LLM 调用
- 如果缓存不存在（旧节点没预生成），fallback 到截取前 50 字符

### 2.2 和 expose 机制的关系

`expose` 控制函数内部对外部的可见性（函数作者声明，静态）。展示状态控制节点自身在渲染时的详细程度（压缩系统动态设置）。两者正交。

### 2.3 和 Microcompact 的关系

函数折叠时的"内部存磁盘留引用"和 Claude Code 的 Microcompact 思路一致：

| | Claude Code Microcompact | 我们的函数折叠 |
|---|---|---|
| 做什么 | 旧工具输出存磁盘，上下文留引用路径 | 已完成函数的内部历史存磁盘，上下文留一行 |
| 存哪里 | 磁盘文件 | `~/.openprogram/sessions/<sid>/func_history/<node_id>.jsonl` |
| 怎么检索 | 模型通过路径读回 | 模型通过路径读回 |
| 什么时候做 | 空闲时 / 每轮调用前 | 函数结束时（自动） |

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

### 3.3 算法步骤

由于已完成的函数已自动折叠（§1.1），压缩时面对的是扁平的节点序列，不需要区分类型。

```
输入：
  - nodes: 当前所有可见节点（render_context 输出，已折叠的函数是一行）
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
5. T_release = T_current - T_target

**降级：最旧的先砍**

6. 按 seq ASC 排序所有节点（最旧的先砍）
7. 逐个将节点从 full 降为 degraded（读 metadata.summary 预生成缓存，无缓存则截取前 50 字符）
   每降一个，T_release 减少 (原 tokens - 降级后 tokens)
   如果 T_release ≤ 0，停止，返回

**兜底：hidden**

8. 如果降级后仍超 T_target，把最旧的 degraded 节点逐个改为 hidden
   直到 T_release ≤ 0
```

关键简化：
- **不区分节点类型**：已完成的函数已经折叠成一行，和对话节点、工具节点一样处理
- **不硬编码层级比例**：按时间顺序（seq）自然决定降级顺序
- **运行中的函数内部**：如果正在执行的函数内部历史太长，对该函数的内部节点执行同样的算法（最旧的先降级）

---

## 4. 推演验证

### 4.1 场景 A：200K context，函数已完成

research_agent 已经跑完，返回了结果。之后用户继续对话。

```
压缩前（70%，140K）：
  L0 系统 prompt:                         5K  (固定)
  对话 + 折叠后函数节点 35 个:           135K
    ├── 20 轮对话（user+llm 对）:         40K  (每轮 ~2K)
    ├── 10 个工具调用:                    20K  (每个 ~2K)
    └── 5 个已完成的函数（每个折叠一行）:  75K  (包括输入输出摘要，大函数的输出可能较长)
  总计:                                  140K (70%)

目标 20%（40K），history 目标 = 35K
```

按 seq ASC 顺序降级最旧的节点：

```
压缩后：
  L0 系统 prompt:              5K
  10 个最近节点 full:         25K
  25 个旧节点 degraded:        2K  (~80 tokens/个)
  总计:                      ~32K (16%) ✅
```

所有节点——对话、工具、折叠后的函数——统一按时间远近砍，不区分类型。

### 4.2 场景 B：200K context，纯聊天 70 轮

```
压缩前（70%，140K）：
  L0 系统 prompt:         5K
  对话 70 轮:           135K  (每轮 ~1.9K)

目标 20%（40K），history 目标 35K

  前 52 轮降级为摘要：~4.2K（80 tokens × 52）
  18 轮保留 full：~34.2K

压缩后：
  L0:       5K
  18 full: 34.2K
  52 degraded: 4.2K
  总计:   ~43.4K (21.7%) ≈ ✅
```

### 4.3 场景 C：1M context

80% 触发线 = 800K。需要 400+ 轮对话才会触发。极少发生，但机制要有。算法相同。

### 4.4 场景 D：函数正在运行，内部历史超限

正在跑的函数内部有 50 个工具调用，内部历史超了 70%。

对函数内部的 50 个节点执行同样的算法：最旧的先降级。

```
50 个工具 × 2.2K = 110K
保留最近 13 个 full（28.6K），前 37 个 degraded（0.37K）
降级后：~29K ✅
```

函数内部大多是工具调用，降级后每个只有 ~10 tokens，效率极高。

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

### 7.3 函数结束时的自动折叠

函数结束时（正常返回或异常退出），在 `@agentic_function` 装饰器的 finally 块中：

1. 把函数内部的工具调用历史序列化到磁盘（`func_history/<node_id>.jsonl`）
2. 把函数节点折叠成一行：`{func_name}({args}) → {result}`
3. 在节点 metadata 中存检索路径：`func_history_path: "~/.openprogram/sessions/<sid>/func_history/<node_id>.jsonl"`

之后这个节点就是一个普通的扁平节点，和对话节点一样参与压缩。

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
| 2 | 函数结束时自动折叠：内部历史序列化到磁盘，节点折叠成一行，metadata 存检索路径 | 无 | 高 |
| 3 | Turn-end 摘要预生成：每轮结束异步生成 1-2 句摘要，存 `metadata.summary` | 无 | 高 |
| 4 | 实现 `apply_degradation(nodes, budget)`：按 seq ASC 降级最旧节点（统一，不区分类型） | 步骤 1, 3 | 高 |
| 5 | `render_dag_messages` 支持 degraded 节点的摘要渲染 | 步骤 1 | 高 |
| 6 | dispatcher 主循环接入：LLM 调用前检查 + 降级 | 步骤 4, 5 | 高 |
| 7 | runtime.exec 接入：同样的检查 + 降级 | 步骤 4, 5 | 高 |
| 8 | 改造 engine.py compact：从同步 LLM 摘要改为读 turn-end 预生成的 summary 缓存 | 步骤 3, 6 | 中 |
| 9 | 端到端测试：纯聊天 + 函数折叠 + 运行中函数压缩 + 混合场景 | 步骤 6, 7 | 高 |
