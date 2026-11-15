# `openprogram/state/store/`

> Durable storage: **session memory**, the **project entity layer**,
> and the **file revert/record** machinery.

This directory is the on-disk truth for everything an agent does that
should persist: the conversation DAG, the agent's file edits, and the
ability to undo them. Sessions are append-only logs, projects use git, and
the in-memory structures are rebuildable caches over those.

> **Hand-written README** (not auto-generated — the doc-gen script skips
> any README without the auto-gen footer). Keep it in sync by hand when
> the directory's shape changes.

## The three groups

The files are split into three sub-packages by concern. Each sub-package
re-exports its public names; the top-level `store/__init__.py` re-exports
only the classes and the per-turn ContextVars (`SessionStore`,
`default_store`, `SessionNodeWriter`, …). Modules are imported from their
own sub-package: `from openprogram.state.store.project import project_commit`.

```
store/
├─ __init__.py            top-level re-exports + the per-turn ContextVars
├─ current.py             current_store(): the active Context's store, or
│                         the process-wide one (resolver registered by the
│                         runtime when it loads)
│
├─ session/   ─ ① SESSION STORAGE — one append-only log per conversation
│    session_store/         SessionStore: the public API (CRUD, branches,
│                           messages) over per-session logs
│    session_log.py         SessionLog: one session's records (`main.jsonl`,
│                           `agents/*.jsonl`), each line checked by a CRC,
│                           folded into the session's state
│    memory_index.py        SessionMemoryIndex: in-memory DAG index, rebuilt
│                           from the log on cache miss
│    nodes.py               the persisted DAG node schema: Call, Graph, the
│                           role constants (render_context and other context
│                           computations stay in runtime/context/nodes.py)
│    session_node_writer.py   SessionNodeWriter: by-node DAG writes (append /
│                           update / load) onto one session log
│    _msg_adapter.py        message-dict ⇄ Call-node translation
│    search.py              cross-session message search (ripgrep)
│    external_import/       import Claude Code / Codex transcripts as
│                           native sessions; a ledger makes re-imports
│                           skip unchanged sources and append grown ones
│    context_commit/        ContextItem / ContextCommit records and their
│                           files (`context/commits/<id>.json`); generating
│                           a commit stays in runtime/context/commit
│    spill.py               write-path spill of over-cap node text into
│                           `large_nodes/` (render only cites the file)
│    weight.py              a text's additive weight (CJK 1, else 0.25),
│                           which decides where spill cuts
│    rewind_recovery.py     replays an interrupted rewind's journal when a
│                           session is opened
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
     checkpoint/            exact per-turn mutation receipts + snapshots
       store.py               prepare/commit/inspect + legacy restore entry
       manifest.py            atomic prepared/committed receipt persistence
       paths.py               layout + backup-name hashing
       gc.py                  evict_old: cap retained turn-dirs (called at
                              turn end by the dispatcher)
       helpers.py             before/after/abort hooks for trusted mutators
```

> **Import paths** after the regroup: the canonical forms are
> `from openprogram.state.store.session import SessionStore`,
> `from openprogram.state.store.project import ProjectGit, resolve_project`,
> `from openprogram.state.store.snapshot import read_tracking` /
> `from openprogram.state.store.snapshot.checkpoint import CheckpointStore`. Every
> module lives under its sub-package — there are no flat top-level module
> aliases.

Designs: [`git-as-entity-memory.md`](../../docs/reference/design/memory/entity-memory-proposal.html)
(why git), [`overview.md`](../../docs/reference/design/memory/overview.html) (entity layer),
[`file-management.html`](../../docs/reference/design/runtime/operations/file-management.html) (snapshot / commit
/ worktree / read-before-edit — how they combine).

## ① Session storage — on-disk layout

Every session is a directory at
`~/.openprogram/projects/<project>/chats/<id>/` (chats with no project belong
to the home project, `default`); `projects/.chats-locations.json` records the
ones kept elsewhere.

```
<session>/
├── main.jsonl                     the append-only log, written by the worker
├── agents/<id>.jsonl              one more log per other writing process
├── context/commits/<id>.json      per-turn ContextCommit (immutable LLM view)
├── large_nodes/                   full text of over-cap nodes
├── events.jsonl                   runtime event log
└── workdir/                       per-session scratch workspace
```

The log is the source of truth; `SessionMemoryIndex` is a query cache, fully
rebuildable from it. Entry point:
`from openprogram.state.store.current import current_store` → a `SessionStore`
(the runtime's `runtime.context.session_db.default_db` is the same lookup).

The store sits below the runtime and never imports it, not even inside a
function (`tests/contracts/repository/test_models_state_below_runtime.py`).
What only the runtime knows arrives through ports the runtime fills in when
it loads (`openprogram/runtime/lower_layers.py`): `current.py` for the
Context's store, `state/execution_facts.py` for the bound session and
execution, running turns and live work on a session (migration fails
closed without it), and `state/event_sink.py` for publishing events.

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

Two cooperating layers feed the transactional history operation:

- **`checkpoint/`** — trusted file tools persist a prepared receipt before
  writing, then commit exact before/after digests, recovery snapshots,
  operation and bounded stats after success. Ordinary Bash and external
  writes are excluded from turn attribution. Recovery data lives in the
  session-root sibling `.file-recovery/`, outside session Git;
  `prompt_checkpoints` holds one recovery point per prompt with shared
  blobs, and `evict_old` caps retained turns and checkpoints.
- **`read_tracking.py`** — the concurrency guard. `read` records a file's
  fingerprint; `edit`/`write`/`apply_patch` refuse to write if the file
  was never read (`NEVER_READ`) or changed on disk since (`STALE`). Keeps
  the snapshots and commits clean — the agent never blindly overwrites a
  concurrent user edit.

`revert_turn` never changes the user's Git. It preflights every recorded
after digest, persists an idempotent intent, applies recovery images under a
workspace lock, and rolls back all already-applied paths after an I/O failure.
`reapply_turn` uses the same path in the opposite direction.

## ContextVars (how deep code finds the session)

`__init__.py` exposes two `ContextVar`s the dispatcher sets per turn so
tools deep in the stack don't need the session threaded through every
call:
- `_store` — the `(SessionStore, session_id)` shim. Used by the backup
  helper, read-tracking, and `Agent method` DAG writes. Unset
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
