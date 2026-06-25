# 上下文压缩

> 状态: **设计完成，待实现**
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`
> 参考: [Claude Code 压缩参考](../../reference/claude-code-compaction.md)

<blockquote>
<b>节点类型图例</b><br>
🔵 <span style="color:#3b82f6">对话节点</span>（user + assistant）<br>
🟢 <span style="color:#22c55e">函数调用节点</span>（ROLE_CODE，@agentic_function）<br>
🟡 <span style="color:#eab308">工具调用节点</span>（bash / read_file / write 等）
</blockquote>

## 1. 核心思路

上下文压缩 = 节点折叠 + 存磁盘留引用 + 时间降级。所有操作都作用在 DAG 节点上。

## 2. 节点生命周期管理

我们独有的设计。Claude Code 是线性对话没有 DAG，不需要这一层。

### 2.1 函数运行中

- **触发**：函数被调用，status="running"
- **做什么**：内部子树（工具调用、嵌套函数、LLM 交互）完整展开在上下文中
- **可见范围**：由 render_context 的 callers/subcalls/expose 控制
- **节点状态**：
  - **函数内部视角**（函数自己的 LLM 调用）：子节点 full（完整渲染 input + output）
  - **外部视角**（调用方看这个函数节点）：默认 expose=io，调用方只看到函数的输入和输出，看不到内部的工具调用和 LLM 交互
- **和节点的关系**：🟢 <span style="color:#22c55e">函数节点</span>（ROLE_CODE）+ 其下所有 🟡 <span style="color:#eab308">工具子节点</span>都参与渲染（仅在函数内部视角）
- **函数调用特殊设计**：render_context 的 frame_entry_seq 参数划分"函数前历史"和"函数内历史"，callers 控制能看多少父辈。expose=io 确保函数内部细节不泄漏给调用方

### 2.2 函数结束 → expose=io 天然隐藏内部

- **触发**：函数返回，status 从 "running" 变为 "completed"/"error"
- **做什么**：不需要额外的"折叠 + 存磁盘"操作。expose=io 天然隐藏内部子树——外部调用方只看到函数的输入和输出，内部的工具调用和 LLM 交互不参与渲染，不占 token
- **DAG 本身就是存储**：子节点保留在 DAG 中（session store 持久化），不需要额外序列化到磁盘。大模型想查内部过程时，可以通过工具读取 DAG 中的子节点
- **折叠后的节点**进入 §3-§5 的常规压缩流程，和 🔵 对话节点一视同仁
- **和节点的关系**：🟢 <span style="color:#22c55e">函数节点</span>从 status=running 变为 status=completed，🟡 <span style="color:#eab308">工具子节点</span>不再参与渲染（expose=io 控制）但保留在 DAG 中
- **函数调用特殊设计**：这是函数调用独有的机制。🔵 对话节点没有 expose 概念，始终 full
- **⚠ 待定**：函数节点的输入+输出本身可能很长（几百到几千 tokens），多个已完成函数累积后仍然占用大量空间。这部分的老化/截断方案待设计

## 3. 每轮常规操作

每次 LLM 调用前都做，和上下文长度无关。

### 3.1 截断超大单个输出（= Claude Code 的 Budget Reduction）

和 Claude Code 的 `applyToolResultBudget()` 一致。检查每个工具调用的输出大小，超大的截断。

- **触发**：每次 LLM 调用前检查所有节点的 output
- **做什么**：单个工具输出超过阈值就截断——保留开头和结尾，中间省略
- **截断方式**：`[原始开头 N tokens] ... [truncated, X lines omitted] ... [原始结尾 M tokens]`
- **作用对象**：所有节点（🔵 <span style="color:#3b82f6">对话节点</span>、🟡 <span style="color:#eab308">工具节点</span>、§2.2 后的 🟢 <span style="color:#22c55e">函数节点</span>）
- **不改变节点状态**，只截断 output 文本。纯字符串操作，不删消息、不调 LLM、不改对话结构
- **和节点的关系**：作用在单个节点的 output 字段上，不影响节点间的结构
- **函数调用特殊设计**：无。expose=io 后的函数节点只有输入+输出，通常不会触发截断

## 4. 空闲时清理

用户一段时间没发消息时做。

### 4.1 旧工具输出存磁盘（= Claude Code 的 Microcompact）

和 Claude Code 的 Microcompact 一致。把过时的旧工具输出存到磁盘，上下文中只留引用路径。最近几轮的结果保持 inline。

- **触发**：距上次用户交互超过阈值（比如 30 秒），空闲时自动清理
- **做什么**：按时间排序，最旧的工具输出存磁盘，上下文中替换为引用路径
- **上下文中变成**：`[content stored on disk, retrievable by path: <路径>]`（约 20 tokens）
- **最近的工具输出保持 inline**（不清理）
- **大模型需要时可以通过 read_file 读回来**
- **两条路径**（同 Claude Code）：
  - 时间路径：旧的先清（默认）
  - 缓存感知路径：优先清理导致 prompt cache miss 的内容
- **和节点的关系**：修改 🟡 <span style="color:#eab308">工具节点</span>的 output 字段（替换为引用路径），节点本身保留在 DAG 中
- **函数调用特殊设计**：
  - 函数运行中（§2.1）：内部的工具输出也可以被 Microcompact 清理（运行时间很长时，旧的内部工具输出存磁盘）
  - 函数结束后（§2.2）：expose=io 已隐藏内部子树，Microcompact 不作用于这些子节点

## 5. 阈值触发的压缩

上下文占比超过阈值时做。

### 5.1 触发条件和目标

- **自动触发**：history tokens 超过 `context_window - 13K`（约 83.5% 占用），和 Claude Code 一致
- **建议手动触发**：用户在 60% 时手动 `/compact`，质量比自动好
- **目标**：不设固定百分比，逐级压缩（§5.2 Snip → §5.3 摘要）直到能继续调用 LLM
- **检查时机**：每次 LLM 调用前（§3 之后）
- **和节点的关系**：计算所有参与渲染的节点的 token 总和

### 5.2 按时间删旧节点（= Claude Code 的 Snip）

和 Claude Code 的 `snipCompactIfNeeded()` 一致。直接删除最旧的几轮对话，不做任何摘要，直接丢掉。简单粗暴，释放大量空间，但信息完全丢失。

- **触发**：§5.1 条件满足
- **做什么**：从最旧的节点开始，逐个从渲染列表移除，直到 token 数降到能继续调用 LLM
- **作用对象**：所有类型的节点（🔵 <span style="color:#3b82f6">对话节点</span>、已完成的 🟢 <span style="color:#22c55e">函数节点</span>、🟡 <span style="color:#eab308">工具调用节点</span>）
- **节点数据仍在 DAG 中**（不物理删除），只是渲染时跳过（visibility=hidden）
- **如果 Snip 后仍不够** → 进入 §5.3
- **和节点的关系**：设置节点的 visibility=hidden，render_context 跳过这些节点
- **函数调用特殊设计**：
  - 已完成的 🟢 函数节点被当作普通节点，按时间排序参与 Snip
  - 正在运行的 🟢 函数（§2.1）不参与 Snip（不能删正在用的）

### 5.3 LLM 摘要（= Claude Code 的 Context Collapse / Auto-Compact）

和 Claude Code 一致，Snip 后仍不够时才调 LLM。不额外预生成摘要——只有在压缩触发或用户手动 `/compact` 时才调 LLM。

Claude Code 提供两种互斥方案（由配置决定）：
- **Context Collapse**：分段摘要，每段独立 LLM 调用，原始消息保留（可回滚）
- **Auto-Compact**：全量摘要，一次 LLM 调用，原始消息替换（不可回滚）

我们同样二选一，默认用 Context Collapse（更精细、可回滚）。

- **触发**：§5.2 Snip 后仍不够
- **做什么**：调 LLM 对旧对话进行摘要
- **作用对象**：🔵 <span style="color:#3b82f6">对话节点</span>和已完成的 🟢 <span style="color:#22c55e">函数节点</span>
- **正在运行的 🟢 函数**（§2.1）：不摘要（正在用）
- **和节点的关系**：被摘要的节点 visibility 变为 hidden，摘要文本作为新的 compaction 节点插入

### 5.4 /compact 手动命令（= Claude Code 的 /compact）

和 Claude Code 一致。用户手动触发 Auto-Compact（LLM 全量摘要），可带提示词。

- **触发**：用户输入 `/compact` 或 `/compact <提示词>`
- **做什么**：直接跳到 Auto-Compact（不经过 §5.2 Snip），LLM 生成全量摘要
- **可以带提示词指导保留什么**（比如 `/compact 保留数据库迁移的讨论`），手动质量比自动好
- **摘要替换所有旧节点**，只保留最近 1-2 轮完整
- **建议时机**：60% 占用时手动做（同 Claude Code 官方建议）
- **和节点的关系**：所有旧节点 visibility=hidden，生成一个新的 compaction summary 节点
- **函数调用特殊设计**：无。已完成的 🟢 函数节点和 🔵 对话节点一起被摘要

## 6. 完整流程推演

场景：用户在 200K context 下做编程任务，中间调用了 research_agent 函数。

### 阶段 1（0-30%）：正常对话

- 🔵 user → 🔵 assistant → 🔵 user → 🔵 assistant
- 没有任何压缩触发
- 🔵 <span style="color:#3b82f6">对话节点</span> visibility=full；如有 🟢 <span style="color:#22c55e">函数节点</span>则 expose=io（外部只看输入输出）

### 阶段 2（30-50%）：调用函数

- 🔵 user: "帮我调研 LLM reasoning"
- 🔵 assistant 调用 🟢 <span style="color:#22c55e">research_agent</span>（函数节点，§2.1 生效）
- 🟢 research_agent 运行中：内部 15 轮 🟡 <span style="color:#eab308">工具调用</span>展开在上下文
- §3.1 生效：某个 🟡 search_papers 返回 20000 tokens，截断到 10000
- §4.1 生效：空闲时旧的 🟡 search_papers 输出存磁盘

### 阶段 3：函数结束

- 🟢 <span style="color:#22c55e">research_agent</span> 返回 5 个 idea
- §2.2 触发：内部 15 轮 🟡 <span style="color:#eab308">工具子树</span>存磁盘（collapsed/abc123.jsonl），🟢 函数节点折叠成一行
- 上下文释放约 30K tokens
- 折叠后的 🟢 函数节点和 🔵 <span style="color:#3b82f6">对话节点</span>一样参与后续压缩

### 阶段 4（50-80%）：继续对话

- 用户继续讨论、改代码
- §4.1：空闲时旧的 read_file 输出存磁盘
- turn 结束时预生成对话摘要（§5.3 的准备）
- 建议用户在 60% 时手动 `/compact`

### 阶段 5（~83.5%）：触发自动压缩

- §5.1：超过 `context_window - 13K`（约 83.5%）自动触发
- §5.2 Snip：删最旧的 🔵 <span style="color:#3b82f6">对话节点</span>（包括那个已折叠的 🟢 <span style="color:#22c55e">research_agent</span> 节点）
- 如果还不够 → §5.3：用预生成的 summary 替换更多旧 🔵 对话
- 逐级压缩直到能继续调用 LLM

### 阶段 6：继续使用

- 压缩后正常对话
- 再次到 ~83.5% 时重复阶段 5（链式压缩）
- 大模型想回顾 research_agent 的具体过程：read_file("collapsed/abc123.jsonl")

## 7. 和 Claude Code 的对应

| 我们 | Claude Code | 说明 |
|---|---|---|
| §2 节点生命周期 | 无 | 我们独有，Claude Code 是线性对话没有 DAG |
| §3.1 截断超大输出 | Budget Reduction | 一致 |
| §4.1 旧输出存磁盘 | Microcompact | 一致 |
| §5.2 Snip | Snip | 一致 |
| §5.3 LLM 摘要 | Context Collapse / Auto-Compact | 一致（二选一，默认 Context Collapse） |
| §5.4 /compact | /compact | 一致 |

来源：[Dive into Claude Code](https://arxiv.org/html/2604.14228v1)（arXiv 2604.14228）

## 8. 实施计划

| 步骤 | 做什么 | 依赖 |
|---|---|---|
| 1 | 截断超大单个输出 — Budget Reduction（§3.1） | 无 |
| 2 | Microcompact 旧工具输出存磁盘（§4.1） | 无 |
| 3 | Snip 按时间删旧节点（§5.2） | 无 |
| 4 | Context Collapse / Auto-Compact — LLM 摘要（§5.3） | 无 |
| 5 | /compact 命令（§5.4） | 步骤 4 |
| 6 | /context 命令（查看 token 分布） | 无 |
| 7 | 函数节点老化方案（§2.2 ⚠ 待定） | 步骤 1-4 |
