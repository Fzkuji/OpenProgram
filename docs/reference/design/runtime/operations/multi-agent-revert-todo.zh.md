# 多 agent 分支与逐 turn 回滚

> 本文描述一个 turn 的文件改动如何变得可回滚，以及 sub-agent 如何在隔离的 git
> worktree 中运行并在之后合并回来。实现状态（含各部分落地的 commit）见附录。

## 1. 逐 turn 文件备份与回滚

`openprogram/store/file_backup/`（paths / manifest / store / gc）记录逐 turn 的
文件提交，与 git 无关。其 hook API 是 `BackupStore.backup_before_edit(turn_id,
abs_path)` 与 `BackupStore.restore_turn(turn_id)`。

turn id 就是 assistant 消息 id。Dispatcher 把 `_current_turn_id` ContextVar 设为
`assistant_msg_id`，`write`、`edit`、`apply_patch` 工具在改动文件系统前调用
`checkpoint_before_edit(path)`。`_runtime.py` 用 `copy_context().run(...)` 包裹
同步工具执行器，使 ContextVar 传播到线程池。

回滚以 dispatcher 函数 `revert_turn(session_id, assistant_msg_id)` 和 WS action
`revert_turn` 的形式暴露。前端 assistant 气泡上有一个 Revert 按钮，调用该 WS action
并通过 toast 反馈。WS action `list_turn_files` 返回
`BackupStore.list_backed_paths(turn_id)`；当该 turn 触碰过任何文件时，assistant
气泡渲染一条 `.turn-files-chips` 条，显示这些文件的文件名。

## 2. 会话 workdir

会话初始化时物化 `<repo>/workdir/`（带 `.gitkeep`），可经 `GitSession.workdir_path`
和 `SessionStore.session_workdir(sid)` 访问。`commit_all` 通过 `git add -A` 收集
workdir 的改动。

`openprogram/agent/internals/_workdir.py::apply_default_workdir(runtime, session_id)` 在取到
chat runtime 之后从 `webui/_execute/__init__.py` 调用，使 `runtime.set_workdir`
默认指向该会话的 `workdir/`；`/api/run` 仍通过自己的 `set_workdir` 调用覆盖。
进程内工具查询 `get_default_workdir()` 而非 `os.getcwd()`，正是这一点让切换 cwd
是安全的。

## 3. worktree 中的 sub-agent

`GitSession` 提供 worktree 原语 `add_worktree(branch, base_ref)`、
`remove_worktree(path)`、`list_worktrees()`。Worktree 位于
`<repo>/_worktrees/<branch>/`。

`openprogram/agent/sub_agent.py::allocate_sub_agent(session_id,
parent_assistant_id, label?)` 与 `release_sub_agent(ws)`，配合
`SessionStore.{allocate,release}_sub_agent_worktree(...)`，创建
`sub_<aid>_<label?>_<hex>` 分支并物化其 worktree。

`agent/sub_agent_run.py::run_sub_agent_turn(parent_session_id,
parent_assistant_id, prompt, agent_id, label?)` 运行该 turn，WS action
`spawn_sub_agent` 将其暴露出来。`session_db.set_db_override / reset_db_override`
的 ContextVar 覆盖使 dispatcher 走一个以 worktree 为根的 SessionStore。隔离依赖
三件事：清除从 worktree 继承的 history 与 context commits、把摘要以
`role="assistant"` 而非 tool 行写入、在该合成写入过程中保留父节点 HEAD。

## 4. 合并

`agent/_merge.py::process_merge_turn(...)` 以 WS action `merge_branches` 暴露，
产生一个写入多父节点 ContextCommit 的合并 turn。`ContextCommit.parent_ids` 是列表，
为向后兼容由 `__post_init__` 从单个 `parent_id` 填充；合并 turn 是第一个往里放入
多于一个条目的写入方。

## 5. 已知约束

这些不阻碍当前行为，是显而易见的下一步迭代。

1. **没有 workdir 级别的合并。** 合并 turn 从每条子分支的摘要中综合出一个文本答案；
   它不会把子分支的文件改动 `git merge` 进父节点的 `workdir/`。当两个 sub-agent
   写了不同的代码时，用户用 `git checkout` 或 `git merge` 手动挑选一个分支。

2. **子分支的 context commit 没有被呈现出来。** 在 `release_sub_agent` 之后
   worktree 目录就没了，提交到子分支上的 ContextCommit JSON 文件只能通过
   `git show <branch>:context/commits/<id>.json` 访问。合并解析器改用父节点的 DAG
   摘要行，这能工作，但不会暴露逐分支的推理链。

3. **`spawn_sub_agent` / `merge_branches` 还没有 UI。** 两个 WS action 都能用，
   但聊天输入框或 DAG 视图里都没有触发它们的按钮。管道已经接好；UI 是下一个可见
   特性。

4. **同一父节点下的并发 sub-agent。** 每个都有自己的 worktree，因此没有文件系统
   争用，但 `default_db()` ContextVar 覆盖是逐 context 的：从同一个 WS handler
   派生的两个 sub-agent 需要各自独立的执行器线程（且每个线程都用
   `copy_context().run(...)`），覆盖才不会互相冲掉。WS handler 用的单线程执行器
   路径对顺序派生是没问题的；并行派生需要一次仔细的处理。

5. **带有损坏 sub_agent 工具行的旧会话需要清理。** 若某会话的
   `~/.openprogram/sessions/<sid>/history/` 里存在 `role="code"` 且
   `name="sub_agent"` 的 JSON 文件（sub-agent 首版实现遗留下来的），在后续 turn 中
   它们仍会触发 `No tool call found for function call output`。处理方式是删掉这些
   文件以及引用它们的 context commit，并 `git commit` 这次删除。新会话不需要这么做。

## 6. 扩展前值得重读的接触点

* `openprogram/agent/dispatcher.py::process_user_turn` —— 在 turn 开始时读取
  `default_db()`；正是这个 ContextVar 覆盖让 sub-agent 隔离成为可能。
* `openprogram/context/engine.py::_build_messages_from_commit` —— 拉取
  `db.get_branch(session_id)` 和 `db.get_messages(session_id)`，按 caller 拼接
  子调用。sub-agent 的 worktree DAG 在这里不能继承父节点的节点。
* `openprogram/store/_msg_adapter.py::_msg_to_node` —— 只有 tool 行会从
  `extra.tool_use.called_by` 取 `called_by`；assistant 行把它留在 metadata 里。
  这就是为什么 sub_agent 摘要写入要手动保留 HEAD。
* `ContextCommit.parent_ids` —— 列表，为向后兼容由 `__post_init__` 从单个
  `parent_id` 设置。

## 附录：实现状态

工作分支 `git-as-truth`，HEAD `6b5c5ea`。

| Commit | 范围 |
|---|---|
| `a0a6700` | `openprogram/store/file_backup/` 子包（paths / manifest / store / gc）+ 7 个单元测试（§1）。 |
| `3674f3e` | `_current_turn_id` ContextVar、工具侧 `backup_for_current_turn(path)`、`_runtime.py` 中的 `copy_context().run(...)`、`revert_turn` dispatcher 函数 + WS action（§1）。 |
| `eb2b06a` | `ContextCommit.parent_ids: list[str]` 与单父节点向后兼容；前端 Revert 按钮及 toast 反馈（§1、§4）。 |
| `6fde168` | 会话初始化时物化 `<repo>/workdir/`；`GitSession.workdir_path` + `SessionStore.session_workdir(sid)`（§2）。 |
| `6de702f` | `GitSession.add_worktree / remove_worktree / list_worktrees` 原语（§3）。 |
| `5ba1314` | `allocate_sub_agent` / `release_sub_agent` 以及 `SessionStore.{allocate,release}_sub_agent_worktree(...)`（§3）。 |
| `1066afd` | WS action `list_turn_files` + assistant 气泡的 `.turn-files-chips` 条（§1）。已通过 chrome MCP 验证。 |
| `feda1d4` | 从 `webui/_execute/__init__.py` 接入 `apply_default_workdir(runtime, session_id)`（§2）。 |
| `acd7fa5` | `run_sub_agent_turn(...)` + `session_db.set_db_override / reset_db_override`；WS action `spawn_sub_agent`（§3）。 |
| `6b5c5ea` | `process_merge_turn(...)`、WS action `merge_branches`、多父节点 ContextCommit 写入（§4），以及 §3 描述的 sub-agent 隔离修复。 |

测试：在 `tests/ --ignore=tests/integration` 下 675 通过 / 0 失败。

端到端（chrome MCP，真实 LLM）：

* `spawn_sub_agent` → `final_text="red"`，子分支 commit 已记录。
* 第二次 `spawn_sub_agent` → `final_text="blue"`，第二条子分支。
* 对两者执行 `merge_branches` → `final_text="Red and blue are two distinct colors."`，
  `commit_id=commit_a64d36670cdfbfd2`，`parent_ids` 携带先前父节点 ContextCommit id
  + 2 条子分支 SHA。

验证基线：

```bash
python -m pytest tests/ --ignore=tests/integration -q
# expected: 675 passed
git log --oneline 55588ad..HEAD
# expected: 1066afd / feda1d4 / acd7fa5 / 6b5c5ea
```
