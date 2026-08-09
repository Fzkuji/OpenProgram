# Multi-agent branches and per-turn revert

> This document describes how a turn's file edits are made reversible and how
> sub-agents run in isolated git worktrees that later merge back. Implementation
> status, including the commits that landed each piece, is in the appendix.

## 1. Per-turn file backup and revert

`openprogram/store/file_backup/` (paths / manifest / store / gc) records
per-turn file commits and is git-agnostic. Its hook API is
`BackupStore.backup_before_edit(turn_id, abs_path)` and
`BackupStore.restore_turn(turn_id)`.

The turn id is the assistant message id. The dispatcher sets the
`_current_turn_id` ContextVar to `assistant_msg_id`, and the `write`, `edit`,
and `apply_patch` tools call `backup_for_current_turn(path)` before mutating the
filesystem. `_runtime.py` wraps the sync-tool executor in
`copy_context().run(...)` so ContextVars propagate into the thread pool.

Revert is exposed as the dispatcher function
`revert_turn(session_id, assistant_msg_id)` and the WS action `revert_turn`. In
the frontend, the assistant bubble carries a Revert button that calls the WS
action and reports through a toast. The WS action `list_turn_files` returns
`BackupStore.list_backed_paths(turn_id)`; when a turn touched any files, the
assistant bubble renders a `.turn-files-chips` strip of their basenames.

## 2. Session workdir

A session materializes `<repo>/workdir/` (with a `.gitkeep`) on init, reachable
via `GitSession.workdir_path` and `SessionStore.session_workdir(sid)`.
`commit_all` picks up workdir edits through `git add -A`.

`openprogram/agent/internals/_workdir.py::apply_default_workdir(runtime, session_id)` is
called from `webui/_execute/__init__.py` after the chat runtime is fetched, so
`runtime.set_workdir` defaults to the session's `workdir/`; `/api/run` still
overrides it with its own `set_workdir` call. In-process tools consult
`get_default_workdir()` rather than `os.getcwd()`, which is what makes the cwd
switch safe.

## 3. Sub-agents in worktrees

`GitSession` provides the worktree primitives `add_worktree(branch, base_ref)`,
`remove_worktree(path)`, and `list_worktrees()`. Worktrees live at
`<repo>/_worktrees/<branch>/`.

`openprogram/agent/sub_agent.py::allocate_sub_agent(session_id,
parent_assistant_id, label?)` and `release_sub_agent(ws)`, together with
`SessionStore.{allocate,release}_sub_agent_worktree(...)`, mint a
`sub_<aid>_<label?>_<hex>` branch and materialize its worktree.

`agent/sub_agent_run.py::run_sub_agent_turn(parent_session_id,
parent_assistant_id, prompt, agent_id, label?)` runs the turn, and the WS action
`spawn_sub_agent` exposes it. The `session_db.set_db_override /
reset_db_override` ContextVar override is what routes the dispatcher through a
worktree-rooted SessionStore. Isolation depends on three things: history and
context commits inherited from the worktree are cleared, the summary is written
as `role="assistant"` rather than a tool row, and the parent HEAD is preserved
across that synthetic write.

## 4. Merge

`agent/_merge.py::process_merge_turn(...)`, exposed as the WS action
`merge_branches`, produces a merge turn that writes a multi-parent
ContextCommit. `ContextCommit.parent_ids` is a list, populated by
`__post_init__` from a single `parent_id` for back-compat; the merge turn is the
first writer that puts more than one entry in it.

## 5. Known constraints

These do not block the current behavior, and are the obvious next iterations.

1. **No workdir-level merge.** The merge turn synthesizes a textual answer from
   each sub-branch's summary; it does not `git merge` the sub-branches' file
   edits into the parent's `workdir/`. When two sub-agents write different code,
   the user picks a branch manually with `git checkout` or `git merge`.

2. **Sub-branch context commits are not surfaced.** After `release_sub_agent`
   the worktree directory is gone, and the ContextCommit JSON files committed
   onto the sub-branch are reachable only via
   `git show <branch>:context/commits/<id>.json`. The merge resolver uses the
   parent's DAG summary row instead, which works but does not expose the
   per-branch reasoning chain.

3. **No UI for `spawn_sub_agent` / `merge_branches`.** Both WS actions work, but
   no button in the chat composer or DAG view fires them. The plumbing is in;
   the UI is the next visible feature.

4. **Concurrent sub-agents off the same parent.** Each gets its own worktree, so
   there is no filesystem contention, but the `default_db()` ContextVar override
   is per-context: two sub-agents spawned from the same WS handler need separate
   executor threads (and a `copy_context().run(...)` per thread) for the
   overrides not to clobber each other. The single-thread executor path the WS
   handler uses is fine for sequential spawns; parallel spawn needs a careful
   pass.

5. **Old sessions with broken sub_agent tool rows need cleanup.** Sessions whose
   `~/.openprogram/sessions/<sid>/history/` holds JSON files with `role="code"`
   and `name="sub_agent"` — left over from the first sub-agent implementation —
   still trip `No tool call found for function call output` on subsequent turns.
   The fix is to delete those files plus the context commits referencing them and
   `git commit` the deletion. Fresh sessions do not need this.

## 6. Touch-points worth re-reading before extending

* `openprogram/agent/dispatcher.py::process_user_turn` — reads `default_db()` at
  turn start; the ContextVar override is what makes sub-agent isolation possible.
* `openprogram/context/engine.py::_build_messages_from_commit` — pulls
  `db.get_branch(session_id)` and `db.get_messages(session_id)`, splices
  sub-calls by caller. A sub-agent's worktree DAG must not inherit the parent's
  nodes here.
* `openprogram/store/_msg_adapter.py::_msg_to_node` — only tool rows pick up
  `called_by` from `extra.tool_use.called_by`; assistant rows leave it in
  metadata. That is why the sub_agent summary write preserves HEAD manually.
* `ContextCommit.parent_ids` — a list, set via `__post_init__` from a single
  `parent_id` for back-compat.

## Appendix: Implementation Status

Working branch `git-as-truth`, HEAD `6b5c5ea`.

| Commit | Scope |
|---|---|
| `a0a6700` | `openprogram/store/file_backup/` subpackage (paths / manifest / store / gc) + 7 unit tests (§1). |
| `3674f3e` | `_current_turn_id` ContextVar, tool-side `backup_for_current_turn(path)`, `copy_context().run(...)` in `_runtime.py`, `revert_turn` dispatcher fn + WS action (§1). |
| `eb2b06a` | `ContextCommit.parent_ids: list[str]` with single-parent back-compat; frontend Revert button with toast feedback (§1, §4). |
| `6fde168` | `<repo>/workdir/` materialized on session init; `GitSession.workdir_path` + `SessionStore.session_workdir(sid)` (§2). |
| `6de702f` | `GitSession.add_worktree / remove_worktree / list_worktrees` primitives (§3). |
| `5ba1314` | `allocate_sub_agent` / `release_sub_agent` and `SessionStore.{allocate,release}_sub_agent_worktree(...)` (§3). |
| `1066afd` | WS action `list_turn_files` + `.turn-files-chips` strip in the assistant bubble (§1). Verified via chrome MCP. |
| `feda1d4` | `apply_default_workdir(runtime, session_id)` wired from `webui/_execute/__init__.py` (§2). |
| `acd7fa5` | `run_sub_agent_turn(...)` + `session_db.set_db_override / reset_db_override`; WS action `spawn_sub_agent` (§3). |
| `6b5c5ea` | `process_merge_turn(...)`, WS action `merge_branches`, multi-parent ContextCommit write (§4), plus the sub-agent isolation fixes described in §3. |

Tests: 675 pass / 0 fail across `tests/ --ignore=tests/integration`.

End-to-end (chrome MCP, real LLM):

* `spawn_sub_agent` → `final_text="red"`, sub-branch commit recorded.
* Second `spawn_sub_agent` → `final_text="blue"`, second sub-branch.
* `merge_branches` over both → `final_text="Red and blue are two distinct colors."`,
  `commit_id=commit_a64d36670cdfbfd2`, `parent_ids` carries the prior parent
  ContextCommit id + 2 sub-branch SHAs.

Verification baseline:

```bash
python -m pytest tests/ --ignore=tests/integration -q
# expected: 675 passed
git log --oneline 55588ad..HEAD
# expected: 1066afd / feda1d4 / acd7fa5 / 6b5c5ea
```
