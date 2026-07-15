# 多 agent + 逐 turn 回滚：进展

工作分支：`git-as-truth`。HEAD = `6b5c5ea`。

## 已完成

| Commit | 范围 |
|---|---|
| `a0a6700` | `openprogram/store/file_backup/` 子包（paths / manifest / store / gc）+ 7 个单元测试。与 git 无关的逐 turn 文件提交。Hook API：`BackupStore.backup_before_edit(turn_id, abs_path)` / `.restore_turn(turn_id)`。 |
| `3674f3e` | Dispatcher 把 `_current_turn_id` ContextVar 设为 `assistant_msg_id`。`write` / `edit` / `apply_patch` 工具在改动文件系统前调用 `backup_for_current_turn(path)`。`_runtime.py` 用 `copy_context().run(...)` 包裹同步工具执行器，使 ContextVar 传播到线程池。暴露 `revert_turn(session_id, assistant_msg_id)` dispatcher 函数 + WS action `revert_turn`。 |
| `eb2b06a` | D + G：`ContextCommit.parent_ids: list[str]`，通过 `__post_init__` 保持单父节点向后兼容；前端 assistant 气泡新增一个 Revert 按钮，调用 `revert_turn` WS action 并带 toast 反馈。 |
| `6fde168` | C 第 1 部分：会话初始化时物化 `<repo>/workdir/`（带 `.gitkeep`）。`GitSession.workdir_path` + `SessionStore.session_workdir(sid)` 访问器。`commit_all` 已通过 `git add -A` 收集 workdir 的改动。 |
| `6de702f` | E 第 1 部分：`GitSession.add_worktree(branch, base_ref) / remove_worktree(path) / list_worktrees()` 原语。Worktree 位于 `<repo>/_worktrees/<branch>/`。 |
| `5ba1314` | E 第 2 部分：`openprogram/agent/sub_agent.py::allocate_sub_agent(session_id, parent_assistant_id, label?) / release_sub_agent(ws)` 以及 `SessionStore.{allocate,release}_sub_agent_worktree(...)`。创建 `sub_<aid>_<label?>_<hex>` 分支 + 物化 worktree。 |
| `1066afd` | H：WS action `list_turn_files` 返回 `BackupStore.list_backed_paths(turn_id)`；当该 turn 触碰过任何文件时，assistant 气泡渲染一条 `.turn-files-chips` chip 条，显示文件名。已通过 chrome MCP 验证。 |
| `feda1d4` | C 第 2 部分：`openprogram/agent/_workdir.py::apply_default_workdir(runtime, session_id)`，在取到 chat runtime 之后从 `webui/_execute/__init__.py` 调用。`runtime.set_workdir` 默认指向该会话的 `workdir/`；`/api/run` 仍通过自己的 set_workdir 调用覆盖。进程内工具查询 `get_default_workdir()` 而非 `os.getcwd()`，所以切换 cwd 是安全的。 |
| `acd7fa5` | E 第 3 部分（首版）：`agent/sub_agent_run.py::run_sub_agent_turn(parent_session_id, parent_assistant_id, prompt, agent_id, label?)` + `session_db.set_db_override / reset_db_override` ContextVar 覆盖，使 dispatcher 走一个以 worktree 为根的 SessionStore。WS action `spawn_sub_agent`。 |
| `6b5c5ea` | F + E 第 3 部分隔离修复：`agent/_merge.py::process_merge_turn(...)`、WS action `merge_branches`、写入多父节点 ContextCommit。同一 commit 加固 sub-agent 隔离：清除从 worktree 继承的 history/+ context commits，把摘要以 `role="assistant"`（而非 tool）写入，在合成写入过程中保留父节点 HEAD。 |

测试：在 `tests/ --ignore=tests/integration` 下 675 通过 / 0 失败。

端到端（chrome MCP，真实 LLM）：

* `spawn_sub_agent` → `final_text="red"`，子分支 commit 已记录。
* 第二次 `spawn_sub_agent` → `final_text="blue"`，第二条子分支。
* 对两者执行 `merge_branches` → `final_text="Red and blue are two distinct colors."`，`commit_id=commit_a64d36670cdfbfd2`，`parent_ids` 携带先前父节点 ContextCommit id + 2 条子分支 SHA。

## 已知约束（MVP 级别，尚未处理）

这些不阻碍当前行为的发布，但是显而易见的下一步迭代：

1. **F 中没有 workdir 级别的合并。** 合并 turn 从每条子分支的摘要中综合出一个文本答案；它不会把子分支的文件改动 `git merge` 进父节点的 `workdir/`。如果两个 sub-agent 写了不同的代码，用户必须用 `git checkout` 或 `git merge` 手动挑选一个分支。

2. **子分支的 context commit 没有被呈现出来。** 在 `release_sub_agent` 之后，worktree 目录就没了。提交到子分支上的 ContextCommit JSON 文件只能通过 `git show <branch>:context/commits/<id>.json` 访问。合并解析器改用父节点的 DAG 摘要行，这能工作，但不会暴露逐分支的推理链。

3. **`spawn_sub_agent` / `merge_branches` 还没有 UI。** 两个 WS action 都能用，但聊天输入框或 DAG 视图里都没有触发它们的按钮。管道已经接好；UI 是下一个可见特性。

4. **同一父节点下的并发 sub-agent。** 每个都有自己的 worktree（无文件系统争用），但 `default_db()` ContextVar 覆盖是逐 context 的——从同一个 WS handler 派生的两个 sub-agent 需要各自独立的执行器线程（且每个线程都用 `copy_context().run(...)`），覆盖才不会互相冲掉。WS handler 用的单线程执行器路径对顺序派生是没问题的；并行派生需要一次仔细的处理。

5. **带有损坏 sub_agent 工具行的旧会话需要清理。** 如果你有 `~/.openprogram/sessions/<sid>/history/` 里 `role="code"` 且 `name="sub_agent"` 的 JSON 文件（E 第 3 部分首版实现遗留下来的），在后续 turn 中它们仍会触发 `No tool call found for function call output`。删掉这些文件 + 引用它们的 context commit，并 `git commit` 这次删除。新会话不需要这么做。

## 扩展前值得重读的接触点

* `openprogram/agent/dispatcher.py::process_user_turn` —— 在 turn 开始时读取 `default_db()`；正是这个 ContextVar 覆盖让 sub-agent 隔离成为可能。
* `openprogram/context/engine.py::_build_messages_from_commit` —— 拉取 `db.get_branch(session_id)` 和 `db.get_messages(session_id)`，按 caller 拼接子调用。sub-agent 的 worktree DAG 在这里绝不能继承父节点的节点。
* `openprogram/store/_msg_adapter.py::_msg_to_node` —— 只有 tool 行会从 `extra.tool_use.called_by` 取 `called_by`；assistant 行把它留在 metadata 里。这就是为什么 sub_agent 摘要写入要手动保留 HEAD。
* `ContextCommit.parent_ids` —— 列表，为了向后兼容，由 `__post_init__` 从单个 `parent_id` 设置。合并 turn 是第一个放入 > 1 个条目的写入方。

## 验证基线

```bash
python -m pytest tests/ --ignore=tests/integration -q
# expected: 675 passed
git log --oneline 55588ad..HEAD
# expected: 1066afd / feda1d4 / acd7fa5 / 6b5c5ea
```
