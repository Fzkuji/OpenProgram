# Entity Memory

## 1. Concept

Entity memory is an immutable, factual historical record backed by git storage. An "entity" is something that actually happened and can be traced back step by step.

There are two kinds of entities:

| Type | Granularity | Storage location |
|------|------|----------|
| **Session-Git** | Per conversation, one commit per turn | `<state>/sessions/<id>/` or `<project>/.openprogram/sessions/<id>/` |
| **Project-Git** | The bound user working directory | `<user-workdir>/.git/` (reuses the existing one) |

Session-Git records the conversation process (the user → LLM → tool call chain). Project-Git records the agent's actual modifications to the user's code/documents. The two are complementary: session stores "what was said", project stores "what was changed".

## 2. Storage Layout

```
~/.openprogram/                              ← get_state_dir()
├── sessions/
│   ├── <session_id>/                        ← one Session-Git repo
│   │   ├── .git/
│   │   ├── meta.json                        title, agent_id, project_id, created_at, ...
│   │   ├── history/                         DAG node files
│   │   │   ├── 000001-u-<id>.json           user message
│   │   │   ├── 000002-a-<id>.json           assistant message
│   │   │   ├── 000003-t-<id>.json           tool result (called_by = assistant)
│   │   │   └── ...
│   │   ├── context/                         per-turn LLM context materialized view
│   │   │   └── commits/<commit_id>.json
│   │   └── workdir/                         temporary working directory for this session
│   │
│   └── locations.json                       ← index: in-project session → real path
│
├── projects/
│   └── projects.json                        project registry
│
└── memory/                                  ← abstract memory layer (see virtual-memory.md)

<user working directory>/
├── .git/                                    ← Project-Git (reuses the existing one, or auto-init)
└── .openprogram/sessions/<id>/              ← session repo bound to this project
```

## 3. Session-Git Lifecycle

### 3.1 Create

**Trigger**: lazy-init when the first message is written (`SessionStore._open(id, create_if_missing=True)`).

**Artifacts created**:
- `git init` → `.git/`
- Write `meta.json` (title, agent_id, project_id, created_at)
- Create the `history/`, `context/`, `workdir/` directories

**Attribution**: each session is bound to a project at creation time:
- A working directory was specified → bind to the real Project-Git, and the session repo lands in `<project>/.openprogram/sessions/<id>/`
- Not specified → bind to the default project (a purely logical label `project_id="default"`), and the session repo lands at the home root

### 3.2 Validity

A directory is treated as a valid session if and only if:

1. The directory exists
2. A `meta.json` file exists in the directory
3. `meta.json` parses as valid JSON

Directories that do not meet the above conditions are **skipped: not listed, no error raised**. This covers:
- Test leftovers (only a `steering/` subdirectory, no meta.json)
- Manually created unrelated directories
- Corrupted sessions (meta.json cannot be parsed)

### 3.3 Title Rules

The session title is the primary identifier by which a user recognizes a conversation in the list.

#### Generation strategy (highest to lowest priority)

1. **User-set name**: a title set by the user via `/rename` or UI right-click rename always takes precedence and is never overwritten.

2. **LLM-generated summary (the preferred automatic method)**: after the first round of conversation ends (the assistant reply is complete), a background thread calls the LLM to generate a descriptive 3-7 word title.
   - **Trigger**: after `finalize_turn` of the first turn, executed asynchronously
   - **Input**: the first 500 characters of the user message + the first 500 characters of the assistant response
   - **Prompt**: "Generate a short, descriptive title (3-7 words) for this conversation. Return ONLY the title, no quotes, no prefix."
   - **Model selection**: the model used by the current session (connection already established, no extra overhead); if unavailable, the cheapest available model in the system
   - **Parameters**: `max_tokens=50`, `temperature=0.3` (highly deterministic)
   - **Post-processing**: strip quotes, strip the "Title:" prefix, truncate to 80 characters
   - **Non-blocking**: executed by a background daemon thread; failures are only logged and do not affect the user
   - **Idempotent**: the `_titled=True` flag in `meta.json`; once generated, it is not triggered again

3. **Fallback — first-message excerpt**: when the LLM is unavailable or the call fails, take the first line of the first user message, truncated to 50 characters + "…".

4. **Presentation-layer fallback**: if none of the above was triggered (the session was created through a non-dispatcher entry point, such as the harness), and the title at listing time is still empty/"New conversation"/"Untitled" → display the preview (first 80 characters of the first user message) instead.

5. **No more filtering of empty placeholders**: all valid sessions are shown in the sidebar, including freshly created conversations whose title/preview is not yet ready. (Historical note: the previous-generation architecture optimistically created an empty placeholder session when "New chat" was clicked, which required a "title placeholder + empty preview → do not show" rule to hide it; now "New chat" no longer creates a session — the conversation is created lazily by the backend only when the first message is sent, at which point content already exists, so the premise for that filter no longer holds and it has been removed.)

#### Sequence

```
User sends the first message
  → dispatcher processes the turn
  → finalize_turn:
      1. Immediately set title = first 50 characters of the first line (Fallback, ensures the sidebar is not empty)
      2. Start a background thread → LLM generates a summary → on success, overwrite title + set _titled
  → User immediately sees the excerpted title in the sidebar
  → A few seconds later the LLM title is ready → broadcast session_updated → the sidebar updates to the summary title
```

#### Design decisions

- **Why not wait for the LLM before displaying?** An LLM call takes 1-5 seconds, and the sidebar cannot be empty when the user switches to another session. Use the excerpt as a placeholder first, then update asynchronously.
- **Why use the current model?** To avoid extra API key / connection overhead. The title-generation prompt is very short (< 1200 tokens), a negligible cost for any model.
- **Why trigger only once?** To avoid the title flipping back and forth as the conversation goes deeper. The first round best represents the user's intent.
- **Why temperature=0.3?** Slightly creative but mostly deterministic. Re-running on the same conversation opener will not produce a completely different title.

### 3.4 Discovery

When listing all sessions, there are two sources:

1. **Global directory scan**: traverse every subdirectory under `<state>/sessions/` and run validity checks one by one
2. **locations.json index**: records the paths of sessions that land inside project directories, validated one by one

The two sources are merged, deduplicated, and sorted by `updated_at` in descending order.

Display rules:
- Validity check passes → shown (including freshly created conversations whose title/preview is not yet ready; empty placeholders are no longer filtered)
- Validity check fails → skipped

The sidebar and the Chats page use the same set of display rules.

### 3.5 Read/Write

**Write**:
- `append_message(session_id, msg)` → synchronously write `history/NNNN-<role>-<id>.json` + update the in-memory index
- `commit_turn(session_id, message)` → one git commit when the turn ends (not one per message)

**Read**:
- `get_branch(session_id, head_id)` → traverse the DAG along parent_id edges and return the rendered message list
- `get_nodes(session_id)` → raw `Call` objects (including tool-call details)
- `session_commits(session_id)` → git log (turn granularity)

**Branch/retry**:
- DAG retry → git branch (`retry-<assistant_id>`)
- Switching the DAG head ↔ git checkout

### 3.6 Management

**Metadata update**: `update_session(session_id, title=..., project_id=..., ...)` → write `meta.json` + update the in-memory index

**Cache**:
- Maintain an `OrderedDict[session_id → (GitSession, SessionMemoryIndex)]` in memory
- LRU, cap=256 (configurable via env `OPENPROGRAM_SESSION_CACHE_CAP`)
- Eviction is lossless: rebuilt from disk on the next access
- Thread-safe: per-session lock + global store lock

**Project binding**:
- The `project_id` field of `meta.json`
- Sessions bound to a real project land inside the project directory (`locations.json` records the path)

### 3.7 Deletion

**Manual deletion**: `delete_session(session_id)` →
1. Remove from the in-memory cache
2. Close the associated runtime (if any)
3. `shutil.rmtree()` the entire session directory (including `.git/`)
4. If there is an entry in `locations.json`, remove it

**Cascading effects**:
- Provenance pointers in abstract memory that reference this session become dangling
- They are automatically skipped on query via the validity check (session directory does not exist → returns None)
- No explicit cleanup of virtual-layer records is needed

**Frontend entry points**:
- Sidebar right-click menu → Delete
- Chats page (to be added: right-click delete)

### 3.8 Garbage Collection

| Scenario | Handling |
|------|------|
| Directory without `meta.json` | Skipped when listing (§3.2 validity check) |
| Has meta.json but history is empty and title is the default value | Listed, marked as placeholder, may be hidden by the frontend |
| Long-inactive session | **Not deleted automatically** (user data, the user decides) |

Design decision: no automatic TTL. Rationale: a session is the user's conversation history, it belongs to the user as user data, and should not be cleaned up automatically by the system. If space reclamation is needed in the future, it is decided by the policy layer (user configuration), not implemented at the store layer.

## 4. Project-Git Lifecycle

### 4.1 Create

**Trigger**: `resolve_project(path, name)` — when the user binds a working directory in the UI, or when a session specifies a workdir.

**Behavior**:
- The directory already has `.git/` → reuse it
- The directory has no `.git/` → `git init`
- Register it in `projects.json`

### 4.2 Write (Auto-commit)

At the end of a turn, if the session is bound to a real project and the agent has modified files:

```
if working_tree_clean_before_agent:
    git add -A
    git commit -c user.name="agent (<model> via OpenProgram)"
              -m "[agent <session_id>] turn <N>: <user msg first 60 chars>"
else:
    skip + UI warning (do not pollute the user's uncommitted changes)
```

Agent commits are identified by an overridden user.name/email to distinguish them from the user's manual commits.

### 4.3 Read

- `ProjectGit.log(limit)` → agent-attributed commits
- `project_commits(project_id)` → the read primitive of the provenance layer

### 4.4 Deletion

Unbinding a project ≠ deleting the git history. The user's `.git/` contains the user's own commits and must not be deleted by OpenProgram.

When unbinding:
- Remove the registration from `projects.json`
- The `project_id` of the associated session is unchanged (historical pointer)
- The session repo stays inside the project directory (it is not moved back to the home root)

## 5. Relationship to Abstract Memory

Entity memory is the **single data source** for abstract memory. The distillation pipeline (Phase 2) reads the DAG nodes of session-git + the commit history of project-git, extracts events and relationships from them, and writes them into the timeline/graph of abstract memory.

Each abstract memory carries a `Provenance` pointer back to the entity layer:
```python
@dataclass
class Provenance:
    project_id: str
    session_id: str
    node_ids: tuple[str, ...]
    commit: str | None
    event_time: float
    ingestion_time: float
```

See [`virtual-memory.md`](virtual-memory.md) for details.
