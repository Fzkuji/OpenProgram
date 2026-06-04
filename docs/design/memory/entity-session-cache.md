# 实体会话缓存 — 完备性审计 + memory 映射就绪度

> 状态: 审计 (2026-06-04) · Owner: store/session + memory
> 关联: [`memory-v2.md`](memory-v2.md) (实体/虚拟两级总设计) ·
> [`git-as-entity-memory.md`](git-as-entity-memory.md) (为什么用 git) ·
> [`store/README.md`](../../../openprogram/store/README.md) (存储层导航)
>
> 本文回答一个具体问题: **memory 要从"实体记忆"映射出来,那个被映射的底座
> (实体会话缓存) 是否完备、内容是否真存好了、有没有给 memory 留出干净的读接口?**
> 结论先行,再逐项给出基于代码 (file:line) 的证据与缺口。

## 0. 结论

实体会话缓存的**核心架构是完备且正确的**,比 Claude Code 的 JSONL transcript 更强:

- **Git 是唯一真相**。每会话一个 git repo,每 turn 一个 commit。节点是 append-only
  的 `history/NNNN-<role>-<id>.json` 文件,会话级标量在 `meta.json`,每 turn 的
  上下文视图在 `context/commits/<id>.json`。
- **内存索引是纯缓存,可无损重建**。`SessionMemoryIndex` 只是查询索引,
  `rebuild_from_paths` 从 `history/` + `meta.json` 完整重建,不丢任何 `Call` 字段
  (`memory_index.py:147-185`)。重启 / cache miss / 子进程写盘后都能从盘上恢复。
- **写盘是同步的**。`append_message` 立刻 `git.write_history` 落文件
  (`session_store.py:481`),`_persist_meta` 每次改 head/title 都写 `meta.json`
  (`session_store.py:205-211`);git **commit** 才是延后到 turn 末。所以即使进程在
  turn 中途崩溃,已 append 的节点和 head 指针都已在盘上,只是少一个 git commit
  边界 —— 下一次启动重建无损。

也就是说,"具体内容保存好了"这一条 **成立**: 对话 DAG (user/llm/code 节点 + reads
边 + called_by 调用链) 都逐节点落盘,turn 边界由 git commit 固定,内存缓存随时可丢可重建。

但有四个缺口,其中**第一个直接卡住"memory 从实体记忆映射"这件事本身**:

1. **[中心缺口] 映射管道根本没读实体层**。memory 的 ingest 读的是 `db.get_branch()`
   渲染出来的对话文本,不是实体层的 `Call` DAG,更没碰 per-turn git commit 或
   project-git。所以实体层对记忆质量的贡献目前 ≈ 0 (memory-v2 §0.5 已记,本文核实)。
2. **[映射接口缺失] 实体层没有给 memory 留干净的读接口**。没有 `Provenance` 数据结构、
   没有增量游标 ("自上次提炼以来的新节点")、没有公开的 session-commit 访问器。
3. **[管理缺口] 缓存无上限**。`_sessions` 字典只增不减,没有 LRU/TTL/上限。一次
   `list_sessions` 会把所有会话载入并永久驻留。
4. **[文档漂移] 死代码 `write_context_file` + `context/messages.json`**。文档说
   `messages.json` 是"当前 LLM 视图",但它从不被写、从不被读。

缺口 3、4 已在本轮修复 (见 §6);缺口 1、2 是 memory-v2 Phase 2 的范围,本文给出
缺口 2 的接口规格 (§5) 作为 Phase 2 的前置。

## 1. 实体会话缓存是什么 (grounded)

```
SessionStore (store/session/session_store.py)   ← 进程内单例, 22 个公开方法
  ._sessions: {session_id: (GitSession, SessionMemoryIndex)}   ← 缓存, lazy
  ._locations: {session_id: abs_repo_path}                     ← 项目内会话的位置索引
        │
        ├── GitSession (git_session.py)          ← 一会话一 git repo, CLI 封装
        │     write_history / write_meta / commit_all / log / checkout
        │
        └── SessionMemoryIndex (memory_index.py) ← 内存 DAG 索引 (纯缓存)
              nodes_by_id / nodes_by_seq
              children_by_predecessor (conv 边) / children_by_caller (调用边)
              head_id / meta / next_seq
```

`from openprogram.agent.session_db import default_db` → `SessionStore` 实例,是全项目
(dispatcher / webui / channels / memory) 统一入口。

### 1.1 一个会话目录的盘上布局 (核实后)

```
<session repo>/                  ~/.openprogram/sessions/<id>/  (随手聊)
│                                或 <project>/.openprogram/sessions/<id>/ (绑了项目)
├── .git/                        每 turn 一 commit (commit_all: git add -A)
├── meta.json                    title/agent_id/project_id/head_id/branches/merged_heads
├── history/NNNN-<u|a|t|s>-<id>.json   append-only DAG 节点, 文件名前缀 = seq 序
├── context/
│   └── commits/<commit_id>.json  每 turn 的 ContextCommit (LLM 看到的物化视图)
│   └── commit.json               (legacy 单文件, read_context_file 仍读, commit/store.py:112)
├── workdir/                      此会话的临时工作目录 (agent 没指定 work_dir 时落这)
└── file_backups/<turn>/          每次编辑前的文件快照 (revert 用)
```

注意: 文档曾称 `context/messages.json` 是"当前 LLM 视图",**这是错的** —— 没有任何
代码写它 (`write_context_file` 零调用方)。真正被写的是 `context/commits/<id>.json`
(由 `context/commit/store.py` 的 `save_commit` 直接写),以及 legacy 的
`context/commit.json` (被 `read_context_file` 读)。已在 §6 修正。

### 1.2 缓存模型: SessionMemoryIndex

| 字段 | 内容 | 盘上来源 |
|---|---|---|
| `nodes_by_id` | id → Call, O(1) 查 | `history/*.json` |
| `nodes_by_seq` | 按 seq 排序的全量节点 | 同上 (文件名即 seq) |
| `children_by_predecessor` | parent_id → [child] (conv 树,含 retry 兄弟) | node.metadata.parent_id |
| `children_by_caller` | caller_id → [callee] (工具子调用) | node.called_by |
| `head_id` | 当前 UI head 指针 | meta.json |
| `meta` | 会话级 dict (title/branches/project_id...) | meta.json |
| `next_seq` | 单调 seq 计数器 | 从 history 最大 seq+1 推出 |

**线程安全**: 每个 index 一把 `threading.Lock`,每个 GitSession 一把,SessionStore
一把 `_sessions` 锁。同会话写串行 (设计上一会话同时只跑一个 turn)。

**进程安全**: 否。`@agentic_function` 工具在 `spawn()` 出来的子进程里跑
(`process_runner.py`),子进程用**自己的** SessionStore 实例直接写盘,父进程缓存看不见。
靠子进程返回后父进程调 `invalidate_cache(session_id)` 丢缓存、下次 `_open` 重建来弥合
(`session_store.py:448-464`,调用点在 `forced_tool.py` / `runtime_attach.py`)。
因为重建无损,这个机制正确,代价是子调用后一次 O(history) 的重新读盘。

## 2. 跟 Claude Code / 其他项目对比

| 维度 | Claude Code | 本项目 (实体会话缓存) |
|---|---|---|
| 持久化底座 | per-session JSONL transcript | per-session **git repo** |
| 历史模型 | 线性 append JSONL | **DAG** (conv 边 + 调用边 + reads 边) |
| turn 边界 | JSONL 行 | **git commit** (可 diff / checkout / revert) |
| 分支/重试 | 无原生 (新会话) | 原生 (DAG fork + branches/merged_heads) |
| 内存态 | 进程内 message list | 可无损重建的查询索引 |
| 时光机 | 无 | `git checkout <turn>` |
| 标准工具可读 | 否 (私有 JSONL) | 是 (`git log`/`grep`/`diff`) |
| 缓存驱逐 | 单会话, 无需 | **本轮新增 LRU 上限** (见 §6) |

实体层比 Claude Code 强在: 不可变 + 可回溯 + 可分支 + agent 能用标准 git 工具自己读。
这正是 memory-v2 选 git 当 episodic memory 底座的理由 (§论文贡献点 1)。

**反过来,Claude Code 强在一处**: 它的 `MEMORY.md` 索引 + topic 文件是**真在用**的
召回回路 —— 索引每次注入,topic 按需读。本项目的实体层虽然更强,但**映射/召回回路还
没接通** (§3),所以"更强的底座"目前没转化成"更好的记忆"。补的就是这一跳。

## 3. 中心缺口: 映射没读实体层 (核实)

memory ingest 的两个入口读的都是渲染文本,不是 DAG:

- `session_watcher.py:99` — `messages = db.get_branch(sid)` (返回 msg dict 链)
- `wiki/ingest.py:455` — `messages = default_db().get_branch(session_id)`
- `wiki/ingest.py:318` — `_render_conversation(messages)` 把链压成纯文本喂 LLM

整个 `openprogram/memory/` 没有任何地方调:
- `db.get_nodes()` (原始 `Call` DAG, 含 code 节点的工具名/参数/结果 + reads 边)
- `GitSession.log()` / session 的 per-turn commit (provenance 坐标)
- project-git 的 commit 历史 (`ProjectGit.log()`, project_store.py:433 — 存在但无人读)

结果: `get_branch` 把"agent 跑了 pytest → 3 个 fail → 改了 38 个文件"这种结构压成
一段对话文本,DAG 里的关系信息 (谁调用谁、什么影响了决策、哪个 commit 修了什么) 在
喂给 LLM 之前就丢了。memory-v2 §5.3 把这点列为"当前最大的缺口",本文核实属实。

**额外发现**: session-git 的 per-turn commit 被**创建但从未被读** —— `GitSession.log()`
的唯一调用方就是 git_session.py 自己。每 turn 一 commit 的 provenance 已经在盘上,只是
还没有任何消费者。这让缺口 1 更明确: 不是"实体层信息不够",而是"映射层没去读"。

## 4. 其余缺口 (按优先级)

```
#   缺口                          严重度   证据                              处置
1   映射读渲染文本而非 DAG/commit   高(中心) session_watcher:99, ingest:455   Phase 2 (memory-v2 §5)
2   无 Provenance / 增量 / 读接口   高       memory/ 全无 get_nodes/log 调用    §5 规格 + 本轮薄层
3   缓存无上限 (只增不减)           中       session_store _sessions 无驱逐     §6 本轮修
4   死代码 write_context_file +     低       零调用方; messages.json 从不写     §6 本轮修
    messages.json 文档漂移
5   locations.json 项目删后残留     低       list_sessions:430 静默跳过        留待 (用户主动删的数据)
6   per-turn commit 无消费者        (并入1)  GitSession.log 仅自调              Phase 2 顺带消费
```

被高估、核实后**不是问题**的:
- "删节点孤儿化附件": 附件只是节点 `extra.attachments` 里的轻量 manifest (count +
  media types,`dispatcher/__init__.py:214-228`),真文件不在 session repo 里,删节点
  不产生孤儿文件。
- "重建与并发 append 竞争": 活跃会话恒在 MRU 端 (刚被访问),LRU 驱逐只碰最久未用的
  空闲会话,在途 turn 持有自己的 (git, idx) 引用,不会被驱逐 (见 §6 论证)。

## 5. 给 memory 的映射接口规格 (Phase 2 前置)

memory 要"从实体记忆映射",需要实体层暴露一个**薄的、不带 LLM 的读接口**。这是
memory-v2 §7 Phase 2 第 1 步 ("先定 Provenance dataclass + 一个不带 LLM 的薄读写层")
的具体化。规格如下,本轮先落 dataclass + 读原语 (§6 Unit D),抽取器 (Stage 2) 留给 Phase 2。

### 5.1 Provenance — 虚拟记忆回指实体层的坐标

```python
@dataclass(frozen=True)
class Provenance:
    project_id: str            # 哪个项目 (会话 meta.project_id)
    session_id: str            # 哪次会话
    node_ids: tuple[str, ...]  # DAG 里哪几个节点 (精确到消息)
    commit: str | None         # session-git 的哪个 commit (turn 边界, 可选)
    event_time: float          # 事情发生的时间 (节点 created_at)
    ingestion_time: float      # 提炼记下的时间 (调用方 stamp)
```

`event_time` vs `ingestion_time` = bi-temporal 两轴 (memory-v2 §3.1)。

### 5.2 读原语 (薄层, 无 LLM, 可单测)

| 原语 | 签名 | 落到实体层哪 |
|---|---|---|
| 增量游标 | `iter_nodes_since(session_id, after_seq) -> list[Call]` | history/ (只回 seq > 游标的新节点) |
| 节点→坐标 | `node_provenance(session_id, node) -> Provenance` | meta.project_id + node 字段 |
| 会话 commit | `session_commits(session_id, limit) -> list[CommitInfo]` | 公开 GitSession.log |
| 项目 commit | `project_commits(project_id, limit) -> list[dict]` | 公开 ProjectGit.log |

有了这层,Phase 2 的 ingest 就能: 读游标拿新节点 → 抽时间轴事件/图实体 → 每条挂
`node_provenance` 给的坐标 → 写虚拟层。召回时虚拟层给坐标,LLM 用导航工具
(`memory_open_session` / `memory_git_show`) 顺坐标钻回实体层取真相。

### 5.3 为什么把读层放在实体侧而不是 memory 侧

memory 是可插拔的 (`MemoryProvider`,README "Plugin point")。把"怎么从 git 读 DAG +
provenance"这件事放在 store/session 侧,任何 memory 后端 (builtin / mem0 / 图库) 都
映射同一个稳定接口,而不是各自去理解 git 布局。实体层负责"暴露可映射的坐标",memory
负责"映射成什么形状"。

## 6. 本轮处置 (小步、各自验证)

| Unit | 缺口 | 动作 | 验证 |
|---|---|---|---|
| B | #4 | 删 `write_context_file`;修 store/__init__ + README 的 messages.json 说法 | py_compile + import + dispatcher 测试 + healthz |
| C | #3 | SessionStore 缓存加 LRU 上限 (move-to-end + evict) | 测试 + healthz |
| D | #2 | 落 `Provenance` + 读原语薄层 (`store/session/provenance.py`) | 单测 + healthz |

缺口 1 (映射读 DAG) = memory-v2 Phase 2,工作量 5-7 天,是独立大改,不在本轮;本轮 Unit D
把它的前置接口铺好。

### 6.1 LRU 驱逐安全性论证 (Unit C)

驱逐空闲会话再重建为何无损、为何不破坏在途 turn:
1. **写已落盘**: `append_message` 同步写 history 文件,`_persist_meta` 同步写 meta;
   只有 git commit 延后。所以盘上已有全部节点 + head,重建无损 (即使 turn 中途)。
2. **活跃会话恒在 MRU**: 每次 `_open` 命中/新建都 move-to-end;在途 turn 刚访问过,
   永远是最近使用,LRU 只驱逐最久未用的空闲会话。
3. **在途 turn 持有自己的引用**: dispatcher 在 turn 开始时拿到 (git, idx) 元组,后续
   append 直接操作该 idx。即便该会话被从字典驱逐,在途 turn 的引用不受影响;它写的
   history 文件已落盘,下次 `_open` 重建能读到。
4. **上限取大** (默认 256): 远超任何正常并发会话数,只防"扫一遍 list_sessions 把上千
   个历史会话永久驻留"这种内存泄漏,不影响热路径。
