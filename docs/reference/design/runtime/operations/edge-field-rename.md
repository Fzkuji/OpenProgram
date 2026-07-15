# DAG Edge Field Rename Design Doc

Status: **draft (pending discussion and confirmation)** · Created: 2026-06-29

> A DAG node has two kinds of parent relationship, and right now both fields are named `called_by` (one at the node's top level,
> one inside metadata), which makes the code repeatedly confused about which one it's reading — this is the common root cause of
> a whole string of past branching/rendering bugs. This doc proposes renaming the two to distinct names (`caller` + `predecessor`),
> eliminating the ambiguity for good.

## 1. The Problem: Two `called_by` Fields With the Same Name

A node needs to express **two different parent-child relationships**:

| Relationship | Meaning | Current name | Example |
|---|---|---|---|
| **caller** | Who "called" me (sub-call edge) | top-level `Call.called_by` | which LLM invoked a tool; ROOT invokes a top-level node |
| **conv predecessor** | who I follow in chat order (conversation-chain edge) | `metadata.called_by` | the second-round user follows the first-round reply |

Both are named `called_by`, just one at the top level and one in metadata. **The names collide, but the semantics are completely different.**

### Why Two Are Needed

They often differ. The canonical example is a top-level user node:
- caller = `ROOT` (it wasn't called by anyone; it initiates the conversation)
- conv predecessor = the previous round's reply (chat order)

A single field can't express both "attached to the root" and "follows the previous utterance" at the same time. Branch distinction **relies on the conv predecessor** (one conv predecessor with multiple children = fork), not on the caller.

## 2. The Full Current Picture (measured via codegraph)

### Backend

| File | Symbol | Current state |
|---|---|---|
| `context/nodes.py` | `Call.called_by: str = ""` | the dataclass's only edge field, semantics = caller |
| `store/session/_msg_adapter.py` | `_msg_to_node` | the msg dict's `called_by` → sometimes goes into `Call.called_by` (tool/attach), sometimes into `metadata.called_by` (user/llm) |
| `store/session/_msg_adapter.py` | `_node_to_msg` | reverse: `Call.called_by` and `metadata.called_by` are stitched back into the two keys `called_by` + `caller` of the msg dict |
| `store/session/session_store.py` | `_node_conv_predecessor` | reads `metadata.called_by` |
| `store/session/session_store.py` | `_node_caller` | reads `Call.called_by` |
| `store/session/memory_index.py` | `append(node, predecessor, caller)` | two indexes: `children_by_predecessor` (conv) / `children_by_caller` (caller) |
| `webui/graph_builder.py` | `build_session_graph` | builds the graph dict, each node carrying `called_by` (conv predecessor) + `caller` |
| `webui/graph_layout/_common.py` | `called_by_of` / `predecessor_of` | **line 23 `called_by_of = predecessor_of` overrides it** → tier/lane/depth/topology all read the conv predecessor |
| `webui/graph_layout/{tier,lane,depth,topology}.py` | — | read the conv predecessor via `called_by_of` |

### Frontend

| File | Symbol | Current state |
|---|---|---|
| `dag/types.ts` | `GNode` | has **3 fields**: `parent_id` (dead field, nobody writes it), `called_by` (conv predecessor), `caller` (sub-call) |
| `dag/types.ts` | `layoutParent(n)` | returns `n.called_by` (conv predecessor), used to build the tree |
| `dag/pipeline.ts` | `render` | line 186 `n.caller` to determine internal; line 202 `m.called_by \|\| m.called_by` (**duplicate or, leftover typo**) |
| `dag/render/{edges,nodes,badges}.ts` | — | read `called_by` (conv predecessor) to draw edges / detect branches |
| `conversations.ts` | `LegacyMessage` / `BranchRow` | msg/branch dict flow |

### Symptoms of the Mess (all real bugs caused by the name collision)

1. `_common.py:23` overrides `called_by_of` with `predecessor_of` — the name says caller, but it actually reads the conv predecessor. The tier-staircase bug stems from this.
2. `pipeline.ts:202` `m.called_by || m.called_by` — both sides are the same; it was clearly meant to be two different fields but both were typed with the same name.
3. In `_msg_to_node`, `called_by = tool_use.get("called_by") or predecessor` — predecessor (conv) and caller get mixed into the same variable.
4. When debugging earlier, I myself repeatedly misspoke about "is it ROOT or empty," precisely because the two fields share a name.

## 3. The Rename Plan

### New Naming

| Relationship | Old name | New name |
|---|---|---|
| sub-call edge (who called me) | `called_by` (top level) | **`caller`** |
| conversation-chain edge (chat predecessor) | `called_by` (metadata) | **`predecessor`** |

Rationale:
- `caller` is already the name the frontend uses (the msg dict's `caller` key, `_node_caller`); unify on it
- `predecessor` expresses "the parent on the conversation chain" more accurately than `called_by`, and doesn't collide with caller

### Backend Changes

| File | What changes |
|---|---|
| `context/nodes.py` | `Call.called_by` → `Call.caller` (dataclass field rename). Add one-time compatibility: `to_dict`/`from_dict` read the old `called_by` key and backfill `caller`, so old on-disk data can still be read |
| `_msg_adapter.py` | `_msg_to_node`: msg's `caller` → `Call.caller`; msg's `predecessor` (new) / `called_by` (old compat) → `metadata.predecessor`. `_node_to_msg`: in reverse, emit two explicit keys `caller` + `predecessor` |
| `session_store.py` | `_node_conv_predecessor` reads `metadata.predecessor`; `_node_caller` reads `Call.caller`. Update comments |
| `memory_index.py` | parameter names / index names unchanged (`predecessor`/`caller` are already clear), just confirm they read the right field |
| `graph_builder.py` | output the graph dict with two explicit keys `predecessor` + `caller` (no longer `called_by`) |
| `graph_layout/_common.py` | **delete the override line `called_by_of = predecessor_of`**. Replace with two explicit functions: `predecessor_of` (reads predecessor), `caller_of` (reads caller). Each layout module calls the right one as needed |
| `graph_layout/{tier,lane,depth,topology}.py` | tier uses `caller_of` (sub-call indentation); lane/depth/topology use `predecessor_of` (conversation chain) |

### Frontend Changes

| File | What changes |
|---|---|
| `dag/types.ts` | `GNode`: drop `parent_id` (dead field); `called_by` → `predecessor`; keep `caller`. `layoutParent` returns `n.predecessor` |
| `dag/pipeline.ts` | `n.caller` to determine internal (unchanged); line 202 `m.called_by \|\| m.called_by` fixed to `m.predecessor`; `_signature` uses `predecessor` |
| `dag/render/{edges,nodes,badges}.ts` | `called_by` → `predecessor` |
| `conversations.ts` etc. | the msg/branch dict's `called_by` → `predecessor` (WS protocol kept in sync) |

### WS Protocol

The backend graph dict and the frontend reads must change the key name in sync (`called_by` → `predecessor`). This is a breaking change spanning both frontend and backend, so it must be changed in the same batch + rebuild.

## 4. No Compatibility (Old Data Already Deleted)

All old session data has been deleted (`~/.openprogram/sessions/local_*`). **No backward-compatibility logic is retained** — the code recognizes only the new field names `caller` / `predecessor`, and does not read the old `called_by`.

- The `Call` dataclass field is renamed directly, with no `called_by` alias left behind
- `_msg_to_node` / `_node_to_msg` handle only the new key names
- `from_dict` does no old-key backfill
- The old field `called_by` disappears entirely from the whole codebase (the frontend `parent_id` dead field is dropped along with it)

## 5. Rollout Steps (each step verified independently)

| Step | What to do | Verification |
|---|---|---|
| 1 | `_common.py`: delete the override line, split into two explicit functions `predecessor_of` + `caller_of` | tier uses caller, lane/depth use predecessor; unit tests |
| 2 | backend `Call.called_by` → `caller` (no compat alias) | full Python test suite passes |
| 3 | `_msg_adapter` + `session_store` + `graph_builder` output the new key names | tests pass + manually inspect the graph dict key names |
| 4 | frontend GNode/layoutParent/pipeline/render use `predecessor`, drop `parent_id` | build passes |
| 5 | WS protocol key names synced, rebuild + restart | browser: new session → chat → fork → DAG drawing, branching, and collapsing all work correctly |

## 6. Design Decisions Pending Discussion

1. ~~**What to call the conv edge?**~~ **Decided: `predecessor`.** (caller = who called me, predecessor = my predecessor on the conversation chain)

2. **Keep the caller edge as `caller` or call it `invoked_by`?**
   `caller` is short and the frontend already uses it. Leaning toward keeping `caller`.

3. **Should we also drop the frontend dead field `parent_id` while we're at it?**
   `parent_id` in GNode is never written, a pure dead field. Leaning toward dropping it in this same pass.

4. ~~**How long to maintain on-disk compatibility?**~~ **Decided: no compatibility, old data already deleted.** The code recognizes only the new key names.

5. **Should the design doc `session-dag.md` be updated in sync?** It says "the only edge `called_by`," which doesn't match the actual two edges. Leaning toward fixing the doc in this same pass, spelling out both edges clearly.
