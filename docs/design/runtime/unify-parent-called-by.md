# 统一 parent_id 与 called_by — 设计方案

> 状态: **规划中**
> 代码: `store/session/_msg_adapter.py`、`webui/persistence.py`、`contextgit/dag.py`、`webui/ws_actions/session.py`

## 1. 问题

DAG 节点有两个"父指针"字段，含义不同，在不同地方被读写，导致遍历结果不一致。

| 字段 | 含义 | 谁写 | 谁读 |
|---|---|---|---|
| `called_by` | 调用关系（谁调用了我） | DAG store（`context/nodes.py` 的 `Call` 对象） | `render_context`、`get_branch`、`_rebuild_runtime_cards` |
| `parent_id` | 对话链（我的上一条消息是谁） | `_msg_adapter.py`（从 `called_by` 复制） | `linear_history`、`aggregate_tool_messages`、`_annotate_spawn_origin` |

**根本问题**：`_msg_adapter.py` 把 `called_by` 直接赋给 `parent_id`（行 132/169/188/206），但两者的语义不同：

- `called_by` 是**调用层级**：user 的 called_by=ROOT，函数的 called_by=ROOT（手动调用）或 assistant_id（LLM 调用），工具的 called_by=函数 id
- `parent_id` 应该是**对话顺序**：第二条消息的 parent_id 应指向第一条，第三条指向第二条

直接赋值导致：同一个会话里两个 ROOT-parented 的 user 节点，它们的 `parent_id` 都是空（ROOT 不是有效的消息 id），`linear_history` 沿 parent_id 走就断了。

## 2. 为什么不能简单替换

之前尝试直接用 `get_branch`（走 called_by）替换 `linear_history`（走 parent_id），导致消息丢失。原因：

| | `aggregate_tool_messages` + `linear_history` | `get_branch` |
|---|---|---|
| 数据来源 | `get_messages()`（所有节点） | DAG index（`nodes_by_id`） |
| 工具聚合 | 有——`role=tool` 折叠进 `assistant` 的 `tool_calls[]` | 无——每个节点独立返回 |
| thinking blocks | 有——从 `extra` 字段提取 `blocks` | 无 |
| 消息格式 | 完整的聊天 UI 格式（tool_calls、blocks 等） | 简单的节点转消息格式 |
| 子节点处理 | 工具调用折叠进父 assistant | expose=io 隐藏内部 |

`get_branch` 返回的是"骨架"（每个节点一条消息），前端需要的是"聚合后的 UI 消息"（assistant + tool_calls[] + blocks）。直接替换就丢了聚合信息。

## 3. 正确的统一方案

**方案：修正 `_msg_adapter.py` 的 parent_id 赋值，让 parent_id 反映真实的对话顺序，而不是简单复制 called_by。**

### 3.1 核心改动

在 `_msg_adapter.py` 的 `_node_to_msg()` 中，`parent_id` 不再从 `called_by` 复制，而是从 DAG 的 seq 顺序推断：

```python
# 当前（错误）：
"parent_id": node.called_by,

# 改为：
"parent_id": _resolve_conv_parent(node, index),
```

`_resolve_conv_parent` 的逻辑：
1. 如果节点有显式的 `conv_predecessor`（分支场景），用它
2. 否则找 seq 比自己小的、在同一对话链上的最近一个节点
3. 对话链 = 同一个 ROOT 下的顶层节点序列

### 3.2 影响范围

| 消费方 | 影响 |
|---|---|
| `linear_history` | parent_id 正确了，遍历结果正确，不再需要 fallback |
| `aggregate_tool_messages` | 用 parent_id 找 assistant 父节点，parent_id 正确后折叠正确 |
| `get_branch` | 不受影响（用 called_by，不用 parent_id） |
| `_rebuild_runtime_cards` | 已改用 called_by（`476aa8f6`），不受影响 |
| `_annotate_spawn_origin` | 用 parent_id 遍历，parent_id 正确后自然正确 |

### 3.3 替代方案（不推荐）

**方案 B：让 get_branch 也做工具聚合**
- 改动大：get_branch 在 store 层，聚合逻辑在 webui 层，跨层耦合
- 风险：get_branch 还在 render_context 等其他地方用，加聚合会影响它们

**方案 C：在 handle_load_session 中后处理**
- 在 get_branch 返回后补做聚合
- 可行但治标不治本——parent_id 还是错的，其他用 parent_id 的地方还会出问题

## 4. 实施计划

| 步骤 | 做什么 | 风险 | 回滚 |
|---|---|---|---|
| 1 | 在 `SessionStore` 或 `MemoryIndex` 中实现 `conv_predecessor` 的计算 | 低 | 删新代码 |
| 2 | 修改 `_msg_adapter.py`：`parent_id = _resolve_conv_parent(node, index)` 代替 `parent_id = node.called_by` | 中——影响所有消息加载 | 改回 `node.called_by` |
| 3 | 移除 `handle_load_session` 中的 `get_branch` fallback（不需要了） | 低 | 恢复 fallback |
| 4 | 验证：刷新含函数调用+对话的会话，消息完整显示 | — | — |
| 5 | 可选：标记 `linear_history` 为 deprecated，长期迁移到统一遍历 | 低 | — |

## 5. 风险和回滚

**主要风险**：步骤 2 修改了所有消息的 parent_id 计算，如果 `_resolve_conv_parent` 有 bug，所有会话的消息加载都会受影响。

**回滚策略**：
- `_resolve_conv_parent` 失败时 fallback 到 `node.called_by`（当前行为）
- 步骤 2 可以通过 feature flag 控制：`USE_CONV_PARENT_ID = True/False`
- 最坏情况：`git revert` 步骤 2 的 commit

**测试验证**：
- 纯对话会话（无函数调用）
- 函数调用 + 后续对话（当前失败场景）
- 多次函数调用
- 函数调用 + 分支/fork
- 分支隔离（A 分支不看到 B 分支）
