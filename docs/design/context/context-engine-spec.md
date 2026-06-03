# Context Engine 完整设计

## 1. 总体心智模型

会话是一个**时序 DAG**。每个 wall-clock 时间点 T 都对应一个**会话状态** `S(T)`:

```
S(T) = (DAG(T), Annotations(T))
```

- `DAG(T)`: 时间 ≤ T 写入的所有节点 (user/assistant/tool/system 等)
- `Annotations(T)`: 每个节点在时间 T 的"上下文状态标记" (full / aged / cleared / ...)

DAG 是 **append-only**, 永远不改 (除非用户主动删 session)。Annotations 是
**派生 + 可重算**, 每个 turn 之后 context engine 跑一次 tick 更新它。

LLM 看到的内容 = `view(S(T), head)` —— 从某个 head 节点出发, 沿 conv 链回溯,
按 annotation 渲染每个节点的呈现形式 (全文 / stub / 隐藏)。

## 2. 节点 Schema

### 2.1 DAG 节点 (DAG truth, 不可变)

```python
@dataclass
class Call:
    # ── 标识与时序 ──────────────────────────────────
    id: str                       # 节点 uuid
    seq: int                      # session 内单调递增, sort key
    created_at: float             # wall-clock unix
    session_id: str               # 外键, 不在 dataclass 里, 在表里

    # ── 边 ────────────────────────────────────────
    # 二选一, 互斥:
    predecessor: Optional[str]    # 对话边 (user/assistant 链)
    caller: Optional[str]         # 调用边 (assistant → tool → sub-llm)

    # ── 类型与内容 ──────────────────────────────────
    role: Literal["user", "assistant", "tool", "system"]
    name: str                     # tool 名 / model 名 / ""
    input: Any                    # tool args / system text / 空
    output: Any                   # tool result / assistant content / user content

    # ── 引用与元信息 ──────────────────────────────
    reads: list[str]              # 这个节点声明读了哪些其他节点 id
    metadata: dict                # 通道 / 提供商 / token / 状态 等杂项
```

**关键约束**:
- `predecessor` 和 `caller` 互斥 (DB schema 不约束, 写入侧保证)
- `seq` 在 session 内严格单调, 但**不等于** wall-clock 序 (两个并发写可能 seq 倒挂, 以 seq 为准)
- `output` 永久原文, 任何 aging/compact 不动它

### 2.2 Annotation 节点 (派生, 可重算)

每个 DAG 节点对应 0 或 1 条 annotation 记录:

```python
@dataclass
class Annotation:
    session_id: str
    node_id: str

    # ── 上下文呈现状态 ──────────────────────────────
    state: Literal[
        "full",         # 默认, 原文进 context
        "aged",         # 替成语义 stub (tool_aging)
        "cleared",      # 替成固定占位符 (microcompact, cache 友好)
        "summarized",   # 跳过, 已折进 summarized_into 指向的 summary 节点
        "pinned",       # 用户钉选, 强制 full
        "hidden",       # 完全不进 context (e.g. 内部 retry 失败)
    ]
    state_set_at: float
    state_reason: str   # "tail_window" / "idle_60min" / "user_pin" / "cited" / ...

    # ── 引用关系 ───────────────────────────────────
    cited_by: list[str] = []      # 哪些较新的节点引用了它, 阻止 aging
    summarized_into: Optional[str] = None   # 折进哪个 summary 节点

    # ── 缓存提示 ───────────────────────────────────
    cache_breaking: bool = False  # 这个 annotation 转换是否破 cache prefix

    # ── 渲染 hint (state != "full" 时用) ─────────
    rendered_text: Optional[str] = None  # aged 时的 stub 字符串
    rendered_token_estimate: int = 0     # rendered 占多少 token
```

### 2.3 Annotation 存储

新建表 `node_annotations`:

```sql
CREATE TABLE node_annotations (
    session_id    TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    state         TEXT NOT NULL,
    state_set_at  REAL NOT NULL,
    state_reason  TEXT NOT NULL DEFAULT '',
    cited_by      TEXT NOT NULL DEFAULT '[]',   -- JSON list
    summarized_into TEXT,
    cache_breaking INTEGER NOT NULL DEFAULT 0,
    rendered_text TEXT,
    rendered_token_estimate INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, node_id),
    FOREIGN KEY (session_id, node_id) REFERENCES nodes(session_id, id)
        ON DELETE CASCADE
);

CREATE INDEX idx_node_annotations_state
    ON node_annotations(session_id, state);
```

**默认值约定**: 不在 annotation 表里的节点 → state="full"。所以新 session
表是空的, 不需要批量初始化。

## 3. Context Engine Tick

每个 turn 之后跑一次"tick", 更新 annotations 到 `S(T_now)`。

### 3.1 Tick 输入

- `session_id`
- `head_node_id` (当前对话 head)
- `agent_profile` (模型 / context_window / 偏好)

### 3.2 Tick 流水线

```
load_dag_branch(session_id, head_node_id)
    → linear list of conv-chain nodes [n_0, n_1, ..., n_k] + attached tool sub-calls
        ↓
load_annotations(session_id, node_ids)
    → dict[node_id, Annotation] (缺的视作 state="full")
        ↓
┌─── annotation pipeline (顺序跑, 每个 annotator 可读 + 改 annotations) ───┐
│                                                                          │
│  1. pinning           读 user pin 标记, 强制 state=pinned                │
│  2. references        扫最近 N 节点的引用, 标 cited_by                   │
│  3. dedup             同 tool+args 多次, 老的标 state=aged_dup           │
│  4. tool_aging        非 tail / 非 pinned / 非 cited tool → state=aged   │
│  5. microcompact      距上次 assistant > 60min 时 → state=cleared        │
│  6. summarize         token 超阈值 → 跑 LLM 摘要 → 标 summarized        │
│  7. thinking_clean    老 thinking block → state=cleared                  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
        ↓
compute_rendered_text(annotations)
    → 给每个 state != full 的 annotation 填 rendered_text + token_estimate
        ↓
persist_annotations(session_id, annotations)
    → upsert 到 node_annotations 表 (只写真正变化的行)
        ↓
build_view(dag_nodes, annotations)
    → list[Message] 给 LLM
```

### 3.3 Annotator 接口

每个 annotator 是个纯函数, 可独立测:

```python
class Annotator(Protocol):
    name: str           # "tool_aging" / "pinning" / ...
    priority: int       # 跑的顺序, 小的先 (pin=10, ref=20, dedup=30, ...)

    def annotate(
        self,
        nodes: list[Call],            # 此 session 的 DAG 节点 (按 seq 排)
        head_id: str,
        annotations: dict[str, Annotation],  # 当前 annotation 状态, 改这个
        ctx: TickContext,             # 提供 model / budget / now / ...
    ) -> None:
        """Mutate annotations in place. Don't touch DAG nodes."""
```

**优先级规则**:
- 先跑保护类 (pin, ref) → 把节点标 pinned / 受保护
- 再跑标记类 (dedup) → 标重复
- 再跑 aging 类 (tool_aging, microcompact) → 但跳过已被保护的
- 最后跑重型 (summarize) → 在 aging 之上仍超 token 时才动手

任何 annotator **不能** 把 state=pinned 的节点改成其它状态。
任何 annotator **可以** 把 state=full 的节点改成更高级状态 (aged/cleared/summarized)。
回退 (aged → full) 只有 unpin / 用户手动 reset 才能做。

### 3.4 Tick 触发时机

```
turn 开始 (prepare 阶段)
  → tick 跑, 基于上一轮已有 annotations + 这一轮新增节点
  → 产出本轮要发给 LLM 的 view
  → 持久化新 annotations

turn 结束 (新 assistant + tool 节点写完)
  → 不立即跑 tick, 等下一轮 prepare 时再跑
  → 节省一次跑 LLM (summarize 是贵的)
  → 例外: token 超硬阈值时强制立即跑 (兜底)
```

## 4. View 构建 (DAG + Annotation → LLM Messages)

`build_view(nodes, annotations, head_id)`:

```python
def build_view(
    nodes: list[Call],
    annotations: dict[str, Annotation],
    head_id: str,
) -> list[Message]:
    out = []
    chain = walk_conv_chain(nodes, head_id)  # head 回溯到 root
    for n in chain:
        ann = annotations.get(n.id) or Annotation(state="full")

        if ann.state == "hidden":
            continue
        if ann.state == "summarized":
            continue  # 折进 summary 节点, 由 summary 节点单独渲染

        if n.role == "assistant":
            # attach this assistant's sub-call (tool) children
            tool_kids = sub_calls_of(nodes, n.id, sorted_by_seq=True)
            asst_content = [TextContent(text=n.output)] if n.output else []
            for tool in tool_kids:
                t_ann = annotations.get(tool.id) or Annotation(state="full")
                if t_ann.state == "hidden":
                    continue
                asst_content.append(ToolCall(
                    id=tool.id, name=tool.name, arguments=tool.input or {},
                ))
            out.append(AssistantMessage(content=asst_content, ...))
            for tool in tool_kids:
                t_ann = annotations.get(tool.id) or Annotation(state="full")
                if t_ann.state == "hidden":
                    continue
                # state 决定 result 内容
                if t_ann.state == "full":
                    result = tool.output
                elif t_ann.state in ("aged", "cleared"):
                    result = t_ann.rendered_text
                elif t_ann.state == "pinned":
                    result = tool.output  # pin 强制全文
                else:
                    result = tool.output
                out.append(ToolResultMessage(
                    tool_call_id=tool.id, content=[TextContent(text=result)],
                ))

        elif n.role == "user":
            out.append(UserMessage(content=[TextContent(text=n.output)]))

    # 把 summary 节点也插进去 (在它们 cut_idx 位置)
    inject_summary_nodes_into_view(out, nodes, annotations)
    return out
```

view 构建是**纯函数**, 同样的 DAG + annotation 永远产出一样的 messages。这让
debugging / 测试 / cache 命中预测都简单。

## 5. UI 两个视图

### V1: Raw History (右栏现有 History 视图)
- 显示完整 DAG, 节点 = circle/triangle/square, 不受 annotation 影响
- 用户能看到所有 tool 调用 / 所有 retry 分支

### V2: Active Context Inspector (新, right-dock 第三个 view)

按时间序列出 `build_view` 的产物, 每条带状态徽章 + token 计数:

```
┌─ Active Context (head: 95b0d8f8_reply, total: 18.4k tok / 200k, 9%)
│
│  [SYSTEM]  fixed system prompt              1.2k tok  ●pinned
│  [USER]    "改一下 dispatch 逻辑"            120 tok  ●full
│  [ASST]    "好的, 先扫描..."                  85 tok  ●full
│  [TOOL]    list(/) → [aged] 82 files...      35 tok  ◐aged
│  [TOOL]    grep("dispatch") → [cleared]      12 tok  ○cleared
│  [TOOL]    read(dispatcher.py) → 完整 4 KB  3.8k tok  ●full
│  [USER]    "再 grep 一下 cancel"              90 tok  ●full
│  ...
│
│  ┌── Pending compact: 距 80% 还差 12k tok
│  └── Last summarize: 5 minutes ago, freed 8.1k tok
```

操作:
- 右键节点: pin / unpin / 手动 clear / 重新 age
- 鼠标 hover: 显示完整 state_reason / state_set_at / rendered_text
- 顶部 toggle: 只看本 head / 包括被 summarized 的节点

V2 是 **debug + 控制** 入口, 也是用户对 context engine 行为的信任来源。

## 6. 现状映射 + 改动清单

### 现状

```
context/
├── engine.py             ContextEngine.prepare() 是入口
├── microcompact.py       单独跑, 直接 mutate history dict
├── summarize.py          单独跑, 产 summary 节点写 DAG
├── references.py         只在 microcompact 内部用
├── tool_aging/           Phase A 新加, 在 _assemble_messages 前 mutate
├── budgets.py            算 token budget
├── tokens.py
└── persistence.py        insert_summary_node, summary_*/k_* 节点
```

问题:
- microcompact / summarize / tool_aging 各自 mutate history dict, 互不知道
- 没有持久 annotation, 每轮全重算
- references.py 算的 cited 只 microcompact 看, 其它 annotator 看不到
- summary 写成 `summary_*` / `k_*` 节点污染 DAG, 又得在 graph_layout filter 出去

### 改造步骤

**Phase 1**: 加 annotation 表 + Annotator 接口框架
- 新建 `node_annotations` 表 (storage.py 加 schema)
- 新建 `context/annotations/_base.py` 定义 Annotator Protocol + TickContext
- 新建 `context/store.py` annotation CRUD
- 老的 microcompact / summarize / tool_aging 暂时不动, 单测覆盖

**Phase 2**: 把现有功能改写成 Annotator
- `annotations/tool_aging.py` ← 现 `tool_aging/__init__.py`, 改成读改 annotation
- `annotations/microcompact.py` ← 现 `microcompact.py`, 同上
- `annotations/summarize.py` ← 现 `summarize.py`, summary 节点不再写 DAG, 而是
  把要被压缩的节点全标 state=summarized, summary 内容塞在一个 annotation 的
  rendered_text 里
- `annotations/references.py` ← 现 `references.py`, 改成统一标 cited_by
- 流水线串联: engine.py 跑一遍各 annotator

**Phase 3**: View 层抽出来
- 新建 `context/views/active.py` build_view 函数 (纯函数)
- engine.py 用它替代当前的 _assemble_messages
- raw view (chat 历史) 也用 build_view 但传 state=ignore_annotations 参数

**Phase 4**: UI Active Context Inspector
- right-dock 加第三个 view
- 后端 ws action: `get_active_context(session_id, head_id)` 返回 annotated view JSON
- 前端 React 组件列出, 加 pin / unpin / clear 按钮

**Phase 5**: 补缺失 annotator
- `annotations/pinning.py` (新)
- `annotations/dedup.py` (新)
- `annotations/thinking_clean.py` (新)

**Phase 6**: Prompt cache (横切)
- view 输出加 `cache_control` marker (system 末 / tools 末 / latest user)
- annotation state 转换时记录 `cache_breaking=true`, UI 提示影响

**Phase 7**: 删 `persistence.insert_summary_node`, summary 不再写 DAG, 老的
`summary_*` / `k_*` 节点迁移脚本清理 (或保留作历史不画图)

## 7. 关键不变式

设计上保证的 invariants, 后续改动不能破:

1. **DAG 是 append-only**, annotator 永远不改节点 content
2. Annotation 任何时刻都可以**完全丢弃重算**, 不丢任何用户可见信息
3. `state=full` 是默认, 缺 annotation 等同 full
4. `state=pinned` 一旦设, 其它 annotator 不能覆盖, 只有用户 unpin 可降
5. `view(DAG, annotations)` 是纯函数, 同输入产同输出
6. annotator 优先级固定, 流水线不允许循环依赖
7. summary 不写 DAG 节点, 全在 annotation 里 (Phase 7 后)

## 8. 取舍说明

**为什么 annotation 单独建表而不是塞 node.metadata?**
- node.metadata 是 DAG truth 的一部分, 不该混入派生信息
- 重建 annotation 时不该需要重写 DAG 节点
- 单独表便于按 state 索引 (如 "全部 pinned 节点")
- DAG 真源跟 view layer 解耦, 后续换不同 engine 实现也只换 annotation

**为什么 tick 在 turn 开始而不是 turn 结束?**
- turn 开始时已经知道 head 是什么, 新 user 节点也写入了, 信息全
- turn 结束跑会让"显示给 LLM 的 context"跟"显示给用户的 context"时序错位
- summarize 这种贵操作放 turn 开始可以并行做, 不卡 LLM 调用

**为什么 summary 不写 DAG 节点了?**
- summary 是派生的, 写进 DAG 制造伪节点 (`summary_*` / `k_*`)
- 后面 graph_layout 还要把它们过滤掉
- DAG 应保持纯净, summary 是 view 层的事
