# 统一 parent_id 与 called_by — 设计方案

> 状态: **部分实施**
> 代码: `store/session/_msg_adapter.py`、`webui/persistence.py`、`contextgit/dag.py`、`webui/ws_actions/session.py`

## 1. 问题

DAG 节点有两个"父指针"字段，含义不同，在不同地方被读写，导致遍历结果不一致。

| 字段 | 含义 | 谁写 | 谁读 |
|---|---|---|---|
| `called_by` | 调用关系（谁调用了我） | DAG store（`context/nodes.py` 的 `Call` 对象） | `render_context`、`get_branch`、`_rebuild_runtime_cards`、`aggregate_tool_messages`（改后） |
| `parent_id` | 对话链（我的上一条消息是谁） | `_msg_adapter.py`（从 `called_by` 复制） | `linear_history`、`_annotate_spawn_origin`、dispatcher 分支管理 |

**根本问题**：`_msg_adapter.py` 把 `called_by` 直接赋给 `parent_id`（行 132/169/188/206），但两者的语义不同：

- `called_by` 是**调用层级**：user 的 called_by=ROOT，函数的 called_by=ROOT（手动调用）或 assistant_id（LLM 调用），工具的 called_by=函数 id
- `parent_id` 应该是**对话顺序**：第二条消息的 parent_id 应指向第一条，第三条指向第二条

直接赋值导致：同一个会话里两个 ROOT-parented 的 user 节点，它们的 `parent_id` 都是空（ROOT 不是有效的消息 id），`linear_history` 沿 parent_id 走就断了。

## 2. 两种数据结构

DAG 和聊天 UI 需要不同的数据格式，两者都需要：

| | DAG 原始节点 | 聊天 UI 消息 |
|---|---|---|
| 用途 | 运行时（render_context 构建上下文） | 前端显示（消息列表、工具调用卡片） |
| 工具调用 | 每个工具一个独立节点 | 折叠进 assistant 消息的 tool_calls[] |
| thinking | 在 extra 字段里 | 提取到 blocks[] |
| 格式 | `{role, name, input, output, called_by, seq}` | `{role, content, tool_calls, blocks, parent_id}` |
| 构建时机 | 写入时 | 加载时（aggregate_tool_messages） |

`aggregate_tool_messages` 就是把 DAG 格式转成 UI 格式的。

## 3. 已完成的修复

| 修复 | commit | 做了什么 |
|---|---|---|
| `_rebuild_runtime_cards` 用 called_by | `476aa8f6` | 函数后代关系用 called_by 判断，不再误 drop user 节点 |
| `aggregate_tool_messages` 优先 called_by | 本次 | 工具→assistant 聚合用 `called_by` 找父节点，`parent_id` 作 fallback |
| `handle_load_session` fallback | `1adfbbc3` | linear_history 不完整时 fallback 到 get_branch |

## 4. 当前策略

**渐进式统一**：不一次性废弃 `parent_id`，而是逐步让关键路径优先使用 `called_by`。

`parent_id` 在 188 处被引用，深入 dispatcher、分支管理、sub_agent 等核心模块。一次性替换风险太高。当前策略：

1. **聚合层**（persistence.py）：优先 `called_by`，`parent_id` 作 fallback ✅ 已完成
2. **渲染层**（session.py _rebuild_runtime_cards）：用 `called_by` ✅ 已完成
3. **加载层**（session.py handle_load_session）：linear_history + get_branch fallback ✅ 已完成
4. **_msg_adapter.py**：继续复制 called_by → parent_id（向后兼容）
5. **linear_history**：保持用 parent_id（有 fallback 兜底）

## 5. 后续计划（低优先级）

当以上修复经过充分验证后，可以进一步：

| 步骤 | 做什么 | 前提 |
|---|---|---|
| A | `_msg_adapter.py` 的 parent_id 按对话 seq 顺序设（而非复制 called_by） | 确认当前修复稳定 |
| B | `linear_history` 改用正确的 parent_id 遍历（步骤 A 后自然正确） | 步骤 A |
| C | 移除 handle_load_session 的 get_branch fallback（不需要了） | 步骤 B |
| D | 标记 parent_id 为 deprecated，长期只用 called_by | 步骤 A-C 全部稳定 |

## 6. 风险

**当前方案的风险**：低。只改了聚合层的字段优先级，parent_id 作 fallback 保留。最坏情况 = called_by 缺失时 fallback 到 parent_id（和改之前行为一致）。

**后续步骤的风险**：中-高。改 _msg_adapter.py 影响所有消息加载，需要 feature flag + 充分测试。
