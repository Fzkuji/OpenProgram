# Context — 上下文层

**把会话历史 + 当前输入,组装成每次喂给 LLM 的内容。** 在
[`../providers/`](../providers/) 的上游:context 产出一个 `Context`(system /
messages / tools),providers 把它翻译成各家 wire 请求。

契约 = `Context`(system / messages / tools,content block 可带 `cache_control`)。
本层决定**喂什么、怎么分层**;providers 决定**怎么发给某一家**。两层解耦。

> 目标态(每次调用按稳定度分层 + 让模型知道自己的处境)见
> [`context-composition.md`](context-composition.md);本文讲现状机制。

---

## 一、现状链路

主聊天路径只有一个真正的上下文组装引擎:

```
dispatcher.process_user_turn
  → engine.prepare()            ← 核心:6 步产出 TurnPrep
      1. 引用扫描(references)
      2. 选历史消息  ── commit-chain(默认) 或 dag(flag) 二选一
      3. 拼 system prompt(身份 + 工作区文件 + 技能 + 记忆)
      4. 算 token 预算(四分:system / history / tools / 输出预留)
      5. usage 融合(provider 实测 + 本地估算)
      6. 返回 TurnPrep
  → should_auto_compact? → compact()(可选,内联调 LLM 总结)
  → agent_loop → Context(system, messages, tools) → provider
```

阈值:70% 提示 / 80% 自动压缩 / 95% 急救(`engine.py`)。

**主路径 vs 辅助路径**:主路径之外,有 8-10 处功能各自单独调一次 LLM(总结 /
记忆 sleep / 分支总结 / mixture-of-agents / agentic runtime),它们绕过
engine.prepare,手搓一个极简 `Context`(基本只有 system + 一条 user,无 tools)。
这是有意的分层 —— 这些调用不需要完整聊天上下文。

---

## 二、存储模型:DAG

会话记忆分两层:

- **DAG**:会话真实历史。append-only,永不改写。存 user / assistant / tool /
  runtime 节点,以及 branch / retry / attach / merge 的拓扑。
- **ContextCommit**:某个 head 下"LLM 输入上下文"的不可变快照(压缩、老化、
  summary、attach 展开后的结果)。见 §三。

### 节点

```python
@dataclass
class Call:
    id: str
    seq: int                      # session 内单调递增,排序唯一依据(≠ wall-clock)
    created_at: float
    role: Literal["user", "assistant", "tool", "system"]
    name: str                     # tool 名 / model 名 / ""
    input: Any                    # tool args / system text
    output: Any                   # tool result / assistant 内容 / user 内容(永久原文)
    predecessor: Optional[str]    # 对话边(user/assistant 链)        ┐ 互斥
    caller: Optional[str]         # 调用边(assistant → tool → sub-llm)┘
    reads: list[str]              # 声明读了哪些节点
    metadata: dict
```

约束:`predecessor`/`caller` 互斥(写入侧保证);`output` 永久原文,任何
aging/compact 都不动它。

### git 隐喻:retry / edit / fork 是同一操作

每个 turn 是一次 "commit",`predecessor` 是它响应的对象。retry / edit / fork
本质相同 —— 在某节点的父下挂一个 divergent 兄弟,只是触发方式不同:

| 操作 | 含义 |
|---|---|
| 发新消息 | 在当前 HEAD 下 append 一个节点,HEAD 前进 |
| retry | 同内容的兄弟(assistant 重采样 / 函数重跑) |
| edit | 不同内容的兄弟(在同一父下分叉) |
| 切版本 `< N/M >` | checkout 到某个兄弟,**纯显示,不重跑** |
| 分支成新会话 | 新 Session,HEAD 指向某节点,未来发散 |

要点:**checkout 永不触发重跑**(切 HEAD 只是重渲染);**agent 运行中禁止
edit/retry**(避免把活动执行树挂到即将"变旧"的节点上);**workdir 不随 checkout
回滚**(副作用用户自有,如 `git checkout` 不会重跑你的测试)。

---

## 三、ContextCommit 不可变快照

某个 head 下"喂给 LLM 的上下文"的不可变快照。当前实现:git-backed JSON
(`<session_repo>/context/commits/<id>.json`),代码在 `openprogram/context/commit/`。

### 数据结构

```python
@dataclass
class ContextCommit:
    id: str
    session_id: str
    parent_ids: list[str]   # 普通 turn 单父;merge turn 多父
    created_at: float
    head_node_id: str
    rules_version: str
    total_tokens: int
    items: list[ContextItem]
    summary: str = ""

@dataclass
class ContextItem:
    source_node_id: str          # 对应 DAG 节点(summary 用虚拟 sm_<hex>)
    role: str
    state: Literal["full", "aged", "cleared", "summarized", "summary"]
    locked: bool                 # True = 规则不再动它
    rendered: str
    tokens: int
    reason: str                  # "new" / "tail_window" / "idle_60min" / "attached_from:X"
    merged_into: Optional[str]
    is_anchor: bool              # summary 时保留的高价值原文
    attached_from: Optional[str] # 来自某个 attach 展开的 source commit
```

state:`full`(原内容)/ `aged`(工具结果替成短文本)/ `cleared`(老工具结果替成
固定占位符,cache 友好)/ `summarized`(已合入某 summary,不渲染)/ `summary`
(合成的摘要 item)。

### 生成

`ensure_latest_commit()`:找 head 祖先链上最近的 commit;已对应当前 head 直接返回;
有新节点则 `generate_commit()`(复制 parent items → 新节点转 `full` item → 跑
`RULE_PIPELINE` → 算 token 存 JSON)。规则只改未锁定 item,不回写 DAG,summary item
只存在于 commit(用 `sm_<hex>`)。

### 压缩:三类规则

| 规则 | 触发 | 效果 |
|---|---|---|
| **tool aging** | tool result 超 tail window(默认 `tail_turns=3`) | full → aged,替成 `[tool <name>] output: <头>… <尾>` |
| **idle clearing** | aged 后 60min 未动 | aged → cleared 固定占位符,cache prefix 更稳 |
| **summarize** | `total_tokens > threshold`(~70-80% budget) | 最老连续若干 item 折成一条 `summary`;被折的标 `summarized`;高价值(cited/pinned/anchor)保留 full |

### 渲染

`render_commit(commit)` → provider messages:`summarized` 跳过;`summary` 渲染为
assistant 文本加 `[Summary]` 前缀;tool item 降级为 user 文本(缺配对信息时避免协议
错误)。**纯函数,同输入同输出** —— 便于测试、调试、缓存命中预测。

---

## 四、Attach / Merge(多分支引用与聚合)

attach 与 merge 复用同一机制 —— 把别的分支的 ContextCommit 在当前 commit 里展开成
一组 item。区别:**attach 被动**(stage,等下次 LLM 跑),**merge 主动**(立即触发一
次 LLM turn,写多父 commit)。

- **Attach**:attach pointer 是 DAG 中 `function="attach"` 节点(metadata 含
  source session/head/commit)。generator 读 `source_commit_id` 展开其 items,用
  open/close marker 包住,每条标 `attached_from`;按 `attached_from` 去重(**跨 turn
  只展开一次**);不可加载则 fallback 单条 user item。展开的 item 默认 `full/未锁`,
  和原生 turn 平等过规则(**能被压缩**);summarize 尊重 attach 边界。

- **Merge**:`process_merge_turn()` 在 target head 后写 N 个临时 attach pointer
  (各指一个 peer 末端 commit)+ 一个 merge instruction → 跑 `process_user_turn()`
  → 保存**多父** commit(`parent_ids=[target_prev, peer_1, …]`)→ 标 peer head
  merged。`base_peer` 的 attach 块标 `locked=True`(保证 merge agent 看到原文),其他
  peer 可被 summarize。多父 commit 仅来自 merge。

---

## 五、跨 turn tool 上下文

**问题**:历史里的 `role=tool` 行若被丢掉,模型跨 turn 看不到自己调过哪些 tool、
参数、返回 —— 会重复调用、瞎编调过的文件。这与整段历史的 summarize 是两件事(逐条
tool aging vs 整段语义压缩)。

**策略**(tool aging,即 §三的第一条规则,细化):

- **tail-window 全文**:最近 `TAIL_TURNS=3` 个 assistant turn 的 tool_use /
  tool_result 完整保留。
- **老 turn aging**:更早的 tool_use 头保留(args 截 `MAX_TOOL_ARGS_CHARS=200`),
  tool_result 换 1 行语义 stub。
- **单条上限**:任何 tool_result 超 `MAX_TOOL_RESULT_CHARS=4000` 截首尾(tail 内也
  截,防单 turn 爆)。
- **关键 tool 保护**:`PRUNE_PROTECTED_TOOLS={todo_read, todo_write, web_search}`
  不参与 aging。

数据流:`get_branch` → 把 caller-children tool 行挂回 assistant → tool aging(tail
全文 / 老的换 stub)→ 单条截断 → (超阈值时)整段 summarize → 渲染成 ToolUse +
ToolResult content blocks。代码:`openprogram/context/tool_aging/`。

阈值取自参考框架:tail-window(OpenCode)、逐条 stub(Hermes)、单条截断 + 关键
tool 保护(OpenCode)。

---

## 六、不变式

1. DAG 是 append-only,规则永不改节点 content。
2. ContextCommit 保存后不回写;新规则只影响后续 commit。
3. ContextItem 压缩状态单向收紧,不回退到更完整状态。
4. `locked=True` 不被规则修改;`state=full` 是默认。
5. summary item 不写 DAG。
6. attach 展开按 `attached_from` 去重,只展开一次。
7. 多父 commit 仅来自 merge。
8. `render_commit` 是纯函数。
9. checkout 纯显示,不重跑,不回滚 workdir。

---

## 七、UI

- **History 视图**:完整 DAG,节点 = circle/triangle/square,显示所有 tool 调用 /
  retry 分支 / merge 汇流(靠 `parent_ids` 画分叉)。
- **ContextCommit Timeline**(右栏):`web/components/right-sidebar/context-commit-timeline/`
  + `ws_actions/context_commits.py`。按状态徽章 + token 计数列出当前 commit 的 items
  (full/aged/cleared/summary、来源 `attached_from`)。

---

## 八、目标态与缺口

[`context-composition.md`](context-composition.md) 是这一层的目标:每次 LLM 调用按
"多久变一次"分三层,既服务缓存(稳定的靠前),也让模型知道自己的处境。

| 层 | 内容 | 变化频率 | 现状 |
|---|---|---|---|
| **L0 恒定** | 身份 / 全局指令 / 工具清单 | 整 session 不变 | ✅ engine 已拼(未按缓存分层) |
| **L1 处境** | 我是哪个函数的零件 / 谁调我 / 在程序哪一步 / 输出去哪 | 每进一个 frame 变 | ❌ **完全缺失** |
| **L2 任务** | frame 内进展 / 继承的上游结果 / 当前输入 / 输出格式 | 每步变 | ✅ engine 已选历史 |

缺口(按价值):

1. **L1 处境层完全缺失** —— 模型不知道自己是哪个 @agentic_function 的零件、谁调它、
   输出会被怎么用。价值最高(论文 LLM-as-Code 核心),现状零实现。
2. **两条历史渲染路并存有 parity 风险** —— commit-chain 与 dag 各一套遍历逻辑,改一
   条易忘另一条(曾有 ThinkingContent 只在 dag 路处理、commit 路漏的真实 bug)。该收
   敛成一条。
3. **辅助路径的 system prompt 不跟 agent 走** —— 总结/分支总结用硬编码的
   `SUMMARIZATION_SYSTEM_PROMPT`,不随用户的 AGENTS.md / 身份 / 技能变。

---

## 九、早期方案(已废,留作追溯)

曾设计过一版 **SQLite `node_annotations` 派生模型**:DAG truth + 每节点一条可重算的
Annotation,每 turn 跑 annotator 流水线更新,再 `build_view` 纯函数渲染。它和
ContextCommit 是两种落地:annotation 是"派生可丢弃重算 + SQL 表",ContextCommit 是
"不可变快照 + git JSON"。**已被 ContextCommit 取代**;其仍成立的思想(压缩单向收紧、
view 纯函数、summary 不写 DAG)已并入上文。pinning / dedup / 用户手动 pin-unpin 在
ContextCommit 下尚未全部落地,属待补能力。
