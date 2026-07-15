# Session Operations

Each operation is written out end to end, from trigger to disk to frontend.

---

## Startup

When the process starts, SessionStore runs a one-time initialization:

1. Read `index.json` and load it into the in-memory `_index` dict
2. If the file does not exist or JSON parsing fails → scan the meta.json of every session directory to rebuild `_index`, then write `index.json`
3. Iterate over `_index` and reset every `status=running` to `idle` (crash recovery)
4. Clean up empty shells: a session with 0 messages and created more than 1 hour ago → delete the directory + delete the registry entry
5. Clean up expired archives: `archived=True` and `updated_at` older than 90 days → delete
6. Capacity check: if the registry exceeds 1000 entries → delete the oldest archived sessions in ascending `updated_at` order

Handling of half-broken sessions:
- Has meta.json but no history/ → treated as an empty shell, deleted in step 4
- Has history/ but no meta.json → the scan in step 2 cannot read meta.json, so it is not registered and is treated as nonexistent

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
  → Create the <state>/sessions/<session_id>/ directory
  → Write meta.json (id, agent_id, title, created_at, updated_at, source, status="idle", ...)
  → Write the registry: _index[session_id] = summary entry
  → Atomically write the registry to disk (temp file → os.rename)
  → No broadcast (the frontend discovers the new session via list_sessions)
```

### Atomicity

For the dispatcher and the channel handler, creation and writing the first message are atomic — `append_message` is called immediately after creation, so no empty shell is produced.

`session_context` creates the session in `__enter__` (because the subsequent ContextVar loading needs a valid session). If it exits abnormally before writing any message, an empty shell is produced, which is handled by the startup cleanup.

---

## Writing a message

```
Caller calls append_message(session_id, msg)
  → Write the message to the DAG (Git history/)
  → If msg.role == "user":
      → preview = take the first 80 characters of msg.content
      → _index[session_id]["preview"] = preview
      → _index[session_id]["updated_at"] = time.time()
      → Mark the registry dirty (write to disk with a 5-second debounce)
  → No broadcast (message content is pushed through a separate streaming channel)
```

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
  → Update the corresponding fields in _index[session_id] + updated_at
  → Atomically write the registry to disk
```

The broadcast is initiated by the WebSocket handler layer via `_broadcast` after it calls `update_session` (already implemented; both rename and flags go through the broadcast):

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

A historical third name, `_titled`, has been abolished — it did both truncation and a permanent lock, which conflicts with the two-phase flow. The entry points **no longer** do their own truncation and set `_titled`; instead, `_maybe_auto_title` uniformly handles phase 1 (truncation) + phase 2 (LLM). The only lock an entry point may set is `_user_titled` (the rename operation).

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
          → Filter by filters
          → Sort by updated_at in descending order
          → Return rows[offset:offset+limit]
      → Fill in the project field (mapped from the project directory)
      → Send {"type": "sessions_list", "data": rows}
  → The frontend renders the sidebar and the Chats page
```

Purely an in-memory operation; it does not touch disk.

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

The registry operation has been internalized into `delete_session`. The broadcast is initiated by the WebSocket handler layer via `_broadcast`.

---

## Archiving

```
Caller calls update_session(session_id, archived=True)
  → Follows the full "Updating fields" flow
  → After the frontend receives the broadcast, it filters the display
```

Archived sessions are subject to the startup-time data maintenance constraints: 90-day expiration + the 1000 capacity cap. Active sessions are not affected.

---

## Writing the registry to disk (general)

All registry-to-disk writes use atomic writes:

```
Write to the temp file index.json.tmp
  → os.rename(index.json.tmp, index.json)
```

This prevents file corruption caused by crashes.
