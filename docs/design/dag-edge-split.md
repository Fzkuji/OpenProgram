# DAG 数据模型重设计: 拆分 conv_pred 与 caller 两条边

> **更新**: 这份文档讲两条**父指针**（sequence / call）的 schema 拆分。
> 完整的节点 / 边语义模型（3 类节点 + 3 类边，含 reference edge，以及
> attach / merge / spawn 节点的位置规则）参见
> `docs/design/dag-node-model.md`。本文是它在数据层的具体落地。

## 背景

当前 `nodes.predecessor` 一个字段同时承担两种完全不同的"父子关系":

- user retry → assistant: 对话链上的接续(同 tier, 是真正的分支维度)
- assistant → 它的 tool call: assistant 内部的子调用(tier+1, 嵌套, 不是分支)

读取时无法干净区分。例如 session `local_28f0a92239`:

```
be71e8b1_reply 处 retry 出 3 个分支, 但 db.list_branches() 返回 31 条 ——
每个 FunctionCall 都被当作 leaf, tool 子调用混入了"分支"概念。
```

之前的 workaround 是在 data_json 里塞 `metadata.called_by`, 读取时启发式 fallback,
但写入路径还是把 predecessor 指向 assistant, 两个信息源不一致, 治标不治本。

## 核心思想

把"对话边"和"调用边"在 schema 层就拆开, 每个节点**只用其中一条父指针**:

- **`conv_pred`** —— 对话边。"我是上一回合之后的下一回合。"
- **`caller`** —— 调用边。"我是某个 assistant 内部产生的子调用。"

约束: `conv_pred` 和 `caller` 互斥, 一个节点至多有一条非空。

## 节点 / 边 对照表

| 节点 | 边 | 说明 |
|---|---|---|
| 首条 user | (无) | 会话根 |
| assistant reply | `conv_pred = <user>` | 正常回应 |
| user retry | `conv_pred = <assistant>` | 同一个 assistant 派生多条 user → 真正的分支 |
| tool call / FunctionCall | `caller = <assistant>` | 内部子调用, **不**在对话链上 |
| sub-LLM (tool 内部又调模型) | `caller = <tool>` | 再深一层 |

## 三个 layout 字段直接由节点自身的边推导

`_tier`, `_lane`, `_depth` 全部由"我的两个父指针 + 父亲的字段"决定, 不需要全图遍历也不需要 `role` 字符串判断。

### `_tier` (调用栈层级)

```
if caller is not None:
    _tier = caller._tier + 1
elif conv_pred is not None:
    _tier = conv_pred._tier
else:
    _tier = 0
```

走 `caller` 边就深一层, 走 `conv_pred` 边就同层。

### `_lane` (列, 决定分支并排)

```
if caller is not None:
    _lane = caller._lane                     # 子调用挤在 caller 同一列
elif conv_pred is not None:
    siblings = [n for n in nodes if n.conv_pred == conv_pred.id]
    sibling_index = siblings.index(self)     # 按 created_at 排
    if sibling_index == 0:
        _lane = conv_pred._lane              # 主线继续, 同列
    else:
        _lane = allocate_new_lane()          # retry, 单开一列
else:
    _lane = 0
```

retry 分支的判定干净: "我是不是某个 assistant 的非首个 conv_pred 子"
—— 是就开新列, 不是就继承。

### `_depth` (行, 决定纵向位置)

```
if conv_pred is not None:
    _depth = conv_pred._depth + 1
elif caller is not None:
    # 我是 caller 的第 k 个子调用 (按 created_at), 排在它下方第 k 行
    sub_calls = [n for n in nodes if n.caller == caller.id]
    k = sub_calls.index(self)
    _depth = caller._depth + 1 + k
else:
    _depth = 0
```

`caller` 子在自己 caller 的下方垂直堆叠, 互相不挤。
**caller 的下一个 conv_pred 子**(下一个 user 接续)的 depth 用 caller 自己的
`_depth + 1`, 不被它的子调用们顶下去 —— 因为 conv_pred 链和 caller 链分开计算。

这条解决了"一长串 tool 在 trunk 下面"的问题: trunk 的下一个 user 用的是
assistant 的 `_depth + 1`, tool 子调用各自堆在 assistant 旁边, 互不影响。

## 分支 (branch tip) 定义

```sql
-- branch tip = 对话节点 + 没有 conv_pred 后继
SELECT id FROM nodes n
WHERE n.caller IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM nodes c
    WHERE c.conv_pred = n.id
  )
```

`caller IS NULL` 自动排除 tool / FunctionCall, 它们物理上不可能成为 branch tip。
SQL 干净, 没有 fallback 启发式。

## Schema 改动

```sql
ALTER TABLE nodes ADD COLUMN conv_pred TEXT;
ALTER TABLE nodes ADD COLUMN caller    TEXT;
CREATE INDEX idx_nodes_conv_pred ON nodes(conv_pred);
CREATE INDEX idx_nodes_caller    ON nodes(caller);
-- 旧 predecessor 字段保留作冗余, 稳定一段时间后再删
```

## 写入侧拆分

| 调用点 | 写入字段 |
|---|---|
| `dispatcher.process_user_turn` 写 user 节点 | `conv_pred = <prev_assistant_id or NULL>` |
| `dispatcher` 写 assistant reply | `conv_pred = <user_id>` |
| `@agentic_function` 写 FunctionCall | `caller = <owning_assistant_id>` |
| `Runtime.exec` 写 ModelCall (tool 内部) | `caller = <tool_id>` |

写的时候**只写其中一条**, 另一条 NULL。
彻底废弃 `metadata.called_by` 这种隐藏字段。

## 读取侧改动

- `list_branches`: 上面那条 SQL, 替换现在的实现。
- `_graph_layout.annotate_graph`: 重写为按上面公式直接读节点字段。
  代码会更短(现在 ~250 行, 重写后估计 ~80 行)。
- `aggregate_tool_messages`: **删掉**。前端不需要预先把 tool 折进 assistant ——
  后端 graph 里 tool 节点天然带 `caller`, 前端按 `caller` 关系渲染成嵌套 /
  折叠形式即可。
- `handle_load_session` 和 `build_branches_payload` 自然同形, 不再有
  两条路径不一致的问题。

## 迁移现有数据

一次性脚本:

```python
for row in db.execute("SELECT id, predecessor, data_json FROM nodes"):
    data = json.loads(row.data_json)
    mc = data.get("metadata", {}).get("called_by") or data.get("called_by")
    if mc:
        # 子调用节点
        db.execute("UPDATE nodes SET caller=?, conv_pred=NULL WHERE id=?",
                   (mc, row.id))
    else:
        # 对话节点
        db.execute("UPDATE nodes SET conv_pred=?, caller=NULL WHERE id=?",
                   (row.predecessor, row.id))
```

本地 `~/.agentic/dag_sessions.sqlite` 跑一次。

## 改动范围

```
新加:
  scripts/migrate_split_edges.py

修改:
  openprogram/context/session_db.py
    - schema 加 conv_pred / caller 字段 + 索引
    - append_node 按节点类型决定写哪条边
    - list_branches 用新 SQL
    - get_messages / load_graph 同步返回新字段
  openprogram/webui/_graph_layout.py
    - 重写 annotate_graph, 按公式直接计算
  openprogram/agent/dispatcher.py
    - 写 user / assistant 用 conv_pred
  openprogram/agentic_programming/function.py
    - 写 FunctionCall 用 caller
  openprogram/agentic_programming/runtime.py (或 context/runtime.py)
    - 写 ModelCall (子 LLM 调用) 用 caller

删除:
  openprogram/webui/persistence.py::aggregate_tool_messages 调用点
  _graph_layout.py 里 role == "tool" 的特判
  data_json.metadata.called_by 写入

前端无变化 —— history-graph.ts 已经在用 _depth/_lane/_tier。
```

## 验收

- session `local_28f0a92239`: `list_branches` 返回 3 条
  (`d476f6c6_reply`, `9d4c98a8_reply`, `95b0d8f8_reply`), 不是 31, 不是 1。
- DAG 渲染: trunk 下面没有 tool 长尾, tool 全部挤在自己 caller 旁边。
- WebSocket `load_session` 和 `list_branches` 给的 graph 形状一致, 不会切换。
