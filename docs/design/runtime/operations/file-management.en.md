# File Modification Management — Industry Analysis and OpenProgram Design

> Status: **Implemented** (2026-06).
> Related: [`agent-worktree.md`](../execution/agent-worktree.md), [`memory-v2.md`](../../memory/memory-v2.md)
> (entity layer), [`git-as-entity-memory.md`](../../memory/git-as-entity-memory.md).
> Code: `store/snapshot/checkpoint/`, `store/shadow_git/`,
> `store/read_tracking.py`, `agent/_revert.py`, `worktree/`.

---

## 1. Two Approaches: Snapshot vs Sandbox

For managing file modifications, AI coding agents split into two approaches across the industry:

| | Snapshot | Sandbox |
|---|---|---|
| **Approach** | The agent operates directly on the user's real files, recording the original state before modification (a backup file or a git commit); if something breaks, it restores from the backup | The agent operates on a copy inside an isolated environment (container/VM); if something breaks, the entire environment is discarded and the host is unaffected |
| **Rollback** | Restore files from backup | Discard the container/environment |
| **Pros** | Zero startup latency; the user operates on files directly; good interactive experience | Naturally full coverage (including bash); secure isolation; no side-effect leakage |
| **Cons** | Hard to track file changes made by bash (unless git is used as a fallback or a unified checkpoint entry is used) | Startup latency; complex environment setup; high resource consumption |
| **Best for** | Local interactive development | Unattended batch tasks, execution of untrusted code |

**It is not an either/or choice.** Leading frameworks are already moving toward running both side by side — snapshots address the day-to-day rollback experience (second-level recovery), and sandboxes address security isolation (full bash coverage).

---

## 2. Industry Comparison

### 2.1 Full Landscape Comparison

| Framework | Snapshot mechanism | Sandbox mechanism | bash coverage | Independent of user git |
|---|---|---|---|---|
| **Claude Code** | per-response snapshot, `/rewind` to roll back | `/sandbox` restricts bash write scope | Snapshot doesn't cover it; sandbox restricts write scope | Yes |
| **Cursor** | per-edit checkpoint | Seatbelt/Landlock/seccomp system-level sandbox | Covered for agent operations | Yes |
| **Hermes** | shadow git checkpoint, unified checkpoint before every tool execution | None | **Yes** (unified entry, including bash) | Yes (shadow store) |
| **Aider** | automatic git commit on every edit | None | **Yes** (git naturally covers everything) | No (pollutes user git) |
| **opencode** | git tree object snapshot | None | Yes (git fallback) | No |
| **OpenHands** | event-sourcing with replay support | Docker container (primary mode) | Full coverage inside the sandbox | N/A |
| **SWE-agent** | None | Docker sandbox (SWEEnv) | Full coverage inside the sandbox | N/A |
| **Devin** | None | Cloud ephemeral sandbox | Full coverage inside the sandbox | N/A |
| **OpenClaw** | No built-in snapshot (local mode) | Docker/Podman container | Full coverage inside the sandbox | N/A |

### 2.2 Three Ways to Implement Snapshots

There are three concrete approaches to snapshot mechanisms in the industry:

| Method | Approach | Representative | bash coverage | Independent of user git |
|---|---|---|---|---|
| **File copy** | `shutil.copy2` to a backup directory before modifying a file | Claude Code, Cursor | No (can only back up known files) | Yes |
| **Git commit** | `git commit` to the user's repository after each edit | Aider, opencode | Yes (git tracks everything) | **No** (pollutes user git) |
| **Shadow git** | Store snapshots as git tree/commit objects in an independent directory | Hermes | Yes (triggered by the unified entry) | Yes |

The three can coexist — they solve different problems:

- File copy: the fastest undo (no dependency on git; works for non-git projects too)
- Git commit: permanent history (queryable via `git log`, but pollutes user git)
- Shadow git: balances diff traceability with not touching user git

### 2.3 Terminology

| Term | Meaning |
|---|---|
| **Checkpoint** | The industry-standard term (Claude Code, Cursor, and Hermes all use it), referring to a snapshot saved before modifying a file. |
| **bash coverage** | Whether, when the agent modifies files through the bash tool (`sed -i`, `> file`, `rm`, etc.), those changes can be tracked and rolled back. This is the core challenge for the snapshot camp — edit tools (write/edit) know exactly which file was changed, but bash does not. |
| **Independent of user git** | Whether the backup mechanism uses its own storage and avoids polluting the user's git history. Aider does `git commit` directly in the user's repository, so `git log` gets flooded with AI auto-commits; Hermes and Claude Code use independent storage, keeping the user's git history clean. |
| **Shadow git** | Uses git's tree/commit mechanism to store snapshots, but in an independent directory (such as `~/.hermes/checkpoints/`), without touching the user's `.git`. Balances git's tracking capability (diff, single-file recovery) with not polluting the user's history. |
| **Unified entry trigger** | The key design in Hermes: rather than triggering the checkpoint inside each edit tool, it triggers uniformly at **the execution entry of all tools**. This way a checkpoint is also taken before bash runs, naturally covering bash's blind spot. |
| **System-level sandbox** | Uses OS kernel mechanisms (Seatbelt / Landlock / seccomp / bubblewrap) to restrict a process's file and network access scope. The process still runs on the host, but is restricted in what it can do. Millisecond-level startup. |
| **Container sandbox** | Runs the agent inside a Docker/Podman container. Full isolation — operations inside the container don't affect the host. After completion, the output is extracted via a git patch or file mount. |
| **Git worktree** | Uses `git worktree` to create an independent copy of the working directory. The agent operates in the copy; if it works out, merge back to the main line; if it goes wrong, discard it. Isolates only files, not processes or the network. |

---

## 3. OpenProgram's Approach

### 3.1 Design Principles

1. **Don't touch user git**. Neither Claude Code nor Hermes writes commits into the user's repository; this is an industry consensus.
2. **Unified entry trigger**. Following Hermes, the checkpoint is triggered uniformly before all tool executions, covering bash.

### 3.2 The Four-Layer Mechanism

Each of the four layers handles one thing, with no overlap. Removing any one of them leaves a capability gap.

```
                ┌─── read-before-edit (concurrency guard, the gate in front of all writes) ───┐
                │  before the agent writes: never read / disk changed after read → reject, make it re-read │
                └───────────────────────────┬──────────────────────────┘
                                             ▼

 ┌──────────────── Snapshot ────────────────┐   ┌──────────── Sandbox ─────────────┐
 │                                                  │   │                                         │
 │  ①  Checkpoint          ②  Shadow git            │   │  ③  Worktree        ④  System-level sandbox        │
 │     "rollback"                 "history"                 │   │     "file isolation"          "permission restriction"         │
 │     turn-level, temporary          independent store, persistent        │   │     independent copy            restricts bash scope     │
 │     doesn't touch user git           doesn't touch user git            │   │     agent enters explicitly      config toggle           │
 │     always on, automatic           on by default, automatic            │   │     on demand                default off             │
 │                                                  │   │                                         │
 └──────────────────────────────────────────────────┘   └─────────────────────────────────────────┘
```

| | ① Checkpoint | ② Shadow git | ③ Worktree | ④ System-level sandbox |
|---|---|---|---|---|
| **Approach** | **Snapshot** | **Snapshot** | **Sandbox** | **Sandbox** |
| **What it solves** | Roll back when something breaks | Permanent history + diff traceability | Discard the copy when something breaks | Restrict what bash can touch |
| **Mechanism** | Full file copy | git tree/commit objects, stored in `~/.openprogram/shadow-git/<project-hash>/` | `git worktree` isolated branch | OS kernel restrictions (Seatbelt / bubblewrap) |
| **Scope** | A single turn | Accumulated across the whole session | A stretch of experimental work | The entire session |
| **Persistence** | Temporary (GC, cap of 100 turns) | Permanent (independent git history) | Until merge / discard | In effect during the session |
| **Touches user git?** | **Never** | **Never** | Uses an independent worktree, only merges back to the main line | **Never** |
| **Trigger** | Unified entry (before all tool executions) | End of turn (auto-commits this turn's changes) | Agent explicitly calls `worktree_create` | Config toggle |
| **bash coverage** | **Yes** (unified entry trigger) | **Yes** (end-of-turn commit includes bash changes) | N/A (inside the isolated environment) | **Yes** (kernel-level interception) |
| **Default** | **Always on** | **On by default** | On demand | **Off by default** |
| **Code** | `store/snapshot/checkpoint/` | `store/shadow_git/` | `worktree/` | `sandbox/` |
| **Rollback entry** | `/rewind` | `/rewind` integration | `worktree_discard` | N/A (preventive, no rollback needed) |
| **Status** | ✅ Implemented | ✅ Implemented | ✅ Implemented | ✅ Implemented |

### 3.3 Division of Labor Between ① Checkpoint and ② Shadow git

Both run simultaneously; it is not an either/or choice:

| Dimension | ① Checkpoint | ② Shadow git |
|---|---|---|
| **Speed** | Fastest (direct file overwrite) | Fairly fast (git checkout) |
| **diff capability** | None (only the original file copy) | Yes (`git diff`, `git log`, single-file recovery) |
| **Non-git projects** | Usable | Usable (the shadow store ships its own git, no dependency on the user's repository) |
| **GC** | Yes (cap of 100 turns) | No GC needed (git compresses naturally) |
| **Primary use** | First choice for a fast undo | Permanent history traceability, diff comparison, precise single-file recovery |

`/rewind` integrates the two on rollback: it first restores files from the checkpoint (fastest), and the shadow git records remain queryable.

---

## 4. Unified Entry Trigger (the Key Change for Covering bash)

### 4.1 Implementation

The three edit tools — write / edit / apply_patch — each call `checkpoint_before_edit` **internally** to do a precise single-file backup — leave this unchanged.

bash coverage is implemented in `_execute_tool_calls` (`agent_loop.py`, the single entry for all tools):

```python
#### agent_loop.py — inside _execute_tool_calls
if tool_name == "bash":
    pre_snapshot = _snapshot_cwd(cwd)       # record file mtime+size
    result = tool.execute(...)
    _checkpoint_changed_files(cwd, pre_snapshot)  # compare, supplement a checkpoint for changed files
else:
    result = tool.execute(...)               # write/edit already have a precise backup internally
```

`_snapshot_cwd`: scans the files under cwd, recording `{path: (mtime_ns, size)}`, skipping dotfile directories.
`_checkpoint_changed_files`: compares the before/after snapshots and calls `checkpoint_before_edit` for added/modified files.

**Known limitation**: the current snapshot only scans the top-level files under cwd; changes in subdirectories are not yet covered (this could later be changed to a recursive scan).

---

## 5. Concurrency Guard: read-before-edit (the Front Gate)

`store/read_tracking.py`. The safety foundation of the whole mechanism: it guarantees the agent never blindly writes over a file that "the user just changed but the agent hasn't seen yet," so every entry that lands in ① checkpoint and ② shadow git is a **clean agent change**, and rollback won't accidentally harm the user.

It copies Claude Code's Edit/Write contract:
- **`read` records the baseline** — when reading a file, record its fingerprint `(mtime_ns, size, sha1)`.
- **Verify before writing** — `edit` / `write overwriting an existing file` / `apply_patch Update` compares before writing:
  - Never read (`NEVER_READ`) → reject, prompt to read first.
  - Read but the disk changed (`STALE`, modified by user/linter/another process) → reject, prompt to re-read. The change **is not written to disk**.
- **New files are skipped** — `write a new file` / `apply_patch Add` doesn't require reading first; record the baseline after writing.
- **A successful write refreshes the baseline** — the same file can be modified again without re-reading.

It uses a **content hash** rather than just mtime: when the user types fast, a change may land in the same mtime tick, and looking only at the timestamp would miss it. The session is resolved via the `_store` ContextVar; outside of a turn (unit tests / standalone calls) the whole guard is a no-op (`UNTRACKED` → allow).

---

## 6. Implementation Details

### 6.1 Coordination Rules

#### Rule A: When a worktree is active, the shadow git commit yields

`shadow_git.commit_turn_changes` first checks `find_active_for_session(sid)`:
- Active worktree exists → skip (the agent's changes are in the worktree copy; committing the original directory is wrong/empty).
- None → proceed as usual.

#### Rule B: /rewind = restore from checkpoint + keep shadow git queryable

When `/rewind` reverts:
1. Restore files from the checkpoint (the fastest path).
2. The shadow git history **is not rolled back** — it stays queryable, so the user can diff to see what the agent changed.
3. gitignored files / non-git folders: the checkpoint is the only fallback.

There's no need to decide between git reset vs revert; the rollback logic stays simple.

### 6.2 Full Lifecycle

Take, as an example, a session **bound to a real project directory**:

```
session start
  │
  ├─ [turn N starts]
  │
  ├─ agent wants to edit a.py
  │     [front gate] read-before-edit verifies a.py freshness (never read / already changed → reject)
  │     [unified entry] checkpoint(turn=N, a.py)   ← copy old content into <session>/checkpoints/N/ before editing
  │     ↓ actually write a.py → refresh read baseline
  │
  ├─ agent runs bash "sed -i 's/old/new/' b.py"
  │     [unified entry] record the hash of the working directory's file state
  │     ↓ actually run bash
  │     [unified entry] compare hashes, b.py changed → supplement checkpoint(turn=N, b.py)
  │
  ├─ [turn N ends]
  │     · session-git commit (conversation DAG)              ← always happens
  │     · ② shadow git commit:                      ← on by default
  │         - active worktree? → skip (Rule A)
  │         - otherwise commit all file changes of this turn to the shadow store
  │     · GC: gc_evict_old(session) ← delete old checkpoints beyond the cap
  │
  ├─ user clicks ↩ rewind or types /rewind
  │     → Rule B: restore files from checkpoint, keep shadow git queryable
  │
  └─ ……
```

### 6.3 Releasing Checkpoints

Checkpoints are stored in `<session>/checkpoints/<turn_id>/`, released as follows:

| Trigger | Implementation |
|---|---|
| **GC (soft cap of 100 turns)** | `gc_evict_old` is called by the dispatcher at the end of each turn, deleting the oldest over-cap turns by mtime |
| **Session deletion** | Deleted along with the entire session repo when it is deleted |

> Note: a checkpoint is a **full file copy** (`shutil.copy2`), deliberately not a hardlink (the agent's `open(w)` truncates the inode, and a shared hardlink would lose the original content). Disk cost is linear in files×turns, capped by the GC limit.

### 6.4 Shadow git Storage

Storage location: `~/.openprogram/shadow-git/<project-hash>/`

- One shadow git store per project directory (distinguished by path hash)
- Doesn't touch the user's `.git`, fully independent
- Auto-commits all file changes of a turn at the end of the turn
- Supports `git diff`, `git log`, single-file recovery
- No GC needed (git compresses objects naturally)

### 6.5 The Relationship Among the Four

The four layers are independent and solve different problems:

| | Rollback | History | File isolation | Permission restriction |
|---|---|---|---|---|
| ① Checkpoint | ✅ | | | |
| ② Shadow git | | ✅ | | |
| ③ Worktree | | | ✅ | |
| ④ System-level sandbox | | | | ✅ |

Coordination points:
- **Checkpoint ↔ Shadow git**: run simultaneously, coordinated uniformly by `undo` (Rule B). Checkpoint handles fast recovery, Shadow git handles permanent history.
- **Worktree ↔ Shadow git**: Rule A, Shadow git yields when a Worktree is active.
- **System-level sandbox**: fully orthogonal to the other three layers — it restricts what bash can touch, without affecting the operation of snapshots and file isolation.

### 6.6 Ad-hoc (Default Project) Sessions

For casual chats not bound to a real directory:
- ① Checkpoint: as usual.
- ② Shadow git: as usual (the shadow store doesn't depend on the user's repository).
- ③ Worktree: not applicable (no source repo).
- ④ System-level sandbox: as usual (restricting bash scope is independent of project binding).

---

## 7. User Decision Matrix

| I want | Configuration | I get |
|---|---|---|
| Like Claude Code, the undo key is enough | Default | ① Checkpoint + ② Shadow git |
| To see what the agent changed (diff) | Default | ② Shadow git provides `git diff` / `git log` |
| No extra storage at all | Turn off shadow git | Only ① Checkpoint |
| The agent makes a high-risk large change without messing up the working tree | (agent does it itself) `worktree_create` | ③ Isolation, merge if it works / discard if it fails |
| Restrict bash from touching files outside cwd | Enable system-level sandbox | ④ bash can only read/write the current project directory |
| The safest mode | Worktree + system-level sandbox | ③ + ④ file isolation + permission restriction |

---

## 8. Usage Guide

### 8.1 Web (webui)

**Rollback operations:**
- Every **user** message has a ↩ button in its top-right corner ("Rewind to here") — after clicking it:
  1. The message's text returns to the input box (re-editable)
  2. This message and all subsequent conversation are removed from the interface
  3. Files are restored to their state before this message (via checkpoint)
  4. The old conversation is preserved in the DAG as a history branch, not lost
- Type `/rewind` in the chat box — lists the recent rollback points (up to 10), each showing a summary and timestamp
- Type `/rewind N` in the chat box — roll back to the N-th rollback point (N chosen from the list)

**Sandbox:**
- Type `/sandbox` in the chat box — enable the system-level sandbox (restricts bash to read/write only the current project directory)
- Type `/sandbox` again — disable the sandbox

### 8.2 CLI

- `/rewind` — list rollback points
- `/rewind N` — roll back to the N-th point
- `/sandbox` — toggle the sandbox

### 8.3 TUI Terminal Interface

- `/rewind`, `/rewind N` — same as the CLI
- `/sandbox` — same as the CLI

### 8.4 Features That Take Effect Automatically (No Manual Action Needed)

These features run automatically in the background, and the user doesn't need to do anything:

| Feature | When it triggers | What it does |
|---|---|---|
| Checkpoint | Before each tool execution | Automatically backs up the files about to be modified (including bash) |
| Shadow git | At the end of each turn | Automatically commits this turn's file changes to the independent git store |
| read-before-edit | Before each file write | Automatically checks whether the file was modified externally, preventing overwrites |

### 8.5 Tools Available to the Agent

The only tools the agent (the LLM) can call are the worktree series, used for file isolation:

| Tool | Function |
|---|---|
| `worktree_create` | Create an independent copy of the working directory |
| `worktree_merge` | Merge the copy's changes back into the main directory |
| `worktree_discard` | Discard the copy |

Checkpoint, Shadow git, and the sandbox are all automatically running low-level mechanisms, not exposed as tools. The user interacts with them through the `/rewind` and `/sandbox` commands.

---

## 9. To Do

| Item | Description |
|---|---|
| Recursive scan for bash checkpoint | Currently only scans the top level of cwd; subdirectory changes are not covered |
| UI indication of the current session's "main rollback path" | Backend is ready, frontend pending |
| Container sandbox (long term) | Unattended scenarios such as research_agent, requiring Docker integration |

---

## 10. Sandbox Isolation — ③ Worktree + ④ System-level Sandbox

There are three ways to implement sandboxing, with isolation levels from low to high:

| Solution | Representative framework | Isolation level | Startup latency | Implementation technology | Best for |
|---|---|---|---|---|---|
| **Git worktree** | Our ③, Claude Code `--worktree` | Files only (independent copy), no process/network isolation | Second-level | `git worktree add` | High-risk code changes |
| **System-level sandbox** | Our ④, Claude Code `/sandbox`, Cursor | File system + network (process-level restriction) | Millisecond-level | Seatbelt / bubblewrap / Landlock | Local interaction, restricting bash scope |
| **Container sandbox** | OpenHands / SWE-agent / Devin | Full isolation (file/network/process) | 30–60 seconds | Docker / Podman | Unattended, untrusted code |

### 10.1 ③ Worktree — File Isolation (✅ Implemented)

The agent calls `worktree_create` to create an independent copy of the working directory; if it works out, `worktree_merge`; if it goes wrong, `worktree_discard`.

**Limitation**: it isolates only files, not processes or the network — bash can still `rm -rf /`, read `~/.ssh/`, and access the network. Suitable for "afraid of breaking the code," not for guarding against "bash going rogue."

### 10.2 ④ System-level Sandbox — Permission Restriction (✅ Implemented)

Uses OS kernel mechanisms to restrict what the bash process can do:
- **File system**: can only read/write cwd and its subdirectories, `rm ~/.ssh/id_rsa` → `Operation not permitted`
- **Network**: no direct connections, controlled through a proxy allowlist
- **Implementation**: Seatbelt (sandbox-exec) on macOS, bubblewrap on Linux
- **Code**: `openprogram/sandbox/__init__.py` (`sandbox_enabled` contextvar + `wrap_command`), `backend/local.py` (`_invocation` integration)
- **Command**: `/sandbox` toggle (CLI `_cli_chat/handlers.py` + webui `ws_actions/chat.py`)

### 10.3 The Relationship Between ③ and ④

The two solve different problems and can be combined:
- **Using ③ alone**: change things in a copy, but bash can do anything
- **Using ④ alone**: change things in the original directory, but bash is scope-restricted
- **Combined**: change things in a copy, and bash is restricted too. The safest

### 10.4 Container Sandbox (Long-term Direction)

Long-running scenarios for unattended agentic functions such as research_agent require full Docker isolation. Not done currently; will be considered once agentic functions mature.
