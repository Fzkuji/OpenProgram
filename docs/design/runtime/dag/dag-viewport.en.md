# DAG Viewport Design Doc

Status: **draft** · Created: 2026-06-28

> The DAG minimap in the right-hand panel. Shows the node structure of the current session, with support for branch switching, collapsing, and scroll sync.

## 1. Overall Architecture

### Current Problems

The graph data is built once in `session.py` and again in `branch.py` — change one place and you miss the other. The frontend `pipeline.ts` is 800 lines, with layout + edge + node + badge + visibility all tangled together. Touching the edge logic affects collapsing, and touching collapsing affects visibility.

### Target Architecture

```
Backend                              Frontend
────                                 ────

graph_builder.py (new)               dag/
  build_session_graph(sid)             index.ts        → public interface
    → {nodes, branches, head}          pipeline.ts     → orchestration: passes → layout → render
                                       types.ts        → GNode interface + constants

graph_layout/ (unchanged)            dag/passes/       → data transforms (draws nothing)
  __init__.py → annotate_graph()       merge-runs.ts
  tier.py                             collapse-runtime-pairs.ts
  depth.py                            demote-decoration-cards.ts
  lane.py                             apply-collapse.ts
  topology.py
  _common.py                        dag/layout/        → frontend-side tree construction (draws nothing)
                                       build-tree.ts
ws_actions/                            assign-lanes.ts
  session.py  → calls build_session_graph  depth.ts
  branch.py   → calls build_session_graph

                                     dag/render/        → draws the SVG (only draws, computes no layout)
                                       edges.ts         → trunk edges + fork edges
                                       nodes.ts         → node shapes + state classes
                                       badges.ts        → branch badge buttons
                                       visibility.ts    → white-fill sync

                                     dag/store/
                                       globals.ts       → module-level state
```

## 2. Backend Interface

### `graph_builder.build_session_graph(session_id) → dict`

The single entry point for building graph data. Returns:

```python
{
    "graph": [           # array of GNode, each node contains:
        {
            "id": str,
            "called_by": str,   # conversation-chain predecessor (conv predecessor)
            "caller": str,      # sub-call parent node (who called me)
            "role": str,        # user / assistant / tool
            "display": str,     # root / runtime / None
            "function": str,    # function name (tool node)
            "preview": str,     # content preview
            "_tier": int,       # horizontal column position (computed by graph_layout)
            "_depth": int,      # vertical row position
            "_lane": int,       # branch column
            ...                 # attach-related fields
        },
    ],
    "branches": [        # list of branch tips
        {
            "head_msg_id": str,
            "name": str | None,
            "active": bool,
            "created_at": float,
        },
    ],
    "head": str | None,  # id of the current HEAD node
}
```

### Callers

| Caller | Scenario |
|---|---|
| `session.py:handle_load_session` | On session load, the graph is placed into the `session_loaded` WS message |
| `branch.py:build_branches_payload` | Branch panel refresh, real-time poller |

Both call `build_session_graph` — they no longer build the graph each on their own.

### Difference Between `called_by` and `caller`

| Field | Meaning | Example | Purpose |
|---|---|---|---|
| `called_by` | conversation-chain predecessor | user2.called_by = llm1 (the previous turn's reply) | building tree parent/child relationships, depth/lane, edge drawing |
| `caller` | sub-call parent node | tool.caller = llm (the model called this tool) | tier computation, internal determination, collapse |

Rule: **whenever you're deciding "who called whom," use `caller`. Whenever you're deciding "conversation order," use `called_by`.**

## 3. Frontend Interface

### `render(graph: GNode[], headId: string): void`

The single rendering entry point. Steps:

```
1. passes: merge-runs → collapse-runtime-pairs → demote-decoration-cards → apply-collapse
2. layout: buildTree → assignDepth → assignLanes
3. compute state: headAncestors (on-head), internalSet (sub-calls)
4. draw SVG:
   a. edges.drawEdges(tree, pos, colors)          → all edges
   b. nodes.drawNodes(tree, pos, colors, states)   → all node shapes
   c. badges.drawBadges(branches, tree, pos)       → branch badge buttons
5. bind events: scroll sync, mutation observer, panel resize
6. trigger visibility.recompute()
```

### Interfaces of the render submodules

#### `edges.drawEdges(edgeG, tree, pos, rootPos, forkRoots, colors)`

Input: SVG group, tree data, pos function, ROOT position, list of fork roots, color table
Output: adds SVG line/path elements into edgeG

Edge rules:
- trunk user nodes draw their edge from the ROOT column
- fork-branch user nodes draw their edge from the fork virtual trunk
- llm/tool nodes draw their edge from the parent
- dashed animated lines are drawn between fork siblings

#### `nodes.drawNodes(nodeG, tree, pos, colors, states)`

Input: SVG group, tree data, pos function, color table, {headAncestors, internalSet, collapsed, collapsible}
Output: adds SVG g.history-node elements into nodeG

#### `badges.drawBadges(svg, branches, tree, pos, colors, sessionId)`

Input: SVG root element, branch list, tree data, pos function, color table, session id
Output: adds a clickable badge beneath each branch tip node

#### `visibility.recompute()`

Scans the visible region of `#chatArea` and marks the corresponding DAG nodes with white fill.

Trigger conditions:
- chat scroll (scroll listener)
- chat DOM changes (MutationObserver)
- after render completes

MutationObserver binding rule: **rebind** on every render (don't use a flag to prevent it), because the `#chatMessages` DOM may be replaced during load_session.

## 4. Node States

### on-head / off-head

Walk back from headId along `called_by` to ROOT — every node on that chain is on-head. The rest are off-head.

Visual: off-head nodes get the CSS class `off-head` and are dimmed.

### visible (white fill)

Depends solely on the chat-viewport scan result. No parent walk-up propagation (the previous walk-up propagated across branches, producing incorrect white fills).

The simplified `recompute()`:
1. scan `#chatArea` for the visible chat bubbles' `data-msg-id`
2. those ids → white fill
3. everything else → transparent
4. no `_parentOf` walk-up, no `_internalOwner` propagation

### internal

A node whose `caller` field (not `called_by`) points to a non-ROOT parent node. Used in collapse computation (collapsing only gathers internal children).

**Not used for visibility propagation** (the influence of `_internalOwner` on visibility is removed).

## 5. Layout

### tier (horizontal column) — tier.py

Fixed by role: ROOT=0, user=1, llm=2, tool=3, deeper sub-calls=caller.tier+1.

### depth (vertical row) — depth.py

DFS order. fork siblings skip DFS and align to the depth of the first sibling.

### lane (branch column) — lane.py

The trunk is lane=0. Non-first fork siblings are assigned a new lane. A 1-column gap is left between lanes for the fork virtual trunk line.

### pos function

```typescript
function pos(n: GNode): {x: number, y: number} {
    x = PAD_X + (laneToCol[n._lane] + n._tier) * COL_W
    y = PAD_Y + depthToRow[n._depth] * ROW_H
}
```

## 6. Edges

### Trunk

```
◇ ROOT (tier=0)
│                    ← vertical trunk in the tier=0 column
├── ○ user1 (tier=1) ← horizontal branch tier=0 → tier=1
│   └── △ llm1 (tier=2)
│
├── ○ user2 (tier=1) ← horizontal branch tier=0 → tier=1 (back to the user column, not chained)
│   └── △ llm2 (tier=2)
│       └── ■ tool (tier=3)
```

Trunk user nodes draw their edge from the ROOT column (tier=0), not from `called_by` (the previous turn's llm).

### Fork Branch

```
trunk                   fork
├── ○ user2  ┈┈┈┈┈┈  │── ○ user1'
│   └── △ llm2        │   └── △ llm1'
│                      │
│                      ├── ○ user2'
│                      │   └── △ llm2'
```

1. dashed bridge: trunk sibling → fork virtual trunk column
2. fork virtual trunk solid line: vertical line from the fork root to the last user
3. fork-internal users branch off horizontally from the virtual trunk

### Collapse

Collapsing a node only hides its `caller` sub-calls; it does not hide the subsequent turns of the `called_by` conversation chain.

## 7. Branch Badge

A badge is shown beneath each branch tip node. The style matches the HEAD label (`--bg-hover` background, rounded corners, no border).

- active branch: bright text
- inactive branch: dimmed text; clicking triggers `checkout_branch` + `load_session`

## 8. Refactor Steps

| Step | What to do | Verify |
|---|---|---|
| 1 | Backend: extract `graph_builder.py`; both session.py and branch.py call it | graph data is identical |
| 2 | Frontend: extract edges.ts (drawEdges function) from pipeline.ts | edges unchanged |
| 3 | Frontend: extract nodes.ts (drawNodes function) from pipeline.ts | nodes unchanged |
| 4 | Frontend: extract badges.ts (drawBadges function) from pipeline.ts | badges unchanged |
| 5 | Frontend: simplify visibility.ts (remove parent walk-up + internal propagation, rebind the observer on every render) | visibility tracks only the chat viewport |
| 6 | Frontend: turn pipeline.ts into pure orchestration (call passes → layout → drawEdges → drawNodes → drawBadges → recompute) | functionality unchanged, file <200 lines |

Each step is independently verifiable: run `npm run build` + confirm in the browser that the rendering is unchanged.
