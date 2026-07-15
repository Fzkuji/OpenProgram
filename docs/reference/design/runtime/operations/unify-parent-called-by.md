# Unifying parent_id and called_by — Design

> Status: **Partially implemented**
> Code: `store/session/_msg_adapter.py`, `webui/persistence.py`, `contextgit/dag.py`, `webui/ws_actions/session.py`

## 1. Problem

A DAG node has two "parent pointer" fields with different meanings. They are read and written in different places, which leads to inconsistent traversal results.

| Field | Meaning | Who writes it | Who reads it |
|---|---|---|---|
| `called_by` | Call relationship (who called me) | DAG store (the `Call` object in `context/nodes.py`) | `render_context`, `get_branch`, `_rebuild_runtime_cards`, `aggregate_tool_messages` (after the change) |
| `parent_id` | Conversation chain (which message precedes mine) | `_msg_adapter.py` (copied from `called_by`) | `linear_history`, `_annotate_spawn_origin`, dispatcher branch management |

**Root cause**: `_msg_adapter.py` assigns `called_by` directly to `parent_id` (lines 132/169/188/206), but the two have different semantics:

- `called_by` is the **call hierarchy**: a user's called_by=ROOT; a function's called_by=ROOT (manual call) or assistant_id (LLM call); a tool's called_by=function id
- `parent_id` should be the **conversation order**: the second message's parent_id should point to the first, the third to the second

The direct assignment causes the following: when a session has two ROOT-parented user nodes, both of their `parent_id` values are empty (ROOT is not a valid message id), so `linear_history` walking along parent_id breaks.

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

## 3. Fixes already completed

| Fix | commit | What it did |
|---|---|---|
| `_rebuild_runtime_cards` uses called_by | `476aa8f6` | Function-descendant relationships are determined by called_by, so user nodes are no longer dropped by mistake |
| `aggregate_tool_messages` prefers called_by | This change | Tool→assistant aggregation uses `called_by` to find the parent node, with `parent_id` as a fallback |
| `handle_load_session` fallback | `1adfbbc3` | When linear_history is incomplete, fall back to get_branch |

## 4. Current strategy

**Incremental unification**: rather than deprecating `parent_id` all at once, gradually make the critical paths prefer `called_by`.

`parent_id` is referenced in 188 places, reaching deep into core modules such as the dispatcher, branch management, and sub_agent. Replacing it all at once is too risky. The current strategy is:

1. **Aggregation layer** (persistence.py): prefer `called_by`, with `parent_id` as a fallback ✅ done
2. **Render layer** (session.py _rebuild_runtime_cards): use `called_by` ✅ done
3. **Load layer** (session.py handle_load_session): linear_history + get_branch fallback ✅ done
4. **_msg_adapter.py**: keep copying called_by → parent_id (backward compatibility)
5. **linear_history**: keep using parent_id (covered by the fallback)

## 5. Follow-up plan (low priority)

Once the fixes above have been thoroughly validated, we can go further:

| Step | What to do | Prerequisite |
|---|---|---|
| A | Have `_msg_adapter.py` set parent_id by conversation seq order (instead of copying called_by) | Confirm the current fixes are stable |
| B | Change `linear_history` to traverse using the correct parent_id (which becomes correct naturally after step A) | Step A |
| C | Remove the get_branch fallback in handle_load_session (no longer needed) | Step B |
| D | Mark parent_id as deprecated and use only called_by in the long term | Steps A–C all stable |

## 6. Risks

**Risk of the current approach**: low. It only changes the field priority in the aggregation layer, with parent_id retained as a fallback. Worst case = falling back to parent_id when called_by is missing (identical to the behavior before the change).

**Risk of the follow-up steps**: medium-to-high. Changing _msg_adapter.py affects all message loading, and needs a feature flag plus thorough testing.
