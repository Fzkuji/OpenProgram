# Multi-agent + per-turn revert: remaining work

Working branch: `git-as-truth`. Pick up from HEAD = `3674f3e`.

## What's done

| Commit | Scope |
|---|---|
| `a0a6700` | `openprogram/store/file_backup/` subpackage (paths / manifest / store / gc) + 7 unit tests. Git-agnostic per-turn file snapshots. Hook API: `BackupStore.backup_before_edit(turn_id, abs_path)` / `.restore_turn(turn_id)`. |
| `3674f3e` | Dispatcher sets `_current_turn_id` ContextVar = `assistant_msg_id`. `write` / `edit` / `apply_patch` tools call `backup_for_current_turn(path)` pre-fs-mutate. `_runtime.py` wraps sync-tool executor in `copy_context().run(...)` so ContextVars propagate to thread-pool. `revert_turn(session_id, assistant_msg_id)` dispatcher fn + WS action `revert_turn` exposed. Tests: 681 pass / 0 fail. |
| `eb2b06a` | D + G: `ContextCommit.parent_ids: list[str]` with single-parent back-compat via `__post_init__`; frontend assistant bubble gets a Revert button that calls the existing `revert_turn` WS action. |
| _pending_ | C (part 1): `<repo>/workdir/` materialized on session init (with `.gitkeep`); `GitSession.workdir_path` + `SessionStore.session_workdir(sid)` accessors land. Dispatcher cwd threading still deferred — done when a chat-agent flow actually needs it. |
| _pending_ | E (part 1): `GitSession.add_worktree(branch, base_ref) / remove_worktree(path) / list_worktrees()` primitives. Per-agent isolation directory lives at `<repo>/_worktrees/<branch>/`. The `spawn_sub_agent` orchestrator (dispatcher integration) is the next chunk. |
| _pending_ | E (part 2): `openprogram/agent/sub_agent.py::allocate_sub_agent(session_id, parent_assistant_id, label?) / release_sub_agent(ws)`. Mints a unique branch name + materializes the worktree via `SessionStore.allocate_sub_agent_worktree`. Dispatcher integration (running a turn against the worktree + posting result back into parent DAG) is the next chunk. |

So the **core machinery** (snapshot pre-edit, restore on demand, expose via WS) is in. What's left is integration with the rest of the system + the actual multi-agent execution path.

## What's left

### C. `workdir/` subdir in session repo, tracked by session git

**Why**: file_backup covers gitignored files, but plain (non-ignored) files agent edits should also live in the session's main git for normal `git log` / diff workflows. Right now the session repo only tracks `history/` + `context/commits/` + `meta.json`. Agent's edits to user files happen wherever cwd points, outside the session repo.

**Plan**:
1. Add `workdir/` subdir under `~/.agentic/sessions-git/<sid>/`.
2. Dispatcher resolves and exports `agent_cwd = session_dir / "workdir"` for tool execution.
3. `commit_turn` in `session_store.py` runs `git add workdir/ history/ context/ meta.json && git commit` so each turn captures both metadata and file edits in one git commit.
4. `BackupStore` continues to track gitignored files in parallel (it doesn't care what git says).
5. For sessions that target a user's external project (not a managed `workdir/`), keep that path as opt-in for later.

**Risk**: cwd switching for tools — many tools currently take `cwd` from env or from `runtime.set_workdir(...)`. Need to thread session_dir/workdir through and make sure user-provided `work_dir=...` overrides still work.

### D. `ContextCommit.parent_ids: list[str]` (multi-parent)

**Why**: merge turns produce a context commit whose ancestry is the N sibling branches that got merged.

**Plan**:
1. `openprogram/context/commit/types.py`: add `parent_ids: list[str] = field(default_factory=list)`. Keep `parent_id: Optional[str]` for back-compat (= `parent_ids[0] if parent_ids else None`); make it a property derived from `parent_ids` or keep both fields in sync at write time.
2. `store.py::_payload_to_commit`: prefer `parent_ids` from payload, fall back to `parent_id` (wrap in single-element list).
3. `generator.py::generate_commit`: pass `parent_ids` through; existing single-parent flow uses `[parent.id]`.
4. `dispatcher.py::commit_turn` already writes a context commit per turn with one parent — no change needed for the normal path.
5. New merge dispatcher path (task F) will pass `parent_ids=[A_id, B_id, C_id]`.
6. UI / `list_commits` ws action returns both fields for compatibility.

**Tests**: add a unit test in `test_dag_context_storage.py` round-tripping a multi-parent ContextCommit through `save_commit → load_commit`.

### E. Per-branch git worktree at agent-spawn time

**Why**: multi-agent concurrent file edits need physical isolation so two agents writing to `workdir/foo.py` don't trample each other. `git worktree add` gives each agent its own materialized branch.

**Plan**:
1. `openprogram/store/git_session.py`: add `add_worktree(branch_name) -> Path` and `remove_worktree(path)` wrappers around `git worktree add/remove`.
2. New module `openprogram/agent/sub_agent.py`: `spawn_sub_agent(parent_session_id, parent_assistant_msg_id, prompt, agent_id) -> sub_branch_name`. It creates a new git branch off the parent's HEAD commit, allocates a worktree under `~/.agentic/sessions-git/<sid>/_wt_<sub_branch>/`, sets that as the sub-agent's `workdir`, and kicks off a `process_user_turn` for it.
3. Sub-agent's `commit_turn` commits to its OWN branch, not parent's.
4. When sub-agent's turn ends, push its final assistant text back into the parent branch's chat DAG as a `tool_result`-style node (the Claude-Code pattern).
5. Worktree cleanup: leave it on disk until parent agent or user decides to merge / discard. Don't auto-prune.

### F. Merge turn

**Why**: when the user (or an orchestrator agent) says "merge these N branches into one", we need a special dispatcher path that takes N branch heads, builds a prompt with each branch's compressed context as attachments, runs the merge LLM call, and writes a multi-parent ContextCommit.

**Plan**:
1. `openprogram/agent/_merge.py`: `process_merge_turn(parent_session, parents=[head_A, head_B, ...], message, agent_id)`.
2. For each `head_X`: call `load_commit_for_head(session, head_X)` to get the latest context commit on that branch. Run the existing summarize rule (or a thinner per-branch summarize) over its items to produce a digest string.
3. Build merge prompt with each digest as a separate `<branch>` block (or attachment). Inject base commit reference too.
4. Run merge agent (regular dispatcher call with a special agent_id like `merger` or just the active chat agent).
5. After completion, produce a new ContextCommit with `parent_ids = [commit_id of each parent's head]`.
6. Resolve workdir conflicts: try `git merge` on each branch's worktree changes; if conflict, the merge agent's own file-edit work is the resolution. (Cross-branch file conflicts surface as the merge agent reading conflicting files via the read tool.)
7. WS action: `merge_branches` with `{session_id, parent_branches: [...], target_branch?}`.

### G. Frontend: revert button per turn

**Why**: WS action is in but no UI calls it yet.

**Plan**:
1. In `web/components/chat/messages/`, every assistant message bubble gets a "↶ Revert" affordance (icon + tooltip).
2. Click sends `{action: "revert_turn", session_id, assistant_msg_id}`.
3. On response: show toast "Reverted N files" + dim/strike the message; pull updated history.

### H. Frontend: per-turn modified-files indicator

**Why**: show users which files a turn touched so revert is informed.

**Plan**:
1. New WS action `list_turn_files {session_id, assistant_msg_id}` → returns `BackupStore.list_backed_paths`.
2. Assistant message bubble auto-fetches this when expanded, shows compact list.

## Suggested order

1. **D** first (no behavior change, prerequisite for F)
2. **C** (gives normal git history for file edits, low risk)
3. **G** (small UI wire, validates the chain end-to-end with a real button)
4. **E** (real worktree per sub-agent — bigger architectural step)
5. **F** (merge turn — depends on D + E)
6. **H** (nice-to-have, pure UI)

## Touch-points to keep in mind

- `dispatcher.py` is already 1100+ lines. New paths (`_revert.py`, `_merge.py`, `sub_agent.py`) should stay siblings, not grow `dispatcher.py` itself.
- `commit_turn` in `session_store.py` is the single place a turn lands on disk. If anything in C touches it, keep the change behind a small helper not inline.
- `ContextCommit.parent_id` rename to `parent_ids` is back-compat-sensitive; test both old single-parent payloads (existing on disk) and new multi-parent payloads round-trip.
- All new code follows the hierarchical-module rule: small files in subpackages, not single 500+-line modules.
- Per-frontend-change discipline: any web/UI work must self-verify via chrome MCP (see `~/.claude/CLAUDE.md §5.1`).
- Term: "context commit" is the official name; never use the legacy `s-n-a-p` word, in code or in chat replies.
