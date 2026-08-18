# 统一 parent_id 与 called_by — 设计方案

> 代码: `store/session/_msg_adapter.py`、`webui/persistence.py`、`context/git/dag.py`、`webui/ws_actions/session.py`

## 一、两个父指针，两种含义

DAG 节点有两个"父指针"字段，含义不同，在不同地方被读写。

| 字段 | 含义 | 谁写 | 谁读 |
|---|---|---|---|
| `called_by` | 调用关系（谁调用了我） | DAG store（`context/nodes.py` 的 `Call` 对象） | `render_context`、`get_branch`、`_rebuild_runtime_cards`、`aggregate_tool_messages` |
| `parent_id` | 对话链（我的上一条消息是谁） | `_msg_adapter.py`（从 `called_by` 复制） | `linear_history`、`_annotate_spawn_origin`、dispatcher 分支管理 |

两者语义不同：

- `called_by` 是**调用层级**：user 的 called_by=ROOT，函数的 called_by=ROOT（手动调用）
  或 assistant_id（LLM 调用），工具的 called_by=函数 id
- `parent_id` 是**对话顺序**：第二条消息的 parent_id 指向第一条，第三条指向第二条

由于 `_msg_adapter.py` 把 `called_by` 直接赋给 `parent_id`，两者目前取值重合，这个赋值
也带来一处遍历缺口：同一个会话里两个 ROOT-parented 的 user 节点，它们的 `parent_id`
都是空（ROOT 不是有效的消息 id），`linear_history` 沿 parent_id 走会提前中断。

## 二、两种数据结构

DAG 和聊天 UI 需要不同的数据格式，两者都需要：

| | DAG 原始节点 | 聊天 UI 消息 |
|---|---|---|
| 用途 | 运行时（render_context 构建上下文） | 前端显示（消息列表、工具调用卡片） |
| 工具调用 | 每个工具一个独立节点 | 折叠进 assistant 消息的 tool_calls[] |
| thinking | 在 extra 字段里 | 提取到 blocks[] |
| 格式 | `{role, name, input, output, called_by, seq}` | `{role, content, tool_calls, blocks, parent_id}` |
| 构建时机 | 写入时 | 加载时（aggregate_tool_messages） |

`aggregate_tool_messages` 就是把 DAG 格式转成 UI 格式的。

## 三、渐进式统一

`parent_id` 在 188 处被引用，深入 dispatcher、分支管理、sub_agent 等核心模块。一次性
全量替换会把所有消息加载置于同一次改动的风险之下。因此设计上逐层把关键路径切到
`called_by`，并保留 `parent_id` 作 fallback——`called_by` 缺失时的行为与切换前完全一致：

1. **聚合层**（persistence.py）：优先 `called_by`，`parent_id` 作 fallback
2. **渲染层**（session.py `_rebuild_runtime_cards`）：用 `called_by`，函数后代关系按调用
   层级判断，不会误 drop user 节点
3. **加载层**（session.py `handle_load_session`）：走 linear_history，不完整时 fallback
   到 get_branch
4. **`_msg_adapter.py`**：继续复制 called_by → parent_id，保持向后兼容
5. **`linear_history`**：保持用 parent_id，由加载层的 fallback 兜底

## 四、目标状态

最终状态下 `parent_id` 按对话顺序设置，而不是复制 `called_by`，对话链因此天然正确：

| 步骤 | 做什么 | 前提 |
|---|---|---|
| A | `_msg_adapter.py` 的 parent_id 按对话 seq 顺序设（而非复制 called_by） | 当前各层稳定 |
| B | `linear_history` 改用 parent_id 遍历（步骤 A 后天然正确） | 步骤 A |
| C | 移除 handle_load_session 的 get_branch fallback，不再需要 | 步骤 B |
| D | 标记 parent_id 为 deprecated，长期只用 called_by | 步骤 A-C 全部稳定 |

步骤 A-D 影响所有消息加载，上线前需要 feature flag 和充分测试。

## 实现状态

部分实施。渐进式统一的第 1-3 层已落地：聚合层优先 `called_by`，
`_rebuild_runtime_cards` 使用 `called_by`，`handle_load_session` 带 get_branch
fallback。第 4-5 层维持上文所述现状，目标状态（步骤 A-D）尚未开始。
