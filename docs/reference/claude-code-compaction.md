# Claude Code 上下文压缩机制 — 完整流程参考

> 调研文档（非设计文档）。记录 Claude Code 的 5 种压缩方式和完整运行流程。
>
> 来源：
> - [Dive into Claude Code](https://arxiv.org/html/2604.14228v1)（arXiv 2604.14228，VILA-Lab 基于源码逆向分析）
> - [Inside Claude Code - Context Compaction](https://y-agent.github.io/inside-claude-code/04-context-compaction.html)
> - [Claude Code's Compaction Engine](https://barazany.dev/blog/claude-codes-compaction-engine)
> - [Context editing - Claude API Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
> - [Context engineering cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)

---

## 1. 五种压缩方式

Claude Code 有 5 种压缩方式，按成本从低到高排列。每种独立工作，解决不同的问题。

| # | 名称 | 解决什么 | 触发时机 | 是否调 LLM | 是否破缓存 |
|---|---|---|---|---|---|
| ① | Budget Reduction | 单个工具输出太大 | 每轮 LLM 调用前 | 否 | 否（截断后内容固定） |
| ② | Microcompact | 旧工具输出累积太多 | 50 次工具调用后 / 空闲 90 分钟 | 否 | cache-aware 路径不破；time-based 路径破 |
| ③ | Snip | 上下文总量超阈值 | 百分比阈值（通过 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` 可调） | 否 | 是（前缀变了） |
| ④ | Context Collapse | Snip 后仍超阈值 | ~90%（和 ⑤ 二选一） | 是（每段一次） | 是 |
| ⑤ | Auto-Compact | Snip 后仍超阈值 / 用户手动 /compact | ~75-87%（和 ④ 二选一） | 是（一次） | 是 |

---

### ① Budget Reduction

**解决什么**：一个工具输出太大（比如 read_file 读了一个 10 万行的文件）。

**触发时机**：每轮 LLM 调用前，不管上下文占了多少。

**具体参数**：
- 截断阈值：**4000 字符**（约 1000 tokens）
- 截断方式：超过 4000 字符的输出，只保留前 **2400 字符**（60%）+ 后 **1600 字符**（40%），中间全部丢弃
- 只处理工具输出（tool_result），不处理 user/assistant 消息

**示例**：
```
截断前（50000 字符）：
[tool_result] "src/a.py:12: TODO fix this\nsrc/b.py:34: TODO refactor\n..."

截断后（4000 字符）：
[tool_result] "src/a.py:12: TODO fix this\n...[truncated, 46000 chars removed]...\nsrc/z.py:99: TODO cleanup"
```

---

### ② Microcompact

**解决什么**：旧的工具输出累积占空间。不管单个输出大不大，旧了就清理。

**触发时机**：有两条路径，触发条件不同。

**压缩效果**：每个被清理的工具输出从原始大小（几百到几千 tokens）变成约 20 tokens 的占位文本。只清工具输出（tool_result），不动 user/assistant 消息。

**只处理特定工具**：FileRead、Shell、Grep、Glob、WebSearch、WebFetch、FileEdit、FileWrite。

#### 路径 A：Cache-aware（主力，不破缓存）

- **触发**：**50 次工具调用**后首次触发，之后**每 25 次**再触发
- **做什么**：通过 Anthropic API 的 Context Editing（`cache_edits`）让服务端清除旧 tool_result
- **客户端消息不变**，缓存前缀完全不破坏
- **保留**：最近 N 个工具结果保持 inline，删除其余
- **只有 Anthropic API 支持**，其他 provider 用不了

#### 路径 B：Time-based（备用，会破缓存）

- **触发**：距上次 assistant 消息 **~90 分钟**（用户长时间没操作）
- **做什么**：客户端直接把旧 tool_result 替换为 sentinel 字符串
- sentinel 做了**字节级归一化**——重复 microcompact 不改变已缓存内容
- **不可恢复**——替换后原始内容丢失
- 此时缓存已冷（90 分钟没活动），破缓存无所谓

**示例**：
```
清理前：
[tool_result] "import os\nimport sys\n\nclass Config:\n    ..."（2000 tokens）

清理后（cache-aware）：
[tool_result] ""（服务端清空，0 tokens，缓存不破）

清理后（time-based）：
[tool_result] "[content no longer available]"（~20 tokens）
```

#### Context Editing API（cache_edits）

Microcompact cache-aware 路径的底层实现。Anthropic API 的公开 beta（header: `context-management-2025-06-27`）。

**工作原理**：客户端发送完整消息历史（不修改），同时在 API 请求中传 `cache_edits` 参数。服务端在缓存内部清除指定的 tool_result 块，缓存前缀不变。和正常 LLM 调用合并，不是额外请求。

| 策略 | 做什么 |
|---|---|
| `clear_tool_uses` | 清除旧的 tool_result，只保留最近 N 个 |
| `clear_thinking` | 清除旧的 thinking blocks |
| `clear_at_least` | 控制最少清多少 token |

**核心原则**：已进缓存的内容，客户端不修改（会破前缀）。要清旧内容时：
- 已在缓存 → cache-aware path，服务端清
- 没在缓存 → time-based path，客户端替换

**限制**：只有 Anthropic API 支持。OpenAI / Google / 其他 provider 没有，只能走 time-based path。

---

### ③ Snip

**解决什么**：上下文总量超阈值，需要快速释放空间。

**触发时机**：上下文占 **约 **80-95%**（通过环境变量可调，默认值在不同版本中有变化）。

**具体做法**：直接删除最旧的几轮对话。不做摘要、不存磁盘、不调 LLM，直接丢。

**具体参数**：
- 删除粒度：**整轮**（user + assistant + 该轮所有工具调用一起删）
- 删多少：从最旧的开始逐轮删，每删一轮重新算 token 数，**删到阈值以下为止**
- 删除的内容**彻底消失**，模型不知道之前聊过什么

**示例**：
```
压缩前（40 轮对话，170K tokens）：
[轮 1-5] ...    ← 删掉（约 25K tokens）
[轮 6-40] ...   ← 保留

压缩后（35 轮，145K tokens）
```

**代价**：信息完全丢失。但免费（不调 LLM），大部分情况下删几轮就够了。

---

### ④ Context Collapse

**解决什么**：Snip 删完还是不够（剩下的每轮都很大），需要进一步压缩但想保留信息。

**触发时机**：Snip 后仍超阈值，约 **90%**。和 ⑤ Auto-Compact **二选一**，由配置决定用哪个。

**具体做法**：把旧对话分成若干段（5-10 轮一段），每段用 LLM 生成摘要。

**关键特性**：原始消息**保留**在 collapse store 中（类似数据库 View——底表不动，查询看到的是摘要）。理论上可回滚。

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

---

### ⑤ Auto-Compact

**解决什么**：Snip 删完还是不够，用全量摘要释放最大空间。或用户手动 `/compact`。

**触发时机**：Snip 后仍超阈值，约 **75-87%**。和 ④ Context Collapse **二选一**。用户手动 `/compact` 也走这个。

**具体做法**：把整个对话历史一次性发给 LLM，生成一个摘要块，替换所有旧消息。

**关键特性**：原始消息被**替换**，不可回滚。信息损失最大。

**`/compact` 手动命令**：
- 用户输入 `/compact` 或 `/compact 保留关于数据库迁移的讨论`
- 直接跳到 Auto-Compact，不经过 Snip
- 带提示词时摘要质量更好（用户指导保留什么）
- 官方建议在 **60% 占用时手动 `/compact`**

**示例**：
```
压缩前（35 轮，150K tokens）：
[35 轮完整对话历史]

压缩后（~30K tokens）：
[system prompt]（从 CLAUDE.md 重新加载）
[compaction block]
"会话摘要：用户在做 Python 项目重构。已完成 utils/config 模块，
正在做 API 模块。用 FastAPI + PostgreSQL。测试全通过。
修改过的文件：src/utils.py, src/config.py, src/api/routes.py..."
[最近 1-2 轮完整保留]
```

**丢失的**：早期指令、设计讨论、具体代码片段、风格偏好。
**保留的**：当前任务、最近的文件和错误、CLAUDE.md 内容（从磁盘重新加载）。

**链式压缩**：压缩后继续聊，再次满了再压。每次在上一次摘要基础上再压，信息逐层衰减。

---

## 2. 执行顺序

每轮 LLM 调用前，按以下顺序执行：

```
常规（每轮都做）：
  ① Budget Reduction → 截断超大工具输出
  ② Microcompact    → 旧工具输出清理（cache-aware 或 time-based）

检查阈值：
  计算当前 token 数
  如果 < 自动压缩阈值 → 直接调 LLM，结束

超阈值才做：
  ③ Snip           → 删最旧的几轮
  如果还超 → 二选一：
    ④ Context Collapse → 分段 LLM 摘要（保留原始）
    ⑤ Auto-Compact     → 全量 LLM 摘要（替换原始）

调 LLM

如果 LLM 返回 prompt_too_long：
  → 尝试 overflow recovery
  → 都失败则终止
```

---

## 3. 完整运行示例

场景：用户在 200K context 下工作，持续几小时。

### 阶段 1（0-30%，0-60K tokens）

正常对话，读代码、改文件。每轮 LLM 调用前：
- ① Budget Reduction 检查工具输出，没有超大的，跳过
- ② Microcompact 还没到 50 次工具调用，跳过
- 不到 自动压缩阈值，直接调 LLM

### 阶段 2（30-60%，60K-120K tokens）

20 轮对话后，工具调用超过 50 次。
- ① Budget Reduction：某个 read_file 返回了 8000 字符，截断到 4000
- ② Microcompact cache-aware 首次触发：清理前 30 次工具调用的输出，释放约 15K tokens
- 还没到 自动压缩阈值，不触发 ③④⑤
- 官方建议此时手动 `/compact`，但大部分用户不会

### 阶段 3（自动压缩阈值，约 170K tokens）

40 轮对话后，尽管 Microcompact 一直在清理旧工具输出，对话消息本身不断累积。
- ① Budget Reduction：正常截断
- ② Microcompact：继续清理旧工具输出（每 25 次触发一轮）
- 超过 自动压缩阈值 → ③ Snip 触发：删最旧的 5 轮，释放约 25K tokens
- 降到阈值以下，不需要 ④⑤

### 阶段 4：继续使用

压缩后从约 145K 继续。Microcompact 继续日常清理。再过 20 轮又到 自动压缩阈值，再次 Snip 删几轮。

### 阶段 5：极端情况

反复 Snip 后，可删的旧轮已经不多了（只剩最近 10 轮），但每轮都很大（大量工具调用）。Snip 删完还是超。
- ④ Context Collapse 触发：把剩下的旧轮分段摘要
- 或 ⑤ Auto-Compact 触发：全量摘要

这种极端情况很少发生——因为 Microcompact 日常就在清理工具输出，Snip 通常够用。

---

## 4. 关键设计原则

1. **Lazy degradation**：最小干预优先。截断 → 存磁盘 → 删旧轮 → 摘要。每层只在上一层不够时才启用。

2. **工具输出优先清理**：旧的 grep / read_file / bash 输出是最大且最不重要的 token 消耗者。先清它们，对话消息尽量保留。

3. **日常靠 Microcompact 控制增长**：cache-aware path 持续释放空间且不破缓存。这使得 Snip/Compact 极少触发。

4. **缓存保护贯穿全流程**：
   - Microcompact cache-aware：服务端清，缓存不破
   - Budget Reduction：截断后内容固定，进缓存后不再变
   - Snip/Compact：会破缓存，但触发频率低

5. **CLAUDE.md 不参与压缩**：从磁盘重新加载，永远不会被压缩丢失。

6. **用户控制优于自动**：`/compact` + `/context` 让用户主导。自动是兜底。

7. **对话消息不单独截断**：user/assistant 消息要么整轮删（Snip），要么 LLM 摘要（Compact）。不对单条消息做截断。
