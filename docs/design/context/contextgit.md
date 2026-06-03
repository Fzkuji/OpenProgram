# ContextGit — context as a git repo

> Rename note: not "ConvGit". The versioned object isn't just the text
> transcript — it's the whole **context** (prompts, assistant responses,
> function execution trees, tool outputs, intermediate state). Git is the
> storage model; context is what's stored.

**Status**: proposal, not implemented.
**Goal**: unify "retry", "edit", "branch into new conversation" and "manually
fork at an agentic function's intermediate step" under one data model with
one set of storage primitives.

## Why git

OpenProgram is not a plain chat UI. Every user turn can launch an agentic
program whose side-effects (bash, file writes, tool calls) form a whole
execution tree. "Retry a message" means "run the tree again"; "edit a
message" means "run the tree with different inputs"; "branch into a new
conversation" means "same history up to here, different future". These are
all the same operation under a git-like DAG — only the trigger differs.

Git's primitives map naturally:

| git                 | ContextGit                                           |
|---------------------|---------------------------------------------------|
| commit              | a turn (user msg, assistant msg, or function run) |
| parent pointer      | what the turn was responding to                   |
| fork (siblings)     | retry / edit — same parent, divergent children    |
| HEAD                | what the UI is currently showing                  |
| branch              | named pointer to a commit                         |
| clone / new repo    | "branch into a new conversation" = new Session    |
| blob                | a content or tree payload (hashable, deduplicable)|
| merge               | **not supported** — dialog DAGs never merge       |

The working tree (filesystem workdir) stays outside git. Switching HEAD
displays an older version in the UI but does **not** roll back disk state —
the user owns side effects, same way `git checkout` doesn't rerun your tests.

## Two layers

```
┌──────────────────────────────────────────────────────┐
│  Session (display layer)                             │
│    - id, title, branch_ref                           │
│    - which commit is HEAD right now                  │
│    - rendering preferences                           │
│  (Multiple Sessions can co-exist on one ContextGit.)    │
└────────────────────────┬─────────────────────────────┘
                         │ reads / writes through
┌────────────────────────▼─────────────────────────────┐
│  ContextGit (storage layer, the "database")             │
│    - Object store: commits, content blobs, trees     │
│    - Append-only, content-addressed (future: hash)   │
│    - Branches: name → commit_id                      │
│    - Operations: commit(parent, …), checkout(id),    │
│      log(branch), diff(a, b)                         │
└──────────────────────────────────────────────────────┘
```

A `ContextGit` repo represents one **project** — a related cluster of
conversations, shared commit objects. Typically one repo per OpenProgram
project / workspace.

A `Session` represents one **conversation view** — "chat #42 in this
project". Multiple sessions on one repo share objects. Branching a session
creates a new session pointing at the same commit with a divergent future.

## Data model

### Commit

```python
@dataclass
class Commit:
    id: str                      # uuid now; future: sha256(content) for dedup
    parent_id: Optional[str]     # None = root
    kind: Literal["user_msg", "assistant_msg", "function_run"]
    author: str                  # "user" | "agent" | program_id
    content_ref: str             # points to a Blob (text / structured payload)
    tree_ref: Optional[str]      # points to a TreeView (function_run only)
    timestamp: int               # ms since epoch
    metadata: dict               # provider, model, thinking level, tool ids, …
```

Commits are **immutable** once persisted. Edits never mutate — they create
a sibling commit.

### Blob

```python
@dataclass
class Blob:
    id: str                      # content hash (future) or uuid (v1)
    kind: Literal["text", "assistant_response", "function_payload"]
    data: bytes                  # serialized JSON or text
```

Every content payload is a blob. Blobs are interned by hash — identical
prompts / tool outputs share storage.

### TreeView

```python
@dataclass
class TreeView:
    id: str
    root_node: dict              # serialized ContextTree root
    # Nodes can also be interned as blobs later; v1 can serialize whole tree
    # as one blob for simplicity.
```

### Branch

```python
@dataclass
class Branch:
    name: str                    # "main", or synthetic "retry-3" for anonymous forks
    commit_id: str               # tip
```

### Session

```python
@dataclass
class Session:
    id: str
    repo_id: str                 # which ContextGit
    title: str
    branch: str                  # which branch this session follows
    head_override: Optional[str] # when user checks out an old commit to look at
    created_at: int
    last_active_at: int
    metadata: dict
```

## Operations

### ContextGit API (storage)

```python
class ContextGit:
    def commit(self, *, parent_id: Optional[str], kind, author,
               content: bytes, tree: Optional[dict] = None,
               metadata: dict = {}) -> Commit: ...

    def get(self, commit_id: str) -> Commit: ...
    def get_blob(self, blob_id: str) -> Blob: ...
    def get_tree(self, tree_id: str) -> TreeView: ...

    def children(self, commit_id: str) -> list[Commit]: ...
    def siblings(self, commit_id: str) -> list[Commit]: ...
    def ancestors(self, commit_id: str) -> Iterator[Commit]: ...

    def set_branch(self, name: str, commit_id: str) -> None: ...
    def get_branch(self, name: str) -> Optional[Branch]: ...
    def list_branches(self) -> list[Branch]: ...

    def log(self, from_commit: str) -> Iterator[Commit]:
        """Walk parents until root. Same as `git log`."""
```

### Session API (view)

```python
class Session:
    def head(self) -> Commit: ...
    def checkout(self, commit_id: str) -> None: ...
    def linear_history(self) -> list[Commit]:
        """Walk from HEAD back to root along parent pointers. The UI's
        visible conversation transcript."""

    def commit_turn(self, *, kind, author, content, tree=None, metadata={}) -> Commit:
        """Append a commit under current HEAD, advance HEAD. Used for
        normal send-message operations."""

    def retry(self, commit_id: str) -> Commit:
        """Create a sibling of `commit_id` with the same content. For
        assistant messages this means re-sampling the LLM; for
        user turns or function runs this means re-running with the
        same input. Advances HEAD to the new sibling."""

    def edit(self, commit_id: str, new_content: bytes) -> Commit:
        """Create a sibling with different content. Same structural
        effect as retry — a new branch forks at `commit_id`'s parent."""

    def fork_into_new_session(self, commit_id: str, title: str) -> "Session":
        """Take the current ContextGit, create a new Session whose HEAD is
        `commit_id`. Equivalent to 'open a new conversation from this
        point'."""
```

## Rendering: what the user sees

Given a `Session`, the UI renders:

1. `linear_history()` = transcript from root to HEAD (top-to-bottom).
2. For each commit with siblings: a `< N / M >` indicator showing sibling
   count and which one is currently selected (HEAD's ancestor on the
   displayed path). Clicking arrows invokes `checkout`.
3. Assistant replies / function trees render from the commit's `tree_ref`.
4. Timestamp shown next to each turn; hover reveals full ISO date.

## Operations → user actions mapping

| User action                       | ContextGit / Session op                          |
|-----------------------------------|-----------------------------------------------|
| Send new message                  | `commit_turn(kind="user_msg")`                |
| Agent replies                     | `commit_turn(kind="assistant_msg" or "function_run")` |
| Retry assistant message           | `retry(commit_id_of_assistant_msg)`           |
| Edit user message                 | `edit(commit_id_of_user_msg, new_content)`    |
| Switch version `< N / M >`        | `checkout(sibling_id)`                        |
| "Branch into new conversation"    | `fork_into_new_session(commit_id, title)`     |
| Mid-tree edit inside function run | (Phase 2) `edit_tree_node(commit_id, node_id, new_content)` — produces a new sibling whose tree shares the unmodified prefix |

## Persistence

### v1 — SQLite + JSON blobs on disk

- SQLite DB per repo: `commits`, `branches`, `sessions` tables.
- `objects/` directory: one file per blob/tree commit, filename = id.
- JSONL changelog (`oplog.jsonl`) mirrors every write for forensic replay.

Append-only semantics give us durable history, easy backup (rsync), and
crash safety.

### v2 — real content addressing

Switch `id` generation to `sha256(canonical_json(data))`. Commits become
naturally deduplicated: two sessions hitting the same prompt share the
same commit. Branches become forks on the DAG, not on storage.

v1 → v2 migration: backfill hashes in a one-shot script; DB schema
doesn't change because `id` is already opaque.

## Phase plan

**Phase 1 — ContextGit + Session core**

- `openprogram/convgit/` module with `ContextGit`, `Session`, `Commit`,
  `Blob`, `TreeView`, `Branch`.
- SQLite persistence.
- Unit tests for commit / checkout / retry / edit / log / siblings.
- CLI debug helper: `openprogram convgit log <session>`.

**Phase 2 — Wire into the chat server**

- Replace current `ConversationStore` with `Session` on top of `ContextGit`.
- REST endpoints become thin wrappers:
  - `POST /api/conv/send` → `session.commit_turn(...)`
  - `POST /api/conv/retry` → `session.retry(...)`
  - `POST /api/conv/edit` → `session.edit(...)`
  - `POST /api/conv/checkout` → `session.checkout(...)`
  - `POST /api/conv/fork_new` → `session.fork_into_new_session(...)`
  - `GET /api/conv/log` → `session.linear_history()` + sibling counts
- WS protocol carries `commit_id` on every streamed event.
- Migration of existing JSONL conversations into the new repo (one-shot
  script, old files archived).

**Phase 3 — Frontend**

- Chat transcript: render from `linear_history()`, hover-shown timestamps.
- Per-commit action bar: Copy / Retry / Edit (pencil) / `< N / M >`.
  - Edit / Retry disabled while an agent run is active — server sets a
    `run_active: true` flag on the session; UI greys the buttons with a
    tooltip "Wait for current run to finish or click Stop".
- Right-side panel: dual-mode switcher at the top.
  - **Tree mode** (existing): shows current run's context tree.
  - **History mode** (new): shows the ContextGit DAG as a commit graph —
    nodes for each commit colored by author (user / agent / program),
    edges for parent pointers, current HEAD highlighted. Click a node
    to `checkout`.
- "Branch into new conversation" already exists; rewires to
  `fork_new_session` instead of the current conv-copy hack.

**Phase 4 — Mid-tree editing inside function runs** (deferred)

- Tree viewer gets per-node "edit & rerun from here".
- New commit's `tree_ref` points at a tree where the prefix is shared
  with the old tree (structural sharing becomes meaningful here).
- Introduces content-addressing for tree nodes.

## Non-goals

- No merge. Dialog DAGs only diverge.
- No distributed sync (no push/pull). Single-machine for now.
- No filesystem rollback on checkout. Workdir state is user-owned.
- No rewriting history. All commits are permanent once written; "delete"
  is a UI-level hide, not a data-layer op.

## Decisions

- **Checkout never triggers a rerun.** Switching HEAD is purely display —
  the UI re-renders the transcript / tree from stored commits, never
  re-executes.

- **Edit is disabled while an agent is running.** The Edit and Retry
  buttons on prior turns go grey until the active run finishes (or the
  user clicks Stop). Forking mid-run would leave the active execution
  tree parented to a commit that's about to become "old" — confusing
  and error-prone. Wait-or-stop is a clearer contract.

- **Branches are a first-class UI concept.** The right-side panel
  (currently showing the function-run node list) becomes dual-mode:
  - "Tree" mode: current run's context tree (what it shows today)
  - "History" mode: full ContextGit commit graph — a DAG visualization
    showing every retry, edit, and fork as branching lines, like a
    git GUI. Clicking a commit dot = `checkout`.

  The two modes coexist because they answer different questions: "what
  did the agent just do" vs "what has this conversation been through".
