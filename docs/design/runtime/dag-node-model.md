# DAG 节点 / 边模型(旧 — 数据语义已被取代)

> **重定向**:节点 / 边 / 上下文检索的**权威数据模型**已迁到
> [`execution-graph.md`](execution-graph.md)(一整张图 / 三种节点 / 单一
> called_by 边 / compute_reads)。本文的早期模型(含 3 类边、reference edge、
> conv 边拆分等)**已被推翻**,不要据此实现。
> 保留本文仅因 `graph_layout/`(lane/depth 布局)代码注释仍引用它做布局语义参考。

写给"这条 history record 应该画成什么 / 接到哪里"这个问题(layout 语义层)。
配合 `graph_layout/README.md`（layout pipeline）—— 那份讲"怎么算 lane / depth"。

## 节点 (3 类)

> 节点 = 一次 action。三元组 `(actor, kind, content)`，actor 跟 chat 头像对齐。

| kind | actor | content | 备注 |
|---|---|---|---|
| `user_msg` | 用户 | 用户输入文本 | 包括 slash command 输入 |
| `llm_reply` | LLM | LLM 输出文本 | 一个 turn 最多一个 |
| `function_call` | 被调函数 | 函数产出 / 引用值 | 见下表细分 |

合成桥（如 `[系统消息]` followup user msg）不是合法节点 —— 它是 runner
在 spawn 完成后为了让 LLM 接着 react 而合成的 trigger，**chat 不渲染、
DAG 也不画**。底层 history 文件保留以供回放/审计。

### function_call 三类

按"对 branch 拓扑的影响"分（不是按"是否改 context"那种抽象口径）：

| 子类 | 例子 | 在 DAG 上的位置 |
|---|---|---|
| **branch-creating** | `task` (spawn sub-agent) | task tool 节点本身跟普通 inline tool 一样（call edge 挂 caller reply）；它**产生**的 sub branch 第一个 user_msg 才是新 lane 的起点 |
| **branch-referencing** | `attach`, `merge` | 是**当前 branch 的新节点**，进 sequence；被引用 branch tip 通过 reference edge 指过来 |
| **inline tool** | `bash`, `read`, `list`, ... | 挂支线（call edge），不进 sequence |

视觉上 `task` / `attach` / `merge` **全部用虚框** (`square_outline`)，作为一类"branch 操作"统一表达：
- `task`：派一条新 branch
- `attach`：引用一条 branch
- `merge`：合一条 branch

它们的共同点是 **节点本身没有自包含的产物，真内容在另一条 lane 上**。普通 inline tool（`bash` / `read` 之类）的产出就在自己节点的 output 字段，用实心方块表示"自给自足"。虚框 vs 实心给用户一眼区分"这个 function_call 跟别的 branch 有牵连"还是"它就是当场跑了点东西"。

inline tool 的结果嵌进 llm_reply 的内部状态，主对话顺序不经过它。
branch-creating / branch-referencing 涉及 branch 拓扑本身，必须在主线
（或新 lane）上占位。

## 边 (3 种)

> 每条边只承担一种语义，不复用。

| 边 | 语义 | 谁连谁 | schema 字段 | 视觉 |
|---|---|---|---|---|
| **sequence** | 同层对话推进 | 上一个 turn 的产物 → 下一个 turn 的输入 | `parent_id` | 主 lane 实线 |
| **call** | 上下级调用 (调用方让出控制) | caller turn → 被调 function_call (或新 branch 起点) | `caller` / `called_by` | 支线 / 跨 lane 实线 |
| **reference** | 指向外部已有节点 (不让出控制) | function_call (attach/merge) → 被引用 branch tip | `attach.head_id` / `merge.parent_ids[1:]` | 虚线 (marching-ants) |

合法的边形态（按起点节点类型）：

```
user_msg     ──sequence──> llm_reply
llm_reply    ──sequence──> user_msg (下一轮)
llm_reply    ──call──────> function_call(inline tool, branch-creating, branch-referencing)
user_msg     ──call──────> function_call (slash command / manual attach 由用户直接触发)
function_call(branch-creating)  ──call──────> 新 branch 的第一个 user_msg
function_call(branch-referencing) ──reference──> 被引用 branch tip
function_call(branch-referencing) ──sequence──> 下一轮 user_msg (它进主线)
function_call(inline tool)      ⊥ (支线叶子，不再生 edge)
```

`sequence` 和 `call` 互斥（一个节点至多有一条非空 parent 指针）—— 跟
`dag-edge-split.md` 的约束一致。`reference` 是额外的边，可以跟 call 共存
（attach 节点同时有 call edge 进来 + reference edge 指外）。

## 三种 attach / merge / spawn 形态

### `/task --async`（spawn + 自动 attach）

```
main lane:  user(/task ...) ────────────────────────────────────────────────────┐
                │ call                                                          │
                ▼                                                                │
sub lane:   function_call(task) ──seq→ sub_user → sub_llm_reply → ... → sub_tip │
                                                                       │        │ seq
                                                                       │ ref    ▼
                                                                       └──→ function_call(attach) ──seq→ llm_reply(auto followup) → user(next)
                                                                            (in main lane)
```

- spawn (`task`) 是 sub lane 的第一个节点（branch-creating）
- 自动 attach 是 main lane 的新节点（branch-referencing），call edge 从同一个
  `user(/task)` 引出（跟 task 是 sibling caller）
- `[系统消息]` followup user msg 不画；auto followup llm_reply 在 main lane 上
  接在 attach 之后

### Branches → Manual Attach

```
main lane:  ... → llm_reply ─call→ function_call(attach, ref=X_tip) ──seq→ user(下一轮) → ...
                                          ▲
                                          │ reference
                                    X branch 的 tip
```

跟 auto attach 形态完全一致 —— 都是当前 branch 的新节点 + reference edge 指过去。
区别只是 caller 不一样（manual 时 caller 是用户当前所在 turn，auto 时 caller
是 `/task` 那条 user_msg）。

### `merge_branches`

跟 attach 同构：

```
main lane:  ... → llm_reply ─call→ function_call(merge, ref=B_tip) ──seq→ user(next) → ...
                                          ▲
                                          │ reference
                                    被合并 branch B 的 tip
```

context state 真正的"汇流"（两条 commit chain 合一）在 **ContextCommit 层**
表达 —— merge commit 的 `parent_ids` 多于一个，回溯 checkout 时直接拿到合并后
的 context。DAG 这层只表达"merge 这个 function_call 被调用了一次，引用了 B"。
两层职责分开。

## 跟现行代码的对应

| 这份文档 | 现行字段 / 实现 |
|---|---|
| `user_msg` / `llm_reply` / `function_call` | `Call.role` + `metadata.function` |
| inline tool | `function ∈ {bash, read, list, ...}` |
| branch-creating | `function == "task"` |
| branch-referencing | `function ∈ {"attach", "merge"}` |
| sequence edge | `parent_id` |
| call edge | `caller` / `called_by` |
| reference edge | `metadata.attach.head_id`（attach）/ `ContextCommit.parent_ids[1:]`（merge） |
| 合成桥（不画） | `source == "task_followup"` AND `role == "user"` |

## Layout 实现影响（要改的部分）

1. **lane.py / depth.py**: 跳过 `source == "task_followup" && role == "user"`
   的节点（不分配 lane / depth → 不画）。
2. **lane.py**: `function == "task"` 强制开新 lane 作为该 lane 的第一个节点
   （目前已经基本是这样，confirm）。
3. **lane.py**: `function ∈ {"attach", "merge"}` 留在 caller 的 lane（main lane），
   不开新 lane —— 因为它们是 branch-referencing，归属调用方 branch。
4. **history-graph.ts**: 三种 function_call 节点的视觉形状区分（inline tool /
   branch-creating / branch-referencing），并支持 reference edge 的虚线表达。
5. **history-graph.ts**: 当前 attach pointer "不画节点" 的 skip 逻辑撤掉；
   attach / merge 都作为正式节点画出来，进 sequence。

## 不在本文档范围

- 节点 collapsing / folding（折叠 inline tool stack 之类）—— 视觉优化层
- ContextCommit chain 的存储模型 —— 见 `context-commit-chain.md`
- branch 命名 / mark_merged 隐藏规则 —— 见 `context-attach-merge.md`
