# Rewind — Roll Back to Any Historical Message

> Status: **Implemented** (2026-06)
> Reference: Claude Code `/rewind`
> Code: `agent/_rewind.py`, `webui/ws_actions/chat.py`, `apps/web/components/chat/messages/message-actions.tsx`

---

## 1. Behavior Definition

After the user clicks the ↩ button on a **user message** (or enters `/rewind N`):

1. **File restore**: file changes from that message's turn and all subsequent turns are restored from the checkpoint
2. **Message text refill**: the text of that user message is placed back into the chat input box
3. **UI update**: that message and all later conversation are removed from the UI
4. **DAG branch**: the old conversation is kept in the DAG (not deleted); the current branch head moves to just before that message
5. The user can edit the text in the input box and resend it → starting a new branch from that point

## 2. Comparison with Claude Code

| | Claude Code | OpenProgram |
|---|---|---|
| Trigger | `/rewind` lists checkpoints, pick one | ↩ button + `/rewind N` |
| Rollback granularity | per-prompt (each user message) | same |
| File restore | restore from checkpoint snapshot | restore from checkpoint snapshot |
| Message refill | user message text placed back into input box | same |
| Conversation handling | fork conversation (new branch) | DAG branch (old conversation kept, not deleted) |
| bash blind spot | warns "does not affect manual/bash edits" | we trigger through a unified entry point, so bash is covered too |

## 3. Implementation

### 3.1 Backend `_rewind.py`

`rewind_to(session_id, user_msg_id)`:

1. Find the user node in the DAG corresponding to `user_msg_id`
2. Extract that node's `output` (i.e., the user message text)
3. Find that node and all subsequent assistant/llm nodes (sorted by seq)
4. Call `revert_turn` for each assistant node to restore files
5. Mark `metadata.rewound = True` on every rewound node
6. Move the store head to the last node before the target (`None` when
   rewinding to the very start — head must never rest on a rewound node)
7. Return `{ user_text, turns_reverted, restored_paths, new_head_id, errors }`

Key point: **it accepts the user node ID directly**, with no need to convert it into an assistant ID.

`new_head_id` is the head the rewind landed on. Callers that keep their
own head mirror must write it back — see 3.2.

### 3.2 Backend WS handler

`handle_rewind(ws, cmd)`:
- Receives `{ session_id, target_msg_id }`
- Refuses with `{ code: "run_active" }` while a run is in flight, so a
  rewind cannot move HEAD out from under a streaming reply
- Calls `rewind_to`
- Whenever `new_head_id` is non-None, feeds it through
  `server._set_active_head` and re-estimates context stats. This is
  keyed on the head move, not on `errors`: a file-restore failure is a
  partial failure of the rewind, but the store head has already moved,
  so the mirror must follow. `errors` still rides the result frame as
  the partial-failure warning
- Returns `{ type: "rewind_result", data: { session_id, user_text, ... } }`

**The `_set_active_head` step is not cosmetic.** `rewind_to` writes only
the store; the webui also keeps a per-session mirror in
`_sessions[sid]` (`head_id` + `messages`) that `_save_session` flushes
straight back into the store. A rewind that skips the mirror leaves it on
the pre-rewind head, and the next save silently reverts the rewind.
Every path that moves HEAD goes through `_set_active_head` for this
reason — it writes the store, re-reads the branch into the mirror, and
drops the message cache in one step.

`handle_rewind_list(ws, cmd)`:
- Receives `{ session_id }` — this is what `/rewind` with no argument sends
- Returns `{ type: "rewind_points", data: { session_id, points } }`, newest first

**Every frame of both types carries `session_id`, including the error
ones.** Listeners match on it: rewinds in two conversations emit the same
frame type, and a listener keyed on type alone answers to whichever lands
first.

### 3.3 Frontend

`rewindToHere()`:
1. Send WS action `{ action: "rewind", session_id, target_msg_id: msg.id }`
2. After receiving a `rewind_result` whose `session_id` matches:
   - Call `useSessionStore.getState().setComposerInput(data.user_text)` to refill the input box
   - Call `wsSend({ action: "load_session", session_id })` to refresh the message list (rewound messages no longer appear on the current branch)
   - Show a toast
3. The listener also detaches on a 30s timeout. A rewind that fails
   without emitting a frame would otherwise leave the row's action
   buttons disabled until a page reload.

`/rewind` with no argument sends `rewind_list`; `use-ws.ts` renders the
returned points as a numbered system line in the transcript, so the
indices stay on screen while the user types `/rewind N`.
