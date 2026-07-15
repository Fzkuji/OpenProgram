# Entity Memory on Git: Session-Git + Project-Git

## Mental Model

"Entity memory" = things that actually happened, replayable step by step. Git is natively this paradigm (a commit
is immutable, the log is a timeline, checkout is a time machine). No more home-grown DAG-as-truth —
just reuse Git directly.

Overall memory model (the user's own words, 4+1):

```
Entity memory (raw, replayable)
  ├─ Session memory     ← session-level git (one commit per turn)
  └─ Project memory     ← project-level git (bound to the working directory, file edits auto-commit)

Abstract memory (derived, distilled from entity memory)   ← do this after entity memory is wired up
  ├─ Journal (timeline)
  └─ Wiki (knowledge graph)

Core (self-knowledge)                 ← already have memory/core.md
```

This document only plans **entity memory** (the first two blocks). Abstract memory is deferred — we'll design the mapping once entity memory is working.

## 1. Relationship to the Existing DAG

**Not a replacement — an overlay.** The DAG (SQLite) stays; git is synced incrementally.

- DAG strengths: fast SQL queries, indexes on caller / parent_id / seq, commit chain already running
- Git strengths: mature tooling (log / diff / checkout / revert), atomic persistence, the user can just cd in and look
- Dual write: after writing the DAG node for each turn, also git commit a JSON serialization

The read path still goes through the DAG (commit etc.). Git is the entry point for **replay + backup + user visibility**.

## 2. Session-Git

### 2.1 File Layout

One independent git repo per session:

```
~/.openprogram/sessions/<session_id>/
├── .git/
├── meta.json              # session metadata (title, agent_id, model, ...)
├── messages/
│   ├── 000001-u-abc123.json    # user message
│   ├── 000002-a-def456.json    # assistant message
│   ├── 000003-t-fc_xxx.json    # tool result (caller=def456)
│   ├── 000004-a-ghi789.json
│   └── ...
└── commits/
    └── commit_xxx.json      # one per context commit (optional, skip for now)
```

File names sort by `<seq>-<role[0]>-<node_id>.json` — the numeric prefix makes the ls order equal the chronological order, without relying on filesystem sort order.

Each message file's content is just the JSON serialization of the DAG node:
```json
{
  "id": "abc123",
  "role": "user",
  "content": "你好",
  "predecessor": null,
  "caller": null,
  "created_at": 1779500000.0,
  "metadata": {...}
}
```

### 2.2 Commit Timing

One commit at the end of **each turn** (not one per message — that's too fine-grained). End of turn =
when dispatcher.process_user_turn() returns a TurnResult.

A turn contains:
- 1 user message
- 1 assistant placeholder → eventually filled with content
- N tool results (caller = assistant)

commit message:

```
turn <N>: <first 60 chars of user msg>

assistant: <first 80 chars of reply>
tools: read, grep × 3, list

[meta: turn took 12.3s, 18 tools, 4521 tokens]
```

### 2.3 Branch / Retry

The DAG already has retry branches (multiple conv-children sharing a parent). Map this onto git:

- the session repo's default branch = `main` (= the current HEAD path)
- when a new retry is triggered on the DAG, create a git branch `retry-<assistant_id>` and jump to it
- switching the DAG head → checkout the git branch
- the user's "switch branch" in the UI → backend `git checkout`

A git branch and a DAG branch are two views of the same thing.

### 2.4 Replay UI (Claude Code style)

Add prev / next buttons at the top of chat / on the right of the history area:

```
[← prev turn]  Turn 7 / 12  [next turn →]    [view full history]
```

- prev: `git checkout HEAD~1` + replay the UI to that state
- next: walk the reflog backward
- view full history: pop a timeline (one line per commit, click to view the message content)

At the implementation level: WS action `git_history(session_id)` returns the commit log, `git_checkout(session_id, commit_sha)` switches to a given state, and the dispatcher continues the next user message on top of that commit.

### 2.5 Dual-Write Consistency

**Main path**: the dispatcher writes the DAG (status quo).
**Mirror**: after a turn completes, asynchronously serialize all of that turn's nodes to the session repo + commit.

It's async because a git commit takes ~100-500ms and shouldn't block the user. Run it in the background with `threading.Thread`; on failure just log, don't block.

Conflict probability: extremely low (one repo per session, serial commits). File lock as a fallback.

### 2.6 Migrating Old Sessions

On startup, scan the existing SessionDB and run a one-time backfill for sessions without a repo: walk the nodes by seq and commit them out turn by turn. Run it once, incremental afterward.

## 3. Project-Git

### 3.1 Concept

A Project = a long-running unit of work. For example "wiki-agent refactor"; it:
- is associated with a **filesystem directory** (the user's code repo / docs repo, e.g. `/Users/x/code/wiki-agent`)
- is associated with **multiple sessions** (the user's many conversations on this project)
- has a name / description / status

A Project itself **is the user's git repo** (if not yet init'd, the agent inits one for them).

When the agent runs tools in the project working directory to edit files → auto-commit to the project repo.

### 3.2 Association

```python
class Project:
    id: str
    name: str
    workdir: str               # absolute path, a directory in the user's filesystem
    sessions: list[str]        # list of session ids (who is working in this project)
    status: "active" | "paused" | "done"
    created_at: float

# Reverse association:
Session.metadata["project_id"] = "proj_xxx"   # added to the sessions table
```

A session can exist standalone (no project, same as today). When there is a project, the commits triggered by the agent's file edits land in the project repo.

### 3.3 Agent File Edits Trigger Auto-Commit

When the dispatcher calls tools today, side-effecting tools like write/edit/apply_patch edit files in the project workdir. Hook:

```
end of turn:
  if session.project_id:
    proj = load_project(session.project_id)
    with cwd(proj.workdir):
      if git_status_dirty():
        git add -A
        git commit -m "[agent <session_id>] turn <N>: <user msg first 60 chars>"
```

Mark the committer "agent (claude-sonnet-4.7 via OpenProgram)" to distinguish from commits the user made manually.

### 3.4 User Manual Edits vs Agent Edits

When the user edits files in their IDE, that's of course not a commit triggered by OpenProgram. A few strategies:

- **Strategy A (recommended)**: before each turn ends, the agent first runs `git status` to check for dirty state.
  - all changes are the agent's → the agent commits once
  - there are uncommitted user changes → the agent **does nothing** (don't pollute the user's working tree) and warns in the UI: "you have uncommitted changes, the agent's edits won't be auto-committed for now"
- **Strategy B**: the agent uses a dedicated branch `agent/<session_id>`, switches to that branch to commit, leaving the user branch (main) untouched. The user decides whether to merge afterward.

I lean toward **A** — simple and intuitive. The user is responsible for managing git themselves; the agent only commits when the tree is clean.

### 3.5 Sessions Not Bound to a Project

Same as today — fully standalone. Not every session is forced to belong to some project.

"Create project" in the UI is an explicit action — the user picks a directory + gives it a name. An existing session can "join" a project, and subsequent work commits to it.

## 4. UI / Entry Points

### 4.1 Session History Replay

At the top of chat / in the right column:
- a timeline view (one line per commit)
- "← prev turn" / "next turn →" buttons
- pick a commit → replay to that state, newly sent messages fork from there

Coexists with the current DAG history view (the DAG shows branch structure, the git timeline shows chronological order).

### 4.2 Projects Panel

Add a "Projects" section to the left sidebar:

```
─ Projects ─────────
  ● Wiki Agent Refactor   2 sessions  ●  active
  ○ DAG Visualization    1 session   done
  ○ ...
  + New Project
```

Clicking a project → enter the project detail page: name / workdir / associated sessions / commit history / abstract-memory entry point (reserved for Phase B).

New project flow: pick a directory → name it → create / reuse a git repo → associate the current session (optional).

### 4.3 Project Indicator at the Top of Chat

If the current session is associated with a project, the top status area shows the project name + an abbreviated workdir; clicking it goes to the project page.

## 5. Implementation Phasing

Phase B (what we're designing here): wire both Project and Session to git
↑ start once the user confirms

**Phase B.1: Session-git infrastructure** (~1 day)
- new module `openprogram/memory/session_git/` (init / commit / log / checkout wrappers)
- hook at the end of dispatcher.process_user_turn: run the commit on a background thread
- backfill script: old sessions → generate git repos
- WS actions: `git_session_log`, `git_session_checkout`

**Phase B.2: Project schema + UI** (~1-2 days)
- DB table `projects` (id, name, workdir, status, ...)
- add `project_id` to the sessions table
- WS: `list_projects`, `create_project`, `add_session_to_project`
- add a Projects section to the left sidebar

**Phase B.3: Project-git auto-commit** (~1 day)
- session.project_id already bound → run the project commit hook after a turn
- Strategy A (commit when clean, warn when dirty)
- UI warning banner

**Phase B.4: Replay UI** (~1-2 days)
- prev/next at the top of chat + timeline view
- WS action calls git_checkout to replay history

**Phase B.5: Migrate old data**
- run backfill over all existing sessions
- existing git projects can be imported as Projects

Total: 5-7 days of work, the bulk being Phase B.1 + B.4.

## 6. Key Invariants

1. **The DAG is the current source of truth, Git is the mirror.** A git failure doesn't affect the DAG. Not the other way around — git landed but the DAG not written is dirty data.
2. **Session-git is one commit per turn**, not subdivided by message.
3. **Project-git favors cleanliness**: the user's working tree must not be polluted by the agent. When there are uncommitted changes the agent skips the commit.
4. **Replay doesn't break the DAG**: a checkout is a read-only view; when the user sends a new message it forks a new branch on the DAG based on that commit.
5. **Abstract memory comes after entity memory is wired up** — don't optimize prematurely.

## 7. Risks

- **Async git commit failure**: invisible to the user, silently dropping data. Mitigation: background-thread retry on failure + validate DAG seq vs git commit count on startup, trigger a backfill on mismatch.
- **Project workdir is not a git repo**: auto `git init` on the agent's first commit. A repo the user already has under git: reuse directly.
- **Multiple sessions concurrently editing the same project**: a file lock serializes project commits. In the extreme case it degenerates to a queue.
- **Semantics of replay + continuing to chat**: after the user goes back to turn 5 and sends a new message, is it a fork or an overwrite of what came after? I lean toward fork (the DAG already has this concept via retry), which maps naturally to a git branch.

## 8. Comparison with Claude Code

- Claude Code also supports replay (it has a "rewind to previous user message" UI)
- the implementation isn't public, but most likely it treats session messages as documents + uses some commit-like mechanism
- our design **uses git explicitly**, so the user can directly `cd ~/.openprogram/sessions/<sid>` to view history — higher transparency

## 9. Relationship with the Existing commit chain

No conflict. The commit chain is "the context view the LLM sees", while git is "the history that actually happened" — two layers:

- DAG nodes (the raw source of truth) → git commits (persisted mirror)
- the ContextCommit chain (the LLM's view) → not in git (derived, recomputable)

A commit can be selectively exported to git (e.g. the user wants to see "what the LLM saw back then"), but it's not mandatory.

---

Shall we go ahead and start Phase B.1 (session-git infrastructure) per this plan? Or adjust some design points first (workdir association / branch strategy / trigger frequency).
