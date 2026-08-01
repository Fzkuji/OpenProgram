# Unifying parent_id and called_by — Design

> Code: `store/session/_msg_adapter.py`, `webui/persistence.py`, `contextgit/dag.py`, `webui/ws_actions/session.py`

## 1. Two parent pointers, two meanings

A DAG node carries two "parent pointer" fields with different meanings, read and
written in different places.

| Field | Meaning | Who writes it | Who reads it |
|---|---|---|---|
| `called_by` | Call relationship (who called me) | DAG store (the `Call` object in `context/nodes.py`) | `render_context`, `get_branch`, `_rebuild_runtime_cards`, `aggregate_tool_messages` |
| `parent_id` | Conversation chain (which message precedes mine) | `_msg_adapter.py` (copied from `called_by`) | `linear_history`, `_annotate_spawn_origin`, dispatcher branch management |

The two carry different semantics:

- `called_by` is the **call hierarchy**: a user's called_by=ROOT; a function's
  called_by=ROOT (manual call) or assistant_id (LLM call); a tool's called_by=function id
- `parent_id` is the **conversation order**: the second message's parent_id points
  to the first, the third to the second

Because `_msg_adapter.py` assigns `called_by` directly to `parent_id`, the two
coincide today, and that assignment is the source of one traversal gap: when a
session has two ROOT-parented user nodes, both of their `parent_id` values are empty
(ROOT is not a valid message id), so `linear_history` walking along parent_id stops
early.

## 2. Two data structures

The DAG and the chat UI need different data formats, and both are required:

| | DAG raw node | Chat UI message |
|---|---|---|
| Purpose | Runtime (render_context building the context) | Frontend display (message list, tool-call cards) |
| Tool calls | One independent node per tool | Folded into the assistant message's tool_calls[] |
| thinking | In the extra field | Extracted into blocks[] |
| Format | `{role, name, input, output, called_by, seq}` | `{role, content, tool_calls, blocks, parent_id}` |
| When built | At write time | At load time (aggregate_tool_messages) |

`aggregate_tool_messages` is what converts the DAG format into the UI format.

## 3. Incremental unification

`parent_id` is referenced in 188 places, reaching deep into core modules such as the
dispatcher, branch management, and sub_agent. Replacing it everywhere at once would
put all message loading at risk in a single move. The design therefore moves the
critical paths onto `called_by` one layer at a time, keeping `parent_id` as a
fallback so that a missing `called_by` behaves exactly as before:

1. **Aggregation layer** (persistence.py): prefer `called_by`, with `parent_id` as a fallback
2. **Render layer** (session.py `_rebuild_runtime_cards`): use `called_by`, so function-descendant
   relationships are determined by the call hierarchy and user nodes are not dropped
3. **Load layer** (session.py `handle_load_session`): linear_history, falling back to get_branch
   when linear_history is incomplete
4. **`_msg_adapter.py`**: keeps copying called_by → parent_id, for backward compatibility
5. **`linear_history`**: keeps using parent_id, covered by the load-layer fallback

## 4. Target state

The end state sets `parent_id` from conversation order rather than copying
`called_by`, which makes the conversation chain correct by construction:

| Step | What to do | Prerequisite |
|---|---|---|
| A | Have `_msg_adapter.py` set parent_id by conversation seq order (instead of copying called_by) | Current layers stable |
| B | Change `linear_history` to traverse using parent_id (correct by construction after step A) | Step A |
| C | Remove the get_branch fallback in handle_load_session, no longer needed | Step B |
| D | Mark parent_id as deprecated and use only called_by long term | Steps A–C all stable |

Steps A–D affect all message loading, so they need a feature flag and thorough
testing before rollout.

## Implementation Status

Partially implemented. Layers 1–3 of the incremental unification are in place: the
aggregation layer prefers `called_by`, `_rebuild_runtime_cards` uses `called_by`,
and `handle_load_session` has the get_branch fallback. Layers 4–5 remain as
described, and the target state (steps A–D) is not yet started.
