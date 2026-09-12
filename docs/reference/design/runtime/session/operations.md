# Session Operations

Each operation is written out end to end, from trigger to disk to frontend.

---

## Startup

When the process starts, SessionStore runs a one-time initialization:

1. Read `index.json` and load it into the in-memory `_index` dict.
2. If the file does not exist or JSON parsing fails, scan both the default and
   nested project layouts plus durable locations and rebuild `_index` from
   `meta.json`, then write `index.json`.
3. Reset every `status=running` entry to `idle`; this repairs the session-list
   status left by a dead worker. Execution recovery is separate: the
   execution subsystem may resume durable checkpoint-backed operations through
   its own recovery path.
4. Remove stale unarchived empty shells: a session older than one hour with
   no `history/` directory is deleted. Archived sessions and sessions whose
   location is unreachable or covered by a migration journal are retained.

Startup does not expire archived sessions and does not enforce a session-count
capacity limit. Explicitly archived sessions remain until the owner deletes
them.

Handling of half-broken sessions:
- Has `meta.json` but no `history/` → an old unarchived shell is eligible for
  deletion; a recent, archived, unreachable, or migrating session is retained.
- Has `history/` but no `meta.json` → it cannot be rebuilt into the registry
  and is not exposed as a session.

---

## Creating a session

Three entry points can trigger creation:

| Entry point | Scenario |
|------|------|
| `dispatcher.process_user_turn` | When a user sends a message and the session does not exist, create it |
| `channel handler` | Create it when a channel message arrives |
| `session_context` | When the CLI / research harness enters the context and the session does not exist, create it |

No other place creates a session.

### Full flow

```
Caller calls create_session(session_id, agent_id, source=..., ...)
  → Resolve placement: <state>/sessions/<session_id>/ for the default project,
    or <state>/sessions/projects/<project_id>/<session_id>/ for a bound project
  → Write meta.json (id, agent_id, title, created_at, updated_at, source, status="idle", ...)
  → Write the registry: _index[session_id] = summary entry
  → Atomically write the registry to disk (temp file → os.rename)
  → No broadcast (the frontend discovers the new session via list_sessions)
```

### Atomicity

For the dispatcher and the channel handler, creation and writing the first message are atomic — `append_message` is called immediately after creation, so no empty shell is produced.

`session_context` creates the session in `__enter__` (because the subsequent ContextVar loading needs a valid session). If it exits abnormally before writing any message, an empty shell is produced, which is handled by the startup cleanup.

---

## Main project binding

A session's **main working directory** is the path of the project it is bound to. The binding is chosen freely while the chat is still a draft, and **freezes when the first real message commits**. After that turn, a different project cannot be bound to the session. The session repository is already under the application state root, grouped by the stable project id; changing the project's path does not change the session-to-project binding or the repository id.

Additional working directories are the opposite and are meant to be: they are added and removed at any point in the session's life. See [additional-working-directories.md](../additional-working-directories.md).

### Where the choice is made

```
draft chat, project picked in the composer chip
  → pendingProjectsByChat[chatKey]  (frontend store, nothing sent yet)
  → first chat frame carries project_id
  → handle_chat creates the session WITH that project → repo lands under
    application state/projects/<project_id>/<session_id>
  → chat_ack sends the idempotent set_session_project (label + reverse index)
```

`set_session_project` accepts a bind naming the project the session already has, at any age — that is what the post-ack idempotent bind above sends, and it arrives after the first turn is committed. A bind naming a **different** project on a session that already has turns is rejected with `project is frozen after the first turn` (`ws_actions/project.py:FROZEN_ERROR`). The composer chip matches: for a session with an id it renders the bound project read-only instead of the picker.

### Repairing a missing directory

A frozen main directory can still be **relocated**, and only for repair: the folder was moved or renamed on disk and the project now points at nothing. `project_workdir_for` returns `None` in that state — it never silently substitutes the default project's home directory. New bound-project work is blocked while the project is missing, replaced, pending, migrating, or otherwise unavailable; the UI receives the location state and missing path so it can offer repair. The session repository remains readable while repair is pending.

The repair is the `relocate_project` ws action:

```
relocate_project {session_id, project_id, path}
  → project_store.relocate_project: validate the new directory, rewrite the
    project's path, KEEP its id (sessions, settings and the frozen binding all
    hang off the id)
  → record_relocate: append the graph node below
  → project_relocated {ok, old_path, path, node_id} + a fresh projects_list
```

It changes the **project's path**, never the session→project binding, which is why it stays legal after the freeze. Every session bound to that project keeps the same stable project id and application-owned session repository. Legacy workdir copies are migrated through the journal and durable location index. The default project refuses to relocate: its display path is the home directory and is restored on every `get_default_project` read; it has no project Git repository of its own.

### Following a move automatically

The project's identity is its stable id; the path is a mutable attribute. Codex matches recorded cwd against recent turn context; DeepSeek validates workspace paths against an immutable session header cwd. Those mechanisms do not track arbitrary folder moves, and they do not mean that moving a folder deletes history. OpenProgram stores conversations under the application state directory and treats the working folder as a location. Three mechanisms reconnect a moved folder:

1. **Native identity, not a HOME scan.** On startup, access, volume reconnect, or a directory/ancestor event, OpenProgram resolves a stored bookmark or inode. A match updates the project path. There is no minute-by-minute walk of HOME.
2. **Application-owned session placement.** Bound conversations live at
   `sessions/projects/<project-id>/<session-id>/`. Relocating a project does
   not move already-centralized chat files. Legacy workdir copies migrate
   through a journaled operation. Per-turn file undo recovery remains in the
   destination's sibling `.file-recovery/<session_id>/<turn>/` tree, outside
   the session Git repository.
3. **Opening a folder never auto-adopts copies.** Names, Git remotes, Git contents and leftover `.openprogram/sessions` markers do not prove continuity. Manual **Locate folder** may replace identity after showing the expected old path and revision, and it refuses a destination already owned by another project.

Startup cleanup respects the same reality: a project-bound session whose
recorded location is unreachable, or whose migration journal is active, is
**not** treated as an empty shell. It is retained until location repair or
migration resolves it.

The composer's draft picker lists `path_missing` projects (clicking one opens the locate flow instead of selecting it), and the `/projects` page shows the warning with a "Locate folder…" action; neither surface silently hides a project whose folder moved.

### Attachments and recovery

Browser uploads are decoded and saved before dispatch under
`<session repository>/workdir/attachments/`. The saved path is written into
the user message marker, and the files are included in the session repository
commit at turn end. The browser's IndexedDB cache is for unsent composer
drafts; it is cleared after publication, so it is not the durable session
attachment store. Channel attachments remain in their channel roots under
`<state>/channels/*/accounts/*/attachments`.

During legacy project migration, the migration journal inventories any
external recovery tree, copies both the session and recovery data under
`<state>/sessions/.migration/staging/`, verifies them, then publishes the
session under `projects/<project_id>/<session_id>/` and recovery data under its
sibling `.file-recovery/<session_id>/<turn>/` tree before the location
authority is updated. A missing or failed publication keeps the project in a
non-available state and prevents new bound-project work.

Historical marker compatibility follows the storage rule in
[storage.md](storage.md): only the exact legacy session attachment shape may
be resolved by the same session id to its unique current repository, with
attachment-root containment and symlink/traversal checks. No arbitrary old
absolute path is remapped.

### The relocate record node

The move changes where every later turn runs, so it is recorded in the session graph rather than mutating the registry silently (`openprogram/store/project/relocate_node.py`):

| Field | Value |
|---|---|
| `role` | `code` |
| `name` | `project/relocate` |
| `caller` | `ROOT` |
| `predecessor` | `None` |
| `output` | `<old path> → <new path>` |
| `metadata` | `{display: "runtime", project_id, old_path, new_path}` |

The shape follows `context/system_prompt` (dag/overview.md §3, §7): the write invariant constrains only conversational nodes, and because `caller` is set the store leaves head where it is — recording a relocation never moves the branch tip. The name is deliberately **not** under `context/`: those are pipeline machinery hidden from the transcript, while a relocation is a user action worth seeing.

---

## Writing a message

```
Caller calls append_message(session_id, msg)
  → Append a JSON node to the conversation DAG in history/
  → Persist meta.json, including the DAG head when the conversational chain advances
  → If msg.role == "user":
      → preview = take the first 80 characters of msg.content
      → _index[session_id]["preview"] = preview
      → _index[session_id]["updated_at"] = time.time()
      → Mark the registry dirty (write to disk with a 5-second debounce)
  → No session-list broadcast (message content is pushed through a separate streaming channel)
```

The raw node write and the `meta.json` update happen before the turn-end Git
commit. A successful turn then commits the session repository with
`GitSession.commit_all`; `session_commits()` reads those Git commits as a
separate per-turn timeline. Git `HEAD` is therefore not the conversation DAG
head.

### preview truncation

```python
def _truncate(text: str | None, max_len: int = 80) -> str | None:
    if not text:
        return None
    t = text.strip().replace("\n", " ")
    return t[:77] + "…" if len(t) > max_len else t
```

### Throttling registry writes to disk

When `append_message` updates the registry, memory is updated immediately, but the disk write is debounced (at most one write per 5 seconds). The registry is flushed on process exit. If the process is SIGKILLed and the flush fails, recovery is simply a matter of rebuilding from meta.json at startup, losing at most 5 seconds of preview updates.

Other operations (create, update, delete) write to disk atomically and immediately.

---

## Updating fields

Updates to the title, status, pinned, archived, unread, and other fields all go through the same path:

```
Caller calls update_session(session_id, title="New title", pinned=True, ...)
  → Write meta.json (only the fields that were passed in are updated)
  → Update the corresponding fields in _index[session_id] (without changing updated_at)
  → Atomically write the registry to disk

updated_at advances on the append-message path only; metadata updates such as rename, pin, archive, and read/unread changes preserve the existing recency value.
```

The broadcast is initiated by the WebSocket handler layer via `_broadcast` after it calls `update_session`; both rename and flags go through the broadcast:

```
→ Broadcast session_updated:
  {"type": "session_updated", "data": {"id": "<session_id>", "title": "New title", "pinned": true}}
→ After the frontend's handleSessionUpdated receives it, it patches the corresponding session and re-renders
```

`data` contains only the changed fields, and the frontend does an incremental patch.

### When status is written

The dispatcher writes status during the turn lifecycle:

| Timing | Value written |
|------|--------|
| Turn start | `update_session(session_id, status="running")` |
| Turn ends normally (foreground) | `update_session(session_id, status="idle")` |
| Turn ends normally (background) | `update_session(session_id, status="done", unread=True)` |
| Turn fails | `update_session(session_id, status="failed")` |
| Waiting for user input | `update_session(session_id, status="needs_input")` |

---

## Naming

Naming has only **one authoritative implementation**: `openprogram/agent/dispatcher/titles.py`. Naming from all entry points (WS / fn-form / channel / CLI / spawn) converges on `finalize_turn end → _maybe_auto_title`; there is no second truncation/lock logic. Title writes all go through `update_session(session_id, title=...)`, following the full "Updating fields" flow above.

### Lock markers (authoritative, only two + one internal counter)

| Marker | Type | Meaning | Who sets it |
|------|------|------|--------|
| `_user_titled` | bool | User manually renamed → permanent lock, auto-naming never runs again | **Only** the rename operation sets it, when the user has entered a name |
| `_auto_titled` | bool | Auto-naming has produced at least one title (first-round truncation or any LLM write) → a "don't re-truncate" dedup bit | **Only** `_maybe_auto_title` sets it |
| `_title_gen_count` | int | Internal counter for progressive renaming (which entry of `_RETITLE_AT_TURNS` was hit) | Internal to `_maybe_auto_title`, not an entry-point lock |

There is no single marker that means both "truncated" and "permanently locked": one marker doing both would conflict with the two-phase flow, where truncation is expected to be superseded by the LLM title. Entry points therefore do not truncate or set a lock of their own; `_maybe_auto_title` handles phase 1 (truncation) and phase 2 (LLM) uniformly. The only lock an entry point may set is `_user_titled`, set by the rename operation.

### Auto-naming (progressive, two phases)

Auto-naming triggers multiple times as the conversation evolves, producing more precise titles as context accumulates.
Trigger thresholds: at the 1st, 6th, 16th, and 40th assistant reply (`_RETITLE_AT_TURNS`).

```
finalize_turn end → _maybe_auto_title:
  1. Check _user_titled → if the user manually renamed, never auto-rename
  2. Count the current number of assistant messages → skip if no threshold is hit
  3. First time (turn 1, phase 1 immediate truncation):
     a. title = _title_from_text(user's first message)
        (strip [attachment:]/<attachment-preview>/<file> markers → take the first line → truncate to 50 chars, appending … if it overflows)
        → update_session(session_id, title=truncated value, _auto_titled=True, _title_gen_count=1)
     b. Start a background daemon thread to call the LLM (phase 2)
  4. Subsequent thresholds (turn 6/16/40):
     a. Directly start a background daemon thread
     b. The LLM input takes the most recent 20 messages (not just the first round)
  5. Background thread (phase 2):
     → Race check: give up if _user_titled
     → On the first time, also check whether title is still the phase 1 truncated value (give up writing if it has been changed)
     → Write update_session(session_id, title=LLM result, _auto_titled=True, _title_gen_count=N+1)
     → Broadcast session_updated
```

### channel (WeChat / Discord, etc.)

Channel conversation naming is **exactly the same** as for ordinary conversations, going through the same two-phase LLM naming. The channel side does **not** perform any additional operation / lock / intervention on the title content (it does not set `_user_titled`, does not set `_auto_titled`, and does not pre-truncate). The source identifier is only added as a bracketed brand prefix in the frontend display layer (e.g. `[WeChat] Weekend plan discussion`); it does not go into the title itself.

### Empty conversations

Creation and writing the first message are atomic, so empty conversations are not normally produced; should one occur, it is handled by startup cleanup or manual deletion. The naming layer does no special filtering for empty conversations.

### User-initiated rename

- Manually enter a new name → `update_session(session_id, title=new name, _user_titled=True)`
  After `_user_titled` is set, auto-naming stops permanently.
- Have the LLM regenerate (click the button, title is empty) → `_llm_rename()` → `update_session(session_id, title=LLM result)`
  `_user_titled` is not set, and auto-naming continues.

For the details of LLM title generation (prompt, parameters, post-processing), see [name.md](name.md).

---

## Listing

```
The frontend sends the WebSocket message {"action": "list_sessions"}
  → handle_list_sessions:
      → session_store.list_sessions():
          → Iterate over the in-memory _index.values()
          → Drop archived rows unless include_archived=True
          → Filter by filters
          → Sort by updated_at in descending order
          → Return rows[offset:offset+limit]
      → Fill in the project field (mapped from the project directory)
      → Send {"type": "sessions_list", "data": rows}
  → The frontend renders the sidebar and the Chats page
```

Purely an in-memory operation; it does not touch disk.

### Archived rows

`list_sessions` hides archived sessions by default — archiving only bounds the
list if the default listing honours it. Two ways to see them:

- `include_archived=True` returns archived and active rows together
- `archived=True` returns only the archived ones

Maintenance passes that must visit every session regardless of the flag —
the memory scan, run-state repair, "clear all", channel-binding lookup,
agent addressing — pass `include_archived=True` explicitly. The sidebar
payload also sends every row and lets the client switch between the active,
archived, and all views without a round trip.

### Fields returned per session

The 15 fields in the registry + preview + project (filled in during listing), 17 in total. See [storage.md](storage.md) for the complete list.

---

## Deleting

```
Caller calls delete_session(session_id)
  → Delete the entire <state>/sessions/<session_id>/ directory
  → Delete _index[session_id]
  → Atomically write the registry to disk
  → Broadcast session_deleted:
    {"type": "session_deleted", "session_id": "<session_id>"}
  → After the frontend receives it, remove it from the list
```

The registry operation is internal to `delete_session`. The broadcast is initiated by the WebSocket handler layer via `_broadcast`.

---

## Archiving

Archiving keeps the session list from growing without end. It is a single
boolean on the session meta — nothing is deleted, nothing is moved, and the
operation is reversible at any time.

```
Caller calls set_archived(session_id, True)
  → update_session(session_id, archived=True)
  → Follows the full "Updating fields" flow
  → After the frontend receives the broadcast, it filters the display
```

`set_archived` returns `False` for an unknown session id, so the CLI and the
REST endpoints can report a missing session instead of silently succeeding.

### What archiving does not touch

`updated_at` records the last time a message was appended, and archiving
appends nothing — so it leaves the timestamp alone. This is the
[index consistency contract](index-consistency.html): an archived session
returns to exactly its old place in the list when unarchived, rather than
jumping to the top. Messages, branches, and history files are untouched;
`get_messages` keeps working while a session is archived.

### Entry points

| Surface | Operation |
|------|------|
| WebSocket | `{"action": "update_session_flags", "session_id": ..., "archived": true}` |
| REST | `POST /api/sessions/archive` / `POST /api/sessions/unarchive`, body `{"session_id": ...}` |
| CLI | `openprogram sessions archive <id>` / `openprogram sessions unarchive <id>` |

All three write the same flag through `set_archived` and broadcast
`session_updated`, so every open tab agrees.

Archived sessions are retained by startup maintenance. They are hidden from
the default listing and remain available until explicitly deleted.

---

## Writing the registry to disk (general)

All registry-to-disk writes use atomic writes:

```
Write to the temp file index.json.tmp
  → os.rename(index.json.tmp, index.json)
```

This prevents file corruption caused by crashes.
