# 分支协作设计文档（通信 · 服务 · 合并）

Status: **draft（待讨论，用户睡前授权持续推进）** · Created: 2026-06-29

> 目标：让 DAG 里的不同分支不只是"平行世界"，而能**互相协作**——一个分支给另
> 一个分支发消息、一个分支为另一个分支干活、两个分支的成果合并成一条。本文盘
> 清现状（很多已实现），补齐缺口，并定义合并/通信节点在 DAG viewport 里的画法。
>
> 前置：边模型（caller + predecessor）见 `session-dag.md`；布局规则见
> `dag-layout-algorithm.md`（含 7 场景 spec.html）。

## 一、现状盘点（codegraph 实测）

分支协作的**数据层和后端大部分已存在**，缺的主要是 ①分支间主动发消息的工具
②合并/attach 节点在 DAG viewport 的画法。

| 能力 | 现状 | 位置 |
|---|---|---|
| fork（分叉） | ✅ 已有。checkout 移 HEAD，下一轮 user 自然成 sibling | `message-actions.tsx` branch() / checkout |
| 分支抽象 | ✅ "分支" = `(session_id, head_id)` 对，同会话/跨会话统一 | `ws_actions/merge.py` |
| **合并（merge）** | ✅ 后端已实现。`merge_branches` action → `process_merge_turn`，写 N 个 attach pointer + 一个 merge assistant 节点，`commit_parents=[target prior, *peers]`（多父） | `ws_actions/merge.py` + `agent/_merge.py` |
| 合并 UI | ✅ MergeModal：equal merge（出新 tip）vs attach-into-★（就地）+ 合并指令 | `merge-modal.tsx` |
| **attach（嵌入）** | ✅ 一个分支的内容作为 attach pointer 嵌入另一个点；展开成 `[Attached from "label"]` 块 | `_merge.py` + `branch.py` `_attach_info` + generator |
| attach 连线 | ✅ DAG 已画 attach_ref 虚线（源 tip → attach 节点） | `dag/render/edges.ts` |
| worktree merge | ✅ 另一套（git worktree ff-only 合并文件） | `worktree-item.tsx` |
| **分支间发消息** | ❌ **缺**。没有"分支 A 的 LLM 主动给分支 B 发一条消息"的工具 | 待新增 |
| **合并节点 DAG 画法** | ⚠️ 数据有（多父），但 viewport 把多父汇聚画成什么样未定义 | 本文定义 |
| **子分支服务模式** | ◐ 部分。/task 子 agent 有，但"子分支干完把结果交回主分支"的合并回流要串起来 | 复用 merge |

**结论**：合并的"引擎"已经造好了（merge_branches + attach + 多父 commit）。本文
主要做三件事：(A) 定义合并/attach 节点的 DAG 画法；(B) 新增分支间发消息工具；
(C) 把"子分支服务 → 结果合并回流"串成完整链路。

## 二、三种协作模式

### 模式 1：分支间发消息（通信）

**场景**：分支 A 的 LLM 想问分支 B 的 LLM 一句话，或把一条信息推给 B。

**机制**：新增一个 agentic 工具 `send_to_branch`：

```
send_to_branch(target_branch, message) -> 对方的回复（可选等待）
```

- `target_branch`：目标分支的 head_id（或分支名）
- `message`：要发的内容
- 行为：在目标分支末尾追加一个 user 节点（`source="from_branch"`，标注来源分支），
  目标分支的 LLM 下一轮看到它并回复；可选同步等待对方回复返回给调用方。

**DAG 画法**：从发起分支的 LLM 节点，画一条**跨分支虚线**（区别于 attach 的虚线，
用不同颜色/线型，如点划线）指向目标分支新加的 user 节点。这条线是"通信边"，
不是 caller/predecessor 结构边——只用于渲染，不进 lane/depth 计算。

> 数据上：目标分支的新 user 节点 predecessor = 目标分支 tip（正常对话链），额外
> 带 `metadata.from_branch = 发起分支的节点 id`，渲染层据此画通信虚线。

### 模式 2：子分支为主分支服务（派活 → 回流）

**场景**：主分支 A 派一个子分支 B 去做一件事（查资料/跑工具），B 干完把结果交回 A。

**机制**：这是 `/task` 子 agent 的"分支版"，复用现有 spawn + attach：
1. A 的 LLM 调 `spawn_branch(task)` → 创建子分支 B（fork 一个新 lane），B 独立跑
2. B 跑完，其 tip 通过 **attach** 嵌回 A（现有 attach 机制：attach pointer
   指向 B 的 tip，展开成 `[Attached from "B"]` 块进 A 的上下文）
3. A 的 LLM 下一轮看到 B 的成果，继续

**DAG 画法**：现有的 spawn edge（点划线，task 节点 → 子分支根）+ attach_ref 虚线
（子分支 tip → attach 节点）已经能表达。子分支 B 是独立 lane（按布局规则从 A 的
lane 最右列+1 起，有自己的竖线）。

### 模式 3：分支合并（汇聚）

**场景**：两条分支各自聊出了成果，合并成一条（最关键，决定合并节点怎么画）。

**两种合并**（MergeModal 已有）：
- **equal merge（平等合并）**：N 条分支平等，合并产出一个**新的 merge 节点**作为新
  tip。merge 节点有 N 个父（汇聚）。
- **attach-into-★（就地合并）**：选一条 base 分支，其余分支 attach 进 base，base
  继续往下，不产生独立 merge 节点（就是模式 2 的多 peer 版）。

**合并节点的数据模型**（已实现）：
- merge 是一个 `role=assistant` 节点（LLM 综合各分支产出的回复）
- 它的 `predecessor` = base 分支的 tip（主对话链父）
- 额外的"被合并进来的分支"通过 **attach pointer 节点**表达：每个 peer 一个 attach
  pointer（`predecessor=target_head`，`attach.head_id=peer tip`）
- `commit_parents = [target prior commit, *peer commit ids]`（多父，溯源用）

## 三、合并节点的 DAG 画法（本文核心定义）

合并是 DAG 里**唯一出现"多线汇聚到一个节点"**的地方（其它都是树状发散）。画法：

```
合并前（两条分支）:           合并后:
列0    列2(fork竖线) 列3        列0         列3
◇ROOT                         ◇ROOT
│                             │
●─user                        ●─user
   │                             │
   ▲─llm    ┊                    ▲─llm    ┊
            ┊  ●─user'(fork)              ┊  ●─user'
            ┊     │                       ┊     │
            ┊     ▲─llm'                  ┊     ▲─llm'
                                          │        ╲
                                          ●─◆ merge ←──┘ (两线汇入)
                                            (新 tip, 多父)
```

**规则**：
1. **merge 节点形状**：用一个特殊形状区分（建议**双环/实心菱形带横杠**），让它一眼
   能认出是"汇聚点"，不同于普通 assistant 三角。
2. **merge 节点的列（lane）**：
   - equal merge：merge 是新主线 tip，回到 **base 分支的 lane**（通常是被合并的主
     分支 lane，或新开一条"合并后主线"）。倾向：合并到 base 的 lane，让合并后主线
     延续 base。
   - 各被合并的 peer 分支画一条**汇入线**（实线或粗虚线）从 peer 的 tip 斜拉到 merge
     节点（类似 git 的 merge 提交两条父线汇合）。
3. **汇入线**：从每个 peer tip → merge 节点，走"先垂直到 merge 行，再水平/斜入"的
   折线，颜色用 peer 分支的 lane 色（让人看出"这条线来自哪条分支"）。
4. **attach pointer 节点**：merge 用的 attach pointer 是 `display=runtime` 的临时
   节点（`merge_temp=true`），**viewport 里不单独画成节点**（会噪），只用汇入线表达
   "这条分支被合并进来了"。当前 filter.py 已过滤 `display=runtime`。

**待确认**：合并后 merge 节点落在哪条 lane——
- 选项 A：落 base 分支 lane（合并后延续 base 主线，其它分支"汇入"base）— 倾向这个
- 选项 B：新开一条 lane（合并产物自成一条新主线）

## 四、分支间发消息工具（新增，需实现）

```python
@function(name="send_to_branch")
def send_to_branch(target_branch: str, message: str, wait_reply: bool = False) -> str:
    """给另一个分支发一条消息。
    target_branch: 目标分支 head_id 或分支名
    message: 内容
    wait_reply: True 则同步等目标分支 LLM 回复并返回；False 只投递
    """
```

实现要点：
- 目标分支末尾追加 user 节点：`predecessor=目标分支tip`，`source="from_branch"`，
  `metadata.from_branch=调用方节点id`
- `wait_reply=True`：触发目标分支一个 turn，等 assistant 回复，返回其文本
- 安全：发消息是副作用（往别的分支写），值守模式下应可被策略层拦（接事件层
  `tool.before`，见 proactive 设计）
- DAG：渲染层读 `metadata.from_branch` 画跨分支通信虚线（新线型，区别 attach/spawn）

## 五、落地步骤（待用户确认后）

| 步 | 做什么 | 验证 |
|---|---|---|
| 1 | 合并节点 DAG 画法：merge 节点特殊形状 + peer 汇入线（lane 色） | 构造一个 merge 会话，dag_dump + 浏览器看汇聚 |
| 2 | 合并后 merge 节点 lane 归属（选项 A：落 base lane） | 同上 |
| 3 | `send_to_branch` 工具 + from_branch 元数据 | 工具调用后目标分支出现 user 节点 |
| 4 | 通信虚线渲染（读 from_branch） | 浏览器看跨分支虚线 |
| 5 | 子分支服务链路串通（spawn_branch → attach 回流） | A 派 B，B 干完 A 看到结果 |

## 六、待讨论的设计决策

1. **合并节点形状**：双环？实心菱形带横杠？还是别的能一眼认出"汇聚"的形状？
2. **合并后 lane**：落 base 分支 lane（延续主线）还是新开 lane？倾向 base。
3. **send_to_branch 是否同步等回复**：默认投递（异步）还是等回复（同步）？倾向参数化。
4. **跨分支通信线型**：和 attach（虚线）、spawn（点划线）区分，用什么线型/颜色？
5. **通信 vs 合并的边界**：send_to_branch 投递一条消息 vs merge 汇聚整条分支——
   是否需要"send 多次后再 merge"的组合工作流？
6. **值守拦截**：分支间发消息、自动合并要不要默认需用户确认（副作用跨分支）？

## 七、相关代码（落地时碰这些）

| 事 | 位置 |
|---|---|
| 合并引擎 | `openprogram/agent/_merge.py` `process_merge_turn` |
| 合并 WS action | `openprogram/webui/ws_actions/merge.py` |
| 合并 UI | `web/components/right-sidebar/branches/merge-modal.tsx` |
| attach 解析 | `openprogram/webui/ws_actions/branch.py` `_attach_info` |
| DAG 连线（attach_ref/spawn 已有，加 merge 汇入线 + 通信线） | `web/lib/runtime-bridge/dag/render/edges.ts` |
| DAG 形状（加 merge 节点形状） | `web/lib/runtime-bridge/dag/shapes.ts` |
| 布局（merge 节点 lane） | `openprogram/webui/graph_layout/{lane,__init__}.py` |
| 新工具 send_to_branch | `openprogram/functions/tools/` 下新建 |
| 验证 | `tools/dag_dump.py` |
