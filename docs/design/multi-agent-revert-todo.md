# Multi-agent + per-turn revert: remaining work

Working branch: `git-as-truth` (pushed to origin). Pick up from
HEAD = `5ba1314`.

## What's done

| Commit | Scope |
|---|---|
| `a0a6700` | `openprogram/store/file_backup/` subpackage (paths / manifest / store / gc) + 7 unit tests. Git-agnostic per-turn file snapshots. Hook API: `BackupStore.backup_before_edit(turn_id, abs_path)` / `.restore_turn(turn_id)`. |
| `3674f3e` | Dispatcher sets `_current_turn_id` ContextVar = `assistant_msg_id`. `write` / `edit` / `apply_patch` tools call `backup_for_current_turn(path)` pre-fs-mutate. `_runtime.py` wraps sync-tool executor in `copy_context().run(...)` so ContextVars propagate to thread-pool. `revert_turn(session_id, assistant_msg_id)` dispatcher fn + WS action `revert_turn` exposed. |
| `eb2b06a` | D + G: `ContextCommit.parent_ids: list[str]` with single-parent back-compat via `__post_init__`; frontend assistant bubble gets a Revert button calling the `revert_turn` WS action with toast feedback. |
| `6fde168` | C part 1: `<repo>/workdir/` materialized on session init (with `.gitkeep`). `GitSession.workdir_path` + `SessionStore.session_workdir(sid)` accessors. `commit_all` already picks up workdir edits via `git add -A`. |
| `6de702f` | E part 1: `GitSession.add_worktree(branch, base_ref) / remove_worktree(path) / list_worktrees()` primitives. Worktrees live at `<repo>/_worktrees/<branch>/`. |
| `5ba1314` | E part 2: `openprogram/agent/sub_agent.py::allocate_sub_agent(session_id, parent_assistant_id, label?) / release_sub_agent(ws)` and `SessionStore.{allocate,release}_sub_agent_worktree(...)`. Mints `sub_<aid>_<label?>_<hex>` branch + materializes worktree. |

Tests: 658 pass / 0 fail across `tests/ --ignore=tests/integration`.

## What's left

The pieces below all need to land in a fresh session — they touch
dispatcher main loop or merge semantics deeply enough that they want
plenty of context headroom.

### C part 2 — Thread session workdir into dispatcher cwd (deferred)

The directory + accessors are in. What's missing is the actual
**cwd switching** so chat-agent tool calls land inside
`session_dir/workdir/` by default.

**Plan**:
1. In `process_user_turn`, before running `agent_loop`, resolve
   `agent_cwd = default_db().session_workdir(req.session_id)` and
   pass it down via an existing runtime hook (likely
   `runtime.set_workdir(...)` — check `openprogram/agentic_programming/runtime.py:367`).
2. Honor caller-supplied `work_dir=...` override (the `/api/run` path
   already routes it via `webui/_execute/run.py`). When override
   present, do NOT touch the session workdir cwd.
3. Verify bash tool inherits the right cwd; write / edit / apply_patch
   should be unaffected since they take absolute paths.
4. Add a unit test driving a fake tool through a turn and asserting
   `os.getcwd()` (or the runtime's reported cwd) equals
   `session_workdir`.

**Risk**: if any tool was implicitly relying on the project root as
cwd (e.g. relative imports, config lookups), it'll break. Walk the
tool list first and grep for `os.getcwd()` / relative path usage.

### E part 3 — Sub-agent dispatcher integration

The workspace allocator is in. The orchestrator that actually runs a
turn against the worktree + posts the result back is missing.

**Plan**:
1. New `openprogram/agent/sub_agent.py::run_sub_agent_turn(parent_session_id, parent_assistant_id, prompt, agent_id, *, label=None)`:
   - call `allocate_sub_agent(...)` → `SubAgentWorkspace`
   - construct a `TurnRequest` with the **sub-agent's worktree** as cwd
     (this needs C part 2 to be the clean path; in the meantime can
     pass an explicit override)
   - call `process_user_turn(...)` (or an asyncio-wrapped variant for
     parallel sub-agents from the same parent)
   - the sub-agent's `commit_turn` should commit to its OWN branch.
     Right now `GitSession.commit_all` runs on the main repo path, not
     a worktree path. **Action**: add `commit_all(*, worktree_path=None)`
     so it can target a worktree's HEAD. Or: spawn the sub-agent's
     dispatcher with a thinner `SessionStore` rooted at the worktree.
     The second option is cleaner — design it that way.
2. After the sub-agent finishes, post its final assistant text into
   the parent branch's chat DAG as a `tool_result`-style node
   (Claude-Code pattern). Parent's `get_branch` then sees one summary
   row pointing at `sub_branch=<name>` so the UI can offer "open
   sub-agent transcript".
3. WS action `spawn_sub_agent {session_id, parent_msg_id, prompt, agent_id, label?}` so frontend can trigger it; returns immediately with the sub-branch name, streams progress through the same event channel.
4. Concurrency: multiple sub-agents off the same parent must run truly
   in parallel — each has its own worktree so no fs contention; the
   parent `SessionStore` `_lock` only serializes meta writes which
   each sub-agent should NOT do (sub-agent writes to its own branch's
   HEAD, parent meta is untouched until merge turn).

### F — Merge turn

The piece that turns N branches into 1. Depends on C part 2 + E part 3.

**Plan**:
1. `openprogram/agent/_merge.py::process_merge_turn(parent_session, parents=[head_A, head_B, ...], message, agent_id)`.
2. For each `head_X`: call `load_commit_for_head(session, head_X)` to
   get the latest context commit on that branch. Run a per-branch
   summarize over its items to produce a digest string (reuse
   `openprogram/context/commit/rules/summarize.py`).
3. Build merge prompt with each digest as a separate `<branch
   name="A">...</branch>` attachment block.
4. Run merge agent (regular dispatcher call with `agent_id` =
   `merger` or active chat agent).
5. After completion, write a `ContextCommit` with
   `parent_ids=[commit_id of each parent's head]` (D is already in
   place, just pass the list).
6. Workdir conflict resolution: try `git merge` of each sub-branch
   into the parent main branch's workdir/. On conflict, leave the
   conflict markers in place — the merge agent reads them via the
   read tool and produces a resolution as its own file edits.
7. WS action `merge_branches {session_id, parent_branches: [...], target_branch?}`.
8. Frontend trigger: button on DAG view when ≥2 sub-branches exist
   off the same fork point.

### H — Per-turn modified-files indicator (UI)

Small, do anytime; doesn't depend on anything above.

**Plan**:
1. New WS action `list_turn_files {session_id, assistant_msg_id}` →
   returns `BackupStore.list_backed_paths(turn_id)`.
2. Assistant message bubble auto-fetches this when expanded; renders
   a compact chip list with the file basenames.
3. Hover shows full path; click could later open a diff viewer.

## Suggested order (next session)

1. **H** — pure UI + small WS action. Easy warmup, validates the
   list_backed_paths chain end-to-end.
2. **C part 2** — thread session workdir as default cwd. Pre-req
   for E part 3 / F. Risk = relative-path tools; mitigate by greppig
   first.
3. **E part 3** — run_sub_agent_turn. The dispatcher integration
   piece. Likely the biggest single chunk.
4. **F** — merge_turn. Depends on D (done), C part 2, E part 3.

## Touch-points to keep in mind

- `dispatcher.py` is already 1100+ lines. New paths (`_revert.py`,
  `_merge.py`, `sub_agent.py`) MUST stay siblings, not grow
  `dispatcher.py` itself.
- `commit_turn` in `session_store.py` is the single place a turn
  lands on disk. If C part 2 touches it, keep the change behind a
  small helper not inline.
- `ContextCommit.parent_ids` back-compat lives in `__post_init__`
  (both fields stay in sync). Don't add a second source of truth.
- All new code follows the hierarchical-module rule: small files in
  subpackages, not single 500+-line modules.
- Per-frontend-change discipline: any web/UI work must self-verify
  via chrome MCP (see `~/.claude/CLAUDE.md §5.1`). Don't ask the user
  to refresh.
- Term: "context commit" is the official name; never use the legacy
  `s-n-a-p` word, in code or in chat replies.

## Verification baseline

Before opening the next session, run:

```bash
python -m pytest tests/ --ignore=tests/integration -q
# expected: 658 passed
git log --oneline origin/main..HEAD
# expected: chain of commits a0a6700..5ba1314 on git-as-truth
```

If any of those drift, fix before adding new work.
