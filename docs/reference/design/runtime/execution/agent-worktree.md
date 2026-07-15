# Agent Worktree 工具

> Agent 在用户真实代码仓库里跑高风险改动时，需要一个隔离的临时工作目录：
> 改成了 merge 回主线，改坏了 discard 一抹了之，主仓库一字未动。
> 底层就是 `git worktree add` / `git worktree remove` 的封装，但要跟
> OpenProgram 自己的 session-git 严格区分开。

参考 Claude Code 的 `EnterWorktreeTool` / `ExitWorktreeTool`
（`references/claude-code-leaked/src/tools/EnterWorktreeTool/`），
本设计照搬其"切 cwd + 状态机 + 退出时 keep/discard"骨架，但适配
OpenProgram 的 runtime / session 模型。

---

## Part 1. 设计需要考虑的维度

### D1. Worktree 实体存什么

每个 active worktree 是一条记录，字段：

- `id`: worktree 短 id（hex，跟 commit id 风格一致）
- `source_repo`: 用户真实仓库 root（绝对路径）
- `worktree_path`: `git worktree add` 落地的目录（绝对路径）
- `branch_name`: worktree 上对应的分支名（默认 `op/wt/<id>`）
- `base_ref`: 创建时的基线 ref（默认 `HEAD`，可指定 origin/main / commit sha）
- `created_at`: unix 时间戳
- `status`: `active` / `committing` / `merged` / `discarded` / `kept`
- `parent_session_id`: 关联的 OpenProgram session（一对一或一对多）
- `parent_task_id`: 关联的 async task（若有）
- `created_by_agent`: agent id（记录是哪个 agent 开的，UI 上能看出来）

记录持久化在 session-git 仓库的 `worktrees/<id>.json`，跟 ContextCommit 平行存。
session 关闭时不自动清理，等 agent 或用户显式 merge / discard。

### D2. cwd 切换机制

OpenProgram 的工具分两类：

1. **Runtime-spawned subprocess**（Codex CLI / Claude CLI 这种带 `--cd` 参数的）
   通过 `runtime.set_workdir(path)` 控制。
2. **In-process `@function` 工具**（bash / edit / write / read 等）
   bash 走 `get_active_backend().run(...)`，目前 `LocalBackend.run` 接收
   `cwd` 参数但调用方没传；edit / write / read 强制要求绝对路径。

设计：在 `openprogram/agent/_runtime.py` 增加一个 ContextVar
`_current_worktree_path: Optional[str]`，dispatcher 每次进 turn 时，
若 session 当前有 active worktree（从 session meta 读），就 `set` 这个 var。
工具实现按需消费：

- bash：`LocalBackend.run(cmd, cwd=_current_worktree_path.get())`
- edit / write / read：相对路径解析时以 `_current_worktree_path` 为根；
  绝对路径必须在 worktree 之下（D6 安全校验）。
- runtime 子进程：保留现有 `apply_default_workdir(runtime, session_id)`，
  改成优先返回 worktree path（若有），否则 session-git 的 `workdir/`。

不引入"显式 cwd 参数"。worktree 是 session 级别的上下文，工具不感知。

### D3. 状态机

```
            create
              │
              ▼
        ┌─ active ─┐
        │          │
  merge │          │ discard
        │          │
        ▼          ▼
     merged    discarded
        │          │
        └────┬─────┘
             │ keep
             ▼
           kept (用户决定保留分支但不 merge 也不删)
```

- `active`: agent 正在用。bash / edit 这类工具 cwd 默认指向这里。
- `committing`: 短暂状态，merge 操作期间持锁，防止并发改文件。
- `merged`: `git merge` 成功，worktree 目录已 `git worktree remove`。
- `discarded`: `git worktree remove --force` 成功，分支也删了。
- `kept`: 用户走 `worktree_keep` —— worktree 目录保留，OpenProgram
  解绑这条记录但不动 git。后续用户自己接管。

### D4. 跟 OpenProgram session-git 的隔离

OpenProgram 自己有 `~/.openprogram/sessions/<sid>/`（每个 session 一个 git repo），
存对话内存的 history / context / workdir。**绝对不能把 agent worktree
落地在这个目录树里**：

- worktree_path 必须不在任何 `~/.openprogram/sessions/*` 之下（D14 校验）。
- source_repo 不能等于 session-git 仓库路径。
- session-git 的 commit 跟 worktree 的 commit 各管各的；UI 上 ContextCommit
  时间线只看 session-git，worktree 时间线另一个 panel 显示。

历史上 OpenProgram 试过 sub-agent worktree（commit `5ba13149`），
落地在 `<session-repo>/_worktrees/<branch>/`，后来被重构成"sub-agent =
peer session + attach"（commit `75e430c0`）。本设计不复用那条路径——
那个是"在 session-git 内部开分支跑 sub-agent"，本设计是"在用户真实代码
仓库开 worktree 给 agent 跑改动"，完全不同的目的。

### D5. Source repo 来源

三个入口，优先级从高到低：

1. **agent 显式传**：worktree_create 工具的 `source_repo` 参数（绝对路径）。
   适合 plan agent 列任务时已经知道目标仓库。
2. **fn-form 的 "Working in a folder"**：用户在 web UI 给程序输入的
   `_work_dir`。dispatcher 在进 turn 前若发现这个 path 是个 git repo 根，
   就当作 default source_repo（worktree_create 不传 source_repo 时用）。
3. **session 当前 cwd 的祖先 git root**：用 `git rev-parse --show-toplevel`
   走一遍。一般是用户启动 OpenProgram 的目录。

入口都失败 → worktree_create 报错 `source_repo_not_a_git_repo`。
不会自动 `git init` 给用户建仓库（破坏性太大）。

### D6. 安全 / 权限

worktree 内 agent 工具的核心安全约束：**bash 命令的 cwd 锁定，但 cmd
内可以 `cd ..` 跑到 worktree 外**。这不是真正的 sandbox，是"默认指向"。
两层补救：

- **绝对路径校验**：edit / write / read 收到 `file_path` 时，若它落在
  worktree_path 之外，记一条 warning 进 ContextCommit metadata
  （`outside_worktree=true`），但不阻止——用户可能确实要读 system 配置。
- **bash 的 cwd 永远是 worktree_path**：即使 LLM 写了 `cd /tmp && rm -rf X`，
  起点是 worktree_path，shell session 不持久（每条 bash 都是新 subprocess），
  下次 bash 又回 worktree_path。

不做：bash 命令的 chroot / namespace 隔离。OpenProgram 已经支持 docker
backend，要 hard sandbox 走那条路。

### D7. Worktree 内的 commit

agent 在 worktree 里写文件 → worktree 目录是脏的。两种语义：

- **自动 commit**：每次 agent 工具调用后（bash 跑 git add / edit / write），
  worktree 工具不自动 commit。让 agent 自己用 bash 跑 `git add -A && git commit`。
  这样 commit message 由 agent 决定，符合 git 习惯。
- **merge 时强制 commit**：worktree_merge 时若 worktree 有 uncommitted
  changes，先报错 `worktree_dirty`，让 agent 显式处理（commit 掉 / stash 掉
  / discard 掉）。不自动 commit-and-merge。

### D8. Merge 策略

`worktree_merge(worktree_id, mode="ff-only" | "squash" | "no-ff")`，默认 ff-only。

- **ff-only**: source_repo 的 HEAD 是 worktree branch 的祖先 → fast-forward。
  否则报错 `not_fast_forward`，让 agent 决定是 rebase 还是切到 squash。
- **squash**: `git merge --squash <branch>` → 多个 worktree commit 压成一条；
  适合 worktree 内是探索性多次小 commit 的情形。
- **no-ff**: 总是创建 merge commit，保留 worktree 的 commit 历史。

merge 之后默认 `git worktree remove <path>` 删掉 worktree 目录，但
**branch 保留**（让用户能 git log 看到这次改动的历史）。
冲突的处理：merge 失败时**不**自动 reset，worktree 状态保持 `committing`
（实际上回滚到 active），让 agent 或用户进 worktree 手动解决冲突。

### D9. Discard 语义

`worktree_discard(worktree_id, force=False)`:

- `force=False`（默认）：worktree 必须 clean（没有 uncommitted / untracked）。
  否则报错 `worktree_dirty`，agent 可以决定是 stash 还是 force。
- `force=True`：`git worktree remove --force <path>` + `git branch -D <branch>`。
  uncommitted 改动直接丢。
- 记录在 worktrees/<id>.json 里 status 改成 `discarded` + 时间戳。文件不删，
  方便审计——但 worktree_path 已经不存在了。

不提供"discard 前自动备份"。讨论过把丢弃的内容打 tar 塞 `~/.openprogram/discarded/`，
但保留这条逃生绳成本不高，留到 Part 6（未来）。

### D10. Worktree 跟 task 的关系

async task 系统（见 `async-task-lifecycle.md`）：

- 一个 task 可以独占创建并使用 worktree（task_create → worktree_create）。
- task cancel 时，task 持有的 worktree 默认走 `discard force=True`。
  task complete 时**不**自动 merge——让 task 完成后由 plan agent / 用户
  显式决定（plan agent 看了 3 个 task 的产出后挑一个 merge）。
- 一个 task 没强制要求开 worktree。轻量 task（读文件、跑 grep）直接在
  source_repo 上跑就行，不开 worktree。

实现上，task lifecycle 在 cancel hook 里调 `worktree_manager.discard_for_task(task_id)`。

### D11. Worktree 跟 ContextCommit 的关系

agent 在 worktree 里跑工具，工具结果（bash stdout / edit confirmation）
正常进 ContextCommit 的 items。**worktree 里的 file diff 不直接进 ContextCommit
内容**——文件 diff 是 git 的事，ContextCommit 只记"工具调用 X 修改了文件 Y"
这类事件级别的事实。

新增一个轻量 metadata：每条工具 item 的 metadata 里加
`worktree_id: Optional[str]`，标明这条工具调用发生在哪个 worktree 上
（None 就是在 source_repo 直接跑）。UI 渲染时给 worktree 内的工具调用
加个角标。

worktree merge / discard 操作本身也写进 ContextCommit，作为 system 节点
（类似 attach pointer 的 marker），content 是 "Merged worktree wt_abc1234
into source_repo (ff-only, 3 files changed)"。

### D12. Agent 工具暴露

四个工具：

| Tool | 参数 | 返回 |
|---|---|---|
| `worktree_create` | `source_repo: str?` `name: str?` `base_ref: str?` | `{id, path, branch, base_sha}` |
| `worktree_merge`  | `worktree_id: str` `mode: str = "ff-only"` `delete_branch: bool = False` | `{merged_sha, files_changed: int, summary: str}` |
| `worktree_discard`| `worktree_id: str` `force: bool = False` | `{status: "discarded"}` |
| `worktree_list`   | `status_filter: str?` | `[{id, path, branch, status, source_repo, age_seconds}]` |

错误码（返回 error 字符串前缀）:

- `not_a_git_repo`: source_repo 不是 git repo
- `worktree_dirty`: worktree 有 uncommitted changes
- `not_fast_forward`: merge 时不能 ff
- `merge_conflict`: merge 期间冲突
- `worktree_in_sessions_dir`: source_repo 落在 sessions 树里（D4 隔离违例）
- `worktree_exists`: 同 source_repo 下同名 branch 已有 worktree

`worktree_create` / `worktree_merge` / `worktree_discard` 默认
`requires_approval=True`，permission_mode=auto 才不弹审批。

不暴露 `worktree_switch` 工具——一个 session 同时只有一个 active worktree
（D2 的 ContextVar 是单值），切换语义复杂（要不要写一条切换 marker？
切换后老 worktree 怎么算？），收益不抵成本。多 worktree 通过 async task
实现，每个 task 一个 worktree。

### D13. UI 表达

- **Composer 工具栏**：当前 session 有 active worktree 时，PromptInput 上方
  显示一个 chip `worktree: wt_abc1234 (3 files changed)`，hover 弹 panel
  显示 worktree_path / branch / 改动文件列表 / Merge / Discard / Keep 按钮。
- **fn-form 的 "Working in a folder"**：保持原样，只显示 source_repo 路径。
  worktree 作为内部 detail 不在 fn-form 里 surface。
- **DAG 时间线**：worktree create / merge / discard marker 节点用区分色
  渲染（跟 attach marker 一致风格）。
- **不做的**：worktree 文件 diff 的内联预览（用户可以点开"open in editor"
  / 用户自己的 git GUI 看）。

### D14. 错误 / 边界

- `source_repo` 不是 git repo → `not_a_git_repo` 错误，提示用户先 `git init`。
- `source_repo` 有 uncommitted changes 但 worktree 是新分支 → OK，
  worktree 从 base_ref（默认 HEAD）创建，不受 source_repo working tree 状态影响。
- `worktree_path` 已存在 → `worktree_exists` 错误。允许用户传 name 重试。
- `source_repo` 在 sessions 树里 → `worktree_in_sessions_dir` 拒绝（D4）。
- `base_ref` 不存在 → git 自己报错，工具透传 stderr。
- agent 误删 worktree_path（绕过 worktree_discard 直接 rm -rf）→
  下次 worktree_list 探测到 path 不存在时自动标记 `status=discarded`
  并写一条 "auto-cleaned" 记录。

### D15. 跟 Async Task 的整合

worktree_create / merge / discard 本身是同步工具（git 子进程），不要 wrap
成 async task。但**worktree 内的长时间工作**（agent 跑测试、跑 build）
通常是 async task 的工作内容：

- async task 启动时可以指定 `worktree_id`（task 的 cwd 锁定到这个 worktree）。
- task 内部跑的 bash / edit 也走 D2 的 ContextVar 路径，cwd 是 worktree_path。
- task cancel hook → 调 `worktree_manager.on_task_cancel(task_id)`，
  默认 discard。
- task complete 不自动 merge（D10）。

---

## Part 2. 场景 × 维度

### 场景 A: 单 agent 单 worktree（基础流程）

agent 接到任务"改 foo.py 加个 logging"，开 worktree → 改 → 跑测试 → merge。

| 维度 | 设计 |
|---|---|
| **D1 实体** | 一条 worktree 记录，`status=active`，绑定当前 session |
| **D2 cwd** | dispatcher 进 turn 时读 session.meta.active_worktree_id → 设 `_current_worktree_path` ContextVar；bash/edit/write/read 全部默认 cwd 这里 |
| **D3 状态** | active → committing（merge 期间）→ merged |
| **D4 隔离** | worktree_path 不在 sessions 树里：默认 `~/.openprogram/worktrees/<id>-<slug>/`（独立目录，跟 source_repo 平级） |
| **D5 source** | fn-form 传的 `_work_dir` 当 source_repo；agent 也可显式传 |
| **D6 安全** | bash 的 cwd 起点是 worktree_path；edit/write 收到 worktree 之外的绝对路径写 warning 不阻止 |
| **D7 commit** | agent 自己用 bash 跑 `git add . && git commit -m "..."`；worktree_merge 前要求 worktree clean |
| **D8 merge** | ff-only 默认；source_repo HEAD 未动则 ff 成功 |
| **D9 discard** | 不走 |
| **D10 task** | 不走 task（直接在主 turn 跑） |
| **D11 commit log** | bash/edit 的工具 item 都标 `worktree_id`；merge 写一条 system marker |
| **D12 工具** | worktree_create → 干活 → worktree_merge |
| **D13 UI** | composer 显 chip "wt_abc1234 (2 files changed)"，merge 后 chip 消失，DAG 加 marker |
| **D14 边界** | source_repo 不是 git repo 时 worktree_create 报错；用户先 git init |
| **D15 task** | N/A |

### 场景 B: 单 agent 多次 worktree（探索失败）

agent 试方案 A 跑测试不过 → discard → 试方案 B → 通过 → merge。

| 维度 | 设计 |
|---|---|
| **D1 实体** | 两条 worktree 记录（不同 id / branch / path）。第一条 status=discarded，第二条 status=merged |
| **D2 cwd** | 任意时刻只有一个 active：discard 完第一条才能 create 第二条；ContextVar 切换由 dispatcher 在 turn 边界做 |
| **D3 状态** | wt1: active → discarded；wt2: active → merged |
| **D4 隔离** | 两个 worktree 各自独立目录 |
| **D5 source** | 同一 source_repo，两次复用 |
| **D6 安全** | 同 A |
| **D7 commit** | wt1 里 agent 可能跑了几次 commit，discard 时随分支一起删；wt2 commit 走正常 merge |
| **D8 merge** | wt2 走 ff-only；如果 wt1 期间 source_repo 没动（只是 worktree 自己改），ff 成功 |
| **D9 discard** | wt1 `force=True`（agent 决定不要这条线了，包括 uncommitted 实验） |
| **D10 task** | 不走 |
| **D11 commit log** | DAG 上 wt1 marker（create + discard）+ wt2 marker（create + merge）|
| **D12 工具** | create → discard → create → merge |
| **D13 UI** | chip 切换两次：wt1 显示后消失，wt2 显示后消失 |
| **D14 边界** | wt1 discard 时 force=True 跳过 dirty 检查 |
| **D15 task** | N/A |

### 场景 C: 并发 worktree（plan agent 分发 3 个 task）

plan agent 列 3 个独立改动 → 3 个 async task，每个 task 一个 worktree
（独立 source_repo 副本）→ 全跑完 → plan agent 看结果挑一个 merge，其余 discard。

| 维度 | 设计 |
|---|---|
| **D1 实体** | 3 条 worktree 记录，每条绑定一个 task_id；status 同步演变 |
| **D2 cwd** | 每个 task 内部跑时，**task runtime 的 ContextVar** 独立设置 `_current_worktree_path=task.worktree_path`；主 session 的 plan agent 自己不 active 任何 worktree（plan agent 不动文件） |
| **D3 状态** | 3 条并行 active → 任务全完 → 2 条 discarded + 1 条 merged |
| **D4 隔离** | 每条 worktree 独立目录；source_repo 都指向同一个，但 git worktree add 本来就支持同时多 worktree（不同 branch）|
| **D5 source** | 全部同一 source_repo |
| **D6 安全** | 每个 task 隔离 cwd，互不影响 |
| **D7 commit** | 每个 task 自己 commit |
| **D8 merge** | 挑中的那个走 ff-only；如果其他 task 都没 merge 过，source_repo HEAD 没动，ff 成功 |
| **D9 discard** | 其余 2 个走 force=True（plan agent 选了 1，剩下的不再要） |
| **D10 task** | 每个 task 创建时分配 worktree；task complete 不自动 merge（D10），等 plan agent 决策 |
| **D11 commit log** | 3 条 worktree 都各自产生 marker；plan agent 写一段 assistant 解释"采用方案 2"|
| **D12 工具** | plan agent 调 worktree_list 看 3 条；调 worktree_merge wt2 + worktree_discard wt1 wt3 |
| **D13 UI** | composer chip 是 plan agent 自己的 session，不显示子 worktree；task panel 里每个 task 卡片显示自己的 worktree chip |
| **D14 边界** | 3 个 worktree 同时 create 时 git worktree add 互斥锁（git 自己有 lockfile）|
| **D15 task** | 完整接入：task 创建→worktree 分配；task cancel→discard；task complete→等决策 |

### 场景 D: 长时间 worktree / 用户接管

agent 跑到一半（worktree 里 commit 了 5 个 patch），用户决定自己接手。

| 维度 | 设计 |
|---|---|
| **D1 实体** | 状态从 active → kept |
| **D2 cwd** | 用户点 "Keep & detach" 后，session 的 active_worktree_id 清空，ContextVar 不再设；后续 agent turn 回到 source_repo 当 cwd |
| **D3 状态** | active → kept |
| **D4 隔离** | 不动 |
| **D5 source** | 不动 |
| **D6 安全** | worktree 还在磁盘上，但 OpenProgram 不再写它；用户在自己的 terminal / IDE 打开 worktree_path 继续干 |
| **D7 commit** | agent 之前的 commit 都保留在 branch 上 |
| **D8 merge** | 不走（用户自己决定 merge / rebase） |
| **D9 discard** | 不走 |
| **D10 task** | 如果是 task 持有的 worktree，task 也同步进 `kept` 状态（task 不再写日志，但记录已保留） |
| **D11 commit log** | 写一条 system marker "Worktree wt_xxx kept for manual handover at <path>"，agent 之后看 ContextCommit 知道有这件事 |
| **D12 工具** | UI 直接调 ws action（不是 agent 工具）`worktree_keep(worktree_id)`；agent 工具也可以暴露 worktree_keep，但低优先级 |
| **D13 UI** | chip 改 "kept — open in editor"，点击复制路径 |
| **D14 边界** | 用户后续把 worktree 目录手动删了 → 下次 OpenProgram 启动 list_worktrees 探测 path 不存在 → 标记 discarded（D14）|
| **D15 task** | task 也进 detached 状态，不影响新 task |

---

## Part 3. 现状 vs 目标

| 能力 | 现状 | 目标 | 差距 |
|---|---|---|---|
| 用户真实 repo 的 worktree 隔离 | 无 | 完整 create/merge/discard | 大 |
| Agent cwd 绑定 worktree | 无（runtime 走 session-git workdir/） | ContextVar 切换 | 中 |
| Bash 工具传 cwd | LocalBackend 接收但 bash 函数没传 | 走 ContextVar | 小 |
| Edit/Write/Read 校验 worktree 边界 | 无（只校验绝对路径） | warning 不阻止 | 小 |
| Worktree 状态机持久化 | 无 | worktrees/<id>.json 在 session-git | 中 |
| UI worktree chip | 无 | composer 顶部 chip + panel | 中 |
| Worktree × Task 整合 | 无（task 系统本身在设计中） | task cancel 自动 discard | 中（依赖 async-task） |
| Sub-agent worktree 历史代码 | 已重构掉（commit `75e430c0`） | 不复用 | N/A |

---

## Part 4. 改动清单

按依赖顺序：

| 步骤 | 文件 | 主要改动 |
|---|---|---|
| 1 | 新建 `openprogram/worktree/types.py` | `Worktree` dataclass + `WorktreeStatus` Enum + 序列化 |
| 2 | 新建 `openprogram/worktree/manager.py` | `WorktreeManager`：create / merge / discard / list / keep；底层 `subprocess.run(["git", "worktree", ...])`；持久化到 `<session-repo>/worktrees/<id>.json` |
| 3 | 新建 `openprogram/worktree/_paths.py` | worktree path 策略：`~/.openprogram/worktrees/<id>-<slug>/`；隔离校验（D4）|
| 4 | 改 `openprogram/agent/_workdir.py` | `apply_default_workdir` 优先返回 active worktree path |
| 5 | 改 `openprogram/agent/dispatcher.py` | turn 开始时读 session.meta.active_worktree_id → 设 `_current_worktree_path` ContextVar |
| 6 | 改 `openprogram/functions/tools/bash/bash.py` | 调 `backend.run(cmd, cwd=_current_worktree_path.get())` |
| 7 | 改 `openprogram/functions/tools/edit/edit.py` + write/read | warning when path outside worktree（D6）|
| 8 | 新建 `openprogram/functions/tools/worktree/` | 4 个 @function 工具：worktree_create / worktree_merge / worktree_discard / worktree_list；走 WorktreeManager |
| 9 | 改 `openprogram/store/session_store.py` | session.meta 加 `active_worktree_id` 字段；helper `set_active_worktree` / `get_active_worktree` |
| 10 | 新建 `openprogram/webui/ws_actions/worktree.py` | `list_worktrees` / `keep_worktree` / `discard_worktree`（用户手动 UI 操作）|
| 11 | 新建 `web/components/chat/composer/worktree-chip.tsx` | chip 组件 + hover panel + Merge/Discard/Keep 按钮 |
| 12 | 改 `web/components/chat/composer/composer.tsx` | 引入 chip |
| 13 | 改 ContextCommit item metadata 渲染 | 工具调用 item 显示 worktree_id 角标 |
| 14 | 改 `openprogram/agent/dispatcher.py` 写 marker | worktree_create / merge / discard 写 system 节点进 ContextCommit |
| 15 | （依赖 async-task）`openprogram/tasks/lifecycle.py` 接 hook | task cancel → `WorktreeManager.on_task_cancel`；task create 可选 attach worktree |
| 16 | Tests | unit: WorktreeManager（create/merge/discard 路径校验、隔离校验）；integration: agent in worktree → merge 全流程 |

---

## Part 5. 关键不变式

实施时必须每条都校验：

1. **worktree_path 永远不在 `~/.openprogram/sessions/` 子树里**
   （隔离 OpenProgram 自己的 git，违反则 worktree_create 拒绝）。

2. **discard 后主仓库零改动**
   `git worktree remove --force` + `git branch -D` 不动 source_repo 的 HEAD
   和 working tree。校验：discard 前后 `git rev-parse HEAD` 一致。

3. **merge 失败时 worktree 不自动消失**
   merge_conflict / not_fast_forward 错误后，worktree status 回 `active`，
   目录保留，让 agent 或用户手动看。

4. **同一 session 同一时刻最多一个 active worktree**
   session.meta.active_worktree_id 是单值；worktree_create 时若已有 active，
   报错 `already_active`，提示先 merge/discard/keep。

5. **bash 命令的 cwd 起点永远是 active worktree_path**（若存在）
   而不是 session-git workdir/；多次 bash 调用之间不持久 shell state，每条
   都是新 subprocess，cwd 都重置回 worktree_path。

6. **worktree_id 在工具 item metadata 里出现 = 这次工具调用在那个 worktree 内执行**
   ContextCommit 读取时能据此区分"在哪儿改的"。

7. **kept worktree 的 branch 不删**
   只解绑 OpenProgram 的引用；用户的 git 仓库里还能 `git checkout` 到这个 branch。

---

## Part 6. 不在本设计范围

- **远程 push**：worktree 只本地；要把 worktree branch 推 origin，agent 自己用
  bash 跑 `git push -u origin <branch>`。worktree_merge 也不做 push。
- **worktree 间 cherry-pick / rebase**：复杂语义，留给 agent 自己用 bash 处理。
- **冲突 resolution UI**：merge 冲突时 OpenProgram 不提供可视化 mergetool；
  让 agent 用 edit 改文件 / bash 跑 git mergetool。
- **跨 source_repo 的 worktree**：一个 worktree 必然对应一个 source_repo；不支持
  把 worktree 改动 merge 到另一个仓库（要做就用 bash 跑 git patch 流程）。
- **discard 前的自动备份**：D9 提到的 `~/.openprogram/discarded/` 打包，留作未来加强。
- **chroot / namespace 真 sandbox**：D6 是"默认 cwd 锁定"不是 sandbox；硬隔离走
  docker backend。
- **session 关闭时自动清理 active worktree**：保留 active worktree 跨 session
  重启（重启后 list_worktrees 探测，仍 active 的标 kept 让用户手动处理）。
