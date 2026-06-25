# 上下文压缩

> 状态: **设计完成，待实现**。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/nodes.py`（`render_context`）。
> 关联: [`context-composition.md`](context-composition.md)（三层上下文构建）、[Claude Code 压缩参考](../../reference/claude-code-compaction.md)

---

## 1. 核心思路

上下文压缩 = 控制 DAG 节点的展示程度。DAG 本身不动，渲染时决定每个节点展示多少。

---

## 2. 节点生命周期管理

DAG 中的函数调用节点有运行中和已结束两种状态，压缩前先处理生命周期。

### 2.1 函数运行中

- **触发条件**：函数正在执行（`status="running"`）
- **做什么**：内部工具调用历史完整展开，按 `render_context` 管道渲染（callers / subcalls / expose 机制控制可见范围）
- **作用对象**：当前 frame 节点及其子节点
- **节点状态**：全部 `full`
- → 如果运行中的函数内部历史超限，对内部节点执行 §5 的降级算法（最旧的内部节点先降）

### 2.2 函数结束：折叠

- **触发条件**：函数正常返回或异常退出（`@agentic_function` 装饰器的 finally 块）
- **做什么**：
  1. 函数内部的工具调用历史序列化到磁盘
  2. 函数节点折叠成一行：`{func_name}({args}) → {result}`
  3. 节点 metadata 存检索路径
- **作用对象**：已完成的函数节点及其整个子树
- **节点状态**：子树从 DAG 渲染中移除，函数节点变为一行摘要
- → 折叠后的节点和对话节点、工具调用节点一样，进入 §3-§5 的常规压缩流程
- → 存磁盘的机制和 §4.1 的 Microcompact 共用同一套存储（`~/.openprogram/sessions/<sid>/`）

折叠前后的 DAG 结构变化：

```
折叠前（函数运行中）：
root
├── user_1 → llm_1
├── user_2 → llm_2
│       ├── tool_call_1（bash "grep ..."）
│       └── func_call_1（research_agent, 运行中）        ← 展开
│           └── llm_3
│               ├── tool_call_3（search_papers）
│               └── tool_call_4（write_note）

折叠后（函数已结束）：
root
├── user_1 → llm_1
├── user_2 → llm_2
│       ├── tool_call_1（bash "grep ..."）
│       └── [func] research_agent("topic") → 5 ideas    ← 一行，内部存磁盘
├── user_3 → llm_4
```

### 2.3 手动调用的函数

- **触发条件**：用户在 CLI/webui 手动执行函数（非 LLM 调用）
- **做什么**：和 §2.2 相同——结束后折叠成一行，只保留输入输出
- **作用对象**：手动调用的函数节点
- **节点状态**：同 §2.2

### 2.4 磁盘存储格式

- **路径**：`~/.openprogram/sessions/<sid>/func_history/<node_id>.jsonl`
- **格式**：JSONL，每行一个内部节点（role / name / input / output / timestamp）
- **检索**：模型可通过 `read_file` 工具读取该路径查看函数运行的具体过程
- → §4.1 的旧工具输出也存磁盘，共用 `~/.openprogram/sessions/<sid>/` 下的存储

---

## 3. 每轮常规操作

每次 LLM 调用前都执行，和上下文长度无关。

### 3.1 截断超大单个输出

- **触发条件**：每轮 LLM 调用前，检查每个节点的 output 大小
- **做什么**：单个工具输出超过阈值（如 10K tokens）时截断——保留开头 + 结尾，中间替换为 `[... 省略 N tokens ...]`
- **作用对象**：所有节点的 output 字段（对话、工具、已折叠的函数）
- **节点状态**：仍为 `full`，只是 output 被截短
- → 现有 `tool_aging/truncate.py` 已实现此功能
- → 这是最便宜的操作，不删节点、不调 LLM、不改 DAG

---

## 4. 空闲时清理

用户一段时间没发消息时后台执行。

### 4.1 旧工具输出存磁盘（Microcompact）

- **触发条件**：距上次用户交互超过阈值（如 30 秒）
- **做什么**：把旧的工具输出（非最近 N 个）存到磁盘，上下文中只留一行引用路径
  ```
  [stored on disk: ~/.openprogram/sessions/<sid>/tool_output/<node_id>]
  ```
- **作用对象**：旧的工具调用节点的 output 字段
- **节点状态**：仍为 `full`，但 output 被替换为引用路径
- → 和 §2.4 共用存储机制（同一个 session 目录），但针对的是单个工具输出而非函数子树
- → 现有 `microcompact.py` 已实现，返回变换后的副本，不改原始数据
- → 模型需要时可通过 `read_file` 读回完整输出

---

## 5. 阈值触发的压缩

上下文占比超过阈值时触发，逐步升级。

### 5.1 触发条件和目标

- **触发条件**：
  - context ≤ 200K：history tokens 占比 ≥ **70%**
  - context > 200K：history tokens 占比 ≥ **80%**
- **目标**：压到 **20%**
- → 触发后按 §5.2 → §5.3 → §5.4 顺序执行，每步检查是否已达目标，达到就停

### 5.2 按时间删旧节点（Snip）

- **触发条件**：§5.1 触发后，第一步
- **做什么**：直接删除最旧的节点（不做摘要，直接从渲染列表中移除）
- **作用对象**：所有类型节点（对话、工具、已折叠函数），按 seq ASC（最旧的先删）
- **节点状态**：`hidden`（DAG 中保留，渲染时跳过）
- → 最便宜——不调 LLM，不生成摘要
- → 删到目标就停；如果删了最旧的 20% 节点还不够，进入 §5.3

### 5.3 对话节点降级为摘要

- **触发条件**：§5.2 后仍超目标
- **做什么**：把剩余的旧对话节点（user+llm 对）从 `full` 降为 `degraded`，用预生成的摘要替换完整内容
  ```
  [turn 3] 用户要求分析代码性能 → 模型发现3个瓶颈
  ```
- **作用对象**：对话节点（user + llm），按 seq ASC（最旧的先降）
- **节点状态**：`degraded`（~50-100 tokens/个，相比 full 的 ~2000 tokens）
- → 摘要来自 `metadata.summary`（每轮 turn 结束时异步预生成，见 §5.3.1）
- → 如果预生成缓存不存在，fallback 到截取 output 前 50 字符

#### 5.3.1 摘要预生成

- **触发条件**：每轮 turn 结束时
- **做什么**：异步调 LLM 生成 1-2 句对话摘要，存在节点 `metadata.summary` 中
- **作用对象**：当前 turn 的 user+llm 节点对
- → 和 shadow git 的 turn-end commit 类似——turn 结束时顺手做
- → 压缩触发时直接读缓存，不需要额外 LLM 调用

### 5.4 已折叠函数节点的进一步压缩

- **触发条件**：§5.3 后仍超目标
- **做什么**：§2.2 折叠后的函数节点已经是一行了，进一步降级为 `hidden`（完全从渲染中移除）
- **作用对象**：已折叠的函数节点，按 seq ASC
- **节点状态**：`hidden`
- → 磁盘上的完整历史仍在（§2.4），模型需要时可检索
- → 这是最后手段，信息损失最大

---

## 6. 完整流程推演

场景：200K context，用户对话 + 调用了 research_agent 函数。

```
阶段 1：对话开始（0-30%，~60K）
  用户发 10 条消息，模型回复，调了几个工具。
  → 无压缩操作。

阶段 2：函数调用（30-50%，~100K）
  模型调用 research_agent，函数内部跑了 20 轮工具调用。
  → §3.1：每轮检查，某个 search_papers 返回 15K 文本，截断到 5K。
  → §2.1：函数运行中，内部 20 个工具调用全部展开。

阶段 3：函数结束（占比下降到 ~35%）
  research_agent 返回结果。
  → §2.2：自动折叠。20 个内部工具调用存磁盘，节点变成一行：
    [func] research_agent("LLM reasoning") → 5 ideas
    (详见 ~/.openprogram/sessions/xxx/func_history/node_id.jsonl)
  → 上下文从 ~100K 骤降到 ~70K（折叠释放了 ~30K）。

阶段 4：继续对话（35-60%）
  用户继续聊了 20 轮。
  → §4.1：空闲时，10 分钟前的 3 个旧工具输出存磁盘留引用（释放 ~5K）。

阶段 5：触发压缩（70%，140K）
  用户又聊了 15 轮，总计 140K。触发 §5。
  → §5.2 Snip：删最旧的 10 个节点（~20K），降到 120K。还超。
  → §5.3 摘要降级：把第 11-25 旧的 15 个对话节点降为 degraded（~1.5K），
    释放 ~28.5K，降到 ~91.5K。还超。
  → 继续 §5.3：再降级 15 个，释放 ~28.5K，降到 ~63K。还超。
  → 继续 §5.3：再降级 10 个，释放 ~19K，降到 ~44K。接近目标。
  → §5.4：折叠后的 research_agent 节点设为 hidden（释放 ~1K），降到 ~43K。
  → 达到 ~21.5%，停止。

阶段 6：压缩后继续
  上下文约 43K（21.5%），保留最近 5 轮完整对话。
  用户继续使用，上下文重新增长。
  再次到 70% 时，重复 §5 流程（链式压缩）。
```

---

## 7. 渲染管道

```
DAG 节点
  │
  ▼
render_context（筛选可见节点：callers / subcalls / expose）     ← 已实现
  │
  ▼
apply_degradation（按 §5 的算法降级旧节点）                     ← 待实现
  │
  ▼
render_dag_messages（渲染为消息列表）                           ← 已实现，需扩展
  │   full 节点 → 完整渲染
  │   degraded 节点 → 一行摘要（metadata.summary 或截取前 50 字符）
  │   hidden 节点 → 跳过
  │
  ▼
LLM 调用
```

- dispatcher 主循环和 runtime.exec 走同一条管道
- → `render_context` 已统一（对话和函数调用都走 DAG 渲染）
- → `apply_degradation` 在 `render_context` 之后、`render_dag_messages` 之前插入

---

## 8. 和 Claude Code 的对应

| 我们 | Claude Code | 说明 |
|---|---|---|
| §2 函数折叠 | （无对应） | 我们独有，DAG 结构天然支持 |
| §3.1 截断超大输出 | Budget Reduction | 每轮做，截断单个大输出 |
| §4.1 旧工具输出存磁盘 | Microcompact | 空闲时做，存磁盘留引用 |
| §5.2 Snip | Snip | 按时间删旧节点 |
| §5.3 摘要降级 | Context Collapse / Auto-Compact | 我们用预生成摘要（零额外 LLM），Claude Code 用分段或全量 LLM 摘要 |
| §5.4 函数节点 hidden | （无对应） | 我们独有 |

参考来源（第三方源码逆向分析）：
- [Dive into Claude Code](https://arxiv.org/html/2604.14228v1)（arXiv 2604.14228，VILA-Lab）
- [How Claude Code Compresses Context](https://harrisonsec.com/blog/claude-code-context-engineering-compression-pipeline/)
- [Inside Claude Code - Context Compaction](https://y-agent.github.io/inside-claude-code/04-context-compaction.html)

---

## 9. 实施计划

| 步骤 | 做什么 | 依赖 | 优先级 |
|---|---|---|---|
| 1 | Call 节点加 `visibility` 字段（full / degraded / hidden），默认 full | 无 | 高 |
| 2 | 函数结束时自动折叠（§2.2）：序列化内部历史到磁盘，节点折叠成一行 | 无 | 高 |
| 3 | Turn-end 摘要预生成（§5.3.1）：每轮结束异步生成摘要，存 metadata.summary | 无 | 高 |
| 4 | 实现 `apply_degradation(nodes, budget)`（§5 算法） | 步骤 1, 3 | 高 |
| 5 | `render_dag_messages` 支持 degraded 节点的摘要渲染 | 步骤 1 | 高 |
| 6 | dispatcher 主循环接入（§5 触发检查 + 降级） | 步骤 4, 5 | 高 |
| 7 | runtime.exec 接入（同样的管道） | 步骤 4, 5 | 高 |
| 8 | 改造 engine.py compact：从同步 LLM 摘要改为读预生成的 summary 缓存 | 步骤 3, 6 | 中 |
| 9 | 端到端测试：纯聊天 + 函数折叠 + 运行中函数压缩 + 链式压缩 | 步骤 6, 7 | 高 |
