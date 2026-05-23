# 跨 turn tool 上下文: 设计整合

## 现状

`openprogram/context/engine.py::_assemble_messages` 只翻译 `role=user` /
`role=assistant` 的 content text, 把 `role=tool` 行整个丢掉。
跨 turn 后, 模型只看到 assistant 自己的文字总结, 看不到:

- 调用过哪些 tool, 参数是什么
- tool 返回了什么具体内容

后果: 第二轮起模型对自己之前的 tool 工作零记忆, 重复调用、胡说调
用过的文件等。

我们的 microcompactor (`summary_*` / `k_*` 节点) 处理的是另一件事 ——
**整段历史**的语义压缩, 不是 tool 行的逐条 aging。

## 行业做法对比

(详见 Explore subagent 报告)

| 框架 | 跨 turn tool 可见 | aging 策略 | 压缩触发 | prompt cache |
|---|---|---|---|---|
| **Claude Code** | yes | sessionMemory 后台提炼, tool 调用计数 >20 触发 | tool-call count | ✓ |
| **OpenCode** | yes | tail 2 turns 全文; tool output 截 2000 字; 关键 tool 受保护 | token 阈值 (PRUNE_MINIMUM=20k / PROTECT=40k) | ✓ (`applyCachePolicy`) |
| **Hermes** | yes | 保护首 3 + 尾 6, 老 tool args 截 200 字, 1 行语义摘要 | 75% 阈值 | ✗ |
| **OpenClaw** | yes | 按 engine 实现, foreground/background 模式 | engine driven | ✓ |
| **我们 (现状)** | **no** | — | microcompact 阈值 | ✗ |

## 整合后的设计

博采众长, 取每家最干净的部分:

1. **tail-window 全文 + 老 turn aging** (OpenCode 的 tail + Hermes 的逐条摘要)
   - 最近 `TAIL_TURNS = 3` 个 assistant turn 的 tool_use / tool_result
     **完整保留**。
   - 老的 turn: tool_use 头保留 (args 截 200 字), tool_result 换成
     1 行语义 stub: `[<tool> args=... → <one_line_summary>]`。

2. **tool result 单条上限** (OpenCode)
   - 任何一个 tool_result 超过 `MAX_TOOL_RESULT_CHARS = 4000` 字符,
     截首部 + 尾巴 `... [truncated N more chars]`。tail-window 内也截 ——
     一个 tool 返回 50MB JSON 也得截, 否则单 turn 内就爆。

3. **关键 tool 保护** (OpenCode)
   - `todo_read` / `todo_write` 之类的状态读写 tool 不参与 aging
     (它们的 result 通常很短且语义关键)。

4. **微 / 宏两级压缩**
   - **微**: tool aging, 本设计。逐条剪枝, 每轮都跑。
   - **宏**: microcompactor (已有), 历史整段压成 `summary_*`。
     仍然在 token >> 阈值 时触发, 但因为 tool aging 已经显著瘦身,
     宏压缩触发频率会下降。

5. **prompt cache 准备** (OpenCode)
   - 不是本设计的强制项, 但 `_assemble_messages` 的输出结构要让
     provider 层能加 `cache_control: ephemeral` 标记 ——
     system 末尾 + tools 末尾 + 最新 user 三个位置。当前结构
     已经兼容, 后续 provider 调用加标记即可。

## 数据流

```
get_branch (db)                              [user, assistant, ...] (无 tool)
   ↓
add_tool_calls(history)                      给每个 assistant 挂上它的
                                              caller-children tool 行
   ↓
age_tool_history(history, head_idx)         tail 全文, 老的换 stub
   ↓
truncate_long_results(history)               单条 > MAX 截断
   ↓
microcompactor (已有, 可选触发)              超 token 阈值时整段压缩
   ↓
_assemble_messages → ContentBlock[]          翻译成 ToolUse + ToolResult
                                              content blocks
   ↓
LLM
```

## 模块拆分

新加目录 `openprogram/context/tool_aging/`, 单一职责小模块:

```
tool_aging/
├── __init__.py                # 公开 enrich_with_tools / age_history
├── attach.py                  # 把 caller-children tool 行挂回 assistant
├── policy.py                  # 常量: TAIL_TURNS / MAX_TOOL_RESULT_CHARS / PRUNE_PROTECTED
├── summarize.py               # 单条 tool 的 1 行语义摘要 (hermes 风)
└── truncate.py                # 长 tool_result 头尾截断
```

`engine.py::_assemble_messages` 调用 `enrich_with_tools(history, head_id)`
拿到 enriched history, 然后翻译 tool_use / tool_result 成 content blocks。

## 阈值默认值

| 常量 | 值 | 出处 |
|---|---|---|
| `TAIL_TURNS` | 3 | OpenCode default 2, 调到 3 给我们的长 tool 链留余量 |
| `MAX_TOOL_RESULT_CHARS` | 4000 | OpenCode 2000 字符显示太短, 4000 平衡 |
| `MAX_TOOL_ARGS_CHARS` | 200 | Hermes |
| `PRUNE_PROTECTED_TOOLS` | `{todo_read, todo_write, web_search}` | OpenCode 思路 |

## Stage 与本轮范围

**Phase A (本轮)**: tool_aging 模块 + `_assemble_messages` 接 enrich 逻辑。
跨 turn 模型能看到自己的工具调用 + 结果, 老的自动 aging。

**Phase B (下轮)**: provider 层加 `cache_control` 标记, 让 system / tools
/ latest user 走 Anthropic prompt cache。

**Phase C (后续)**: 宏 / 微压缩协同 —— 当 tool aging 不够时 (整段 user
text 也很多), 触发 microcompactor 整段压缩, 包含 aged tool stubs。

## 验收

- session `local_c3786f69c8`: 第二轮前 4 assistant turn 都调过 tool。
  下一轮发"你刚才看了哪些文件", 模型应能列出来 (不是凭 assistant 自
  己的文字总结猜)。
- 同 session 跨 5 轮 / 30 tool 总 token 不应该比现在 baseline 多 2x
  以上 (老 turn 的 tool 全 aging 到 stub)。
- 单测覆盖: attach / age / truncate 各独立可测, 不要 monolith。
