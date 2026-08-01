# DAG Edge Field Naming

> A DAG node carries two distinct parent relationships. They are stored under two
> distinct names, `caller` and `predecessor`, so that no piece of code has to guess
> which relationship a field refers to. This document describes the two edges, why
> they need separate names, and where each name appears across the backend, the
> frontend, and the WS protocol.

## 1. Two Parent Relationships

A node expresses **two different parent-child relationships**:

| Relationship | Meaning | Field | Example |
|---|---|---|---|
| **caller** | Who called me (sub-call edge) | top-level `Call.caller` | which LLM invoked a tool; ROOT invokes a top-level node |
| **conv predecessor** | Who I follow in chat order (conversation-chain edge) | `metadata.predecessor` | the second-round user follows the first-round reply |

### Why Two Are Needed

The two often differ. The canonical example is a top-level user node:

- caller = `ROOT` (it wasn't called by anyone; it initiates the conversation)
- conv predecessor = the previous round's reply (chat order)

A single field cannot express both "attached to the root" and "follows the previous
utterance" at the same time. Branch distinction **relies on the conv predecessor**
(one conv predecessor with multiple children = fork), not on the caller.

Giving both relationships the same name is what makes the two easy to confuse in
code. When both were called `called_by` — one at the node's top level, one inside
metadata — a layout helper named after the caller in fact read the conv predecessor,
and a rendering expression meant to fall back from one field to the other read the
same field twice. Distinct names remove the whole class of mistake.

## 2. Naming

| Relationship | Name |
|---|---|
| sub-call edge (who called me) | **`caller`** |
| conversation-chain edge (chat predecessor) | **`predecessor`** |

Rationale:

- `caller` is the name the frontend already uses (the msg dict's `caller` key,
  `_node_caller`), so both sides agree on one term.
- `predecessor` states "the parent on the conversation chain" precisely, and does
  not collide with caller.

## 3. Where the Two Names Appear

### Backend

| File | Symbol | Role |
|---|---|---|
| `context/nodes.py` | `Call.caller` | the dataclass edge field, semantics = caller |
| `store/session/_msg_adapter.py` | `_msg_to_node` | msg's `caller` → `Call.caller`; msg's `predecessor` → `metadata.predecessor` |
| `store/session/_msg_adapter.py` | `_node_to_msg` | reverse: emits two explicit keys, `caller` + `predecessor` |
| `store/session/session_store.py` | `_node_conv_predecessor` | reads `metadata.predecessor` |
| `store/session/session_store.py` | `_node_caller` | reads `Call.caller` |
| `store/session/memory_index.py` | `append(node, predecessor, caller)` | two indexes: `children_by_predecessor` (conv) / `children_by_caller` (caller) |
| `webui/graph_builder.py` | `build_session_graph` | builds the graph dict with two explicit keys, `predecessor` + `caller` |
| `webui/graph_layout/_common.py` | `predecessor_of` / `caller_of` | two explicit accessors; each layout module calls the one it needs |
| `webui/graph_layout/tier.py` | — | uses `caller_of` (sub-call indentation) |
| `webui/graph_layout/{lane,depth,topology}.py` | — | use `predecessor_of` (conversation chain) |

### Frontend

| File | Symbol | Role |
|---|---|---|
| `dag/types.ts` | `GNode` | carries `predecessor` (conv) and `caller` (sub-call) |
| `dag/types.ts` | `layoutParent(n)` | returns `n.predecessor` (conv predecessor), used to build the tree |
| `dag/pipeline.ts` | `render` | `n.caller` determines internal; `m.predecessor` drives the conversation chain; `_signature` uses `predecessor` |
| `dag/render/{edges,nodes,badges}.ts` | — | read `predecessor` to draw edges / detect branches |
| `conversations.ts` | `LegacyMessage` / `BranchRow` | msg/branch dict flow, both keys carried through |

### WS Protocol

The backend graph dict and the frontend reads share the key names `caller` and
`predecessor`. The two sides are one protocol: a change to either key name has to
land on both sides in the same batch, with a rebuild.

## 4. No On-Disk Compatibility Layer

The code recognizes only `caller` / `predecessor`. There is no alias for an older
key name and no backfill:

- The `Call` dataclass field is `caller`, with no alias behind it
- `_msg_to_node` / `_node_to_msg` handle only these key names
- `from_dict` does no backfill from any other key
- The frontend `GNode` has no unused parent field

## Implementation Status

Implemented across backend and frontend. `Call.caller` and `metadata.predecessor`
are the only edge fields; `graph_builder` and the WS graph dict emit both explicit
keys; `_common.py` exposes `predecessor_of` and `caller_of` as separate accessors,
with tier reading the caller and lane/depth/topology reading the predecessor. The
session data model in [`../dag/session-dag.md`](../dag/session-dag.md) documents
both edges as authoritative.
