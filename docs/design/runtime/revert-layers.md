# 三层回退/记录机制 — 快照 / Project-Git / Worktree 的组合设计

> 状态: **已实现** (2026-05)。统一规划 agent 改文件后的**回退**与**记录**。
> 关联: [`agent-worktree.md`](agent-worktree.md)、[`memory-v2.md`](../memory/memory-v2.md)
> (实体层)、[`git-as-entity-memory.md`](../memory/git-as-entity-memory.md)。
> 代码: `store/file_backup/`、`store/project_commit.py`、`store/project_store.py`、
> `store/read_tracking.py`、`agent/_revert.py`、`worktree/`。

## 0. 一句话

agent 改用户文件这件事,有**三层**机制,按"作用域 / 持久度 / 风险"从低到高排成一条梯子,各司其职;外加一道**并发防护 (read-before-edit)** 守在所有写操作前面,保证落进快照和 git 的永远是干净的 agent 改动。

```
                 ┌─── read-before-edit (并发防护, 所有写操作的前置闸) ───┐
                 │  agent 写前: 没读过 / 读后磁盘变了 → 拒绝, 让它重读     │
                 └───────────────────────────┬──────────────────────────┘
                                              ▼
低风险 / 临时 ────────────────────────────────────► 高风险 / 持久 / 隔离

①  快照 (file_backup)         ②  Project-Git commit        ③  Worktree
   "撤销键 (Ctrl+Z)"             "存档点 + 永久历史"           "草稿分支 / 沙盒"
   turn 级, 临时, 不碰 git       会话累积, 永久, 写 git         一段实验, 隔离副本
   永远开, 自动                  默认开, 自动 (可关)            agent 显式进入
```

| | ① 快照 | ② Project-Git | ③ Worktree |
|---|---|---|---|
| **回答的问题** | "撤销 agent 刚才这一步" | "永久记录改了啥, 能 log/revert" | "高风险大改, 别碰我工作树" |
| **机制** | 全量文件拷贝 | `git commit` | `git worktree` 隔离分支 |
| **作用域** | 单个 turn | 整条会话累积 | 一段实验性工作 |
| **持久度** | 临时 (GC, 上限 100 turn) | 永久 (git 历史) | 直到 merge / discard |
| **碰用户的 git 吗** | **完全不碰** | 写 commit (首次会自动 init, 见规则 A) | 用独立 worktree, merge 才回主线 |
| **谁触发** | 自动 (每次编辑前) | turn 结束 (默认开) | agent 显式调 `worktree_create` |
| **默认** | **一直开** | **默认开** | 按需 |
| **代码** | `store/file_backup/` | `store/project_commit.py` | `worktree/` |
| **回退入口** | WS `revert_turn` (快照 **+** git 一起退) | 同左 | `worktree_discard` |

**对标 Claude Code**: Claude Code 做了 ①(per-prompt 文件快照 checkpoint, 不碰 git)+ read-before-edit 防护, 但**没有** ②③ —— 你在 Claude Code 里看不到 agent 改动的长期 git 历史。本设计在 Claude Code 的体验之上叠加了 git 记录 (②) 和隔离沙盒 (③)。

## 0.5 并发防护: read-before-edit (前置闸)

`store/read_tracking.py`。这是整套的安全地基: 它保证 agent 永远不会在"用户刚改过、agent 还没看到"的文件上盲写, 于是落进 ① 快照和 ② commit 的每一笔都是**干净的 agent 改动**, 回退时才不会误伤用户。

照搬 Claude Code 的 Edit/Write 契约:
- **`read` 记基线** —— 读文件时记下它的指纹 `(mtime_ns, size, sha1)`。
- **写前校验** —— `edit` / `write 覆盖已有文件` / `apply_patch Update` 写之前比对:
  - 没读过 (`NEVER_READ`) → 拒绝, 提示先读。
  - 读过但磁盘变了 (`STALE`, 用户/linter/别的进程改了) → 拒绝, 提示重读。改动**不落盘**, 什么都没覆盖。
- **新文件跳过** —— `write 新文件` / `apply_patch Add` 不要求先读 (没法读不存在的文件), 写完记基线。
- **写成功刷新基线** —— 同一文件能接着改, 不用重读。

用**内容 hash** 而非只看 mtime: 用户手速快时改动可能落在同一个 mtime tick 里, 光看时间戳会漏。session 经 `_store` ContextVar 解析, 不在 turn 里 (单测/独立调用) 时整个防护 no-op (`UNTRACKED` → 放行)。

**跨文件 vs 同文件**: 用户改 A、agent 改 B (不同文件) → 撤回天然安全, 互不相干。用户和 agent 改**同一个文件** → 正是 read-before-edit 在源头拦掉的场景。

## 1. 三者的关系: 谁正交、谁重叠

### 1.1 Worktree 是正交的 (平时不在场)

Worktree **只在 agent 显式调 `worktree_create` 时存在**, 走独立目录 `~/.openprogram/worktrees/<id>/`, 有自己的生命周期 (active→merged/discarded/kept), 跟 ①② 零交叉引用。它是"agent 主动进入的隔离沙盒", 用户日常碰不到。**所以它跟 ①② 不冲突。** 唯一需要协调的点见 §3 规则 B。

### 1.2 快照 ↔ Project-Git: 由 `revert_turn` 统一协调

两者都管"agent 改的文件" (① 拷贝盖回、② 写 git 历史)。如果回退时只动一个, 就会出现"文件回旧了但 git 里 commit 还在"这种历史与工作树脱节。**解决: `revert_turn` 把两者一起退** —— git 先退 (reset/revert), 快照只在 git 退不了时兜底。见 §3 规则 C。

## 2. 完整生命周期: 什么时候快照、什么时候释放、什么时候 commit

以一条**绑定了真实项目目录**的会话为例 (ad-hoc 会话见 §4):

```
会话开始
  │
  ├─ [turn N 开始]
  │     ② snapshot_baseline()         ← 记进入 turn 前哪些文件已脏 (区分用户的活儿)
  │                                     非 git 仓且自动提交开 → 此刻安全 auto-init
  │                                     (基线提交用户已有文件, 见规则 A)
  │
  ├─ agent 要编辑 a.py
  │     [前置闸] read-before-edit 校验 a.py 新鲜度 (没读过/已变 → 拒绝)
  │     ① backup_before_edit(turn=N, a.py)   ← 编辑前拷旧内容进 <session>/file_backups/N/
  │     ↓ 真正写 a.py → 刷新 read 基线
  │
  ├─ [turn N 结束]
  │     · session-git commit (对话 DAG)              ← 一直发生
  │     · ② commit_turn_changes():                  ← 默认开
  │         - 有活跃 worktree? → 跳过 (规则 B)
  │         - 非 git 仓? → 已在 turn 开始 auto-init (或被重目录挡, 警告)
  │         - 用户 baseline 还脏? → SKIPPED_DIRTY + 警告 (Strategy A)
  │         - 否则 git commit "[agent <sid>] <msg>", sha 记到 assistant 节点
  │           metadata.project_commit = {repo, sha}
  │     · GC: gc_evict_old(session) ← 删超过上限的旧快照
  │
  ├─ 用户点"撤销 turn N" (WS revert_turn)  → 见 §3 规则 C (git + 快照一起退)
  │
  └─ ……
```

### 2.1 快照的释放 ✅

快照存 `<session>/file_backups/<turn_id>/`, 释放有两个层级:

| 触发 | 实现 |
|---|---|
| **GC (软上限 100 turn)** | `gc_evict_old` 在**每个 turn 结束**由 dispatcher 调用, 按 mtime 删最老的超额 turn。✅ (此前定义了没人调 → 已接上) |
| **会话删除** | 会话仓整个删掉时连带删除 (file_backups 在会话仓内) ✅ |
| **commit 成功后** | **不释放** —— 快照与 commit 各司其职: 快照是"最近 N 个 turn 的快速撤销 + gitignored/非 git 兜底", git 是长期历史。两者不互相删。 |

> 注: 快照是**全量文件拷贝** (`shutil.copy2`), 故意不用 hardlink (agent 的 `open(w)` 会 truncate inode, 共享 hardlink 会丢原内容)。磁盘成本线性于 files×turns, 由 GC 上限兜底。

## 3. 三条协调规则

### 规则 A: 默认 auto-init, 但安全 (`project_auto_commit` 默认开)

**绑定文件夹本身零 git 副作用** —— `resolve_project` 不 init, 只放一个 `.openprogram/.gitignore` (`*`, 隐藏我们自己的足迹) 。git 工作推迟到 turn 结束:

- 文件夹**已是 git 仓** → agent 改了文件就 commit。
- 文件夹**不是 git 仓** → 第一次 agent 改文件时**自动 init** (`auto_init_for_agent`), 安全两点:
  1. **先提基线**: init 发生在 **turn 开始** (`snapshot_baseline`, 唯一 pre-agent 的时刻), 把用户已有文件提一笔 `openprogram: baseline (pre-existing files)`。然后 agent 的改动作为**干净 diff** 提在上面 —— 不会变成"agent 一把 add 了你整个项目"。
  2. **重目录护栏**: 有 `node_modules`/`.venv`/`target`/`dist`/… 时**拒绝 init** (否则首笔 commit 几个 G), 发 `autoinit_blocked` 提示用户自己 `git init` + `.gitignore`。

效果: 打开任何文件夹用, agent 一改就有 git 记录, 不用手动 init; 但只在 agent 真改了文件时才出现 `.git`, 光看不改不会凭空多出仓。三套对"碰 git"的态度一致 —— 默认开 + 安全, 用户始终能自己掌控 (`project_auto_commit: false` / env `OPENPROGRAM_PROJECT_AUTOCOMMIT=0` 关掉)。

### 规则 B: Worktree 活跃时, Project-Git commit 让位

`snapshot_baseline` 和 `commit_turn_changes` 都先查 `find_active_for_session(sid)`:
- 有活跃 worktree → ② **直接跳过** (agent 改动在 worktree 副本里, 提交用户原始目录是错的/空的; worktree 有自己的 merge/discard 生命周期, 自动提交不该插手)。
- 无 → ② 照常。

### 规则 C: 撤回 = 快照 + git 一起退 (智能选 reset/revert)

`revert_turn` (`agent/_revert.py`) 撤一个 turn 时:

1. **先 git 退** (若该 turn 有 `metadata.project_commit`): `ProjectGit.revert_agent_commit(sha)` **自动选最安全的操作**:
   - **`reset`** (`git reset --hard <sha>^`) —— **仅当**该 commit 是 HEAD **且** 工作树干净 **且** 没 push。最干净: commit 像没发生过。日常"我反悔了 agent 上一轮"就走这条。
   - **`revert`** (`git revert`, 追加反向 commit) —— reset 不安全时 (commit 不在顶/用户在它之上提过/已 push)。**绝不改写已发布历史, 绝不动用户自己的 commit。**
   - **`skipped`** —— revert 撞冲突 → 干净 abort, git 不动, 交给快照兜底。
   - **`absent`** —— sha 不在仓里 → no-op。
2. **再快照兜底**: git 成功做了 reset/revert 时**跳过快照** (git 已设好文件状态; 尤其 revert 保留了用户后续 commit 的情况, 快照再盖会破坏)。只有 git skipped/absent/无 commit 时, 快照才是实际执行回退的那个 —— 也是 gitignored 文件 / 非 git 文件夹的唯一兜底。

**为什么不无脑 reset**: reset 会丢"撤回点之后用户自己提的 commit / 工作树里没提交的改动"。三个判断 (是不是 HEAD、干净不干净、push 没 push) 把"会害到用户"的场景准确识别出来, 自动退化成 revert。安全时给干净, 危险时保命, 机器自己判断。

## 4. Ad-hoc (默认项目) 会话

没绑真实目录的随手聊:
- ① 快照: 照常 (agent 在会话 `workdir/` 里改文件也快照)。
- ② Project-Git: **不适用** (默认项目是逻辑标签, 无真实 repo)。
- ③ Worktree: 不适用 (没有 source repo)。

所以 ad-hoc 会话只有 ① 一层, 回退永远走快照。简单、无歧义。

## 5. 决策矩阵 (用户视角)

| 我想要 | 配置 | 得到 |
|---|---|---|
| 像 Claude Code, 撤销键就够 | (目录非 git 仓时) | ① 快照 |
| agent 改动的永久 git 记录 / `git log` / `revert` | 默认 (目录是 git 仓, 或允许 auto-init) | ① + ②, 撤回时一起退 |
| 不想 agent 碰我的 git | `project_auto_commit: false` | 只有 ① |
| agent 做高风险大改, 别弄乱工作树 | (agent 自行) `worktree_create` | ③ 隔离, 改好 merge / 改砸 discard |
| 目录有 node_modules 等重目录 | — | ① 快照; ② 被护栏挡 + 提示自己 init |

## 6. 实施状态 ✅

| # | 项 | 状态 |
|---|---|---|
| 1 | ① 快照写入 + `revert_turn` 回退 | ✅ |
| 2 | ② Project-Git commit + Strategy A + 开关 | ✅ |
| 3 | ③ Worktree create/merge/discard + 工具 | ✅ |
| 4 | 接上 GC (`gc_evict_old` 每 turn 末调用) | ✅ |
| 5 | 修 `gc.py` docstring (hardlink → 全量拷贝) | ✅ |
| 6 | 规则 A: 绑定零副作用 + 默认开 + 安全 auto-init (基线优先 + 重目录护栏) | ✅ |
| 7 | 规则 B: worktree 活跃时 ② 让位 | ✅ |
| 8 | 规则 C: `revert_turn` 同步 git, 智能选 reset/revert | ✅ |
| 9 | read-before-edit 并发防护 (前置闸) | ✅ |
| 10 | UI 明示当前会话的"主回退路径" (快照 / worktree) | ⏳ 未做 (后端就绪, 待前端) |

**冒烟测试**: `scripts/smoke_entity_layer.py` (实体层 + 快照 GC + auto-init + 规则 A/B)、
`scripts/smoke_read_before_edit.py` (并发防护, 13 项)、`scripts/smoke_revert_ux.py`
(reset/revert/absent + revert_turn 端到端, 11 项)。均在隔离 profile 跑, 全过。

## 7. 与 Claude Code 的差异小结

| | Claude Code | 本设计 |
|---|---|---|
| 撤销 (文件) | checkpoint 快照, per-prompt | ① 快照, per-turn |
| 并发防护 | read-before-edit | read-before-edit (同) |
| git 记录 | ❌ 无 (checkpoint 独立于 git) | ② 默认开, 自动 init, 撤回同步 git |
| 隔离沙盒 | `--worktree` 纯隔离, merge 归用户 | ③ 框架管 merge/discard 生命周期 |
| 撤回粒度 | 回到某条消息之前 (时间倒流) | 单 turn 回退 (`revert_turn`) |

普通用户得到 ≈ Claude Code 的简洁 (快照 + 并发防护); 想要 git 工作流 / 高风险隔离的进阶用户额外得到 ②③。

---

## 8. 行业对比 — 文件修改管理策略

各框架按**架构路线**分为两派：快照派（在宿主机上操作，通过备份/git 提供回滚）和沙箱派（在隔离环境中操作，丢弃环境即回滚）。

### 8.1 快照派

在用户的真实文件系统上工作，通过某种形式的备份提供回滚能力。

| 框架 | 备份机制 | 回滚方式 | bash 覆盖 | 独立于用户 git |
|---|---|---|---|---|
| **Hermes** | shadow git checkpoint（所有工具执行前统一 checkpoint 到 `~/.hermes/checkpoints/`） | `/rollback N` + 单文件恢复 + diff 预览 | **是**（统一入口，含 bash） | **是**（shadow store，不碰用户 `.git`） |
| **Claude Code** | per-response 文件快照（增量，存 `~/.claude/file-history/`） | `/rewind` 按检查点回退 | **否**（只追踪 Write/Edit/NotebookEdit） | **是** |
| **Cursor** | per-edit checkpoint（存本地隐藏目录） | "Discard to checkpoint" 按钮 | Agent 操作覆盖，用户终端不覆盖 | **是** |
| **Aider** | 每次 AI 编辑自动 `git commit`（Conventional Commits 格式） | `/undo` = 回退最近一次 aider commit | **是**（git 天然追踪一切） | **否**（直接用用户 git，会污染 git 历史） |
| **opencode** | git tree object 快照 | `/undo` + `/redo`（已知 bug：文件变更与对话状态脱节） | 是（git 兜底） | **否** |
| **OpenProgram（我们）** | per-turn BackupStore（write/edit/apply_patch 三个工具） + Project-Git commit + Worktree 隔离 | `revert_turn` git-aware（智能选 reset/revert） + 快照兜底 | **否**（bash 不追踪，见 §9） | **是**（BackupStore 独立；Project-Git 写用户 git 但可关） |

### 8.2 沙箱派

在隔离环境（容器/云端）中工作，天然解决文件追踪问题。

| 框架 | 隔离机制 | 回滚方式 | bash 覆盖 | 产出交付 |
|---|---|---|---|---|
| **OpenHands** | Docker 容器（V1 支持 Docker/Local/Remote 三种 runtime） | 丢弃容器；完成后提取 `git_patch` | **天然全覆盖** | git patch 选择性 apply |
| **SWE-agent** | Docker 沙箱（SWEEnv） | 丢弃沙箱 | **天然全覆盖** | git diff 提取 |
| **Devin** | 云端 ephemeral 沙箱（per-session，含 shell/编辑器/浏览器） | 沙箱临时，不影响本地 | **天然全覆盖** | PR 形式提交 |
| **OpenClaw** | Docker/Podman 容器（`agents/sandbox/`），有 workspace mount、fs-bridge、网络隔离 | 容器隔离 | **天然全覆盖** | 容器内操作完提取 |

### 8.3 术语解释

| 术语 | 含义 |
|---|---|
| **快照** | 在修改文件前记录原始状态（备份原文件或 git commit），改坏了从备份恢复。agent 直接操作真实文件。 |
| **沙箱** | 在隔离环境（容器/虚拟机）中执行，agent 操作的是副本，改坏了丢掉整个环境，宿主机不受影响。 |
| **bash 覆盖** | agent 通过 bash 工具改文件（`sed -i`、`> file`、`rm` 等）时，这些变更能否被追踪和回滚。快照派的核心难题。 |
| **独立于用户 git** | 备份机制是否使用自己的存储，不污染用户的 git 历史（`git log` 不会出现 AI 自动 commit）。 |
| **shadow git** | Hermes 的做法：用 git 的 tree/commit 机制存快照，但存在独立的 shadow store 目录，不碰用户的 `.git`。兼顾 git 的追踪能力和不污染用户历史。 |

### 8.4 两派取舍

| | 快照派 | 沙箱派 |
|---|---|---|
| **优点** | 零启动延迟；用户直接操作文件；交互体验好 | 天然全覆盖（含 bash）；安全隔离；无副作用泄漏 |
| **缺点** | bash 改文件难追踪（除非用 git 兜底或统一入口 checkpoint） | 启动延迟；环境配置复杂；资源占用大 |
| **适合** | 本地交互式开发 | 无人值守批量任务、不信任的代码执行 |

### 8.5 我们的定位

OpenProgram 既有交互式聊天（CLI/webui），又有 agentic function 无人值守执行（research_agent 等长时间运行）。因此：

- **交互式场景**：走快照派（①② 已实现），补 bash 盲点（见 §9）
- **无人值守场景**：③ Worktree 已提供轻量隔离；完整沙箱（Docker 容器）作为远期方向

---

## 9. 已知缺口：bash 工具文件修改不追踪

### 9.1 问题

write / edit / apply_patch 三个编辑工具在修改文件前通过 `backup_for_current_turn` 做快照备份。但 bash 工具执行的命令（`sed -i`、`> file`、`rm`、`mv` 等）改文件时**完全不追踪**，没有备份也无法通过快照回滚。

`functions/tools/bash/bash.py:38` 有 TODO 承认此问题，原因是精确追踪 bash 命令需要解析命令语义或 LD_PRELOAD，复杂度高。

### 9.2 行业现状

**这是行业共同缺口**——Claude Code、Cursor 都没做。只有两种方案真正覆盖了 bash：

1. **Hermes 的统一入口 checkpoint**：在所有工具执行前（不区分工具类型）做 checkpoint，bash 自然覆盖。
2. **Aider 的纯 git 方案**：直接用用户 git，`/undo` = git 回退，git 天然追踪一切。但会污染用户 git 历史。
3. **沙箱派**：容器隔离天然覆盖，但代价大。

### 9.3 推荐方案

借鉴 Hermes：把 checkpoint 触发从"编辑工具内部调用 `backup_for_current_turn`"提升到"工具执行统一入口 `_execute_tool_calls`"。具体：

1. 在 `agent_loop.py` 的 `_execute_tool_calls`（所有工具的单一入口）中，**每个工具执行前**检查该工具是否可能改文件（bash / write / edit / apply_patch），如果是则先对工作目录做快照
2. bash 的快照不能只备份单个文件（不知道 bash 会改哪个文件），需要改成 **per-turn 的工作目录级快照**——但全目录快照太重
3. 实际可行的折中：bash 执行前后对工作目录做 `git status --short` diff，记录哪些文件变了，然后对变了的文件补做快照备份。这样：
   - 在 git 仓库中：git 天然追踪变更（② Project-Git commit 会在 turn 结束时 commit，包含 bash 改动）
   - 不在 git 仓库中：bash 前后 diff 文件列表，对变更文件补做 `backup_before_edit`

### 9.4 实施优先级

**中优先级**。当前 bash 改文件的场景不多（大部分文件编辑走 write/edit），且 ② Project-Git commit 已经在 turn 结束时覆盖了 bash 改动的 git 记录。真正缺的是"bash 改了非 git 仓库里的文件时无法撤销"这个边缘场景。
