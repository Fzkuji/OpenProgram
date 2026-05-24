# Multi-agent + per-turn revert: status

Working branch: `git-as-truth`. HEAD = `6b5c5ea`.

## What's done

| Commit | Scope |
|---|---|
| `a0a6700` | `openprogram/store/file_backup/` subpackage (paths / manifest / store / gc) + 7 unit tests. Git-agnostic per-turn file snapshots. Hook API: `BackupStore.backup_before_edit(turn_id, abs_path)` / `.restore_turn(turn_id)`. |
| `3674f3e` | Dispatcher sets `_current_turn_id` ContextVar = `assistant_msg_id`. `write` / `edit` / `apply_patch` tools call `backup_for_current_turn(path)` pre-fs-mutate. `_runtime.py` wraps sync-tool executor in `copy_context().run(...)` so ContextVars propagate to thread-pool. `revert_turn(session_id, assistant_msg_id)` dispatcher fn + WS action `revert_turn` exposed. |
| `eb2b06a` | D + G: `ContextCommit.parent_ids: list[str]` with single-parent back-compat via `__post_init__`; frontend assistant bubble gets a Revert button calling the `revert_turn` WS action with toast feedback. |
| `6fde168` | C part 1: `<repo>/workdir/` materialized on session init (with `.gitkeep`). `GitSession.workdir_path` + `SessionStore.session_workdir(sid)` accessors. `commit_all` already picks up workdir edits via `git add -A`. |
| `6de702f` | E part 1: `GitSession.add_worktree(branch, base_ref) / remove_worktree(path) / list_worktrees()` primitives. Worktrees live at `<repo>/_worktrees/<branch>/`. |
| `5ba1314` | E part 2: `openprogram/agent/sub_agent.py::allocate_sub_agent(session_id, parent_assistant_id, label?) / release_sub_agent(ws)` and `SessionStore.{allocate,release}_sub_agent_worktree(...)`. Mints `sub_<aid>_<label?>_<hex>` branch + materializes worktree. |
| `1066afd` | H: WS action `list_turn_files` returns `BackupStore.list_backed_paths(turn_id)`; assistant bubble renders a `.turn-files-chips` chip strip showing basenames when the turn touched any files. Verified via chrome MCP. |
| `feda1d4` | C part 2: `openprogram/agent/_workdir.py::apply_default_workdir(runtime, session_id)` called from `webui/_execute/__init__.py` after the chat runtime is fetched. `runtime.set_workdir` defaults to the session's `workdir/`; `/api/run` still overrides via its own set_workdir call. In-process tools consult `get_default_workdir()` not `os.getcwd()`, so the cwd switch is safe. |
| `acd7fa5` | E part 3 (first pass): `agent/sub_agent_run.py::run_sub_agent_turn(parent_session_id, parent_assistant_id, prompt, agent_id, label?)` + `session_db.set_db_override / reset_db_override` ContextVar override so the dispatcher routes through a worktree-rooted SessionStore. WS action `spawn_sub_agent`. |
| `6b5c5ea` | F + E part 3 isolation fixes: `agent/_merge.py::process_merge_turn(...)`, WS action `merge_branches`, multi-parent ContextCommit write. Same commit hardens sub-agent isolation: clear worktree-inherited history/+ context commits, write summary as `role="assistant"` (not tool), preserve parent HEAD across the synthetic write. |

Tests: 675 pass / 0 fail across `tests/ --ignore=tests/integration`.

End-to-end (chrome MCP, real LLM):

* `spawn_sub_agent` → `final_text="red"`, sub-branch commit recorded.
* Second `spawn_sub_agent` → `final_text="blue"`, second sub-branch.
* `merge_branches` over both → `final_text="Red and blue are two distinct colors."`, `commit_id=commit_a64d36670cdfbfd2`, `parent_ids` carries the prior parent ContextCommit id + 2 sub-branch SHAs.

## Known constraints (MVP-level, not yet addressed)

These don't block shipping the current behavior but are the obvious next iterations:

1. **No workdir-level merge in F.** The merge turn synthesizes a textual answer from each sub-branch's summary; it doesn't `git merge` the sub-branches' file edits into the parent's `workdir/`. If two sub-agents wrote different code, the user has to pick a branch manually with `git checkout` or `git merge`.

2. **Sub-branch context commits aren't surfaced.** After `release_sub_agent`, the worktree dir is gone. The ContextCommit JSON files committed onto the sub-branch are reachable only via `git show <branch>:context/commits/<id>.json`. The merge resolver uses the parent's DAG summary row instead, which works but doesn't expose the per-branch reasoning chain.

3. **No UI yet for `spawn_sub_agent` / `merge_branches`.** Both WS actions work, but there's no button in the chat composer or DAG view that fires them. Plumbing is in; UI is the next visible feature.

4. **Concurrent sub-agents off the same parent.** Each gets its own worktree (no fs contention), but `default_db()` ContextVar override is per-context — two sub-agents spawned from the same WS handler would need separate executor threads (and `copy_context().run(...)` per thread) so the overrides don't clobber. The single-thread executor path used by the WS handler is fine for sequential spawns; parallel spawn would need a careful pass.

5. **Old sessions with broken sub_agent tool rows need cleanup.** If you have `~/.agentic/sessions-git/<sid>/history/` JSON files where `role="code"` and `name="sub_agent"` (left over from the first E-part-3 implementation), they'll still trip `No tool call found for function call output` on subsequent turns. Wipe those files + their referencing context commits and `git commit` the deletion. Fresh sessions don't need this.

## Touch-points worth re-reading before extending

* `openprogram/agent/dispatcher.py::process_user_turn` — reads `default_db()` at turn start; the ContextVar override is what makes sub-agent isolation possible.
* `openprogram/context/engine.py::_build_messages_from_commit` — pulls `db.get_branch(session_id)` and `db.get_messages(session_id)`, splices sub-calls by caller. Sub-agent's worktree DAG must NOT inherit parent's nodes here.
* `openprogram/store/_msg_adapter.py::_msg_to_node` — only tool rows pick up `called_by` from `extra.tool_use.called_by`; assistant rows leave it in metadata. That's why the sub_agent summary write preserves HEAD manually.
* `ContextCommit.parent_ids` — list, set via `__post_init__` from single `parent_id` for back-compat. Merge turn is the first writer that puts > 1 entry.

## Verification baseline

```bash
python -m pytest tests/ --ignore=tests/integration -q
# expected: 675 passed
git log --oneline 55588ad..HEAD
# expected: 1066afd / feda1d4 / acd7fa5 / 6b5c5ea
```
