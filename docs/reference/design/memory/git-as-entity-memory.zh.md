# 实体记忆走 Git: Session-Git + Project-Git

## 心智模型

"实体记忆"= 真实发生过的事, 可逐步回溯. Git 天生就是这个范式 (commit
是不可变的, log 是时间线, checkout 能时光机), 所以实体记忆直接复用 Git,
而不是另建一套存储.

整体记忆模型是 4+1:

```
实体记忆 (raw, 可回溯)
  ├─ Session memory     ← session-level git (每 turn 一个 commit)
  └─ Project memory     ← project-level git (绑工作目录, 改文件自动 commit)

抽象记忆 (derived, 从实体提炼)
  ├─ Journal (时间轴)
  └─ Wiki (知识图谱)

Core (自我认知)                       ← memory/core.md
```

本文档讲**实体记忆** (前两块). 抽象记忆以及两层之间的映射见
[`virtual-memory.md`](virtual-memory.md) 和 [`overview.md`](overview.md).

## 1. 跟现有 DAG 的关系

**不替换, 是叠加.** DAG (SQLite) 保留, git 增量同步.

- DAG 优势: SQL 查询快, 索引 caller / parent_id / seq, commit chain 已经在跑
- Git 优势: 工具成熟 (log / diff / checkout / revert), 持久化 atomic, 用户能直接 cd 进去看
- 双写: 每个 turn 写 DAG 节点之后, 同步 git commit 一份 JSON 序列化

读路径还是走 DAG (commit 等). git 是**回溯 + 备份 + 用户可视**入口.

## 2. Session-Git

### 2.1 文件布局

每个 session 一个独立 git repo:

```
~/.openprogram/sessions/<session_id>/
├── .git/
├── meta.json              # session 元数据 (title, agent_id, model, ...)
├── messages/
│   ├── 000001-u-abc123.json    # user message
│   ├── 000002-a-def456.json    # assistant message
│   ├── 000003-t-fc_xxx.json    # tool result (caller=def456)
│   ├── 000004-a-ghi789.json
│   └── ...
└── commits/
    └── commit_xxx.json      # 每个 context commit 一份 (可选)
```

文件名按 `<seq>-<role[0]>-<node_id>.json` 排序 — 数字前缀让 ls 顺序就是时序, 不依赖文件系统排序.

每个 message 文件内容就是 DAG 节点的 JSON 序列化:
```json
{
  "id": "abc123",
  "role": "user",
  "content": "你好",
  "predecessor": null,
  "caller": null,
  "created_at": 1779500000.0,
  "metadata": {...}
}
```

### 2.2 Commit 时机

每个 **turn 结束**时一次 commit, 不是每条消息一次 —— 按消息切太碎, 没有实用价值.
turn 结束 = dispatcher.process_user_turn() 返回 TurnResult 时.

一个 turn 包含:
- 1 个 user message
- 1 个 assistant placeholder → 最终带 content
- N 个 tool result (caller = assistant)

commit message:

```
turn <N>: <first 60 chars of user msg>

assistant: <first 80 chars of reply>
tools: read, grep × 3, list

[meta: turn took 12.3s, 18 tools, 4521 tokens]
```

### 2.3 Branch / Retry

DAG 有 retry 分支 (多个 conv-child 共享 parent), 映射到 git:

- session repo 默认 branch = `main` (= 当前 HEAD 路径)
- DAG 上一个新的 retry 触发时, 创建 git branch `retry-<assistant_id>` 并切过去
- 切换 DAG head → checkout git branch
- 用户在 UI 上的 "switch branch" → 后端 `git checkout`

git branch 跟 DAG branch 是同一个东西的两个视角.

### 2.4 回溯 UI

chat 顶部 / 历史区右侧的 prev / next 控件:

```
[← prev turn]  Turn 7 / 12  [next turn →]    [view full history]
```

- prev: `git checkout HEAD~1` + 重放 UI 到那时状态
- next: 反向走 reflog
- view full history: 弹一个 timeline (每个 commit 一条, 点开看 message 内容)

实现层面: WS action `git_history(session_id)` 返回 commit log, `git_checkout(session_id, commit_sha)` 切到某个状态, dispatcher 下一轮 user message 续在那个 commit 上.

### 2.5 双写一致性

**主路径**: dispatcher 写 DAG.
**镜像**: turn 完成后, 异步把这一 turn 的所有节点序列化到 session repo + commit.

异步是因为 git commit ~100-500ms, 不该卡用户. 用 `threading.Thread` 后台跑, 失败只记日志不阻塞.

冲突可能性极低 (一个 session 一个 repo, 串行 commit), 用文件锁兜底.

### 2.6 老 session 迁移

启动时扫已有 SessionDB, 给没 repo 的 session 跑一次性 backfill: 按 seq 遍历节点, 逐 turn commit 出来. 一次跑完, 之后增量.

## 3. Project-Git

### 3.1 概念

Project = 一个长期工作单元, 比如 "wiki-agent 重构". 它:
- 关联一个**文件系统目录** (用户的代码仓 / 文档仓 e.g. `/Users/x/code/wiki-agent`)
- 关联**多个 session** (用户在这 project 上的多次对话)
- 有名字 / 描述 / 状态

Project 本身**就是用户的 git repo** (如果还没 init, agent 帮 init 一个).

Agent 在 project 工作目录里跑 tool 改文件, 这些改动自动 commit 到 project repo.

### 3.2 关联

```python
class Project:
    id: str
    name: str
    workdir: str               # 绝对路径, 用户文件系统目录
    sessions: list[str]        # session id 列表 (谁在这 project 里干活)
    status: "active" | "paused" | "done"
    created_at: float

# 反向关联:
Session.metadata["project_id"] = "proj_xxx"   # 加到 sessions 表
```

session 可以独立存在 (没 project). 有 project 时, agent 修改文件触发的 commit 落在 project repo.

### 3.3 Agent 改文件触发自动 commit

write/edit/apply_patch 这类有副作用的 tool 改了 project workdir 里的文件. 钩子:

```
turn 结束:
  if session.project_id:
    proj = load_project(session.project_id)
    with cwd(proj.workdir):
      if git_status_dirty():
        git add -A
        git commit -m "[agent <session_id>] turn <N>: <user msg first 60 chars>"
```

提交者 (committer) 标 "agent (claude-sonnet-4.7 via OpenProgram)" 跟用户自己手动改的 commit 区分.

### 3.4 用户手动改 vs agent 改

用户在 IDE 里改文件, 不是 OpenProgram 触发的 commit. agent 每 turn 结束前, 先 `git status` 看有没有 dirty:

- 全是 agent 改的 → agent 一次 commit
- 有用户未提交的改 → agent **不动** (不能污染用户 working tree), 在 UI 警告 "你有未提交改动, agent 修改先不自动 commit"

另一种做法是给 agent 专属 branch `agent/<session_id>`, 切到那 branch 上 commit, 完成后用户决定 merge —— 这比问题本身需要的机制更重, 未采用. 用户自己负责管 git, agent 只在干净时 commit.

### 3.5 没绑 project 的 session

完全独立. 不强制每个 session 都属于某个 project.

UI 上"创建 project"是显式操作 — 用户选个目录 + 起个名字. 已有 session 可以加入 project, 后续的工作 commit 到那.

## 4. UI / 入口

### 4.1 Session 历史回溯

chat 顶部 / 右栏:
- timeline 视图 (每 commit 一条)
- "← prev turn" / "next turn →" 按钮
- 选某个 commit → 重放到那个状态, 新发的消息从那分叉

跟 DAG history 视图共存: DAG 看分支结构, git timeline 看时间序.

### 4.2 Projects panel

左 sidebar 的 "Projects" section:

```
─ Projects ─────────
  ● Wiki Agent Refactor   2 sessions  ●  active
  ○ DAG Visualization    1 session   done
  ○ ...
  + New Project
```

点击 project → 进 project detail page: 名字 / workdir / 关联 sessions / 提交历史 / 抽象记忆入口.

新建 project 流程: 选个目录 → 起名 → 创建 / 复用 git repo → 把当前 session 关联进去 (可选).

### 4.3 Chat 顶部 project 指示

如果当前 session 关联了 project, 顶部 status 区显示 project 名字 + workdir 简写, 点击进 project page.

## 5. 关键不变式

1. **DAG 是当前真源, Git 是镜像**. Git 失败不影响 DAG. 反过来不行 — git 落盘但 DAG 没写就是脏数据.
2. **Session-git 一 turn 一 commit**, 不按消息细分.
3. **Project-git 干净优先**: 用户 working tree 不能被 agent 污染. 有未提交改动时 agent 跳过 commit.
4. **回溯不破坏 DAG**: checkout 是只读视图, 用户发新消息会基于该 commit 在 DAG 上 fork 新分支.
5. **抽象记忆建立在实体记忆之上**, 而不是反过来.

## 6. 风险点

- **Git 异步 commit 失败**: 用户看不到, 静默漏数据. 缓解: 后台线程失败重试 + 启动时校验 DAG seq vs git commit 数, 不对就触发 backfill.
- **Project workdir 不是 git 仓**: agent 第一次 commit 时自动 `git init`. 用户已经有 git 的仓: 直接复用.
- **多 session 并发改同一 project**: file lock 串行化 project commit. 极端情况退化到队列.
- **回溯 + 继续聊天的语义**: 用户回到 turn 5 后发新消息, 结果是 fork 新分支, 而不是覆盖之后的内容 (DAG retry 已有这个概念), git 自然映射到 branch.

## 7. 跟 Claude Code 对比

- Claude Code 也支持回溯 (它有 "rewind to previous user message" UI)
- 实现没公开, 但大概率是把 session messages 当文档 + 用某种 commit-like 机制
- 本设计**显式用 git**, 用户能直接 `cd ~/.openprogram/sessions/<sid>` 看历史, 透明性更高

## 8. 跟现有 commit chain 的关系

不冲突. commit chain 是"LLM 看到的 context view", 跟 git 是"实际发生过的 history" 是两个层:

- DAG 节点 (raw 真源) → git commit (持久化镜像)
- ContextCommit chain (LLM 视角) → 不入 git (派生, 可重算)

commit 可以选择性 export 到 git (e.g. 用户想看"那时 LLM 看到啥"), 但不是强制.

## 附录: 构建顺序

工作拆成五块, 每块独立可验证:

1. **Session-git 基础设施** — 模块 `openprogram/memory/session_git/`
   (init / commit / log / checkout 包装); `dispatcher.process_user_turn` 末尾 hook,
   后台线程跑 commit; backfill 脚本把老 session 转成 git repo; WS action
   `git_session_log`, `git_session_checkout`.
2. **Project schema + UI** — DB 表 `projects` (id, name, workdir, status, …);
   sessions 表加 `project_id`; WS `list_projects`, `create_project`,
   `add_session_to_project`; 左 sidebar 加 Projects section.
3. **Project-git auto-commit** — 已绑 `project_id` 的会话在 turn 后跑 project
   commit hook; 干净时 commit, 有 dirty 就警告; UI 警告 banner.
4. **回溯 UI** — chat 顶部 prev/next + timeline view; WS action 调 git_checkout
   重放历史.
5. **数据迁移** — 现有 session 全部跑一遍 backfill; 已有 git 项目 import 成 Project.
