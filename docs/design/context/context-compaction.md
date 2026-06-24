# 上下文压缩 — 按 L0/L1/L2 分层的压缩策略

> 状态: **部分实现**。文本级管道（dispatcher 主循环）已完整；DAG 节点级降级待实现；agentic function 内部压缩待实现。
> 代码: `context/engine.py`、`context/budget.py`、`context/microcompact.py`、`context/tool_aging/`、`context/summarize.py`、`context/nodes.py`（`render_context`）。

---

## 1. 核心思路

上下文压缩 = **按 L0/L1/L2 分层压缩，每层策略不同**。

上下文由三层组成（详见 context-composition.md）：

```
L0  系统级（身份/指导/工具/环境）     ← 配好不动，不压缩
L1  会话级（项目文件 + 统一调用树）   ← 调用树是压缩核心
L2  任务级（situation/输入/输出）     ← 纯本次，不压缩
```

**只有 L1 的统一调用树（history）需要压缩**——它是唯一会无限膨胀的部分。L0 和 L2 是固定开销。

---

## 2. 按层压缩策略

### 2.1 L0 系统级 — 不压缩

L0 包含 identity、tool_enforcement、model_guidance、platform_format、inline_prompt、skills_index、memory_global、environment、current_date。

- 会话内基本不变，通常占 context 的 5-10%
- 不值得压缩——内容固定且必须完整
- 唯一的大小控制：`workspace_files` 的 `MAX_WORKSPACE_CHARS=8000` 截断

### 2.2 L1 会话级 — 压缩核心

L1 包含项目文件（AGENTS.md）、PI 检测、记忆、git 信息，以及**统一调用树（history）**。

前面几项是项目级固定内容，大小可控。**统一调用树是唯一会无限增长的部分**——每个 turn 追加节点，工具调用产生大量输出。所有压缩策略的核心都在这里。

### 2.3 L2 任务级 — 不压缩

L2 包含 situation、git_status、todo_progress、当前输入、输出格式。

- 纯本次，每次全变，通常占 context 的 10-15%
- 不需要压缩——内容量本身就小

### 2.4 预算分配

| 层 | 占比 | 压缩 |
|---|---|---|
| L0 系统级 | ~10%，固定 | 不压缩 |
| L1 项目信息 | ~5%，固定 | workspace_files 截断 |
| **L1 调用树（history）** | **~70%，动态** | **节点降级 + 工具老化 + LLM 摘要** |
| L2 任务级 | ~15%，固定 | 不压缩 |

---

## 3. 调用树（History）压缩 — 核心设计

调用树是 DAG 上的节点序列。压缩 = 控制哪些节点参与渲染、每个节点展示多少内容。

### 3.1 节点可见性

每个 Call 节点在渲染时有三种可见性（通过 `metadata.visibility` 控制）：

| 状态 | 渲染行为 | 信息保留 |
|---|---|---|
| `full`（默认） | 完整渲染 input + output | 全部 |
| `summary` | 一行摘要 | 关键信息 |
| `hidden` | 跳过，不参与渲染 | 无（DAG 中仍保留） |

和现有 `expose` 机制的关系：`expose` 控制函数内部对外部的可见性（函数作者声明，静态）。`visibility` 控制节点自身在渲染时的详细程度（压缩系统动态设置）。两者正交。

### 3.2 层级定义

DAG 的层级通过 `called_by` 链的深度定义：

```
层级 0（顶层对话）：called_by == ""
  └─ 层级 1（函数调用）：called_by → 层级 0
      └─ 层级 2（嵌套调用）：called_by → 层级 1
          └─ ...
```

### 3.3 降级顺序

越旧越深越先降级：

| 步骤 | 操作 | 目标 |
|---|---|---|
| 1 | 层级 2+ 的旧节点 → `hidden` | 最深最旧，信息价值最低 |
| 2 | 层级 1 的同辈函数（非当前执行的）→ `summary` | 已完成的函数只保留摘要 |
| 3 | 层级 1 的旧工具调用 → `summary` | 工具结果压成一行 |
| 4 | 层级 0 的旧轮次 → `summary` | 旧对话压成摘要 |
| 5 | 层级 0 的旧摘要 → `hidden` | 摘要的摘要没意义，丢掉 |

每步降级后检查总 token 数，够了就停。

### 3.4 预算驱动的动态窗口

不固定每层保留多少轮，而是让 token 预算倒推：

```
层级 0（顶层对话）：60% 预算 → 从最新的往前填充，用完就停
层级 1（函数调用）：30% 预算 → 当前函数完整，同辈函数 summary
层级 2+（嵌套）：  10% 预算 → 只保留最近的几个
```

这样总长度永远不超过 context window——预算用完就停止填充，更旧的自动变 `hidden`。

### 3.5 对话 vs 函数调用

两者现在统一走 `render_context`（已实现），降级策略相同。差异由 `render_context` 的 `frame_entry_seq` 参数处理：

| 场景 | frame_entry_seq | 效果 |
|---|---|---|
| 对话（顶层） | `None`（全部可见） | 线性，按时间远近降级 |
| 函数调用 | 函数进入时的 seq | 树状，按深度降级 + 同辈函数只保留 summary |

`apply_visibility` 不需要区分场景——它只看预算和节点层级。

---

## 4. 渲染管道

```
DAG 节点
  │
  ├─ render_context（粗筛：expose 过滤 + render_range 裁剪）  ← 已实现
  │
  ├─ apply_visibility（精筛：按预算降级节点）                  ← 待实现
  │
  ├─ render_dag_messages（渲染：full→完整 / summary→一行 / hidden→跳过）← 已实现，需扩展
  │
  └─ 文本级兜底（工具老化 / 微压缩 / LLM 摘要）               ← 已实现
```

节点级控制在粗筛之后、渲染之前插入。文本级作为兜底——如果节点级控制后仍超预算（单个节点太大），再用文本级手段。

### 4.1 summary 生成

| 方式 | 做法 | 延迟 |
|---|---|---|
| **模板摘要**（默认） | `[{role}] {name}: {truncate(output, 80)}` | 零 |
| **LLM 摘要**（可选） | LLM 生成一句话摘要，缓存到 `metadata.summary_text` | 秒级 |

默认用模板摘要。LLM 摘要只在空闲时预生成。

### 4.2 和现有文本级管道的关系

| 现有机制 | 作用 | 保留/改造 |
|---|---|---|
| 预算分配（budget.py） | 分配 system/tools/history/output 四个槽 | 保留，增加按层细分 |
| 自动压缩（engine.py） | 80% 时 LLM 摘要旧消息 | 改造：整合到节点降级流程 |
| 微压缩（microcompact.py） | 空闲时清理旧 tool result | 保留，作为文本级兜底 |
| 工具老化（tool_aging/） | 截断/摘要旧工具结果 | 保留，作为文本级兜底 |

---

## 5. Agentic function 内部的压缩

### 5.1 现状

`runtime.exec`（agentic function 的 LLM 调用路径）完全没有压缩：不调 ContextEngine、不检查预算、不做工具老化、超了直接 API 报错。

### 5.2 方案

在 `runtime.exec` 调 LLM 前加预防性检查 + 两步降级：

```python
# runtime.exec 每次调 LLM 前
token_count = estimate_tokens(messages)

# 步骤 1：节点降级（无 LLM，毫秒级）
if token_count > context_window * 0.7:
    messages = apply_visibility(graph, read_ids, budget=context_window * 0.7)
    messages = render_dag_messages(graph, messages)

# 步骤 2：文本级兜底（工具老化，无 LLM）
if token_count > context_window * 0.8:
    messages = age_tool_results(messages)

# 步骤 3：LLM 摘要（最后手段）
if token_count > context_window * 0.9:
    messages = summarize_old_turns(messages, keep_recent=3)
```

复用同一套 `render_context` + `apply_visibility` 管道，不需要新写。

---

## 6. 和 Claude Code 的差异

| | Claude Code | OpenProgram |
|---|---|---|
| 数据结构 | 线性消息列表 | DAG（调用树） |
| 压缩维度 | 时间（旧消息摘要） | 时间 + 深度（越旧越深优先降级） |
| 压缩粒度 | 消息文本（截断/摘要） | 节点（整体 hidden/summary/full） |
| 子树处理 | 无（线性） | 隐藏整个子树 = 一步释放大量 token |
| agentic 路径 | 子 agent 各自独立压缩 | 统一 render_context 管道 |

核心优势：隐藏一个深层子树可能立刻释放几千 token，文本级摘要需要逐条处理。

---

## 7. 实施计划

| 步骤 | 做什么 | 优先级 |
|---|---|---|
| 1 | 实现 `apply_visibility(graph, read_ids, token_budget)` | 高 |
| 2 | 扩展 `render_dag_messages` 支持 visibility（summary→一行，hidden→跳过） | 高 |
| 3 | 模板摘要生成（从节点字段提取） | 高 |
| 4 | 在 dispatcher 和 runtime.exec 中接入 apply_visibility | 高 |
| 5 | 预算按 L0/L1/L2 细分（`compute_windows`） | 中 |
| 6 | LLM 摘要预生成 + 缓存到 `metadata.summary_text` | 低 |
| 7 | 端到端测试：会话级 + 函数调用级统一验证 | 高 |
