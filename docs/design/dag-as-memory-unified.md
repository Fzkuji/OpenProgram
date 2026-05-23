# DAG = Session Memory: 统一的上下文 / 记忆模型

## Core 想法

**DAG 是会话的全部记忆**, 不是 "DAG 之外另有一份 session memory"。

DAG 本身已经记录了:
- 用户每条输入
- assistant 每条回复
- 每个 tool 调用 + 参数 + 完整结果
- retry 分支 / 子调用层级 / 时间戳

这些足以重建任何 "session 记忆" 视图。我们之前讨论"加 session memory 模块"是错路 ——
正确做法: **把现有 compact / aging / 摘要全部重新定义为 "DAG 上的标注"**, 而不是
"另一个存储"。

```
DAG (真源)
  ↑
  └ annotations  (aged / cleared / summarized / pinned / cited 等标记)
       ↑
       └ context view  (运行时计算: 实际发给 LLM 的消息流)
```

annotation 是派生的, 可以从 DAG 任意重建; context view 是从 DAG + annotation
计算出来的, 不持久化。

## 两个视图

### V1: Raw History (现有)
- DAG 全貌
- 所有分支, 所有 tool, 所有 retry
- 用户在 chat UI 看到的全部对话

用途: chat 历史, 分支导航, debugging, 删除 / 编辑。

### V2: Active Context (新)
- "此时此刻 LLM 真正看到的消息流"
- 显示: 哪些节点全文 / 哪些 aged / 哪些 cleared / 哪些 summarize 进了某条节点
- 显示: pinned / cited 的节点高亮
- 用户能"看见"context 在如何被管理

UI 形态: 现在 right-dock 已经有 Viewport / Context 切换 (那个白色实心标记)。扩展成
完整的 "active context" 视图:
- 不只标 "哪些进 context", 还要标 **以什么形式进** (全文 / stub / cleared)
- token 占用 / cache 命中率 / aging 时间线

## Annotation 模型

每个 DAG 节点 metadata 加 4 个字段, 由 context engine 计算 + 持久化:

```python
{
    "context_state": "full" | "aged" | "cleared" | "summarized" | "pinned",
    "context_state_set_at": <timestamp>,
    "context_state_reason": "tail_window" | "idle_60min" | "user_pinned" | ...,
    "summarized_into": "<summary_node_id>"  # if state == summarized
}
```

annotation 不修改节点 content (DAG 真源不变)。LLM 渲染时根据 state 决定怎么取:
- `full`: 节点 output 原样
- `aged`: 替成 stub 字符串
- `cleared`: 替成 `[content cleared]` 占位
- `summarized`: 跳过 (内容已折进 summary 节点)
- `pinned`: 强制 full, 不受其它 aging 影响

annotation 由 context engine 在 prepare() 阶段计算 + 写回。下一轮可以读这次的
annotation 当起点, 增量推进, 不每次从零跑。

## 现有 / 缺失的 context 功能盘点

###  已有
| 功能 | 实现 | 状态 |
|---|---|---|
| microcompact (60min idle 清老 tool) | `context/microcompact.py` | ✓ |
| autoCompact (LLM 摘要 + 替换) | `context/summarize.py` + `persistence.insert_summary_node` | ✓ |
| tool_aging (逐条 stub) | `context/tool_aging/` | ✓ (Phase A 刚加) |
| 引用追踪 (cited_tool_use_ids 保护) | `context/references.py` | ✓ 但只在 microcompact 用 |
| Token budget 计算 | `context/budgets.py` | ✓ |
| Context window 大小检测 | `context/tokens.py` | ✓ |

### 缺失 (按价值从高到低)

**1. Pinned messages (用户钉选, 永远不 age)**
- 用户右键消息 → "Pin to context", 该节点 annotation `pinned=True`
- 所有 aging / compact 流程跳过 pinned 节点
- Claude Code 没有显式 pin, 但 sessionMemory 是隐式 pin (永远在 system prompt)
- 实现成本: 低 (annotation + UI menu)

**2. Cross-session context (载入其它 session 作为前置)**
- "继续昨天那个会话" / "把 session A 的结论作为这次的起点"
- 现在的 get_branch 只走一个 session 的 conv 链
- 需要: 一种"虚拟父指针"指向另一 session 的某节点, 拉过来作为只读前缀
- 实现成本: 中 (DAG 跨 session 边 + UI 选择器)

**3. Per-tool source-side truncation (Claude Code 路线)**
- 每个 tool 在 execute() 时自己截到 reasonable size, context engine 不再二次截
  - Bash: 30K 字符 + 全文落盘到 `/tmp/bash-out-<id>.txt`, LLM 看摘要 + 落盘路径
  - Read: 默认 2000 行 + 分页
  - Grep: 限 100 个 match + "and N more"
- 现在我们的 tool 全部不截, 大输出全进 context 才在 aging 阶段砍
- 实现成本: 中, 但要改每个 tool

**4. Compaction warning (软提示)**
- token 到 80% 时 UI 显示警告: "context 接近上限, 建议手动 compact / 新建会话"
- 现在到 autoCompact 阈值才硬触发, 没有早期警告
- 实现成本: 低 (复用 budget 数据 + UI badge)

**5. Selective branch visibility**
- 当前在 branch A, 模型默认看不见 branch B 的内容
- 但用户可能想说 "结合 B 的尝试和 A 的方向"
- 需要: 多分支 merge view, annotation 标 "from branch B"
- 实现成本: 中

**6. Reference / citation auto-protection**
- 模型说 "如 turn 3 所述" → turn 3 自动 pinned, 不被 aging 砍
- references.py 已有数据 (cited_tool_use_ids), 但只在 microcompact 里用了
- 扩展到所有 aging / compact 流程
- 实现成本: 低

**7. Tool result dedup**
- 同 session 内 read(foo.py) 调 3 次, 只在 context 留最新一次 result, 老的换 stub: `[duplicate of turn N read]`
- 实现成本: 低 (按 tool+args 哈希)

**8. Memory recall (主动从老 DAG 拉信息)**
- 用户问 "你记得我们之前讨论过 X 吗", 系统先 FTS / embedding 搜全部 session DAG, 找到相关节点, 临时 attach 到 context
- 现在没有这个机制, 模型只能从当前 session context 里找
- 实现成本: 高 (要 embedding 索引)

**9. Thinking block 缓存友好处理**
- Anthropic thinking blocks 占大量 token, 但每次重发会破 cache
- Claude Code 用 `clear_thinking_20251015` 让服务端清掉
- 我们没接 Anthropic alpha API, 折中: 老 thinking 在客户端就清掉, 类似 microcompact
- 实现成本: 低

**10. Skill / role 动态注入**
- 用户说 "切换到 frontend 模式" → system prompt 自动加一段 frontend skill 描述
- 跟 agent profile 类似但更细粒度, 单 turn 内
- 实现成本: 中

### Anthropic / OpenAI prompt cache 接入
单列, 因为是横切关注点:
- system prompt 末尾打 `cache_control: ephemeral`
- 最新 user 之前的全部历史打 `cache_control: ephemeral`
- tools 列表打 `cache_control: ephemeral`
- annotation 中 state 转换 (full → aged) 时, 要小心选时机, 别频繁破 cache prefix

## 模块重构方向

现在 `openprogram/context/` 是个平面:
```
context/
├── engine.py
├── microcompact.py
├── summarize.py
├── references.py
├── tool_aging/
├── budgets.py
├── tokens.py
├── persistence.py
```

按"annotation + view"模型重组:
```
context/
├── engine.py             # 入口, 调度
├── annotations/          # 各种标注产生器, 单一职责
│   ├── tool_aging.py     # full → aged
│   ├── microcompact.py   # full → cleared
│   ├── summarize.py      # full → summarized + 新建 summary 节点
│   ├── references.py     # 计算 cited, 阻止其它 annotation 覆盖
│   ├── pinning.py        # 用户 pin
│   └── dedup.py          # 重复 tool result 标记
├── views/                # 从 DAG + annotation 计算视图
│   ├── raw.py            # V1 raw history
│   ├── active.py         # V2 active context (LLM 看到的)
│   └── budget.py         # token / cache 统计
├── store.py              # annotation 持久化 (DAG 节点 metadata)
└── README.md
```

每个 annotation 模块独立可测、可关闭。view 层组合多个 annotation 出渲染结果。

## Memory 三块的对应

之前讨论的 3+1 memory:
- **Core** (`memory/core.py`): 还是独立 (用户维护的 always-on 文档)
- **Wiki** (`memory/wiki/`): 跨 session 知识, 从 DAG 抽取 (sleep 任务消费)
- **Journal** (`memory/journal.py`): 跨 session 时间序, 从 DAG 抽取
- **Session memory** (之前讨论缺的): **不需要单独建** —— 它就是 DAG +
  annotation。UI 上 V2 active context 就是"用户能看到的 session memory"

所以最终结构:
- 跨 session: wiki + journal + core (独立持久, 跟 session 解耦)
- 当前 session: DAG + annotation (一体, 不需要单独 memory 文件)

## 显示

UI 两个视图:

**V1 (现有 chat 滚动 + 右侧 DAG):**
- chat 滚动: 用户视角全文历史
- DAG: 拓扑结构, 显示所有 branch / tool / retry

**V2 (新): Active Context Inspector**
- 入口: right-dock 加第三个 view (除了 History / Detail)
- 内容:
  - 按时间序列出 LLM 此刻看到的每条消息
  - 每条消息旁标 state: full / aged / cleared / summarized / pinned, 颜色区分
  - token 计数: 每条多少 / 累计多少 / 占 window 百分比
  - cache 命中预测: 哪部分能 hit, 哪部分会 miss
- 用户能"看见"我们的 context engine 在做什么, 能信任 / debug 它
- annotation 操作: 右键 unpin / repin / manual-clear

## 路线

**Phase 1**: 重新组织 `context/` 目录结构 (annotations + views 分层), 现有功能搬过去, 行为零变化
**Phase 2**: annotation 持久化 (写到 DAG node metadata)
**Phase 3**: V2 Active Context Inspector UI
**Phase 4**: 补缺失功能, 按价值排序 (Pin → Compaction warning → Reference 全局保护 → Per-tool truncate → ...)
**Phase 5**: Prompt cache 接入 (横切)
**Phase 6**: Cross-session context / Memory recall (大功能, 单独立项)
