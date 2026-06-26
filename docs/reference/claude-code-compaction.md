# Claude Code 上下文压缩机制 — 完整流程参考

> 调研文档（非设计文档）。以一个完整对话的时间线，记录 Claude Code 从对话开始到多次压缩的全过程。
>
> 来源：
> - [Dive into Claude Code](https://arxiv.org/html/2604.14228v1)（arXiv 2604.14228，VILA-Lab 基于源码逆向分析）
> - [Inside Claude Code - Context Compaction](https://y-agent.github.io/inside-claude-code/04-context-compaction.html)
> - [Claude Code VS OpenCode §5.3](https://0xtresser.github.io/Claude-Code-VS-OpenCode/en/Chapter_05_Session_and_Context/5.3_Context_Compaction.html)
> - [Context Compression — What Survives](https://okhlopkov.com/claude-code-compaction-explained/)
> - [How Claude Code Got Better by Protecting More Context](https://hyperdev.matsuoka.com/p/how-claude-code-got-better-by-protecting)
> - [Context Compaction Deep Dive](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/)

---

## 常规操作（和上下文长度无关，每轮都做）

以下两个操作不是"压缩"，而是常规的输出大小管理。不管上下文用了 10% 还是 80% 都会执行。和后面的压缩流程无关。两者解决不同的问题：

- **Budget Reduction**：管"单个太大"——一个工具输出超大就截断它
- **Microcompact**：管"旧的太多"——时间久了旧的工具输出存磁盘腾空间

### Budget Reduction（每轮 LLM 调用前）— 管"单个太大"

`applyToolResultBudget()` 检查每个工具调用的输出大小。超大的单个输出会被截断——只保留开头和结尾，中间省略。

```
截断前：
[tool_result] "src/a.py:12: TODO fix this\nsrc/b.py:34: TODO refactor\n..."（8000 tokens）

截断后：
[tool_result] "src/a.py:12: TODO fix this\n... [truncated, 150 lines omitted] ...\nsrc/z.py:99: TODO cleanup"（500 tokens）
```

纯字符串截断，不删消息、不调 LLM、不改对话结构。只处理超大的单个输出，不动正常大小的消息。

### Microcompact（空闲时 + 每轮调用前）— 管"旧的太多"

把"过时"的旧工具输出存到磁盘，上下文中只留引用路径。最近几轮的结果保持 inline。和 Budget Reduction 的区别：Budget Reduction 看单个输出大不大（新旧都看），Microcompact 看时间远不远（只清旧的，不管大小）。

```
替换前：
[tool_result] "import os\nimport sys\n\nclass Config:\n    ..."（2000 tokens）

替换后：
[tool_result] "[content stored on disk, retrievable by path: /tmp/claude-cache/config.py.result]"（20 tokens）
```

有两条路径：

- **Time-based path（时间路径，默认）**：按时间远近，旧的先清。空闲 ~90 分钟后触发。旧 tool_result 内容替换为 sentinel 字符串。sentinel 做了**字节级归一化（byte-stable canonical form）**——重复 microcompact 不会改变已缓存的内容，保护 prompt cache 前缀稳定。

- **Cache-aware path（缓存感知路径）**：使用 Anthropic API 的 **context editing** 能力（`cache_edits`），服务端直接清除旧 tool_result。**客户端消息不变，缓存前缀完全不破坏。** 这是日常缓存保护的核心机制。

#### Context Editing API（cache_edits）

Anthropic API 的公开 beta 能力（beta header: `context-management-2025-06-27`），不是 Claude Code 专有，普通开发者可以用。

| 策略 | 做什么 |
|---|---|
| `clear_tool_uses` | 自动清除旧的 tool_result，只保留最近 N 个。超过 token 阈值的旧结果被替换为占位文本 |
| `clear_thinking` | 清除旧的 thinking blocks |
| `clear_at_least` | 控制最少清多少 token |

**工作原理**：客户端发送完整的消息历史（不做任何修改），同时传一个 `cache_edits` 参数告诉服务端"帮我清掉旧的 tool_result"。服务端在缓存内部执行清除操作，缓存前缀不变。客户端完全不需要知道哪些被清了。

**核心原则**：已经进了缓存的内容不再修改（修改会破坏缓存前缀）。只有还没进缓存的内容才会被客户端替换（走 time-based path）。

**限制**：只有 Anthropic API 支持。OpenAI / Google / 其他 provider 没有类似能力，只能走 time-based path。

来源：
- [Context editing - Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Context engineering cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)
- [Claude Code's Compaction Engine](https://barazany.dev/blog/claude-codes-compaction-engine)
- [Claude Code Cache Fix](https://github.com/cnighswonger/claude-code-cache-fix)

---

## 压缩流程（阈值触发，以一个完整对话的时间线展示）

### 场景设定

用户打开 Claude Code，在一个中等复杂度的项目上工作。模型用 Sonnet（200K context）。
整个会话过程中，用户让 Claude 读代码、改文件、跑测试、修 bug，持续几个小时。

---

### 阶段 1：对话开始（0-30% 占用，约 0-60K tokens）

用户发第一条消息："帮我看一下这个项目的结构"。

Claude Code 在调 LLM 之前，先组装上下文：

```
[system prompt]                    ← 固定，从 CLAUDE.md 加载
[工具定义]                          ← 所有可用工具的 schema
[用户消息] "帮我看一下这个项目的结构"
```

LLM 回复后，上下文变成：

```
[system prompt]
[工具定义]
[user] "帮我看一下这个项目的结构"
[assistant] "让我看一下..." + tool_use(bash, "find . -type f | head -50")
[tool_result] "src/main.py\nsrc/utils.py\n..."（500 tokens）
[assistant] "项目结构是这样的..."
```

这时候总共约 5K-10K tokens。**什么压缩都不触发。** Claude Code 每轮 LLM 调用前都会计算当前 token 数，但远没到阈值。

---

### 阶段 2：对话增长（30-60% 占用，约 60K-120K tokens）

用户继续工作："读一下 main.py"、"帮我改这个函数"、"跑一下测试"。

每一轮都往上下文里加内容：
- `read_file("main.py")` 的结果：可能 3000 tokens
- `write("main.py", ...)` 的改动记录
- `bash("pytest")` 的测试输出：可能 2000 tokens
- 每轮 assistant 回复：500-1500 tokens

20 轮对话后，上下文可能长这样（简化）：

```
[system prompt]                                          5K tokens
[工具定义]                                                3K tokens
[user] "看项目结构"
[assistant] ... + tool_use(bash) + tool_result + 回复       2K tokens
[user] "读 main.py"
[assistant] ... + tool_use(read_file) + tool_result(3K)     4K tokens
[user] "改这个函数"
[assistant] ... + tool_use(write) + 回复                    2K tokens
[user] "跑测试"
[assistant] ... + tool_use(bash, "pytest") + tool_result(2K) 3K tokens
... 重复 16 轮 ...
总计约 80K-100K tokens（40-50%）
```

**这个阶段 Claude Code 官方建议用户手动 `/compact`。** 因为用户能带提示词指导保留什么（比如 `/compact 保留关于数据库迁移的讨论`），手动压缩的质量比自动的好。但大部分用户不会在这个时候主动 compact。

---

### 阶段 3：触发压缩（75%+ 占用，约 150K+ tokens）

对话持续增长，常规操作（Budget Reduction / Microcompact）虽然一直在清理超大/过时的工具输出，但对话消息本身不断累积，最终超过阈值。

**触发线：大约 75% 占用**（早期版本是 90%+，后来改成更早触发——留出足够空间给压缩过程本身使用）。

此时 Claude Code 进入压缩流程。先做 Snip（删旧消息），然后根据配置二选一：Context Collapse（分段模板摘要）或 Auto-Compact（LLM 全量摘要）。**Context Collapse 和 Auto-Compact 是互斥的，不会同时执行。**

#### Snip

`snipCompactIfNeeded()` 直接**删除最旧的几轮对话**。不做任何摘要，直接丢掉。

```
压缩前（40 轮对话）：
[轮 1] user + assistant + tools    ← 删掉
[轮 2] user + assistant + tools    ← 删掉
[轮 3] user + assistant + tools    ← 删掉
[轮 4] user + assistant + tools    ← 删掉
[轮 5] user + assistant + tools    ← 删掉
[轮 6-40] ...                      ← 保留

压缩后（35 轮）：
[轮 6] user + assistant + tools
[轮 7] user + assistant + tools
... 
```

简单粗暴，释放大量空间。但信息完全丢失——模型不知道前 5 轮讨论了什么。

#### Context Collapse 和 Auto-Compact（二选一）

Snip 之后如果仍然超阈值，根据配置二选一执行 Context Collapse 或 Auto-Compact。两者是**互斥的替代方案**，核心区别：

| | Context Collapse | Auto-Compact |
|---|---|---|
| **做法** | 把历史分成若干段，逐段用 LLM 摘要 | 把整个对话历史一次性发给 LLM 生成一个摘要块 |
| **原始消息** | **保留**（摘要是 View 叠加，底层数据不动） | **替换**（摘要替代所有旧消息，原始不可恢复） |
| **可回滚** | 是（原始消息还在，理论上可以重建） | 否（旧消息被替换，不可逆） |
| **触发阈值** | ~90%（非阻塞），95%（阻塞强制） | ~75-87% |
| **保留结构** | 是（最近 N 轮完整，旧的按段折叠，仍有分段边界） | 否（全部压成一段文字，结构丢失） |
| **信息损失** | 较少（每段有独立摘要，关键节点可保留） | 较多（只剩一段总结，中间细节全丢） |
| **LLM 调用** | 多次（每段一次） | 一次 |
| **手动触发** | 无 | `/compact` 命令 |

**具体例子——20 轮对话后触发：**

Context Collapse 的结果（分段摘要，结构保留）：
```
[turns 1-5 摘要] 讨论了数据库迁移方案，决定用 Alembic，排除了 Django ORM
[turns 6-10 摘要] 实现了 User 表迁移，修了 FK 约束问题，添加了回滚脚本
[turn 11] user: "现在做 Order 表"                    ← 最近的完整保留
[turn 12] assistant: "好的，让我先看一下 Order 模型..." + tool_use + ...
...
[turn 20] assistant: "Payment 表的外键已经修好了"
```
原始的 turn 1-10 仍然保存在 collapse store 中，只是模型看到的是折叠版本。

Auto-Compact 的结果（全量摘要，结构丢失）：
```
[compaction summary]
用户在做数据库迁移项目。使用 Alembic。已完成 User 表和 Order 表的迁移。
当前在处理 Payment 表的外键约束。关键决策：用 batch migration 避免锁表。
修改过的文件：migrations/001_user.py, migrations/002_order.py, src/models.py

[turn 19] user: "修一下 Payment 的外键"              ← 只保留最近 1-2 轮
[turn 20] assistant: "Payment 表的外键已经修好了"
```
原始的 turn 1-18 **永久丢失**，无法恢复。

#### Context Collapse 详细机制

如果启用了 Context Collapse，`applyCollapsesIfNeeded()` 进一步压缩。

它把历史分成**几个段**，每段用 LLM 生成摘要：

```
压缩前：
[轮 6] "读 utils.py" → 读了文件 → "这个文件有个 bug..."
[轮 7] "改一下" → 写了文件 → "改好了"
[轮 8] "跑测试" → 跑了 pytest → "3 个测试失败"
[轮 9] "修 test_a" → 写了文件 → "修好了"
[轮 10] "再跑" → 跑了 pytest → "全过了"

压缩后（一个 collapse 段）：
[collapse] "Turns 6-10: Fixed bug in utils.py (read → edit → test → fix → pass)"
```

关键点：**Context Collapse 是读时投影（read-time projection）**——原始历史不删，collapse 摘要存在单独的 collapse store 里（类似数据库的 View——底表不动，查询看到的是摘要视图）。模型看到的是折叠后的版本，但完整历史保留着，理论上可以重建。

#### Auto-Compact 详细机制

**如果没有启用 Context Collapse**，触发 `compactConversation()`——这是唯一生成**全量摘要**的压缩步骤。

过程：
1. 执行 PreCompact hooks（通知系统即将压缩）
2. 用 `getCompactPrompt()` 构造压缩提示（类似"请把以下对话历史压缩成关键事实"）
3. 把整个对话历史发给 LLM，让它生成摘要
4. 用 `buildPostCompactMessages()` 构建压缩后的消息

```
压缩前（35 轮，150K tokens）：
[system prompt]
[35 轮完整对话历史，包含所有工具调用和结果]

压缩后（~30K tokens）：
[system prompt]                    ← 从 CLAUDE.md 重新加载（不是从压缩摘要来的）
[工具定义]                          ← 重新加载
[compaction block]                  ← LLM 生成的摘要，约 2000-5000 tokens
"会话摘要：用户在做一个 Python 项目的重构。
已完成：修了 utils.py 的 bug、重构了 config 模块、添加了 3 个测试。
当前状态：所有测试通过。用户正在处理 API 模块。
关键决定：使用 FastAPI 替换 Flask、数据库用 PostgreSQL。
修改过的文件：src/utils.py, src/config.py, tests/test_utils.py, tests/test_config.py"
```

**压缩后信息损失严重。** 丢失的东西：
- 早期的指令（"不要碰这个文件"）
- 中间的设计讨论和推理过程
- 50+ 轮前的具体代码片段
- 微妙的风格偏好（格式规则等）

**保留的东西：**
- 当前任务和近期上下文
- 最近修改的文件名
- 最近的错误和解决方案
- CLAUDE.md 的内容（从磁盘重新加载，不依赖摘要）

---

### 阶段 4：压缩后继续对话

压缩后上下文从 150K 降到约 30K-40K（15-20%）。用户看到的变化：

1. **底栏的 context 计数器重置**（从 75%+ 跳回 15-20%）
2. **一次性的 API 费用峰值**（压缩本身是一次额外的 LLM 调用）
3. **模型可能会问已经讨论过的问题**——这是压缩后信息丢失的信号

用户继续工作："现在帮我改 API 模块"。新的对话继续累积：

```
[system prompt]
[compaction block]                ← 上次的压缩摘要
[user] "改 API 模块"
[assistant] ... + tool_use + ...
...新的 20 轮对话...
```

上下文再次增长：30K → 50K → 80K → 100K → ...

---

### 阶段 5：链式压缩（第二次触发）

继续工作 30 轮后，上下文又到了 150K（75%）。再次触发压缩。

这次压缩的输入是：

```
[compaction block]     ← 上次的摘要（2000-5000 tokens）
[30 轮新对话]           ← 新积累的历史（~120K tokens）
```

LLM 在上次摘要的基础上，把新的 30 轮也压进去：

```
第二次压缩后：
[compaction block v2]
"会话摘要（第二轮压缩）：
之前：用户完成了 utils/config 模块重构，测试全通过。
最近：重构了 API 模块（Flask → FastAPI），添加了 5 个新端点，
修了 CORS 配置问题。当前正在写 API 文档。
修改过的文件：src/api/routes.py, src/api/middleware.py, ..."
```

**每次压缩都在上一次的摘要基础上再压。** 信息逐层衰减——第一次压缩丢了早期细节，第二次压缩又丢了中期细节。长会话中可能压缩 3-5 次，最终只剩最近的讨论和一个很粗的历史概要。

---

## 每轮 LLM 调用前的完整检查流程

每次要调 LLM 之前，Claude Code 按顺序执行以下检查（`query.ts:365-453`）：

```
常规操作（每轮都做，和上下文长度无关）：
  1. Budget Reduction → 截断超大的单个工具输出（管"单个太大"）
  2. Microcompact → 旧工具结果存磁盘留引用（管"旧的太多"）

压缩操作（检查阈值，超了才做）：
  3. 计算当前 token 数，如果 < 75%：直接调 LLM，跳到步骤 7
  4. Snip → 删除最旧的历史片段
  5. 二选一（由配置决定）：
     a. Context Collapse → 分段历史用模板摘要（更精细，保留结构）
     b. Auto-Compact → 调 LLM 摘要全部历史（更粗暴，信息损失大）

  6. 调 LLM

  7. 如果 LLM 返回 prompt_too_long 错误
   → 尝试 context-collapse overflow recovery
   → 尝试 reactive compaction（至多每轮一次）
   → 都失败则终止，reason = 'prompt_too_long'
```

---

## /compact 手动命令

用户在任何时候可以输入 `/compact` 手动触发压缩：

```
/compact
```

或带提示词指导保留什么：

```
/compact 保留关于数据库迁移的设计决定和 API 端点列表
```

手动 compact **直接跳到 Auto-Compact**（LLM 摘要），不经过 Snip/Context Collapse。
因为用户在提示词里指导了保留什么，摘要质量比自动触发的好。

官方建议：**在 60% 占用时手动 `/compact`，不要等到自动触发。**

---

## 关键设计原则

1. **Lazy degradation（最小干预优先）**
   截断单个输出（最便宜）→ 存磁盘留引用 → 删旧消息 → 模板摘要 → LLM 摘要（最贵）。
   每层只在上一层不够时才启用。

2. **前四层不调 LLM**
   Budget Reduction / Snip / Microcompact / Context Collapse 都是纯本地操作。
   只有 Auto-Compact 才调 LLM，是最后手段。

3. **工具输出优先清理**
   旧的 grep 结果、文件读取、测试输出是最大且最不重要的 token 消耗者。
   先清它们，对话消息（用户的意图和模型的推理）尽量保留。

4. **CLAUDE.md 不参与压缩**
   项目配置、安全规则、编码标准放在 CLAUDE.md 里——它从磁盘重新加载，不是从压缩摘要来的。
   所以 CLAUDE.md 的内容永远不会被压缩丢失。

5. **链式压缩在摘要基础上继续**
   每次压缩后摘要成为新的起点。信息逐层衰减但不会从头重建。

6. **用户控制优于自动触发**
   `/compact` + `/context` 让用户主导压缩时机和保留内容。
   自动触发是兜底，不是首选。

7. **缓存保护贯穿全流程**
   整套压缩机制的设计围绕**保护 prompt cache 前缀稳定**：
   - **日常**：Microcompact 的 cache-aware path 通过 context editing API 在服务端清除旧 tool_result，客户端消息不变，缓存完全不破。这是日常持续释放空间的主力。
   - **Microcompact time-based path**：sentinel 字符串做了字节级归一化，重复替换不改变已缓存内容。
   - **Budget Reduction**：截断发生在消息发给 API 之前，截断后的内容进缓存后就不再变。
   - **Snip / Compact**：会破坏缓存（前缀变了），但因为 Microcompact 日常已经控制住增长，Snip 极少触发。偶尔一次缓存失效的代价可接受。
   - **System prompt**：从 CLAUDE.md 重新加载，始终不变，至少这部分能缓存命中。
