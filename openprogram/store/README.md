# `openprogram/store/`

> Git-backed storage: **session memory**, the **project entity layer**,
> and the **file revert/record** machinery.

This directory is the on-disk truth for everything an agent does that
should persist: the conversation DAG, the agent's file edits, and the
ability to undo them. Git is the storage substrate throughout — commits
are the audit trail, and the in-memory structures are just rebuildable
caches over git.

> **Hand-written README** (not auto-generated — the doc-gen script skips
> any README without the auto-gen footer). Keep it in sync by hand when
> the directory's shape changes.

## The three groups

The files are split into three sub-packages by concern. Each sub-package
re-exports its public names, and the top-level `store/__init__.py`
re-exports the historical surface — so `from openprogram.store import
SessionStore` and `from openprogram.store import project_commit` keep
working unchanged.

```
store/
├─ __init__.py            top-level re-exports + the per-turn ContextVars
│
├─ session/   ─ ① SESSION STORAGE — one git repo per conversation
│    session_store.py       SessionStore: the 22-method public API (CRUD,
│                           branches, messages) over per-session git repos
│    git_session.py         GitSession: thin wrapper over the `git` CLI for
│                           one session repo (init / write / commit / log /
│                           checkout). UTF-8 forced so CJK commits work.
│    memory_index.py        SessionMemoryIndex: in-memory DAG index, rebuilt
│                           from git on cache miss
│    graphstore_shim.py     back-compat shim emulating the old GraphStore API
│    _msg_adapter.py        message-dict ⇄ Call-node translation
│    search.py              cross-session message search (ripgrep)
│
├─ project/   ─ ② PROJECT ENTITY LAYER + AUTO-COMMIT — user's working dir as git
│    project_store.py       Project + ProjectGit. A project = a real working
│                           directory. ProjectGit does safe auto-init,
│                           agent-attributed commits (Strategy A), and the
│                           reset/revert-of-a-commit primitive.
│    project_commit.py      wires the agent's per-turn edits into the project
│                           repo at turn end (rules A/B, default-on)
│
└─ snapshot/  ─ ③ SNAPSHOT / REVERT HELPERS — undo + concurrency safety
     read_tracking.py       read-before-edit freshness gate: refuse to write
                            a file the agent never read / that changed on
                            disk since (Claude-Code-style)
     file_backup/           per-turn file snapshots — the "Ctrl+Z" layer
       store.py               BackupStore: backup_before_edit / restore_turn
       manifest.py            per-turn manifest (path → backup, pre_existing)
       paths.py               layout + backup-name hashing
       gc.py                  evict_old: cap retained turn-dirs (called at
                              turn end by the dispatcher)
       helpers.py             backup_for_current_turn — one-line tool hook
```

> **Import paths** after the regroup: the canonical forms are
> `from openprogram.store.session import SessionStore`,
> `from openprogram.store.project import ProjectGit, resolve_project`,
> `from openprogram.store.snapshot import read_tracking` /
> `from openprogram.store.snapshot.file_backup import BackupStore`. The old
> flat paths (`openprogram.store.session_store`, `…project_commit`, etc.)
> still resolve via top-level aliases, so existing call sites didn't break.

Designs: [`git-as-entity-memory.md`](../../docs/design/memory/git-as-entity-memory.md)
(why git), [`memory-v2.md`](../../docs/design/memory/memory-v2.md) (entity layer),
[`file-management.md`](../../docs/design/runtime/file-management.md) (snapshot / commit
/ worktree / read-before-edit — how they combine).

## ① Session storage — on-disk layout

Every session is its own git repo at `~/.openprogram/sessions/<id>/`
(ad-hoc chats) — or, for a project-bound session, inside the project at
`<project>/.openprogram/sessions/<id>/` (the home root's
`sessions/locations.json` indexes the out-of-tree ones).

```
<session repo>/
├── .git/                          one commit per turn
├── meta.json                      title / agent_id / project_id / head_id / branches
├── history/NNNN-{u|a|t|s}-<id>.json   append-only DAG nodes (user/llm/code)
├── context/commits/<id>.json      per-turn ContextCommit (immutable LLM view)
├── workdir/                       per-session scratch workspace
└── file_backups/<turn>/           ③ pre-edit file snapshots (see below)
```

Git is the source of truth; `SessionMemoryIndex` is a query cache, fully
rebuildable from the `history/` files. Entry point everywhere:
`from openprogram.agent.session_db import default_db` → a `SessionStore`.

## ② Project entity layer — `git log` of what the agent changed

A **Project** is a long-lived working unit bound to a real filesystem
directory (the default project is a pure label with no repo). When the
agent edits files in a bound, git-backed folder, `project_commit` records
them as an agent-attributed commit at turn end — so the user gets a
`git log` / `git diff` / `git revert`-able history.

Key behaviors (full detail in `file-management.md` §3):
- **Default ON**, but binding a folder has zero git side-effects;
  auto-init happens only on the first agent edit, and only safely
  (baseline commit of pre-existing files first, refuse on
  node_modules/.venv heavy dirs).
- **Strategy A**: never fold the user's uncommitted work into an agent
  commit (refuse if the tree was dirty pre-turn).
- **Yields to an active worktree** (rule B).
- `ProjectGit.revert_agent_commit(sha)` picks the safe op:
  clean `reset` (HEAD + clean + unpushed) else additive `revert`.

Registry: `~/.openprogram/projects/projects.json` (id → name/path/sessions).

## ③ Revert / record — how "undo" works

Two independent layers, coordinated by `agent/_revert.py::revert_turn`:

- **`file_backup/`** — before each edit, the file's prior contents are
  copied into `file_backups/<turn>/` (full copy, not hardlink). Restoring
  a turn copies them back (and deletes files the agent created). The
  always-available undo — works even for gitignored files and non-git
  folders. `evict_old` caps retained turns (called at turn end).
- **`read_tracking.py`** — the concurrency guard. `read` records a file's
  fingerprint; `edit`/`write`/`apply_patch` refuse to write if the file
  was never read (`NEVER_READ`) or changed on disk since (`STALE`). Keeps
  the snapshots and commits clean — the agent never blindly overwrites a
  concurrent user edit.

`revert_turn` undoes both together: git first (reset/revert), snapshot as
the fallback when git can't (or there was no commit).

## ContextVars (how deep code finds the session)

`__init__.py` exposes two `ContextVar`s the dispatcher sets per turn so
tools deep in the stack don't need the session threaded through every
call:
- `_store` — the `(SessionStore, session_id)` shim. Used by the backup
  helper, read-tracking, and `@agentic_function` DAG writes. Unset
  outside a turn ⇒ those features no-op gracefully.
- `_current_turn_id` — the assistant message id of the turn in flight;
  keys the per-turn file backups.

## Common ops

```bash
# Inspect a session's git history (the turn-by-turn commits)
git -C ~/.openprogram/sessions/<id> log --oneline

# What did the agent change in a bound project?
git -C <project> log --author="OpenProgram Agent" --oneline

# Smoke-test the storage layers (throwaway profile, never touches real state)
python scripts/smoke_entity_layer.py        # session + project + commit + GC
python scripts/smoke_read_before_edit.py    # the read-before-edit gate
python scripts/smoke_revert_ux.py           # reset/revert + revert_turn
```
