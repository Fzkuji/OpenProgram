# 文件修改管理 — 行业分析与 OpenProgram 设计

> 状态: **已实现** (2026-06)。Checkpoint + Shadow git 替代 Project-Git。
> 关联: [`agent-worktree.md`](agent-worktree.md)、[`memory-v2.md`](../memory/memory-v2.md)
> (实体层)、[`git-as-entity-memory.md`](../memory/git-as-entity-memory.md)。
> 代码: `store/snapshot/checkpoint/`（已从 `file_backup/` 改名）、
> `store/shadow_git/`（新）、~~`store/project/project_commit.py`~~（废弃，默认关闭）、
> `store/read_tracking.py`、`agent/_revert.py`、`worktree/`。

---

## 1. 两大路线：快照 vs 沙箱

AI coding agent 管理文件修改，行业里分成两条路线：

| | 快照 (Snapshot) | 沙箱 (Sandbox) |
|---|---|---|
| **做法** | agent 直接操作用户的真实文件，修改前记录原始状态（备份文件或 git commit），改坏了从备份恢复 | agent 在隔离环境（容器/虚拟机）中操作副本，改坏了丢掉整个环境，宿主机不受影响 |
| **回滚** | 从备份恢复文件 | 丢弃容器/环境 |
| **优点** | 零启动延迟；用户直接操作文件；交互体验好 | 天然全覆盖（含 bash）；安全隔离；无副作用泄漏 |
| **缺点** | bash 改文件难追踪（除非用 git 兜底或统一入口 checkpoint） | 启动延迟；环境配置复杂；资源占用大 |
| **适合** | 本地交互式开发 | 无人值守批量任务、不信任的代码执行 |

**不是二选一。** 头部框架已经往两者并存的方向走——快照解决日常回滚体验（秒级恢复），沙箱解决安全隔离（bash 全覆盖）。

---

## 2. 行业对比

### 2.1 全景对比

| 框架 | 快照机制 | 沙箱机制 | bash 覆盖 | 独立于用户 git |
|---|---|---|---|---|
| **Claude Code** | per-response 快照，`/rewind` 回退 | `/sandbox` 限制 bash 写入范围 | 快照不覆盖；沙箱限制写入范围 | 是 |
| **Cursor** | per-edit checkpoint | Seatbelt/Landlock/seccomp 系统级沙箱 | Agent 操作覆盖 | 是 |
| **Hermes** | shadow git checkpoint，所有工具执行前统一 checkpoint | 无 | **是**（统一入口，含 bash） | 是（shadow store） |
| **Aider** | 每次编辑自动 git commit | 无 | **是**（git 天然覆盖一切） | 否（污染用户 git） |
| **opencode** | git tree object 快照 | 无 | 是（git 兜底） | 否 |
| **OpenHands** | event-sourcing 支持回放 | Docker 容器（主要模式） | 沙箱内全覆盖 | N/A |
| **SWE-agent** | 无 | Docker 沙箱（SWEEnv） | 沙箱内全覆盖 | N/A |
| **Devin** | 无 | 云端 ephemeral 沙箱 | 沙箱内全覆盖 | N/A |
| **OpenClaw** | 无内置快照（本地模式） | Docker/Podman 容器 | 沙箱内全覆盖 | N/A |

### 2.2 快照的三种实现方式

行业里快照机制有三种具体做法：

| 方式 | 做法 | 代表 | bash 覆盖 | 独立于用户 git |
|---|---|---|---|---|
| **文件拷贝** | 改文件前 `shutil.copy2` 到备份目录 | Claude Code、Cursor | 否（只能备份已知文件） | 是 |
| **Git commit** | 每次编辑后 `git commit` 到用户仓库 | Aider、opencode | 是（git 追踪一切） | **否**（污染用户 git） |
| **Shadow git** | 用 git tree/commit 对象存快照，存在独立目录 | Hermes | 是（统一入口触发） | 是 |

三种可以共用——它们解决的问题不同：

- 文件拷贝：最快的撤销（不依赖 git，非 git 项目也能用）
- Git commit：永久历史（`git log` 可查，但污染用户 git）
- Shadow git：兼顾 diff 追溯能力和不碰用户 git

### 2.3 术语解释

| 术语 | 含义 |
|---|---|
| **Checkpoint** | 行业通用叫法（Claude Code、Cursor、Hermes 都用），指改文件前保存的快照。等同于我们之前叫的 "BackupStore / file_backup"。 |
| **bash 覆盖** | agent 通过 bash 工具改文件（`sed -i`、`> file`、`rm` 等）时，这些变更能否被追踪和回滚。快照派的核心难题——编辑工具（write/edit）能精确知道改了哪个文件，bash 不能。 |
| **独立于用户 git** | 备份机制是否使用自己的存储，不污染用户的 git 历史。Aider 直接在用户仓库里 git commit，`git log` 会混入大量 AI 自动 commit；Hermes 和 Claude Code 用独立存储，用户 git 历史保持干净。 |
| **Shadow git** | 用 git 的 tree/commit 机制存快照，但存在独立目录（如 `~/.hermes/checkpoints/`），不碰用户的 `.git`。兼顾 git 的追踪能力（diff、单文件恢复）和不污染用户历史。 |
| **统一入口触发** | Hermes 的关键设计：不在每个编辑工具内部触发 checkpoint，而在**所有工具的执行入口**统一触发。这样 bash 执行前也会做 checkpoint，自然覆盖 bash 盲点。 |

---

## 3. OpenProgram 的方案

### 3.1 设计原则

两个核心决策：

1. **不碰用户 git**。Claude Code 和 Hermes 都不往用户仓库写 commit，这是行业趋势。Project-Git（自动往用户 git 写 commit）废弃——它污染用户 git 历史、和用户操作冲突（rebase/push）、需要复杂的协调逻辑。
2. **统一入口触发**。学 Hermes，checkpoint 在所有工具执行前统一触发，覆盖 bash。

### 3.2 三层机制

```
              ┌─── read-before-edit (并发防护, 所有写操作的前置闸) ───┐
              │  agent 写前: 没读过 / 读后磁盘变了 → 拒绝, 让它重读     │
              └───────────────────────────┬──────────────────────────┘
                                           ▼
低风险 / 临时 ───────────────────────────────────► 高风险 / 持久 / 隔离

①  Checkpoint (文件拷贝)       ②  Shadow git               ③  Worktree
   路线: 快照                     路线: 快照                    路线: 沙箱
   "撤销键 (Ctrl+Z)"             "永久历史 + diff"             "草稿分支 / 隔离沙盒"
   turn 级, 临时, 不碰 git       独立 store, 不碰用户 git       一段实验, 隔离副本
   永远开, 自动                  默认开, 自动                  agent 显式进入
```

| | ① Checkpoint | ② Shadow git | ③ Worktree |
|---|---|---|---|
| **路线** | 快照 | 快照 | 沙箱（轻量） |
| **回答的问题** | 撤销 agent 刚才这一步 | 永久记录改了啥, 能 diff/恢复 | 高风险大改, 别碰我工作树 |
| **机制** | 全量文件拷贝 | git tree/commit 对象，存在 `~/.openprogram/shadow-git/<project-hash>/` | `git worktree` 隔离分支 |
| **作用域** | 单个 turn | 整条会话累积 | 一段实验性工作 |
| **持久度** | 临时 (GC, 上限 100 turn) | 永久（独立 git 历史） | 直到 merge / discard |
| **碰用户的 git 吗** | **完全不碰** | **完全不碰** | 用独立 worktree, merge 才回主线 |
| **触发** | 统一入口（所有工具执行前） | turn 结束（自动 commit 本 turn 变更） | agent 显式调 `worktree_create` |
| **bash 覆盖** | **是**（统一入口触发） | **是**（turn 结束 commit 含 bash 改动） | N/A（隔离环境内） |
| **默认** | **一直开** | **默认开** | 按需 |
| **代码** | `store/snapshot/checkpoint/`（原 `file_backup/`） | `store/shadow_git/` | `worktree/` |
| **回退入口** | `undo`（原 `revert_turn`） | `undo` 联动 | `worktree_discard` |

### 3.3 ① Checkpoint 和 ② Shadow git 的分工

两者同时运行，不是二选一：

| 维度 | ① Checkpoint | ② Shadow git |
|---|---|---|
| **速度** | 最快（直接文件覆盖） | 较快（git checkout） |
| **diff 能力** | 无（只有原文件副本） | 有（`git diff`、`git log`、单文件恢复） |
| **非 git 项目** | 可用 | 可用（shadow store 自带 git，不依赖用户仓库） |
| **GC** | 有（上限 100 turn） | 无需 GC（git 天然压缩） |
| **主要用途** | 快速撤销的第一选择 | 永久历史追溯、diff 对比、单文件精确恢复 |

`undo` 回滚时联动：先从 checkpoint 恢复文件（最快），shadow git 记录保持可查。

### 3.4 与 ~~Project-Git~~ 的对比（为什么废弃）

| | ~~Project-Git~~（废弃） | Shadow git（替代） |
|---|---|---|
| 存哪里 | 用户的 `.git` | `~/.openprogram/shadow-git/<project-hash>/` |
| 用户 git log 可见 | 是（混入 agent commit） | 否（完全隔离） |
| 和用户操作冲突 | 可能（rebase/push/merge） | 不可能（完全独立） |
| 非 git 项目 | 需要 auto-init（创建 `.git`） | 不需要（shadow store 自带） |
| 推上远程 | agent commit 会被 push | 不会 |
| 回滚复杂度 | 高（判断 reset/revert/安全性） | 低（从 shadow store 恢复，和用户 git 无关） |
| 协调规则 | 需要规则 A（auto-init）、规则 C（智能 reset/revert） | 不需要 |

### 3.5 与 Claude Code / Hermes 的对比

| | Claude Code | Hermes | OpenProgram（目标态） |
|---|---|---|---|
| 临时备份 | checkpoint（文件拷贝） | shadow git checkpoint | ① Checkpoint（文件拷贝） |
| 永久历史 | 无（用户自己 commit） | shadow git 兼任 | ② Shadow git |
| 碰用户 git | 否 | 否 | **否**（废弃 Project-Git） |
| bash 覆盖 | 否 | 是（统一入口） | **是**（统一入口） |
| 隔离沙盒 | `/sandbox`（系统级限制） | 无 | ③ Worktree（git 级隔离） |
| 回滚命令 | `/rewind` | `/rollback N` | `/undo` |

---

## 4. 统一入口触发（覆盖 bash 的关键改动）

### 4.1 ✅ 已实现

write / edit / apply_patch 三个编辑工具**各自内部**调用 `checkpoint_before_edit`（原 `backup_for_current_turn`）做精确的单文件备份——保留不动。

bash 工具的覆盖在 `_execute_tool_calls`（`agent_loop.py`，所有工具的单一入口）中实现：

```python
# agent_loop.py — _execute_tool_calls 内部
if tool_name == "bash":
    pre_snapshot = _snapshot_cwd(cwd)       # 记录文件 mtime+size
    result = tool.execute(...)
    _checkpoint_changed_files(cwd, pre_snapshot)  # 对比，变更文件补做 checkpoint
else:
    result = tool.execute(...)               # write/edit 内部已有精确备份
```

`_snapshot_cwd`：扫描 cwd 下的文件，记录 `{path: (mtime_ns, size)}`，跳过 dotfile 目录。
`_checkpoint_changed_files`：对比前后快照，对新增/修改的文件调用 `checkpoint_before_edit`。

**已知限制**：当前快照只扫描 cwd 顶层文件，子目录中的变更暂未覆盖（可后续改为递归扫描）。

### 4.2 行业参考

这是 Hermes 的做法——在所有工具执行前统一 checkpoint，bash 自然覆盖。我们借鉴其触发策略，保持编辑工具内部的精确备份 + bash 前后 diff 补做。

---

## 5. 并发防护: read-before-edit (前置闸)

`store/read_tracking.py`。整套机制的安全地基: 保证 agent 永远不会在"用户刚改过、agent 还没看到"的文件上盲写, 于是落进 ① checkpoint 和 ② shadow git 的每一笔都是**干净的 agent 改动**, 回退时不会误伤用户。

照搬 Claude Code 的 Edit/Write 契约:
- **`read` 记基线** —— 读文件时记下它的指纹 `(mtime_ns, size, sha1)`。
- **写前校验** —— `edit` / `write 覆盖已有文件` / `apply_patch Update` 写之前比对:
  - 没读过 (`NEVER_READ`) → 拒绝, 提示先读。
  - 读过但磁盘变了 (`STALE`, 用户/linter/别的进程改了) → 拒绝, 提示重读。改动**不落盘**。
- **新文件跳过** —— `write 新文件` / `apply_patch Add` 不要求先读, 写完记基线。
- **写成功刷新基线** —— 同一文件能接着改, 不用重读。

用**内容 hash** 而非只看 mtime: 用户手速快时改动可能落在同一个 mtime tick 里, 光看时间戳会漏。session 经 `_store` ContextVar 解析, 不在 turn 里 (单测/独立调用) 时整个防护 no-op (`UNTRACKED` → 放行)。

---

## 6. 实现细节

### 6.1 协调规则

#### 规则 A: Worktree 活跃时, shadow git commit 让位

`shadow_git.commit_turn_changes` 先查 `find_active_for_session(sid)`:
- 有活跃 worktree → 跳过（agent 改动在 worktree 副本里, 提交原始目录是错的/空的）。
- 无 → 照常。

#### 规则 B: undo = checkpoint 恢复 + shadow git 保持可查

`undo`（原 `revert_turn`）撤一个 turn 时:
1. 从 checkpoint 恢复文件（最快路径）。
2. shadow git 历史**不回退**——保持可查，用户可以 diff 看 agent 改了什么。
3. gitignored 文件 / 非 git 文件夹：checkpoint 是唯一兜底。

对比 ~~旧规则 C~~（已废弃）：不再需要判断 git reset vs revert、是不是 HEAD、有没有 push。回滚逻辑大幅简化。

### 6.2 完整生命周期

以一条**绑定了真实项目目录**的会话为例:

```
会话开始
  │
  ├─ [turn N 开始]
  │
  ├─ agent 要编辑 a.py
  │     [前置闸] read-before-edit 校验 a.py 新鲜度 (没读过/已变 → 拒绝)
  │     [统一入口] checkpoint(turn=N, a.py)   ← 编辑前拷旧内容进 <session>/checkpoints/N/
  │     ↓ 真正写 a.py → 刷新 read 基线
  │
  ├─ agent 执行 bash "sed -i 's/old/new/' b.py"
  │     [统一入口] 记录工作目录文件状态 hash
  │     ↓ 真正执行 bash
  │     [统一入口] 对比 hash, b.py 变了 → 补做 checkpoint(turn=N, b.py)
  │
  ├─ [turn N 结束]
  │     · session-git commit (对话 DAG)              ← 一直发生
  │     · ② shadow git commit:                      ← 默认开
  │         - 有活跃 worktree? → 跳过 (规则 A)
  │         - 否则 commit 本 turn 所有文件变更到 shadow store
  │     · GC: gc_evict_old(session) ← 删超过上限的旧 checkpoint
  │
  ├─ 用户点"撤销 turn N" (/undo)
  │     → 规则 B: 从 checkpoint 恢复文件, shadow git 保持可查
  │
  └─ ……
```

### 6.3 Checkpoint 的释放

checkpoint 存 `<session>/checkpoints/<turn_id>/`（原 `file_backups/`），释放:

| 触发 | 实现 |
|---|---|
| **GC (软上限 100 turn)** | `gc_evict_old` 在每个 turn 结束由 dispatcher 调用, 按 mtime 删最老的超额 turn |
| **会话删除** | 会话仓整个删掉时连带删除 |

> 注: checkpoint 是**全量文件拷贝** (`shutil.copy2`), 故意不用 hardlink (agent 的 `open(w)` 会 truncate inode, 共享 hardlink 会丢原内容)。磁盘成本线性于 files×turns, 由 GC 上限兜底。

### 6.4 Shadow git 存储

存储位置: `~/.openprogram/shadow-git/<project-hash>/`

- 每个项目目录一个 shadow git store（按路径 hash 区分）
- 不碰用户的 `.git`，完全独立
- turn 结束时自动 commit 本 turn 所有文件变更
- 支持 `git diff`、`git log`、单文件恢复
- 不需要 GC（git 天然压缩对象）

### 6.5 三者的关系

- **Worktree 是正交的**（平时不在场）: 只在 agent 显式调 `worktree_create` 时存在, 走独立目录 `~/.openprogram/worktrees/<id>/`, 跟 ①② 零交叉引用。唯一需要协调的是规则 A。
- **Checkpoint ↔ Shadow git**: 同时运行, 由 `undo` 统一协调（规则 B）。Checkpoint 负责快速恢复, Shadow git 负责永久历史。

### 6.6 Ad-hoc (默认项目) 会话

没绑真实目录的随手聊:
- ① Checkpoint: 照常。
- ② Shadow git: 照常（shadow store 不依赖用户仓库）。
- ③ Worktree: 不适用（没有 source repo）。

---

## 7. 用户决策矩阵

| 我想要 | 配置 | 得到 |
|---|---|---|
| 像 Claude Code, 撤销键就够 | 默认 | ① Checkpoint + ② Shadow git |
| 看 agent 改了什么（diff） | 默认 | ② Shadow git 提供 `git diff` / `git log` |
| 不想任何额外存储 | 关掉 shadow git | 只有 ① Checkpoint |
| agent 做高风险大改, 别弄乱工作树 | (agent 自行) `worktree_create` | ③ 隔离, 改好 merge / 改砸 discard |

---

## 8. 命名变更

| 旧名 | 新名 | 原因 |
|---|---|---|
| BackupStore / file_backup | **Checkpoint** / checkpoint | 行业通用叫法（Claude Code、Cursor、Hermes 都用） |
| revert_turn | **undo** | Aider/opencode 用 undo，最直觉；revert 容易和 git revert 混淆 |
| ~~Project-Git~~ | **Shadow git** | 不再碰用户 git，改用独立 store |

---

## 9. 实施计划

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 命名重构: BackupStore → CheckpointStore | ✅ `c0a73c1c` | 目录 `file_backup/` → `checkpoint/`，类名/函数名/import 全部改名，保留向后兼容 alias |
| 2 | 统一入口触发: bash 前后 diff 在 `_execute_tool_calls` | ✅ `69432d88` | `_snapshot_cwd` + `_checkpoint_changed_files`，7 个测试 |
| 3 | bash 覆盖 | ✅ 含在 #2 | 当前限制：只扫顶层目录 |
| 4 | Shadow git: 独立 git store | ✅ `ad6551c7` | `store/shadow_git/`，支持 commit/diff/restore/log，13 个测试 |
| 5 | 废弃 Project-Git: 默认关闭 | ✅ `98550cf8` | `project_auto_commit` 默认 False + deprecation warning |
| 6 | 简化 undo: 不再需要 git reset/revert 判断 | ⏳ | 依赖 shadow git 接入 dispatcher |
| 7 | 命令改名: `revert_turn` → `undo` | ⏳ | 用户面对的命令名 |

### 已实现（保留）

| # | 项 | 状态 |
|---|---|---|
| ✅ | ① Checkpoint 写入 + 回退 | ✅（已改名，`store/snapshot/checkpoint/`） |
| ✅ | ③ Worktree create/merge/discard + 工具 | ✅ |
| ✅ | GC (`gc_evict_old` 每 turn 末调用) | ✅ |
| ✅ | read-before-edit 并发防护 (前置闸) | ✅ |
| ⏳ | UI 明示当前会话的"主回退路径" | ⏳ 未做 (后端就绪, 待前端) |
| ⏳ | 完整 Docker 沙箱 (无人值守场景) | ⏳ 远期 |

### 废弃

| # | 项 | 状态 | 原因 |
|---|---|---|---|
| ~~✅~~ | ~~② Project-Git commit + auto-init + 开关~~ | ~~废弃~~ | 改用 Shadow git，不碰用户 git |
| ~~✅~~ | ~~规则 A: auto-init~~ | ~~废弃~~ | Shadow git 不需要用户仓库有 `.git` |
| ~~✅~~ | ~~规则 C: 智能 reset/revert~~ | ~~废弃~~ | undo 直接从 checkpoint 恢复，不操作用户 git |

**冒烟测试**: `scripts/smoke_entity_layer.py`、`scripts/smoke_read_before_edit.py` (13 项)、
`scripts/smoke_revert_ux.py` (11 项)。均在隔离 profile 跑, 全过。Project-Git 废弃后需更新测试。

---

## 10. 沙箱隔离 — 行业方案与我们的定位

### 10.1 三种沙箱方案

| 方案 | 代表框架 | 隔离级别 | 启动延迟 | 实现技术 | 适合场景 |
|---|---|---|---|---|---|
| **系统级沙箱** | Claude Code（macOS Seatbelt / Linux bubblewrap）、Cursor（Seatbelt / Landlock + seccomp） | 文件系统 + 网络（进程级限制，只允许读写 cwd 及子目录） | 毫秒级 | OS 内核机制（sandbox-exec / bwrap / Landlock） | 本地交互式开发，限制 bash 能碰的范围 |
| **容器沙箱** | OpenHands / SWE-agent（Docker）、Devin（云端 ephemeral 容器） | 完整隔离（文件系统 / 网络 / 进程全隔离） | 30-60 秒（新容器），2-15 秒（复用） | Docker / Podman / 云端 VM | 无人值守批量任务、不信任的代码执行 |
| **Git worktree** | OpenProgram（我们现有） | 仅文件系统（独立副本），无进程/网络隔离 | 秒级 | `git worktree add` | 高风险代码改动（实验性重构） |

### 10.2 术语解释

| 术语 | 含义 |
|---|---|
| **系统级沙箱** | 用 OS 内核机制（Seatbelt / Landlock / seccomp / bubblewrap）限制进程的文件访问和网络访问范围。进程仍在宿主机上跑，但被限制了能做什么。 |
| **容器沙箱** | 在 Docker/Podman 容器内运行 agent。完整隔离——容器内的操作不影响宿主机。完成后通过 git patch 或文件 mount 提取产出。 |
| **Git worktree** | 用 `git worktree` 创建独立的工作目录副本。agent 在副本里操作，改好了 merge 回主线，改砸了 discard 丢掉。只隔离文件，不隔离进程和网络。 |

### 10.3 Worktree 的局限

Worktree 只隔离文件，不隔离进程和网络：
- bash 命令仍然能 `rm -rf /`（删除 worktree 之外的文件）
- bash 命令能访问网络（发请求、下载恶意代码）
- bash 命令能读敏感文件（`~/.ssh/`, `~/.aws/` 等）

Worktree 适合"怕改坏代码"（实验性重构），不适合"怕恶意行为"（不信任的代码执行）。

### 10.4 我们的沙箱方向

当前：③ Worktree（轻量 git 隔离），已实现，按需使用。

远期方向（未实施）：
- **本地交互场景**：系统级沙箱（方案 A），毫秒级启动，限制 bash 写入范围。参考 Claude Code 的 Seatbelt/bubblewrap 方案。
- **无人值守 agentic function**：容器沙箱（方案 B），research_agent 等长时间跑的场景。需要 Docker 集成。

优先级：低。当前 Checkpoint + Shadow git + 统一入口触发已覆盖文件回滚需求。沙箱主要解决安全隔离（防恶意行为），在 agentic function 成熟后再考虑。
