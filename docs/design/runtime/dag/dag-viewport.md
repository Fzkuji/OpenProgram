# DAG Viewport 设计文档

Status: **draft** · Created: 2026-06-28

> 右侧面板的 DAG 小地图。显示当前会话的节点结构，支持分支切换、折叠、滚动同步。

## 一、整体架构

### 现状问题

graph 数据构建在 `session.py` 和 `branch.py` 各写了一遍，改一处漏一处。前端 `pipeline.ts` 800 行，layout + edge + node + badge + visibility 全混在一起。改连线逻辑会影响折叠，改折叠会影响 visibility。

### 目标架构

```
后端                                 前端
────                                 ────

graph_builder.py (新)                dag/
  build_session_graph(sid)             index.ts        → 对外接口
    → {nodes, branches, head}          pipeline.ts     → 调度：passes → layout → render
                                       types.ts        → GNode 接口 + 常量

graph_layout/ (不动)                 dag/passes/       → 数据变换（不画任何东西）
  __init__.py → annotate_graph()       merge-runs.ts
  tier.py                             collapse-runtime-pairs.ts
  depth.py                            demote-decoration-cards.ts
  lane.py                             apply-collapse.ts
  topology.py
  _common.py                        dag/layout/        → 前端侧树构建（不画任何东西）
                                       build-tree.ts
ws_actions/                            assign-lanes.ts
  session.py  → 调 build_session_graph  depth.ts
  branch.py   → 调 build_session_graph
                                     dag/render/        → 画 SVG（只管画，不算布局）
                                       edges.ts         → 主干连线 + fork 连线
                                       nodes.ts         → 节点形状 + 状态 class
                                       badges.ts        → 分支 badge 按钮
                                       visibility.ts    → 白色填充同步

                                     dag/store/
                                       globals.ts       → 模块级状态
```

## 二、后端接口

### `graph_builder.build_session_graph(session_id) → dict`

唯一的 graph 数据构建入口。返回：

```python
{
    "graph": [           # GNode 数组，每个节点包含：
        {
            "id": str,
            "called_by": str,   # 对话链前驱（conv predecessor）
            "caller": str,      # 子调用父节点（谁调用了我）
            "role": str,        # user / assistant / tool
            "display": str,     # root / runtime / None
            "function": str,    # 函数名（tool 节点）
            "preview": str,     # 内容预览
            "_tier": int,       # 水平列位置（graph_layout 计算）
            "_depth": int,      # 垂直行位置
            "_lane": int,       # 分支列
            ...                 # attach 相关字段
        },
    ],
    "branches": [        # 分支 tip 列表
        {
            "head_msg_id": str,
            "name": str | None,
            "active": bool,
            "created_at": float,
        },
    ],
    "head": str | None,  # 当前 HEAD 节点 id
}
```

### 调用方

| 调用方 | 场景 |
|---|---|
| `session.py:handle_load_session` | 加载会话时，graph 放入 `session_loaded` WS 消息 |
| `branch.py:build_branches_payload` | 分支面板刷新、实时 poller |

两处都调 `build_session_graph`，不再各自构建。

### `called_by` 和 `caller` 的区别

| 字段 | 含义 | 举例 | 用途 |
|---|---|---|---|
| `called_by` | 对话链前驱 | user2.called_by = llm1（上一轮回复） | 构建 tree 父子关系、depth/lane、edge 绘制 |
| `caller` | 子调用父节点 | tool.caller = llm（模型调了这个工具） | tier 计算、internal 判定、collapse |

规则：**凡是判断「谁调用了谁」用 `caller`。凡是判断「对话顺序」用 `called_by`。**

## 三、前端接口

### `render(graph: GNode[], headId: string): void`

唯一的渲染入口。步骤：

```
1. passes：merge-runs → collapse-runtime-pairs → demote-decoration-cards → apply-collapse
2. layout：buildTree → assignDepth → assignLanes
3. 计算状态：headAncestors（on-head）、internalSet（子调用）
4. 画 SVG：
   a. edges.drawEdges(tree, pos, colors)          → 所有连线
   b. nodes.drawNodes(tree, pos, colors, states)   → 所有节点形状
   c. badges.drawBadges(branches, tree, pos)       → 分支 badge 按钮
5. 绑定事件：scroll sync、mutation observer、panel resize
6. 触发 visibility.recompute()
```

### 各 render 子模块的接口

#### `edges.drawEdges(edgeG, tree, pos, rootPos, forkRoots, colors)`

输入：SVG group、tree 数据、pos 函数、ROOT 位置、fork root 列表、颜色表
输出：往 edgeG 里添加 SVG line/path 元素

连线规则：
- 主干 user 节点从 ROOT 列画边
- fork 分支 user 节点从 fork 虚拟主干画边
- llm/tool 节点从 parent 画边
- fork siblings 之间画虚线动画

#### `nodes.drawNodes(nodeG, tree, pos, colors, states)`

输入：SVG group、tree 数据、pos 函数、颜色表、{headAncestors, internalSet, collapsed, collapsible}
输出：往 nodeG 里添加 SVG g.history-node 元素

#### `badges.drawBadges(svg, branches, tree, pos, colors, sessionId)`

输入：SVG 根元素、分支列表、tree 数据、pos 函数、颜色表、session id
输出：在分支 tip 节点下方添加可点击的 badge

#### `visibility.recompute()`

扫描 `#chatArea` 可见区域，标记对应 DAG 节点白色填充。

触发条件：
- 聊天滚动（scroll listener）
- 聊天 DOM 变化（MutationObserver）
- render 完成后

MutationObserver 绑定规则：每次 render 时**重新绑定**（不用 flag 阻止），因为 `#chatMessages` DOM 可能在 load_session 时被替换。

## 四、节点状态

### on-head / off-head

从 headId 沿 `called_by` 回溯到 ROOT，链上所有节点 on-head。其余 off-head。

视觉：off-head 节点加 CSS class `off-head`，颜色暗淡。

### visible（白色填充）

只依赖聊天视口扫描结果。不做 parent walk-up 传播（之前的 walk-up 会跨分支传播，制造错误的白色填充）。

简化后的 `recompute()`：
1. 扫描 `#chatArea` 里可见的聊天气泡 `data-msg-id`
2. 这些 id → 白色填充
3. 其余 → 透明
4. 不走 `_parentOf` walk-up，不走 `_internalOwner` 传播

### internal

`caller` 字段（不是 `called_by`）指向非 ROOT 父节点的节点。用于 collapse 计算（折叠只收 internal children）。

**不用于 visibility 传播**（删除 `_internalOwner` 对 visibility 的影响）。

## 五、布局

### tier（水平列）— tier.py

按 role 固定：ROOT=0, user=1, llm=2, tool=3, 更深子调用=caller.tier+1。

### depth（垂直行）— depth.py

DFS 序。fork siblings 跳过 DFS，对齐首个 sibling 的 depth。

### lane（分支列）— lane.py

主干 lane=0。fork 非首个 siblings 分配新 lane。lane 之间留 1 列间距给 fork 虚拟主干线。

### pos 函数

```typescript
function pos(n: GNode): {x: number, y: number} {
    x = PAD_X + (laneToCol[n._lane] + n._tier) * COL_W
    y = PAD_Y + depthToRow[n._depth] * ROW_H
}
```

## 六、连线

### 主干

```
◇ ROOT (tier=0)
│                    ← 垂直主干在 tier=0 列
├── ○ user1 (tier=1) ← 水平分支 tier=0 → tier=1
│   └── △ llm1 (tier=2)
│
├── ○ user2 (tier=1) ← 水平分支 tier=0 → tier=1（回到 user 列，不串链）
│   └── △ llm2 (tier=2)
│       └── ■ tool (tier=3)
```

主干 user 节点从 ROOT 列（tier=0）画边，不从 `called_by`（上一轮 llm）画。

### fork 分支

```
主干                    fork
├── ○ user2  ┈┈┈┈┈┈  │── ○ user1'
│   └── △ llm2        │   └── △ llm1'
│                      │
│                      ├── ○ user2'
│                      │   └── △ llm2'
```

1. 虚线桥：主干 sibling → fork 虚拟主干列
2. fork 虚拟主干实线：垂直线从 fork root 到最后一个 user
3. fork 内部 user 从虚拟主干横向分出

### 折叠

折叠一个节点只隐藏其 `caller` 子调用，不隐藏 `called_by` 对话链后续 turn。

## 七、分支 badge

每个分支 tip 节点下方显示 badge。样式跟 HEAD 标签一致（`--bg-hover` 背景、圆角、无边框）。

- 活跃分支：文字亮色
- 非活跃分支：文字暗色，点击触发 `checkout_branch` + `load_session`

## 八、重构步骤

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | 后端：抽 `graph_builder.py`，session.py 和 branch.py 都调它 | graph 数据一致 |
| 2 | 前端：从 pipeline.ts 抽出 edges.ts（drawEdges 函数） | 连线不变 |
| 3 | 前端：从 pipeline.ts 抽出 nodes.ts（drawNodes 函数） | 节点不变 |
| 4 | 前端：从 pipeline.ts 抽出 badges.ts（drawBadges 函数） | badge 不变 |
| 5 | 前端：visibility.ts 简化（删 parent walk-up + internal 传播，每次 render 重绑 observer） | visibility 只跟聊天视口 |
| 6 | 前端：pipeline.ts 变成纯调度（调 passes → layout → drawEdges → drawNodes → drawBadges → recompute） | 功能不变，文件 <200 行 |

每步独立可验证，跑 `npm run build` + 浏览器确认画面不变。
