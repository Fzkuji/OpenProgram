# 分支协作（通信 · 服务 · 合并）

> DAG 里的分支不只是"平行世界"，它们能**互相协作**——一个分支给另一个分支发消
> 息、一个分支为另一个分支干活、两个分支的成果合并成一条。本文定义三种协作模式，
> 以及它们的节点与图的关系。
>
> 前置：边模型（caller + predecessor）见 `dag/overview.md`；布局与连线的权威规范见
> `rendering.md`。

## 一、什么是分支，协作指什么

**分支** = 一个 `(session_id, head_id)` 对——同会话与跨会话共用这一套抽象。分叉本
身不是特殊操作：checkout 移 HEAD，下一轮 user 自然成为 sibling。

分支之间可以做三件事：

| 模式 | 含义 |
|---|---|
| **通信** | 分支 A 往分支 B 投递一条消息，可选等待 B 的回复 |
| **服务** | 分支 A 派子分支 B 去做一件事，B 的成果回流进 A |
| **合并** | 两条或更多分支汇聚成一条延续 |

合并与嵌入共用一套引擎：`merge_branches` 写 N 个 attach pointer 加一个 merge
assistant 节点，`commit_parents = [target prior, *peers]`（多父）。它建立在 attach
这个嵌入原语之上——一个分支的内容作为 attach pointer 进入另一个点，展开成
`[Attached from "label"]` 块。

连线视觉规则在 `rendering.md` 第三节（颜色=分支、线型=类型；线型表；通信线默认
隐藏）。本文不维护副本。

## 二、三种协作模式

### 模式 1：分支间发消息（通信）

**场景**：分支 A 的 LLM 想问分支 B 的 LLM 一句话，或把一条信息推给 B。

**机制**：agentic 工具 `send_to_branch`：

```
send_to_branch(target_branch, message) -> 对方的回复（可选等待）
```

- `target_branch`：目标分支的 head_id（或分支名）
- `message`：要发的内容
- 行为：在目标分支末尾追加一个 user 节点（`source="from_branch"`，标注来源分支），
  目标分支的 LLM 下一轮看到它并回复；可选同步等待对方回复返回给调用方。

**DAG 画法**：从发起分支的 LLM 节点画一条**通信线**（点线 `1 5`，区别于其它线型）
指向目标分支新加的 user 节点。线的颜色用**目标分支的 lane 色**（颜色永远=分支，类型
只靠线型）。这条线是"通信边"，不是 caller/predecessor 结构边——只用于渲染，不进
lane/depth 计算。

> 数据上：目标分支的新 user 节点 predecessor = 目标分支 tip（正常对话链），额外
> 带 `metadata.from_branch = 发起分支的节点 id`，渲染层据此画通信虚线。

### 模式 2：子分支为主分支服务（派活 → 回流）

**场景**：主分支 A 派一个子分支 B 去做一件事（查资料/跑工具），B 干完把结果交回 A。

**机制**：这是 `/task` 子 agent 的"分支版"，建立在 spawn + attach 之上：
1. A 的 LLM 调 `spawn_branch(task)` → 创建子分支 B（fork 一个新 lane），B 独立跑
2. B 跑完，其 tip 通过 **attach** 嵌回 A：attach pointer 指向 B 的 tip，展开成
   `[Attached from "B"]` 块进 A 的上下文
3. A 的 LLM 下一轮看到 B 的成果，继续

**DAG 画法**：spawn edge（点划线，task 节点 → 子分支根）加 attach_ref 虚线（子分支
tip → attach 节点）表达这一过程。子分支 B 是独立 lane（按布局规则从 A 的 lane 最右
列+1 起，有自己的竖线）。

### 模式 3：分支合并（汇聚）

**场景**：两条分支各自聊出了成果，合并成一条。这一场景决定合并节点怎么画。

**两种合并**（MergeModal 都提供）：
- **equal merge（平等合并）**：N 条分支平等，合并产出一个**新的 merge 节点**作为新
  tip。merge 节点有 N 个父（汇聚）。
- **attach-into-★（就地合并）**：选一条 base 分支，其余分支 attach 进 base，base
  继续往下，不产生独立 merge 节点（就是模式 2 的多 peer 版）。

**合并节点的数据模型**：
- merge 是一个 `role=assistant` 节点（LLM 综合各分支产出的回复）
- 它的 `predecessor` = base 分支的 tip（主对话链父）
- 额外的"被合并进来的分支"通过 **attach pointer 节点**表达：每个 peer 一个 attach
  pointer（`predecessor=target_head`，`attach.head_id=peer tip`）
- `commit_parents = [target prior commit, *peer commit ids]`（多父，溯源用）

**DAG 画法**：以 `rendering.md` 场景 10 为权威。merge 节点形状是**双环 ◎**，
全图唯一的汇聚形状；它落在 base 分支 lane——合并后的主线延续 base。attach pointer
节点本身在 viewport 不画，只画汇入线。

## 三、发消息工具

```python
@function(name="send_to_branch")
def send_to_branch(target_branch: str, message: str, wait_reply: bool = False) -> str:
    """给另一个分支发一条消息。
    target_branch: 目标分支 head_id 或分支名
    message: 内容
    wait_reply: True 则同步等目标分支 LLM 回复并返回；False 只投递
    """
```

设计要点：
- 目标分支末尾追加 user 节点：`predecessor=目标分支tip`，`source="from_branch"`，
  `metadata.from_branch=调用方节点id`
- `wait_reply=True`：触发目标分支一个 turn，等 assistant 回复，返回其文本
- 安全：发消息是副作用（往别的分支写），值守模式下可被策略层拦（接事件层
  `tool.before`，见 proactive 设计）
- DAG：渲染层读 `metadata.from_branch` 画跨分支通信虚线（专属线型，区别 attach/spawn）

## 四、待定问题

1. **send_to_branch 是否同步等回复**：默认投递（异步）还是等回复（同步）？倾向参数化。
2. **通信 vs 合并的边界**：send_to_branch 投递一条消息 vs merge 汇聚整条分支——
   是否需要"send 多次后再 merge"的组合工作流？
3. **值守拦截**：分支间发消息、自动合并要不要默认需用户确认（副作用跨分支）？

## 五、相关代码

| 事 | 位置 |
|---|---|
| 合并引擎 | `openprogram/agent/internals/_merge.py` `process_merge_turn` |
| 合并 WS action | `openprogram/webui/ws_actions/merge.py` |
| 合并 UI | `apps/web/components/right-sidebar/branches/merge-modal.tsx` |
| attach 解析 | `openprogram/webui/ws_actions/branch.py` `_attach_info` |
| DAG 连线 | `apps/web/lib/runtime-bridge/dag/render/edges.ts` |
| DAG 形状 | `apps/web/lib/runtime-bridge/dag/render/shapes.ts` |
| 布局（merge 节点 lane） | `openprogram/webui/graph_layout/{lane,__init__}.py` |
| send_to_branch 工具 | 待在 `openprogram/programs/tools/` 下新建 |
| 验证 | `scripts/dag_dump.py` |

## 附录：实现状态

| 能力 | 状态 | 位置 |
|---|---|---|
| fork（分叉） | 已实现 | `message-actions.tsx` branch() / checkout |
| 分支抽象（`(session_id, head_id)`） | 已实现 | `ws_actions/merge.py` |
| 合并后端（attach pointer + merge 节点 + 多父 commit） | 已实现 | `ws_actions/merge.py` + `agent/_merge.py` |
| 合并 UI（equal merge vs attach-into-★，含合并指令） | 已实现 | `merge-modal.tsx` |
| attach（嵌入） | 已实现 | `_merge.py` + `branch.py` `_attach_info` + generator |
| attach 连线（attach_ref 虚线，源 tip → attach 节点） | 已实现 | `dag/render/edges.ts` |
| worktree merge（另一套：git worktree ff-only 合并文件） | 已实现 | `worktree-item.tsx` |
| 合并节点画法（形状、lane、汇入线） | 规范见 `rendering.md` 场景 10 | `dag/render/shapes.ts`、`dag/render/edges.ts` |
| 分支间发消息（`send_to_branch`） | 尚未实现 | 待新增 |
| 子分支服务链路（spawn_branch → attach 回流） | 部分：/task 子 agent 已有，回流仍需串通 | 复用 merge |
