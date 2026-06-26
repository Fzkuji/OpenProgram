# Claude Code 上下文压缩机制 — 完整流程参考

> 调研文档（非设计文档）。基于源码逆向分析，记录 Claude Code 的压缩机制。
> 注意：Claude Code 的阈值和实现在不同版本中有调整，以下数字来自逆向分析，可能不完全准确。
>
> 来源：
> - [Dive into Claude Code](https://arxiv.org/html/2604.14228v1)（arXiv 2604.14228，VILA-Lab 源码逆向分析）
> - [Inside Claude Code - Context Compaction](https://y-agent.github.io/inside-claude-code/04-context-compaction.html)
> - [Claude Code's Compaction Engine](https://barazany.dev/blog/claude-codes-compaction-engine)
> - [Context editing - Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
> - [Compaction - Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/compaction)
> - [DeepWiki - Context Window & Compaction](https://deepwiki.com/anthropics/claude-code/3.3-context-window-and-compaction)

---

## 1. 总览

Claude Code 有两类压缩机制：

**常规操作**（每轮 LLM 调用前都做，和上下文长度无关）：
- Budget Reduction：截断超大的单个工具输出

**级联压缩**（按 Tier 1→2→3→4→5 的顺序，前一级不够才上后一级）：
- Tier 1 Microcompact：清理旧工具输出
- Tier 2 Snip：删最旧的几轮对话
- Tier 3 Context Collapse：分段 LLM 摘要
- Tier 4 Auto-Compact：全量 LLM 摘要
- Tier 5 Reactive：API 返回 413 时紧急压缩

| | 名称 | 做什么 | 调 LLM | 破缓存 | 信息损失 |
|---|---|---|---|---|---|
| 常规 | Budget Reduction | 截断超大单个输出 | 否 | 否 | 中间内容丢 |
| Tier 1 | Microcompact | 旧工具输出清理 | 否 | cache-aware 不破 | 旧输出丢 |
| Tier 2 | Snip | 删最旧几轮 | 否 | 是 | 整轮丢 |
| Tier 3 | Context Collapse | 分段摘要 | 是 | 是 | 细节丢，要点留 |
| Tier 4 | Auto-Compact | 全量摘要 | 是 | 是 | 大量丢，只留概要 |
| Tier 5 | Reactive | 紧急压缩 | 是 | 是 | 同 Tier 4 |

**级联的意思**：每轮 LLM 调用前，先检查 Tier 1 的条件，满足就做；做完后检查 Tier 2，满足就做；以此类推。不是跳着执行，是从 1 到 5 顺序检查。前面的 Tier 成本低，能解决就不上后面的。

---

## 2. 常规操作：Budget Reduction

**和级联无关**，每轮 LLM 调用前都做。

**做什么**：检查每个工具输出的大小，超过阈值就截断。

**参数**：
- 截断阈值：**4000 字符**（约 1000 tokens）
- 截断方式：超过 4000 字符的输出，只保留前 **2400 字符**（60%）+ 后 **1600 字符**（40%），中间丢弃
- 只处理工具输出（tool_result），不处理 user/assistant 消息

**示例**：
```
截断前（50000 字符）：
[tool_result] "src/a.py:12: TODO fix this\nsrc/b.py:34: ..."

截断后（4000 字符）：
[tool_result] "src/a.py:12: TODO fix this\n...[46000 chars removed]...\nsrc/z.py:99: TODO cleanup"
```

---

## 3. 级联压缩

每轮 LLM 调用前，Budget Reduction 之后，按 Tier 顺序检查和执行。

### Tier 1：Microcompact

**做什么**：清理旧的工具输出，释放空间。

**触发条件**：
- Cache-aware path：**50 次工具调用**后首次触发，之后**每 25 次**再触发
- Time-based path：距上次 assistant 消息 **~90 分钟**

**只处理特定工具**：FileRead、Shell、Grep、Glob、WebSearch、WebFetch、FileEdit、FileWrite

**两条路径**：

| | Cache-aware（主力） | Time-based（备用） |
|---|---|---|
| 做什么 | 通过 Context Editing API 让服务端清除旧 tool_result | 客户端把旧 tool_result 替换为 sentinel 字符串 |
| 破缓存 | 不破（服务端操作，客户端消息不变） | 破（客户端改了消息） |
| 触发 | 50 次工具调用后，每 25 次 | 空闲 ~90 分钟 |
| 可恢复 | 否 | 否 |
| 依赖 | Anthropic API 的 Context Editing（其他 provider 不支持） | 无依赖 |

**压缩效果**：每个被清理的工具输出从原始大小（几百到几千 tokens）→ 0 tokens（cache-aware）或 ~20 tokens（time-based）。例：一次 Microcompact 清理了 10 个旧工具结果，每个平均 500 tokens → 释放约 4800 tokens。

**示例**：
```
清理前：
[tool_result] "import os\nimport sys\n\nclass Config:\n    ..."（2000 tokens）

清理后（cache-aware）：
[tool_result] ""（服务端清空，0 tokens，缓存不破）

清理后（time-based）：
[tool_result] "[content no longer available]"（~20 tokens）
```

**Cache-aware path 细节**：sentinel 字符串做了**字节级归一化（byte-stable canonical form）**——重复 microcompact 不改变已缓存内容。核心原则：已进缓存的内容，客户端不修改（修改会破前缀匹配）。要清旧内容时：
- 已在缓存 → cache-aware path，通过 Context Editing API 让服务端清
- 没在缓存 → time-based path，客户端直接替换为 sentinel

#### Context Editing API（cache_edits）

Microcompact cache-aware path 的底层实现。Anthropic API 的公开 beta（header: `context-management-2025-06-27`），不是 Claude Code 专有，普通开发者可用。

**工作原理**：客户端发送完整消息历史（不做任何修改），同时在 API 请求中传一个 `cache_edits` 参数。服务端收到后：
1. 在缓存内部找到旧的 tool_result 块
2. 把它们替换为空或占位文本
3. 缓存前缀不变（因为客户端发的消息没变，服务端只改了缓存内部的数据）
4. 客户端完全不需要知道哪些被清了

和正常 LLM 调用合并在一起，不是额外请求——发消息的同时顺便告诉服务端"清掉旧的 tool_result"。

| 策略 | 做什么 |
|---|---|
| `clear_tool_uses` | 清除旧的 tool_result，只保留最近 N 个 |
| `clear_thinking` | 清除旧的 thinking blocks |
| `clear_at_least` | 控制最少清多少 token |

**和其他 Tier 的关系**：Context Editing 不是独立的 Tier，它是 Tier 1 Microcompact cache-aware path 的底层实现。Tier 2-5 都不用这个 API——它们直接修改客户端的消息列表。

**限制**：只有 Anthropic API 支持。OpenAI / Google / 其他 provider 没有类似能力，只能走 time-based path（客户端替换，会破缓存）。

### Tier 2：Snip

**做什么**：直接删除最旧的几轮对话。不做摘要、不存磁盘，直接丢。

**触发条件**：Tier 1 做完后仍超预算（逆向分析中出现 ~13K tokens buffer 的说法，但具体阈值可能随版本变化）

**参数**：
- 删除粒度：**整轮**（user + assistant + 该轮所有工具调用一起删）
- 删多少：从最旧的开始逐轮删，删到阈值以下为止
- 删除的内容彻底消失，模型不知道之前聊过什么

**代价**：信息完全丢失。但免费（不调 LLM）。

### Tier 3：Context Collapse

**做什么**：把旧对话分成若干段（5-10 轮一段），每段用 LLM 生成摘要。

**触发条件**：Tier 2 Snip 做完后仍超阈值（约 90%，非阻塞触发；95% 时阻塞强制触发）

**关键特性**：原始消息**保留**在 collapse store 中。这是**读时投影（read-time projection）**——类似数据库 View，底表不动，查询看到的是摘要视图。原始历史保留着，理论上可以重建/回滚。

**分段标准**：5-10 轮一段。每段独立用 LLM 摘要。

**和 Tier 4 的区别**：
- Context Collapse 是**分段**摘要，每段独立，保留分段边界和时间线结构
- Auto-Compact 是**全量**摘要，一把压成一段，结构丢失

**示例**：
```
压缩前（35 轮）：
[轮 6-10] 读代码、找 bug、修复、测试
[轮 11-15] 重构 config 模块
[轮 16-35] 改 API 模块...

压缩后：
[摘要] "Turns 6-10: 修了 utils.py 的 bug，添加了回滚脚本"
[摘要] "Turns 11-15: 重构了 config 模块，抽出了 Settings 类"
[轮 16-35] ...（最近的完整保留）
```
原始的 turn 6-15 仍然保存在 collapse store 中，模型看到的是折叠版本。

### Tier 4：Auto-Compact

**做什么**：把整个对话历史一次性发给 LLM，生成一个摘要块，替换所有旧消息。

**触发条件**：Tier 3 做完后仍超阈值。或用户手动 `/compact`。

**关键特性**：原始消息被**替换**，不可回滚。信息损失最大。

**具体过程**：
1. 执行 PreCompact hooks（通知系统即将压缩）
2. 用 `getCompactPrompt()` 构造压缩提示（类似"请把以下对话历史压缩成关键事实"）
3. 把整个对话历史发给 LLM，生成摘要
4. 用 `buildPostCompactMessages()` 构建压缩后的消息
5. system prompt 从 CLAUDE.md **重新加载**（不是从摘要来的，所以 CLAUDE.md 的内容永远不丢）

**`/compact` 手动命令**：
- `/compact` 或 `/compact 保留关于数据库迁移的讨论`
- 直接触发 Auto-Compact，不经过 Tier 1-3
- 带提示词时摘要质量更好（用户指导保留什么）
- 官方建议在 **60% 占用时手动 `/compact`**

**丢失的**：早期指令、设计讨论、推理过程、50+ 轮前的代码片段、风格偏好。
**保留的**：当前任务、最近修改的文件名、最近的错误和解决方案、CLAUDE.md 内容。

**示例**：
```
压缩前（35 轮，150K tokens）：
[system prompt]
[35 轮完整对话历史]

压缩后（~30K tokens）：
[system prompt]（从 CLAUDE.md 重新加载）
[compaction block]
"会话摘要：用户在做 Python 项目重构。
已完成：修了 utils.py 的 bug、重构了 config 模块、添加了 3 个测试。
当前状态：所有测试通过。用户正在处理 API 模块。
关键决定：使用 FastAPI 替换 Flask、数据库用 PostgreSQL。
修改过的文件：src/utils.py, src/config.py, tests/test_utils.py"
[最近 1-2 轮完整保留]
```

**链式压缩**：压缩后继续聊，再次满了再压。每次在上一次摘要基础上再压，信息逐层衰减——第一次丢早期细节，第二次丢中期细节。长会话可能压缩 3-5 次。

**链式压缩**：压缩后继续聊，再次满了再压。每次在上一次摘要基础上再压，信息逐层衰减。

### Tier 5：Reactive

**做什么**：API 返回 413（prompt too long）错误时的紧急压缩。

**触发条件**：LLM 调用失败，返回 prompt_too_long / 413 错误。

**做法**：尝试 context-collapse overflow recovery，失败则做紧急 compact。每轮至多触发一次。都失败则终止会话。

---

## 4. 执行流程

每轮 LLM 调用前的完整流程：

```
1. Budget Reduction → 截断超大工具输出（每轮都做）

2. 级联检查（按 Tier 顺序）：
   Tier 1: Microcompact 条件满足？→ 清旧工具输出
   Tier 2: 仍超阈值？→ Snip 删旧轮
   Tier 3: 仍超阈值？→ Context Collapse 分段摘要
   Tier 4: 仍超阈值？→ Auto-Compact 全量摘要

3. 调 LLM

4. 如果 API 返回 413：
   Tier 5: Reactive → 紧急压缩 → 重试
   → 失败则终止
```

---

## 5. 完整运行示例

场景：200K context，持续工作几小时。

**阶段 1（0-30%）**：正常对话。Budget Reduction 检查工具输出大小，没超的跳过。级联都不触发。

**阶段 2（30-50%）**：工具调用超过 50 次。Tier 1 Microcompact cache-aware 首次触发，清理前 30 次工具调用的输出，释放约 15K tokens。之后每 25 次再清一轮。级联到 Tier 1 就够了，Tier 2-4 不触发。

**阶段 3（超阈值）**：对话消息累积超阈值。Tier 1 Microcompact 做了，还是超。Tier 2 Snip 触发，删最旧的 5 轮，释放约 25K tokens。降到阈值以下，Tier 3-4 不触发。

**阶段 4**：继续使用。再次超阈值时重复阶段 3（Microcompact + Snip）。

**阶段 5（极端）**：反复 Snip 后可删的旧轮不多了，Snip 不够用。Tier 3 Context Collapse 触发，分段摘要。还不够则 Tier 4 Auto-Compact。这种情况很少——因为 Microcompact 日常控制住了工具输出的增长，Snip 通常够用。

---

## 6. 关键设计原则

1. **Lazy degradation**：成本低的先做。Microcompact（免费不破缓存）→ Snip（免费破缓存）→ Context Collapse（调 LLM）→ Auto-Compact（调 LLM，最激进）。

2. **工具输出优先清理**：旧的 grep / read_file / bash 输出是最大且最不重要的消耗者。先清它们，对话消息尽量保留。

3. **日常靠 Microcompact**：cache-aware path 持续释放空间且不破缓存。这使得 Snip/Compact 极少触发。

4. **缓存保护**：Microcompact cache-aware 通过 Context Editing API 服务端清除，缓存不破。Budget Reduction 截断后内容固定。Snip/Compact 会破缓存但触发频率低。

5. **对话消息不单独截断**：user/assistant 消息要么整轮删（Snip），要么 LLM 摘要（Compact）。不对单条消息截断。

6. **CLAUDE.md 不参与压缩**：从磁盘重新加载，永远不丢。

7. **用户控制**：`/compact` + `/context` 让用户主导。自动是兜底。
