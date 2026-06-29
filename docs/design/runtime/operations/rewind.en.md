# Rewind — Roll Back to Any Historical Message

> Status: **Implemented** (2026-06)
> Reference: Claude Code `/rewind`
> Code: `agent/_rewind.py`, `webui/ws_actions/chat.py`, `web/components/chat/messages/message-actions.tsx`

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
6. Return `{ user_text, turns_reverted, restored_paths, errors }`

Key point: **it accepts the user node ID directly**, with no need to convert it into an assistant ID.

### 3.2 Backend WS handler

`handle_rewind(ws, cmd)`:
- Receives `{ session_id, target_msg_id }`
- Calls `rewind_to`
- Returns `{ type: "rewind_result", data: { user_text, ... } }`

### 3.3 Frontend

`rewindToHere()`:
1. Send WS action `{ action: "rewind", session_id, target_msg_id: msg.id }`
2. After receiving `rewind_result`:
   - Call `useSessionStore.getState().setComposerInput(data.user_text)` to refill the input box
   - Call `wsSend({ action: "load_session", session_id })` to refresh the message list (rewound messages no longer appear on the current branch)
   - Show a toast
