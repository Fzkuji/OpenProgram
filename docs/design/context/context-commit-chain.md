# Session Memory: DAG + ContextCommit

状态：当前准文档，按 git-backed SessionStore 和 `openprogram/context/commit/` 实现整理。

## 1. 当前口径

Session memory 由两层组成：

- DAG：会话真实历史，存 user / assistant / tool / runtime 节点，以及 branch / retry / attach / merge 的拓扑关系。
- ContextCommit：某个 head 下 LLM 输入上下文的不可变记录，存压缩、老化、summary、attach 展开后的结果。

ContextCommit 以 JSON 文件形式存入 session-git 仓库。

## 2. 存储实现

实现文件：

- `openprogram/context/commit/types.py`
- `openprogram/context/commit/store.py`
- `openprogram/context/commit/ensure.py`
- `openprogram/context/commit/generator.py`
- `openprogram/context/commit/views.py`

每个 session 是一个 git-backed repo。ContextCommit 文件写在：

```text
<session_repo>/context/commits/<commit_id>.json
```

`openprogram/context/commit/store.py::init_schema()` 是 no-op，只保留给旧调用方兼容。

当前没有单独的应用层 blob 去重表。重复文本的磁盘去重依赖 git object storage；ContextCommit JSON 自身仍保存 `ContextItem.rendered`。

## 3. ContextCommit 数据结构

`ContextCommit` 字段：

```python
@dataclass
class ContextCommit:
    id: str
    session_id: str
    parent_id: Optional[str]
    created_at: float
    head_node_id: str
    rules_version: str
    total_tokens: int
    items: list[ContextItem]
    summary: str = ""
    parent_ids: list[str] = field(default_factory=list)
```

`parent_id` 是兼容字段。普通 turn 的 `parent_ids` 通常只有一个 parent；merge turn 会写多父：

```text
parent_ids = [target_previous_commit_id, peer_commit_id_1, peer_commit_id_2, ...]
```

## 4. ContextItem 数据结构

`ContextItem` 字段：

```python
@dataclass
class ContextItem:
    source_node_id: str
    role: str
    state: Literal["full", "aged", "cleared", "summarized", "summary"]
    locked: bool
    rendered: str
    tokens: int
    state_set_at: str
    reason: str
    merged_into: Optional[str]
    is_anchor: bool
    anchor_for_summary: Optional[str]
    attached_from: Optional[str]
```

状态含义：

| state | 含义 | 渲染 |
|---|---|---|
| `full` | 原内容进入上下文 | 渲染 |
| `aged` | 工具结果被替换为较短文本 | 渲染 |
| `cleared` | 老工具结果被清空为固定占位符 | 渲染 |
| `summarized` | 已经合入 summary item | 不渲染 |
| `summary` | 合成 summary item | 渲染 |

`locked=True` 的 item 不再被规则修改。`is_anchor=True` 表示 summary 过程中保留的高价值原文 item。`attached_from` 表示该 item 来自某个 attach pointer 展开的 source ContextCommit。

## 5. 生成流程

入口是 `ensure_latest_commit()`：

1. 通过 `load_commit_for_head(store, session_id, head_node_id)` 找当前 branch head 祖先链上最近的 ContextCommit。
2. 如果找到的 commit 已经对应当前 head，直接返回。
3. 如果当前 head 之后有新 DAG 节点，调用 `generate_commit()`。
4. 如果当前 branch 没有 commit，把当前 branch history 作为 cold start 输入生成第一份 commit。

`generate_commit()` 做四件事：

1. 从 parent commit 复制已有 items。
2. 把本轮新增 DAG 节点转成 `ContextItem(state="full")`。
3. 运行 `RULE_PIPELINE`。
4. 计算 `total_tokens` 并保存新的 ContextCommit JSON。

## 6. 规则流水线

当前规则从 `openprogram/context/rules/__init__.py::RULE_PIPELINE` 引入。规则的共同约束：

- 只修改未锁定 item。
- 不回写 DAG 节点内容。
- summary item 只存在于 ContextCommit 中，`source_node_id` 使用 `sm_<hex>`。
- `state="summarized"` 的 item 不再渲染给 provider。

当前实现还包含 anchor 机制：summary 时可保留部分原文 item，让 summary 与少量原文同时进入上下文。

## 7. Provider 渲染

`render_commit(commit)` 将 ContextCommit 转为 provider messages：

- `summarized` item 跳过。
- `summary` item 渲染为 assistant 文本，并加 `[Summary]` 前缀。
- `user` item 渲染为 user message。
- `assistant` item 渲染为 assistant message。
- `tool` item 降级为 user 文本消息，避免缺少 provider tool call 配对信息时触发协议错误。

## 8. Attach

Attach pointer 是 DAG 中的 `function="attach"` 节点。它的 metadata 当前使用以下字段：

```json
{
  "attach": {
    "session_id": "...",
    "head_id": "...",
    "label": "...",
    "manual": true,
    "source_commit_id": "..."
  }
}
```

生成 ContextCommit 时，generator 会读取 `source_commit_id`：

- 如果 source commit 可加载，展开 source commit 的 items。
- 展开结果用 open marker、内容 item、close marker 包住。
- 每条展开 item 写 `attached_from = source_commit_id`。
- 如果 parent commit 中已经有同一个 `attached_from`，本次 attach pointer 不再重复展开。
- 如果 source commit 不可加载，走 legacy fallback，生成单条 user-role item。

Merge 的 base peer 会在临时 attach pointer 中写 `is_base=True`。generator 看到后把该 attach block 的 item 设为 locked，避免 base 内容在 merge 输入阶段被压缩规则移除。

## 9. Merge

Merge 通过 `openprogram/agent/_merge.py::process_merge_turn()` 实现：

1. 解析 peers，每个 peer 是 `(session_id, head_id)`。
2. 为每个 peer 找到对应 ContextCommit。
3. 在 target session 当前 head 上写临时 attach pointer。
4. 调用 `process_user_turn()` 生成一次新的 assistant reply。
5. 保存 multi-parent ContextCommit。
6. 将被合并的 peer head 标记为 merged，使 Branches panel 不再把它们作为独立可见分支列出。

当前已实现 multi-parent ContextCommit；DAG history graph 对 merge reference edge 的展示仍需另行对齐。

## 10. UI

右侧栏当前使用 `ContextCommitTimeline`：

- 前端组件：`web/components/right-sidebar/context-commit-timeline/`
- WS action：`openprogram/webui/ws_actions/context_commits.py`
- 注册入口：`openprogram/webui/server.py::_build_ws_action_registry()`

## 11. 当前不变式

1. DAG 是会话历史来源；ContextCommit 是 LLM 输入上下文记录。
2. ContextCommit 保存后不回写修改；新规则只影响后续 commit。
3. ContextItem 的压缩状态不回退到更完整的状态。
4. `locked=True` 的 item 不被规则修改。
5. summary item 不写 DAG。
6. attach 展开通过 `attached_from` 去重。
7. merge 是当前唯一会写多父 ContextCommit 的常规路径。
8. 当前存储是 git-backed JSON 文件，不是 SQLite schema。
