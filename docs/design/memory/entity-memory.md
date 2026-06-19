# 实体记忆 (Entity Memory)

## 1. 概念

实体记忆是不可变的真实历史记录，基于 git 存储。"实体"= 真实发生过的事，可逐步回溯。

两种实体：

| 类型 | 粒度 | 存储位置 |
|------|------|----------|
| **Session-Git** | 每次对话，每 turn 一 commit | `<state>/sessions/<id>/` 或 `<project>/.openprogram/sessions/<id>/` |
| **Project-Git** | 绑定的用户工作目录 | `<user-workdir>/.git/`（复用已有） |

Session-Git 记录对话过程（user → LLM → tool 调用链）。Project-Git 记录 agent 对用户代码/文档的实际修改。两者互补：session 存"说了什么"，project 存"改了什么"。

## 2. 存储布局

```
~/.openprogram/                              ← get_state_dir()
├── sessions/
│   ├── <session_id>/                        ← 一个 Session-Git repo
│   │   ├── .git/
│   │   ├── meta.json                        title, agent_id, project_id, created_at, ...
│   │   ├── history/                         DAG 节点文件
│   │   │   ├── 000001-u-<id>.json           user message
│   │   │   ├── 000002-a-<id>.json           assistant message
│   │   │   ├── 000003-t-<id>.json           tool result (called_by = assistant)
│   │   │   └── ...
│   │   ├── context/                         per-turn LLM context 物化视图
│   │   │   └── commits/<commit_id>.json
│   │   └── workdir/                         此会话的临时工作目录
│   │
│   └── locations.json                       ← 索引：项目内 session → 真实路径
│
├── projects/
│   └── projects.json                        project 注册表
│
└── memory/                                  ← 抽象记忆层（见 virtual-memory.md）

<用户工作目录>/
├── .git/                                    ← Project-Git（复用已有，或 auto-init）
└── .openprogram/sessions/<id>/              ← 绑定此项目的 session repo
```

## 3. Session-Git 生命周期

### 3.1 创建 (Create)

**触发时机**：第一条消息写入时 lazy-init（`SessionStore._open(id, create_if_missing=True)`）。

**创建产物**：
- `git init` → `.git/`
- 写入 `meta.json`（title, agent_id, project_id, created_at）
- 创建 `history/`, `context/`, `workdir/` 目录

**归属**：每个 session 创建时绑定一个 project：
- 指定了工作目录 → 绑定真实 Project-Git，session repo 落在 `<project>/.openprogram/sessions/<id>/`
- 未指定 → 绑定默认项目（纯逻辑标签 `project_id="default"`），session repo 落在 home 根

### 3.2 合法性 (Validity)

一个目录被视为有效 session，当且仅当：

1. 该目录存在
2. 目录下存在 `meta.json` 文件
3. `meta.json` 可解析为有效 JSON

不满足以上条件的目录：**跳过，不列入列表，不报错**。这涵盖了：
- 测试残留（只有 `steering/` 子目录，无 meta.json）
- 手动创建的无关目录
- 损坏的 session（meta.json 不可解析）

### 3.3 Title 规则

Session title 是用户在列表中识别对话的主要标识。

#### 生成策略（优先级从高到低）

1. **用户手动命名**：用户通过 `/rename` 或 UI 右键 rename 设置的 title，永远优先，不被覆盖。

2. **LLM 生成摘要（首选自动方式）**：第一轮对话结束后（assistant 回复完成），后台线程调用 LLM 生成 3-7 词的描述性标题。
   - **触发时机**：第一轮 turn 的 `finalize_turn` 之后，异步执行
   - **输入**：user message 前 500 字符 + assistant response 前 500 字符
   - **Prompt**："Generate a short, descriptive title (3-7 words) for this conversation. Return ONLY the title, no quotes, no prefix."
   - **模型选择**：当前 session 使用的模型（已建立连接，无额外开销）；如果不可用，用系统最便宜的可用模型
   - **参数**：`max_tokens=50`, `temperature=0.3`（确定性高）
   - **后处理**：去引号、去 "Title:" 前缀、截断到 80 字符
   - **非阻塞**：后台 daemon 线程执行，失败只记日志不影响用户
   - **幂等**：`meta.json` 中 `_titled=True` 标记，已生成过不再触发

3. **Fallback — 第一条消息截取**：LLM 不可用或调用失败时，取第一条 user message 的第一行，截断到 50 字符 + "…"。

4. **展示层 Fallback**：如果以上都没有触发（session 通过非 dispatcher 入口创建，如 harness），列举时 title 仍为空/"New conversation"/"Untitled" → 用 preview（第一条 user message 前 80 字符）替代显示。

5. **真空壳**：title 为 placeholder 且 preview 也为空（从未发送过消息）→ 不显示。

#### 时序

```
用户发送第一条消息
  → dispatcher 处理 turn
  → finalize_turn:
      1. 立即设 title = 第一行前 50 字符（Fallback, 确保侧边栏不空）
      2. 启动后台线程 → LLM 生成摘要 → 成功则覆盖 title + 标记 _titled
  → 用户看到侧边栏立即显示截取标题
  → 几秒后 LLM 标题就绪 → 广播 session_updated → 侧边栏更新为摘要标题
```

#### 设计决策

- **为什么不等 LLM 再显示？** LLM 调用需要 1-5 秒，用户切换到别的 session 时侧边栏不能是空的。先用截取占位，再异步更新。
- **为什么用当前模型？** 避免额外的 API key / 连接开销。title 生成的 prompt 很短（< 1200 tokens），对任何模型都是微不足道的开销。
- **为什么只触发一次？** 避免对话深入后标题来回变。第一轮最能代表用户意图。
- **为什么 temperature=0.3？** 稍有创意但基本确定性。同样的对话开头重跑不会得到完全不同的标题。

### 3.4 发现与列举 (Discovery)

列举所有 session 时，来源有两个：

1. **全局目录扫描**：遍历 `<state>/sessions/` 下所有子目录，逐一做合法性校验
2. **locations.json 索引**：记录了落在项目目录内的 session 路径，逐一做合法性校验

两个来源合并去重后，按 `updated_at` 降序排列。

展示规则：
- 合法性校验通过 + title 有值（原始或 fallback）→ 显示
- 合法性校验通过 + title 和 preview 都为空 → 隐藏（empty placeholder）
- 合法性校验不通过 → 跳过

侧边栏和 Chats 页面使用同一套展示规则。

### 3.5 读取与写入 (Read/Write)

**写入**：
- `append_message(session_id, msg)` → 同步写 `history/NNNN-<role>-<id>.json` + 更新内存索引
- `commit_turn(session_id, message)` → turn 结束时一次 git commit（不是每条消息一次）

**读取**：
- `get_branch(session_id, head_id)` → 按 parent_id 边遍历 DAG，返回渲染后的消息列表
- `get_nodes(session_id)` → 原始 `Call` 对象（含工具调用细节）
- `session_commits(session_id)` → git log（turn 粒度）

**分支/重试**：
- DAG 的 retry → git branch（`retry-<assistant_id>`）
- 切换 DAG head ↔ git checkout

### 3.6 管理 (Management)

**元数据更新**：`update_session(session_id, title=..., project_id=..., ...)` → 写 `meta.json` + 更新内存索引

**缓存**：
- 内存中维护 `OrderedDict[session_id → (GitSession, SessionMemoryIndex)]`
- LRU，cap=256（env `OPENPROGRAM_SESSION_CACHE_CAP` 可配）
- 驱逐无损：下次访问时从磁盘重建
- 线程安全：per-session lock + 全局 store lock

**Project 绑定**：
- `meta.json` 的 `project_id` 字段
- 绑定了真实项目的 session 落在项目目录内（`locations.json` 记录路径）

### 3.7 删除 (Deletion)

**手动删除**：`delete_session(session_id)` →
1. 从内存缓存中移除
2. 关闭关联的 runtime（如有）
3. `shutil.rmtree()` 整个 session 目录（包括 `.git/`）
4. 如在 `locations.json` 中有记录，移除该条目

**级联影响**：
- 抽象记忆中引用该 session 的 provenance 指针变为 dangling
- 查询时通过合法性检查自动跳过（session 目录不存在 → 返回 None）
- 不需要显式清理虚拟层记录

**前端入口**：
- 侧边栏右键菜单 → Delete
- Chats 页面（待补充：右键删除功能）

### 3.8 GC 策略 (Garbage Collection)

| 场景 | 处理 |
|------|------|
| 无 `meta.json` 的目录 | 列举时跳过（§3.2 合法性校验） |
| 有 meta.json 但 history 为空且 title 为默认值 | 列入列表，标记为 placeholder，前端可隐藏 |
| 长期不活跃的 session | **不自动删除**（用户数据，由用户决定） |

设计决策：不设自动 TTL。理由：session 是用户的对话历史，属于用户数据，不应被系统自动清理。如果未来需要空间回收，由 policy 层（用户配置）决定，不在 store 层实现。

## 4. Project-Git 生命周期

### 4.1 创建

**触发**：`resolve_project(path, name)` — 用户在 UI 绑定工作目录时，或 session 指定 workdir 时。

**行为**：
- 目录已有 `.git/` → 复用
- 目录无 `.git/` → `git init`
- 注册到 `projects.json`

### 4.2 写入（Auto-commit）

Turn 结束时，如果 session 绑了真实 project 且 agent 改过文件：

```
if working_tree_clean_before_agent:
    git add -A
    git commit -c user.name="agent (<model> via OpenProgram)"
              -m "[agent <session_id>] turn <N>: <user msg first 60 chars>"
else:
    skip + UI 警告（不污染用户未提交的改动）
```

Agent commit 用覆盖的 user.name/email 标识，跟用户手动 commit 区分。

### 4.3 读取

- `ProjectGit.log(limit)` → agent-attributed commits
- `project_commits(project_id)` → provenance 层的读原语

### 4.4 删除

取消项目绑定 ≠ 删除 git 历史。用户的 `.git/` 里包含用户自己的 commit，不可由 OpenProgram 删除。

取消绑定时：
- 从 `projects.json` 移除注册
- 关联 session 的 `project_id` 不变（historical pointer）
- Session repo 仍在项目目录内（不搬回 home 根）

## 5. 跟抽象记忆的关系

实体记忆是抽象记忆的**唯一数据源**。提炼管道（Phase 2）读 session-git 的 DAG 节点 + project-git 的 commit 历史，从中抽取事件和关系，写入抽象记忆的 timeline/graph。

每条抽象记忆都带 `Provenance` 指针回指实体层：
```python
@dataclass
class Provenance:
    project_id: str
    session_id: str
    node_ids: tuple[str, ...]
    commit: str | None
    event_time: float
    ingestion_time: float
```

详见 [`virtual-memory.md`](virtual-memory.md)。
