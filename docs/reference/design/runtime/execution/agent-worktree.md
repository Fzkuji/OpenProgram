# Agent Worktree Tool

> When an agent runs high-risk changes inside the user's real code repository, it needs an isolated temporary working directory:
> if the changes are good, merge them back into the mainline; if they go wrong, discard them with a single wipe, and the main repo stays untouched.
> Under the hood this is just a wrapper around `git worktree add` / `git worktree remove`, but it must be kept strictly separate from
> OpenProgram's own session-git.

Referencing Claude Code's `EnterWorktreeTool` / `ExitWorktreeTool`
(`references/claude-code-leaked/src/tools/EnterWorktreeTool/`),
this design copies its "switch cwd + state machine + keep/discard on exit" skeleton, but adapts it to
OpenProgram's runtime / session model.

---

## Part 1. Dimensions the Design Must Consider

### D1. What a Worktree Entity Stores

Each active worktree is one record, with fields:

- `id`: short worktree id (hex, same style as commit ids)
- `source_repo`: the user's real repository root (absolute path)
- `worktree_path`: the directory created by `git worktree add` (absolute path)
- `branch_name`: the branch name corresponding to the worktree (defaults to `op/wt/<id>`)
- `base_ref`: the baseline ref at creation time (defaults to `HEAD`, can be set to origin/main / commit sha)
- `created_at`: unix timestamp
- `status`: `active` / `committing` / `merged` / `discarded` / `kept`
- `parent_session_id`: the associated OpenProgram session (one-to-one or one-to-many)
- `parent_task_id`: the associated async task (if any)
- `created_by_agent`: agent id (records which agent opened it, so it is visible in the UI)

Records are persisted in the session-git repository at `worktrees/<id>.json`, stored alongside ContextCommit.
They are not cleaned up automatically when the session closes; they wait for the agent or user to explicitly merge / discard.

### D2. cwd Switching Mechanism

OpenProgram's tools fall into two categories:

1. **Runtime-spawned subprocess** (Codex CLI / Claude CLI, which take a `--cd` argument)
   controlled via `runtime.set_workdir(path)`.
2. **In-process `@function` tools** (bash / edit / write / read, etc.)
   bash goes through `get_active_backend().run(...)`; currently `LocalBackend.run` accepts
   a `cwd` argument but callers don't pass it; edit / write / read require absolute paths.

Design: add a ContextVar `_current_worktree_path: Optional[str]` in `openprogram/agent/_runtime.py`.
Each time the dispatcher enters a turn, if the session currently has an active worktree (read from session meta),
it `set`s this var. Tool implementations consume it as needed:

- bash: `LocalBackend.run(cmd, cwd=_current_worktree_path.get())`
- edit / write / read: resolve relative paths against `_current_worktree_path` as the root;
  absolute paths must be under the worktree (D6 security check).
- runtime subprocess: keep the existing `apply_default_workdir(runtime, session_id)`,
  but change it to prefer returning the worktree path (if any), otherwise session-git's `workdir/`.

No "explicit cwd argument" is introduced. The worktree is session-level context; tools are unaware of it.

### D3. State Machine

```
            create
              │
              ▼
        ┌─ active ─┐
        │          │
  merge │          │ discard
        │          │
        ▼          ▼
     merged    discarded
        │          │
        └────┬─────┘
             │ keep
             ▼
           kept (user decides to keep the branch but neither merge nor delete it)
```

- `active`: the agent is using it. Tools like bash / edit default their cwd here.
- `committing`: a brief state, holding a lock during the merge operation to prevent concurrent file edits.
- `merged`: `git merge` succeeded, the worktree directory has been `git worktree remove`d.
- `discarded`: `git worktree remove --force` succeeded, the branch was deleted too.
- `kept`: the user went through `worktree_keep` — the worktree directory is kept, OpenProgram
  unbinds this record but does not touch git. The user takes over from there.

### D4. Isolation from OpenProgram session-git

OpenProgram has its own `~/.openprogram/sessions/<sid>/` (one git repo per session),
storing the conversation memory's history / context / workdir. **The agent worktree must never
be created inside this directory tree**:

- worktree_path must not be under any `~/.openprogram/sessions/*` (D14 check).
- source_repo must not equal a session-git repository path.
- session-git commits and worktree commits are managed independently; in the UI the ContextCommit
  timeline only looks at session-git, while the worktree timeline is shown in a separate panel.

Historically OpenProgram tried sub-agent worktrees (commit `5ba13149`),
created under `<session-repo>/_worktrees/<branch>/`, which was later refactored into "sub-agent =
peer session + attach" (commit `75e430c0`). This design does not reuse that path —
that one was "open a branch inside session-git to run a sub-agent", whereas this design is "open a worktree
in the user's real code repository for the agent to run changes". Completely different purposes.

### D5. Where source_repo Comes From

Three entry points, in descending priority:

1. **Explicitly passed by the agent**: the `source_repo` parameter of the worktree_create tool (absolute path).
   Suitable when a plan agent already knows the target repository while listing tasks.
2. **The fn-form "Working in a folder"**: the `_work_dir` the user supplies to the program in the web UI.
   Before entering a turn, if the dispatcher finds that this path is a git repo root,
   it treats it as the default source_repo (used when worktree_create is called without source_repo).
3. **The ancestor git root of the session's current cwd**: resolved via `git rev-parse --show-toplevel`.
   Usually the directory from which the user launched OpenProgram.

If all entry points fail → worktree_create reports the error `source_repo_not_a_git_repo`.
It will not automatically `git init` a repository for the user (too destructive).

### D6. Security / Permissions

The core security constraint for agent tools inside a worktree: **the bash command's cwd is locked, but the cmd
itself can `cd ..` to run outside the worktree**. This is not a true sandbox; it is a "default direction".
Two mitigations:

- **Absolute path check**: when edit / write / read receive a `file_path` that falls outside
  worktree_path, log a warning into the ContextCommit metadata
  (`outside_worktree=true`), but do not block — the user may genuinely want to read a system config.
- **bash's cwd is always worktree_path**: even if the LLM writes `cd /tmp && rm -rf X`,
  the starting point is worktree_path, the shell session is not persistent (each bash is a fresh subprocess),
  and the next bash returns to worktree_path.

Not doing: chroot / namespace isolation for bash commands. OpenProgram already supports a docker
backend; for a hard sandbox, go that route.

### D7. Commits Inside a Worktree

The agent writes files in the worktree → the worktree directory is dirty. Two semantics:

- **Auto commit**: after each agent tool call (bash running git add / edit / write),
  the worktree tool does not auto-commit. It lets the agent run `git add -A && git commit` via bash itself.
  This way the commit message is decided by the agent, consistent with git conventions.
- **Forced commit at merge time**: when worktree_merge runs, if the worktree has uncommitted
  changes, it first reports the error `worktree_dirty` and lets the agent handle it explicitly (commit it /
  stash it / discard it). It does not auto-commit-and-merge.

### D8. Merge Strategy

`worktree_merge(worktree_id, mode="ff-only" | "squash" | "no-ff")`, defaults to ff-only.

- **ff-only**: source_repo's HEAD is an ancestor of the worktree branch → fast-forward.
  Otherwise reports the error `not_fast_forward`, letting the agent decide whether to rebase or switch to squash.
- **squash**: `git merge --squash <branch>` → multiple worktree commits squashed into one;
  suitable when the worktree contains many exploratory small commits.
- **no-ff**: always creates a merge commit, preserving the worktree's commit history.

After a merge, by default `git worktree remove <path>` deletes the worktree directory, but
**the branch is kept** (so the user can `git log` to see the history of this change).
Handling conflicts: on merge failure it does **not** auto-reset; the worktree status stays `committing`
(effectively rolling back to active), letting the agent or user enter the worktree to resolve conflicts manually.

### D9. Discard Semantics

`worktree_discard(worktree_id, force=False)`:

- `force=False` (default): the worktree must be clean (no uncommitted / untracked).
  Otherwise reports the error `worktree_dirty`, and the agent can decide whether to stash or force.
- `force=True`: `git worktree remove --force <path>` + `git branch -D <branch>`.
  Uncommitted changes are dropped directly.
- The record in worktrees/<id>.json has its status changed to `discarded` + a timestamp. The file is not deleted,
  for auditing convenience — but worktree_path no longer exists.

No "auto-backup before discard" is provided. There was discussion of tar-ing the discarded content into
`~/.openprogram/discarded/`, but keeping this escape rope is not costly; it is left to Part 6 (future).

### D10. Relationship Between Worktree and Task

The async task system (see `async-task-lifecycle.md`):

- A task can exclusively create and use a worktree (task_create → worktree_create).
- On task cancel, the worktree held by the task defaults to `discard force=True`.
  On task complete it does **not** auto-merge — after the task completes, the plan agent / user
  decides explicitly (the plan agent picks one to merge after looking at the output of 3 tasks).
- A task is not required to open a worktree. Lightweight tasks (reading files, running grep) just run
  directly on source_repo without opening a worktree.

In implementation, the task lifecycle calls `worktree_manager.discard_for_task(task_id)` in the cancel hook.

### D11. Relationship Between Worktree and ContextCommit

When the agent runs tools in a worktree, the tool results (bash stdout / edit confirmation)
go into the ContextCommit items normally. **The file diff inside the worktree does not go directly into ContextCommit
content** — file diffs are git's business; ContextCommit only records event-level facts like
"tool call X modified file Y".

Add a lightweight metadata field: each tool item's metadata gets
`worktree_id: Optional[str]`, indicating which worktree this tool call happened in
(None means it ran directly in source_repo). When the UI renders, it adds a badge
to tool calls inside a worktree.

The worktree merge / discard operations themselves are also written into ContextCommit as system nodes
(similar to an attach pointer marker), with content like "Merged worktree wt_abc1234
into source_repo (ff-only, 3 files changed)".

### D12. Agent Tool Surface

Four tools:

| Tool | Parameters | Returns |
|---|---|---|
| `worktree_create` | `source_repo: str?` `name: str?` `base_ref: str?` | `{id, path, branch, base_sha}` |
| `worktree_merge`  | `worktree_id: str` `mode: str = "ff-only"` `delete_branch: bool = False` | `{merged_sha, files_changed: int, summary: str}` |
| `worktree_discard`| `worktree_id: str` `force: bool = False` | `{status: "discarded"}` |
| `worktree_list`   | `status_filter: str?` | `[{id, path, branch, status, source_repo, age_seconds}]` |

Error codes (prefix of the returned error string):

- `not_a_git_repo`: source_repo is not a git repo
- `worktree_dirty`: the worktree has uncommitted changes
- `not_fast_forward`: cannot ff during merge
- `merge_conflict`: conflict during merge
- `worktree_in_sessions_dir`: source_repo falls inside the sessions tree (D4 isolation violation)
- `worktree_exists`: a worktree with the same branch name already exists under the same source_repo

`worktree_create` / `worktree_merge` / `worktree_discard` default to
`requires_approval=True`; only permission_mode=auto skips the approval prompt.

No `worktree_switch` tool is exposed — a session has only one active worktree at a time
(D2's ContextVar is single-valued), switching semantics are complex (do we write a switch marker?
what happens to the old worktree after switching?), and the benefit does not outweigh the cost. Multiple
worktrees are achieved through async tasks, one worktree per task.

### D13. UI Representation

- **Composer toolbar**: when the current session has an active worktree, a chip
  `worktree: wt_abc1234 (3 files changed)` is shown above the PromptInput; hovering pops a panel
  showing worktree_path / branch / the list of changed files / Merge / Discard / Keep buttons.
- **The fn-form "Working in a folder"**: kept as-is, only showing the source_repo path.
  The worktree is an internal detail and is not surfaced in the fn-form.
- **DAG timeline**: worktree create / merge / discard marker nodes are rendered in a distinguishing color
  (same style as the attach marker).
- **Not doing**: inline preview of worktree file diffs (the user can click "open in editor"
  / use their own git GUI to view).

### D14. Errors / Edge Cases

- `source_repo` is not a git repo → `not_a_git_repo` error, prompting the user to `git init` first.
- `source_repo` has uncommitted changes but the worktree is a new branch → OK,
  the worktree is created from base_ref (defaults to HEAD), unaffected by the source_repo working tree state.
- `worktree_path` already exists → `worktree_exists` error. The user is allowed to pass a name and retry.
- `source_repo` is inside the sessions tree → `worktree_in_sessions_dir` rejection (D4).
- `base_ref` does not exist → git reports the error itself, the tool passes through stderr.
- the agent accidentally deletes worktree_path (bypassing worktree_discard with a direct rm -rf) →
  next time worktree_list detects the path no longer exists, it automatically marks `status=discarded`
  and writes an "auto-cleaned" record.

### D15. Integration with Async Task

worktree_create / merge / discard are themselves synchronous tools (git subprocesses); do not wrap
them into async tasks. But **long-running work inside a worktree** (the agent running tests, running a build)
is usually the work content of an async task:

- when an async task starts it can specify `worktree_id` (the task's cwd is locked to this worktree).
- the bash / edit run inside the task also goes through the D2 ContextVar path, with cwd being worktree_path.
- task cancel hook → calls `worktree_manager.on_task_cancel(task_id)`,
  which defaults to discard.
- task complete does not auto-merge (D10).

---

## Part 2. Scenario × Dimension

### Scenario A: Single agent, single worktree (basic flow)

The agent receives the task "modify foo.py to add logging", opens a worktree → modifies → runs tests → merges.

| Dimension | Design |
|---|---|
| **D1 entity** | one worktree record, `status=active`, bound to the current session |
| **D2 cwd** | when the dispatcher enters a turn it reads session.meta.active_worktree_id → sets the `_current_worktree_path` ContextVar; bash/edit/write/read all default cwd here |
| **D3 status** | active → committing (during merge) → merged |
| **D4 isolation** | worktree_path is not in the sessions tree: defaults to `~/.openprogram/worktrees/<id>-<slug>/` (an independent directory, sibling to source_repo) |
| **D5 source** | the `_work_dir` passed by the fn-form is the source_repo; the agent can also pass it explicitly |
| **D6 security** | bash's cwd starts at worktree_path; edit/write receiving an absolute path outside the worktree writes a warning but does not block |
| **D7 commit** | the agent runs `git add . && git commit -m "..."` via bash itself; worktree_merge requires the worktree to be clean beforehand |
| **D8 merge** | ff-only default; ff succeeds since source_repo HEAD has not moved |
| **D9 discard** | not used |
| **D10 task** | no task (runs directly in the main turn) |
| **D11 commit log** | the bash/edit tool items are all tagged with `worktree_id`; merge writes a system marker |
| **D12 tools** | worktree_create → do the work → worktree_merge |
| **D13 UI** | composer shows chip "wt_abc1234 (2 files changed)", the chip disappears after merge, the DAG adds a marker |
| **D14 edge** | worktree_create reports an error when source_repo is not a git repo; the user git inits first |
| **D15 task** | N/A |

### Scenario B: Single agent, multiple worktrees (failed exploration)

The agent tries approach A, tests fail → discard → tries approach B → passes → merge.

| Dimension | Design |
|---|---|
| **D1 entity** | two worktree records (different id / branch / path). The first has status=discarded, the second has status=merged |
| **D2 cwd** | only one is active at any moment: the second can be created only after discarding the first; the ContextVar switch is done by the dispatcher at the turn boundary |
| **D3 status** | wt1: active → discarded; wt2: active → merged |
| **D4 isolation** | the two worktrees each have an independent directory |
| **D5 source** | the same source_repo, reused twice |
| **D6 security** | same as A |
| **D7 commit** | in wt1 the agent may have run a few commits, deleted along with the branch on discard; wt2's commits go through a normal merge |
| **D8 merge** | wt2 goes through ff-only; if source_repo did not move during wt1 (only the worktree itself changed), ff succeeds |
| **D9 discard** | wt1 `force=True` (the agent decides to drop this line, including uncommitted experiments) |
| **D10 task** | not used |
| **D11 commit log** | on the DAG, wt1 markers (create + discard) + wt2 markers (create + merge) |
| **D12 tools** | create → discard → create → merge |
| **D13 UI** | the chip switches twice: wt1 shows then disappears, wt2 shows then disappears |
| **D14 edge** | when discarding wt1, force=True skips the dirty check |
| **D15 task** | N/A |

### Scenario C: Concurrent worktrees (plan agent dispatches 3 tasks)

The plan agent lists 3 independent changes → 3 async tasks, one worktree per task
(independent copies of source_repo) → all complete → the plan agent looks at the results, picks one to merge, discards the rest.

| Dimension | Design |
|---|---|
| **D1 entity** | 3 worktree records, each bound to a task_id; status evolves in sync |
| **D2 cwd** | when running inside each task, **the task runtime's ContextVar** independently sets `_current_worktree_path=task.worktree_path`; the main session's plan agent itself does not activate any worktree (the plan agent does not touch files) |
| **D3 status** | 3 parallel active → all tasks complete → 2 discarded + 1 merged |
| **D4 isolation** | each worktree has an independent directory; source_repo all point to the same one, but git worktree add inherently supports multiple worktrees at once (different branches) |
| **D5 source** | all the same source_repo |
| **D6 security** | each task isolates its cwd, not affecting one another |
| **D7 commit** | each task commits itself |
| **D8 merge** | the chosen one goes through ff-only; if none of the other tasks merged, source_repo HEAD has not moved, ff succeeds |
| **D9 discard** | the remaining 2 go through force=True (the plan agent picked 1, the rest are no longer wanted) |
| **D10 task** | each task is assigned a worktree at creation; task complete does not auto-merge (D10), waiting for the plan agent's decision |
| **D11 commit log** | all 3 worktrees each produce markers; the plan agent writes an assistant explanation "adopting approach 2" |
| **D12 tools** | the plan agent calls worktree_list to see the 3; calls worktree_merge wt2 + worktree_discard wt1 wt3 |
| **D13 UI** | the composer chip is the plan agent's own session, not showing sub-worktrees; in the task panel each task card shows its own worktree chip |
| **D14 edge** | when 3 worktrees are created at once, git worktree add mutex (git has its own lockfile) |
| **D15 task** | fully integrated: task creation → worktree assignment; task cancel → discard; task complete → wait for decision |

### Scenario D: Long-running worktree / user takeover

The agent is halfway through (5 patches committed in the worktree), the user decides to take over themselves.

| Dimension | Design |
|---|---|
| **D1 entity** | status goes from active → kept |
| **D2 cwd** | after the user clicks "Keep & detach", the session's active_worktree_id is cleared, the ContextVar is no longer set; subsequent agent turns return to source_repo as cwd |
| **D3 status** | active → kept |
| **D4 isolation** | unchanged |
| **D5 source** | unchanged |
| **D6 security** | the worktree is still on disk, but OpenProgram no longer writes to it; the user opens worktree_path in their own terminal / IDE to continue |
| **D7 commit** | the agent's earlier commits are all kept on the branch |
| **D8 merge** | not used (the user decides merge / rebase themselves) |
| **D9 discard** | not used |
| **D10 task** | if it is a worktree held by a task, the task also moves into the `kept` state in sync (the task no longer writes logs, but the record is kept) |
| **D11 commit log** | writes a system marker "Worktree wt_xxx kept for manual handover at <path>", so the agent later looking at ContextCommit knows this happened |
| **D12 tools** | the UI directly calls the ws action (not an agent tool) `worktree_keep(worktree_id)`; the agent tool can also expose worktree_keep, but low priority |
| **D13 UI** | the chip changes to "kept — open in editor", clicking copies the path |
| **D14 edge** | the user later deletes the worktree directory manually → next OpenProgram startup, list_worktrees detects the path no longer exists → marks discarded (D14) |
| **D15 task** | the task also enters a detached state, not affecting new tasks |

---

## Part 3. Current State vs Target

| Capability | Current State | Target | Gap |
|---|---|---|---|
| Worktree isolation in the user's real repo | none | full create/merge/discard | large |
| Agent cwd bound to worktree | none (runtime uses session-git workdir/) | ContextVar switching | medium |
| Bash tool passing cwd | LocalBackend accepts it but the bash function doesn't pass it | go through ContextVar | small |
| Edit/Write/Read checking worktree boundary | none (only checks absolute paths) | warning without blocking | small |
| Worktree state machine persistence | none | worktrees/<id>.json in session-git | medium |
| UI worktree chip | none | chip + panel at the top of the composer | medium |
| Worktree × Task integration | none (the task system itself is in design) | task cancel auto-discard | medium (depends on async-task) |
| Sub-agent worktree legacy code | already refactored away (commit `75e430c0`) | not reused | N/A |

---

## Part 4. Change List

In dependency order:

| Step | File | Main change |
|---|---|---|
| 1 | new `openprogram/worktree/types.py` | `Worktree` dataclass + `WorktreeStatus` Enum + serialization |
| 2 | new `openprogram/worktree/manager.py` | `WorktreeManager`: create / merge / discard / list / keep; underlying `subprocess.run(["git", "worktree", ...])`; persists to `<session-repo>/worktrees/<id>.json` |
| 3 | new `openprogram/worktree/_paths.py` | worktree path policy: `~/.openprogram/worktrees/<id>-<slug>/`; isolation check (D4) |
| 4 | edit `openprogram/agent/_workdir.py` | `apply_default_workdir` prefers returning the active worktree path |
| 5 | edit `openprogram/agent/dispatcher.py` | at the start of a turn, read session.meta.active_worktree_id → set the `_current_worktree_path` ContextVar |
| 6 | edit `openprogram/functions/tools/bash/bash.py` | call `backend.run(cmd, cwd=_current_worktree_path.get())` |
| 7 | edit `openprogram/functions/tools/edit/edit.py` + write/read | warning when path outside worktree (D6) |
| 8 | new `openprogram/functions/tools/worktree/` | 4 @function tools: worktree_create / worktree_merge / worktree_discard / worktree_list; go through WorktreeManager |
| 9 | edit `openprogram/store/session_store.py` | add an `active_worktree_id` field to session.meta; helpers `set_active_worktree` / `get_active_worktree` |
| 10 | new `openprogram/webui/ws_actions/worktree.py` | `list_worktrees` / `keep_worktree` / `discard_worktree` (user manual UI operations) |
| 11 | new `web/components/chat/composer/worktree-chip.tsx` | chip component + hover panel + Merge/Discard/Keep buttons |
| 12 | edit `web/components/chat/composer/composer.tsx` | bring in the chip |
| 13 | edit ContextCommit item metadata rendering | tool call items show a worktree_id badge |
| 14 | edit `openprogram/agent/dispatcher.py` to write markers | worktree_create / merge / discard write system nodes into ContextCommit |
| 15 | (depends on async-task) hook into `openprogram/tasks/lifecycle.py` | task cancel → `WorktreeManager.on_task_cancel`; task create can optionally attach a worktree |
| 16 | Tests | unit: WorktreeManager (create/merge/discard path checks, isolation check); integration: agent in worktree → merge full flow |

---

## Part 5. Key Invariants

Every one of these must be checked during implementation:

1. **worktree_path is never inside the `~/.openprogram/sessions/` subtree**
   (isolating OpenProgram's own git; if violated, worktree_create rejects).

2. **Zero changes to the main repo after discard**
   `git worktree remove --force` + `git branch -D` do not touch source_repo's HEAD
   or working tree. Check: `git rev-parse HEAD` is identical before and after discard.

3. **The worktree does not disappear automatically when a merge fails**
   after a merge_conflict / not_fast_forward error, the worktree status returns to `active`,
   the directory is kept, letting the agent or user inspect it manually.

4. **At most one active worktree per session at any moment**
   session.meta.active_worktree_id is single-valued; if there is already an active worktree at worktree_create time,
   it reports the error `already_active`, prompting to merge/discard/keep first.

5. **The starting cwd of a bash command is always the active worktree_path** (if it exists)
   rather than session-git workdir/; shell state is not persistent across bash calls, each one
   is a fresh subprocess, with cwd reset back to worktree_path.

6. **worktree_id appearing in a tool item's metadata = this tool call executed inside that worktree**
   when ContextCommit reads it, it can distinguish "where the change was made" accordingly.

7. **The branch of a kept worktree is not deleted**
   only OpenProgram's reference is unbound; in the user's git repository they can still `git checkout` to this branch.

---

## Part 6. Out of Scope for This Design

- **Remote push**: the worktree is local-only; to push the worktree branch to origin, the agent runs
  `git push -u origin <branch>` via bash itself. worktree_merge does not push either.
- **cherry-pick / rebase between worktrees**: complex semantics, left for the agent to handle via bash itself.
- **conflict resolution UI**: on merge conflicts OpenProgram does not provide a visual mergetool;
  the agent uses edit to modify files / bash to run git mergetool.
- **worktrees across source_repos**: one worktree necessarily corresponds to one source_repo; merging
  worktree changes into another repository is not supported (do it via a bash git patch flow if needed).
- **auto-backup before discard**: the `~/.openprogram/discarded/` archiving mentioned in D9, left as a future enhancement.
- **chroot / namespace true sandbox**: D6 is "default cwd lock", not a sandbox; for hard isolation, go through the
  docker backend.
- **auto-cleanup of the active worktree when the session closes**: the active worktree is kept across session
  restarts (after restart, list_worktrees probes, and ones still active are marked kept for the user to handle manually).
