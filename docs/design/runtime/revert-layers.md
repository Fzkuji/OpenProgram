# 文件修改管理 — 行业分析与 OpenProgram 设计

> 状态: **已实现** (2026-06)。
> 关联: [`agent-worktree.md`](agent-worktree.md)、[`memory-v2.md`](../memory/memory-v2.md)
> (实体层)、[`git-as-entity-memory.md`](../memory/git-as-entity-memory.md)。
> 代码: `store/snapshot/checkpoint/`、`store/shadow_git/`、
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
| **Checkpoint** | 行业通用叫法（Claude Code、Cursor、Hermes 都用），指改文件前保存的快照。 |
| **bash 覆盖** | agent 通过 bash 工具改文件（`sed -i`、`> file`、`rm` 等）时，这些变更能否被追踪和回滚。快照派的核心难题——编辑工具（write/edit）能精确知道改了哪个文件，bash 不能。 |
| **独立于用户 git** | 备份机制是否使用自己的存储，不污染用户的 git 历史。Aider 直接在用户仓库里 git commit，`git log` 会混入大量 AI 自动 commit；Hermes 和 Claude Code 用独立存储，用户 git 历史保持干净。 |
| **Shadow git** | 用 git 的 tree/commit 机制存快照，但存在独立目录（如 `~/.hermes/checkpoints/`），不碰用户的 `.git`。兼顾 git 的追踪能力（diff、单文件恢复）和不污染用户历史。 |
| **统一入口触发** | Hermes 的关键设计：不在每个编辑工具内部触发 checkpoint，而在**所有工具的执行入口**统一触发。这样 bash 执行前也会做 checkpoint，自然覆盖 bash 盲点。 |
| **系统级沙箱** | 用 OS 内核机制（Seatbelt / Landlock / seccomp / bubblewrap）限制进程的文件访问和网络访问范围。进程仍在宿主机上跑，但被限制了能做什么。毫秒级启动。 |
| **容器沙箱** | 在 Docker/Podman 容器内运行 agent。完整隔离——容器内的操作不影响宿主机。完成后通过 git patch 或文件 mount 提取产出。 |
| **Git worktree** | 用 `git worktree` 创建独立的工作目录副本。agent 在副本里操作，改好了 merge 回主线，改砸了 discard 丢掉。只隔离文件，不隔离进程和网络。 |

---

## 3. OpenProgram 的方案

### 3.1 设计原则

1. **不碰用户 git**。Claude Code 和 Hermes 都不往用户仓库写 commit，这是行业共识。
2. **统一入口触发**。学 Hermes，checkpoint 在所有工具执行前统一触发，覆盖 bash。

### 3.2 四层机制

四个层各管一件事，没有重叠。去掉任何一个都会缺一块能力。

```
                ┌─── read-before-edit (并发防护, 所有写操作的前置闸) ───┐
                │  agent 写前: 没读过 / 读后磁盘变了 → 拒绝, 让它重读     │
                └───────────────────────────┬──────────────────────────┘
                                             ▼

 ┌──────────────── 快照 (Snapshot) ────────────────┐   ┌──────────── 沙箱 (Sandbox) ─────────────┐
 │                                                  │   │                                         │
 │  ①  Checkpoint          ②  Shadow git            │   │  ③  Worktree        ④  系统级沙箱        │
 │     "回滚"                 "历史"                 │   │     "文件隔离"          "权限限制"         │
 │     turn 级, 临时          独立 store, 持久        │   │     独立副本            限制 bash 范围     │
 │     不碰用户 git           不碰用户 git            │   │     agent 显式进入      配置开关           │
 │     永远开, 自动           默认开, 自动            │   │     按需                默认关             │
 │                                                  │   │                                         │
 └──────────────────────────────────────────────────┘   └─────────────────────────────────────────┘
```

| | ① Checkpoint | ② Shadow git | ③ Worktree | ④ 系统级沙箱 |
|---|---|---|---|---|
| **路线** | **快照** | **快照** | **沙箱** | **沙箱** |
| **解决什么** | 改坏了能回滚 | 永久历史 + diff 追溯 | 改坏了丢副本 | 限制 bash 能碰的范围 |
| **机制** | 全量文件拷贝 | git tree/commit 对象，存在 `~/.openprogram/shadow-git/<project-hash>/` | `git worktree` 隔离分支 | OS 内核限制（Seatbelt / bubblewrap） |
| **作用域** | 单个 turn | 整条会话累积 | 一段实验性工作 | 整个会话 |
| **持久度** | 临时 (GC, 上限 100 turn) | 永久（独立 git 历史） | 直到 merge / discard | 会话期间生效 |
| **碰用户的 git 吗** | **完全不碰** | **完全不碰** | 用独立 worktree, merge 才回主线 | **完全不碰** |
| **触发** | 统一入口（所有工具执行前） | turn 结束（自动 commit 本 turn 变更） | agent 显式调 `worktree_create` | 配置开关 |
| **bash 覆盖** | **是**（统一入口触发） | **是**（turn 结束 commit 含 bash 改动） | N/A（隔离环境内） | **是**（内核级拦截） |
| **默认** | **一直开** | **默认开** | 按需 | **默认关** |
| **代码** | `store/snapshot/checkpoint/` | `store/shadow_git/` | `worktree/` | `sandbox/` |
| **回退入口** | `undo` | `undo` 联动 | `worktree_discard` | N/A（预防性，不需要回退） |
| **状态** | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 | ✅ 已实现 |

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

---

## 4. 统一入口触发（覆盖 bash 的关键改动）

### 4.1 实现方式

write / edit / apply_patch 三个编辑工具**各自内部**调用 `checkpoint_before_edit`做精确的单文件备份——保留不动。

bash 工具的覆盖在 `_execute_tool_calls`（`agent_loop.py`，所有工具的单一入口）中实现：

```python
#### agent_loop.py — _execute_tool_calls 内部
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

`undo`撤一个 turn 时:
1. 从 checkpoint 恢复文件（最快路径）。
2. shadow git 历史**不回退**——保持可查，用户可以 diff 看 agent 改了什么。
3. gitignored 文件 / 非 git 文件夹：checkpoint 是唯一兜底。

不需要判断 git reset vs revert，回滚逻辑简洁。

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
  ├─ 用户点"撤销 turn N" (/rewind)
  │     → 规则 B: 从 checkpoint 恢复文件, shadow git 保持可查
  │
  └─ ……
```

### 6.3 Checkpoint 的释放

checkpoint 存 `<session>/checkpoints/<turn_id>/`，释放:

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

### 6.5 四者的关系

四个层各自独立，解决不同问题：

| | 回滚 | 历史 | 文件隔离 | 权限限制 |
|---|---|---|---|---|
| ① Checkpoint | ✅ | | | |
| ② Shadow git | | ✅ | | |
| ③ Worktree | | | ✅ | |
| ④ 系统级沙箱 | | | | ✅ |

协调点：
- **Checkpoint ↔ Shadow git**: 同时运行, 由 `undo` 统一协调（规则 B）。Checkpoint 负责快速恢复, Shadow git 负责永久历史。
- **Worktree ↔ Shadow git**: 规则 A，Worktree 活跃时 Shadow git 让位。
- **系统级沙箱**: 和其他三层完全正交——它限制 bash 能碰什么，不影响快照和文件隔离的运作。

### 6.6 Ad-hoc (默认项目) 会话

没绑真实目录的随手聊:
- ① Checkpoint: 照常。
- ② Shadow git: 照常（shadow store 不依赖用户仓库）。
- ③ Worktree: 不适用（没有 source repo）。
- ④ 系统级沙箱: 照常（限制 bash 范围与项目绑定无关）。

---

## 7. 用户决策矩阵

| 我想要 | 配置 | 得到 |
|---|---|---|
| 像 Claude Code, 撤销键就够 | 默认 | ① Checkpoint + ② Shadow git |
| 看 agent 改了什么（diff） | 默认 | ② Shadow git 提供 `git diff` / `git log` |
| 不想任何额外存储 | 关掉 shadow git | 只有 ① Checkpoint |
| agent 做高风险大改, 别弄乱工作树 | (agent 自行) `worktree_create` | ③ 隔离, 改好 merge / 改砸 discard |
| 限制 bash 别碰 cwd 以外的文件 | 开启系统级沙箱 | ④ bash 只能读写当前项目目录 |
| 最安全模式 | Worktree + 系统级沙箱 | ③ + ④ 文件隔离 + 权限限制 |

---

## 8. 使用指南

### 8.1 网页端（webui）

**回退操作：**
- 每条 **user** 消息右上角有 ↩ 按钮（"Rewind to here"）——点击后：
  1. 该消息的文本回到输入框（可重新编辑）
  2. 该消息及之后的所有对话从界面上移除
  3. 文件恢复到该消息之前的状态（通过 checkpoint）
  4. DAG 中旧对话保留为历史分支，不丢失
- 在聊天框输入 `/rewind`——列出最近的回退点（最多 10 条），每条显示摘要和时间
- 在聊天框输入 `/rewind N`——回退到第 N 个回退点（N 从列表中选）

**沙箱：**
- 在聊天框输入 `/sandbox`——开启系统级沙箱（限制 bash 只能读写当前项目目录）
- 再次输入 `/sandbox`——关闭沙箱

### 8.2 CLI 命令行

- `/rewind`——列出回退点
- `/rewind N`——回退到第 N 个点
- `/sandbox`——开关沙箱

### 8.3 TUI 终端界面

- `/rewind`、`/rewind N`——同 CLI
- `/sandbox`——同 CLI

### 8.4 自动生效的功能（用户不需要手动操作）

这些功能在后台自动运行，用户不需要做任何操作：

| 功能 | 什么时候触发 | 做什么 |
|---|---|---|
| Checkpoint | 每次工具执行前 | 自动备份即将被修改的文件（含 bash） |
| Shadow git | 每个 turn 结束时 | 自动 commit 本 turn 的文件变更到独立 git store |
| read-before-edit | 每次写文件前 | 自动检查文件是否被外部修改过，防止覆盖 |

### 8.5 Agent 可用的工具

这些是 agent（LLM）可以调用的工具，用户不直接操作：

**Worktree（文件隔离沙箱）：**

| 工具 | 功能 |
|---|---|
| `worktree_create` | 创建独立工作目录副本 |
| `worktree_merge` | 把副本的改动合并回主目录 |
| `worktree_discard` | 丢弃副本 |

**Checkpoint（快照回滚）：**

| 工具 | 功能 |
|---|---|
| `checkpoint_list` | 列出当前会话的 checkpoint 列表（turn ID、时间、备份文件） |
| `checkpoint_restore` | 恢复指定 turn 的文件到 checkpoint 状态 |

**Shadow git（永久历史）：**

| 工具 | 功能 |
|---|---|
| `shadow_git_log` | 查看 shadow git 的 commit 历史 |
| `shadow_git_diff` | 对比两个 commit 之间的文件差异 |
| `shadow_git_restore_file` | 从某个 commit 恢复单个文件 |

**沙箱（权限限制）：**

| 工具 | 功能 |
|---|---|
| `sandbox_status` | 查看沙箱当前状态（开/关、可用性） |
| `sandbox_toggle` | 开关系统级沙箱 |

---

## 9. 待做

| 项 | 说明 |
|---|---|
| bash checkpoint 递归扫描 | 当前只扫 cwd 顶层，子目录变更未覆盖 |
| UI 明示当前会话的"主回退路径" | 后端就绪, 待前端 |
| 容器沙箱（远期） | research_agent 等无人值守场景，需 Docker 集成 |

---

## 10. 沙箱隔离 — ③ Worktree + ④ 系统级沙箱

沙箱有三种实现方式，隔离级别从低到高：

| 方案 | 代表框架 | 隔离级别 | 启动延迟 | 实现技术 | 适合场景 |
|---|---|---|---|---|---|
| **Git worktree** | 我们 ③、Claude Code `--worktree` | 仅文件（独立副本），无进程/网络隔离 | 秒级 | `git worktree add` | 高风险代码改动 |
| **系统级沙箱** | 我们 ④、Claude Code `/sandbox`、Cursor | 文件系统 + 网络（进程级限制） | 毫秒级 | Seatbelt / bubblewrap / Landlock | 本地交互，限制 bash 范围 |
| **容器沙箱** | OpenHands / SWE-agent / Devin | 完整隔离（文件/网络/进程） | 30-60 秒 | Docker / Podman | 无人值守、不信任代码 |

### 10.1 ③ Worktree — 文件隔离（✅ 已实现）

agent 调 `worktree_create` 创建独立工作目录副本，改好了 `worktree_merge`，改砸了 `worktree_discard`。

**局限**：只隔离文件，不隔离进程和网络——bash 仍能 `rm -rf /`、读 `~/.ssh/`、访问网络。适合"怕改坏代码"，不防"bash 乱来"。

### 10.2 ④ 系统级沙箱 — 权限限制（✅ 已实现）

用 OS 内核机制限制 bash 进程能做什么：
- **文件系统**：只能读写 cwd 及子目录，`rm ~/.ssh/id_rsa` → `Operation not permitted`
- **网络**：不能直连，通过代理 allowlist 控制
- **实现**：macOS 用 Seatbelt（sandbox-exec），Linux 用 bubblewrap
- **代码**：`openprogram/sandbox/__init__.py`（`sandbox_enabled` contextvar + `wrap_command`）、`backend/local.py`（`_invocation` 集成）
- **命令**：`/sandbox` 开关（CLI `_cli_chat/handlers.py` + webui `ws_actions/chat.py`）

### 10.3 ③ 和 ④ 的关系

两者解决不同问题，可以组合：
- **单独用 ③**：在副本里改，但 bash 什么都能做
- **单独用 ④**：在原目录改，但 bash 被限制范围
- **组合用**：在副本里改，bash 也被限制。最安全

### 10.4 容器沙箱（远期方向）

research_agent 等无人值守 agentic function 的长时间运行场景，需要 Docker 完整隔离。当前不做，等 agentic function 成熟后考虑。
