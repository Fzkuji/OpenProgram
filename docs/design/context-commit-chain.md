# Session Memory: DAG + Context Snapshot

## 心智模型

**session memory = DAG + Context Snapshot**, 两层完全独立的存储:

```
Layer 1: DAG (会话的真实历史)
  - append-only, 不可变, 不被压缩动到
  - 用户主动删整个 session 才会消失

Layer 2: Context Snapshot Chain (LLM 视角的上下文演化)
  - 每个 snapshot = 某个时刻 LLM 看到的完整 context
  - 一个 snapshot 一旦生成就不可变
  - 时间序排列, 像 git commit log, 增量生成
  - 老 snapshot 可以归档 / GC, 不影响 Layer 1
```

类比:
- DAG 是录像 (全量, 永久)
- Context Snapshot 是每个时刻"剪辑过的镜头" (压缩 / 替换 / 删减后的版本)

任何时刻看 "LLM 实际看到啥" = 读对应 snapshot, **不需要从 DAG 重算**。

## 1. DAG 节点存储 (Layer 1)

**Schema (现有, 不动)**:

```sql
CREATE TABLE nodes (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    type         TEXT NOT NULL,        -- "user"/"assistant"/"tool"/"system"
    predecessor  TEXT,                  -- conv edge (与 caller 互斥)
    caller       TEXT,                  -- call edge
    created_at   REAL NOT NULL,
    seq          INTEGER NOT NULL,
    data_json    TEXT NOT NULL          -- name/input/output/metadata
);
```

**约束**:
- 节点写入后只 `metadata.status` 可改 (生命周期翻转), `output` 一经填好不动
- 用户删 session 才 cascade delete, 单条永不主动删
- conv-edge 和 call-edge 互斥, 同节点至多一条入边

## 2. Context Snapshot 存储 (Layer 2)

### 2.1 表结构

```sql
CREATE TABLE context_snapshots (
    id              TEXT PRIMARY KEY,    -- snap_<hex>
    session_id      TEXT NOT NULL,
    parent_id       TEXT,                -- 上一个 snapshot, NULL = 第一个
    created_at      REAL NOT NULL,
    head_node_id    TEXT NOT NULL,       -- 这 snapshot 对应 DAG 哪个 head
    rules_version   TEXT NOT NULL,       -- 哪一版规则生成的
    total_tokens    INTEGER NOT NULL,
    items_json      TEXT NOT NULL,       -- list[ContextItem]
    summary         TEXT NOT NULL DEFAULT ''  -- 这次变化的 1 行描述
);

CREATE INDEX idx_snapshots_session
    ON context_snapshots(session_id, created_at DESC);
CREATE INDEX idx_snapshots_parent
    ON context_snapshots(parent_id);
```

### 2.2 ContextItem

一个 snapshot 是按渲染顺序排好的 ContextItem 列表:

```python
@dataclass
class ContextItem:
    # ── 溯源 ───────────────────────────────────────
    source_node_id: str          # 来自 DAG 哪个节点
    role: str                    # "user"/"assistant"/"tool"/"summary"
                                  #   role="summary" 是合成的, source_node_id 用
                                  #   "sm_<hex>" 虚拟 id, 不在 DAG 里

    # ── 当前呈现状态 ──────────────────────────────
    state: Literal[
        "full",        # 全文进 context
        "aged",        # 替成语义 stub
        "cleared",     # 替成固定占位符
        "summarized",  # 已合进某个 summary item, 这条不渲染
    ]
    locked: bool                 # True = 状态已锁定, 后续规则跳过

    # ── 渲染内容 ──────────────────────────────────
    rendered: str                # state=full 时 = 原 output
                                  # state=aged 时 = stub 字符串
                                  # state=cleared 时 = 固定占位符
                                  # state=summarized 时 = "" (不渲染)
    tokens: int                  # rendered 的 token 估算

    # ── 决策追溯 ──────────────────────────────────
    state_set_at: str            # 这个 state 是在哪个 snapshot 第一次定的
    reason: str                  # "new" / "tail_window" / "idle_60min" /
                                  #  "summarized_into:sm_abc" 等
    merged_into: Optional[str]   # state=summarized 时, 指向 summary item id
```

**没有用户 pin 机制**。"必须永久保留" 的内容走 `memory/core.md` (always-on 系统提示)
或 `memory/wiki` (按主题召回), 不在 session 级 / 消息级解决。

## 3. Snapshot 生成 (增量, 不从 DAG 重算)

### 3.1 核心原则

**每个新 snapshot 都是从上一个 snapshot 复制过来 + 加 delta**, 不重新扫 DAG。
规则只动 **unlocked** 的 item, 已经决定状态的 item (locked=True) 永不重判。

每轮真实工作量: 1 次 DB 读 + 3-5 条 item 改 state + 1 次 DB 写。
不是 O(全部历史), 是 O(delta)。

### 3.2 生成算法

```python
def generate_snapshot(
    session_id: str,
    parent_snapshot_id: Optional[str],
    new_nodes: list[Call],        # 这一轮新增的 DAG 节点
    head_id: str,
    budget: TokenBudget,
) -> Snapshot:

    # Step 1: 起点 = 上一 snapshot 的 items 复制一份 (没有就空)
    if parent_snapshot_id:
        items = list(load_snapshot(parent_snapshot_id).items)
    else:
        items = []   # 新 session 或冷启动

    # Step 2: 把新节点追加为 state="full" 的新 items (locked=False)
    for node in new_nodes:
        if node.role in ("user", "assistant", "tool"):
            items.append(ContextItem(
                source_node_id=node.id,
                role=node.role,
                state="full",
                locked=False,
                rendered=node.output or "",
                tokens=estimate_tokens(node.output),
                state_set_at=this_snap_id,
                reason="new",
                merged_into=None,
            ))

    # Step 3: 应用压缩规则 pipeline
    # 规则只看 locked=False 的 item, 已 lock 的跳过
    apply_compaction_rules(items, budget)

    # Step 4: 计算总 token, 存 snapshot
    total = sum(i.tokens for i in items)
    snap = Snapshot(
        id=new_id("snap"),
        session_id=session_id,
        parent_id=parent_snapshot_id,
        created_at=now(),
        head_node_id=head_id,
        rules_version=CURRENT_RULES,
        total_tokens=total,
        items=items,
        summary=describe_changes(items, parent_snapshot_id),
    )
    save_snapshot(snap)
    return snap
```

### 3.3 压缩规则 pipeline

```python
def apply_compaction_rules(items: list[ContextItem], budget):
    # 顺序固定. 每个规则:
    #   - 只看 locked=False 的 item
    #   - 满足条件就改 state + 设 locked=True
    #   - 已 locked 的 item 永不改
    
    rule_dedup_tool_results(items)        # 同 tool+args 老的标 aged_dup
    rule_tool_aging(items, tail_turns=3)  # 超 tail 的 tool result → aged
    rule_idle_clear(items, gap_min=60)    # 60min idle 老 tool → cleared
    
    if total_tokens(items) > budget.summarize_threshold:
        rule_summarize_old(items, budget) # 跑 LLM 摘要, 生成 summary item

# 状态转移规则:
# full → aged / cleared / summarized   ✓
# aged → cleared                       ✓ (可继续严)
# cleared / summarized → 任何          ✗ (已锁定)
# 任何 → full                          ✗ (不能回头)
```

每个规则的工作集只是少数 unlocked item。tail_turns=3 时:
- 上 snapshot 已 aged 的 70 条 → locked, 跳过
- 新加的 3-5 条 (state=full, unlocked)
- 边界移动后落出 tail 的 1-3 条 (state=full, unlocked)
→ 实际 check 8-10 条, 不是全量。

### 3.4 summary item 怎么生成

`rule_summarize_old` 触发时:
1. 挑最老一段连续 items (比如最老 5 条 user/asst + 它们的 tool)
2. 跑 LLM 生成 summary 文本
3. 把这些 item 标 state="summarized" + locked=True + merged_into="sm_xxx"
4. 在它们位置插入新 ContextItem(role="summary", source_node_id="sm_xxx",
   state="full", rendered=summary 文本)
5. summary item 不写 DAG —— 只活在 snapshot 里, source_node_id 是虚拟的

下一次再触发 summarize 时, summary item 本身可以被合进更大的 summary 里
(再合一层)。彻底解决了之前 `summary_*` / `k_*` 污染 DAG 的问题。

## 4. 任意时刻 LLM 看到啥

### 4.1 当前 turn 发给 LLM

```python
def get_llm_messages(session_id):
    snap = load_latest_snapshot(session_id)
    return [
        render_to_provider_message(item)
        for item in snap.items
        if item.state != "summarized"  # 已合进 summary, 跳过
    ]
```

复杂度: 1 次 DB 读 + 内存映射, 无计算。

### 4.2 回溯任意历史时刻

```python
def get_context_at(session_id, target_time):
    snap = find_snapshot_before(session_id, target_time)
    return snap.items  # 那时 LLM 看到的
```

## 5. Retry / 分支

DAG 已经原生支持 (predecessor 多个 conv-child)。Snapshot 链对应 fork:

```
DAG:                  Context Snapshot:

user1                 snap_1 ← snap_2 ← snap_3a (主线)
  |                                ↖
  asst1                              snap_3b (retry 分支)
  ├── user2a (主线)
  └── user2b (retry)
        |
        asst2b
```

用户在 user2 处 retry 走第二条 → snap_3b.parent_id = snap_2 (跟主线同 fork 点)。

## 6. 存储成本控制

100 turn × 100 item/snap × ~500 字节 = 5 MB/snap, 100 snap = 500 MB。
不可接受, 必须 dedup。

### 内容寻址 blob

ContextItem.rendered 抽到独立表:

```sql
CREATE TABLE context_blobs (
    hash         TEXT PRIMARY KEY,    -- SHA1(content)
    content      TEXT NOT NULL,
    refcount     INTEGER NOT NULL DEFAULT 0
);
```

ContextItem 只存 rendered_hash, 不存 rendered。

90% 的 item 在相邻 snapshot 里 rendered 没变 (老节点 state 锁了, 内容不变), 同
hash 共享 blob。实际 blob 数 ≈ DAG 节点数 + summary 节点数, 跟 snapshot 数无关。

100 snap × 100 item 列表, 真实 blob 大概 200-300 行, 总存储 < 10 MB。

### GC 策略

- 默认保留最近 50 snapshot 全量 items_json
- 老 snapshot 转 "metadata-only" (留 summary + total_tokens, items_json 清空)
- 用户能看的: 最近 50 轮任意回溯, 更老的只能看 snapshot summary

blob 表的 refcount 维护:
- 新 snap 引用一个 hash → refcount +1
- 老 snap GC 掉 → 它引用的 hash refcount -1, 归零的 blob 删

## 7. 跟现状对比 + 迁移

### 现状问题
```
context/
├── microcompact.py          每轮 mutate history dict
├── summarize.py             写 summary_*/k_* 节点污染 DAG
├── tool_aging/              每轮 mutate history dict
└── references.py            只 microcompact 内部用

问题:
- 每轮重算, 不增量
- 没有"时刻状态"概念, 查不到 5 分钟前 LLM 看到啥
- summary 污染 DAG
- 多模块互相不知道对方做了啥
```

### 新结构
```
context/
├── snapshot.py              生成 / 加载 snapshot 的入口
├── rules/                   压缩规则模块, 每个一个文件
│   ├── tool_aging.py
│   ├── microcompact.py
│   ├── summarize.py
│   └── dedup.py
├── blob_store.py            内容寻址存储
└── views.py                 snapshot → provider Message 翻译
```

### 迁移
- 现 session 没 snapshot, 第一次跑时从 DAG 当前 head 倒推生成 snap_0
- 老的 `summary_*` / `k_*` DAG 节点保留但不渲染, 也不再新增
- 新生成 summary 全走 snapshot, 不写 DAG

## 8. 实施 Phase

| Phase | 工作 | 时长 |
|---|---|---|
| 1 | snapshot 表 + 生成器 + LLM 改读最新 snap | 1-2 天 |
| 2 | UI snapshot timeline (right-dock 第三 tab) | 1 天 |
| 3 | blob dedup + GC | 1 天 |
| 4 | 把现有 microcompact / summarize / tool_aging 改造成 rules/ 下的规则 | 1 天 |
| 5+ | 后续补规则 (per-tool source cap / thinking_clean 等) | 按需 |

## 9. 不变式

1. **DAG 永不被压缩动到** —— 所有压缩操作产生新 snapshot, 不改 DAG
2. **Snapshot 一旦生成就不可变** —— 升级规则 → 下个 snapshot 用新规则, 老 snap 保持原样
3. **state 不可回退** —— full → aged 可以, aged → full 不行
4. **locked item 永不被规则改** —— 保证决策一次性
5. **summary item 不写 DAG** —— 只活在 snapshot 里
6. **没有强制保留机制** —— 没有 pin。需要保留就走 core.md / wiki / summarize
7. **同份 rendered 只存一次** —— blob hash dedup
