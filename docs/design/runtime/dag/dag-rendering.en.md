# DAG Rendering Spec (Layout · Edges · Legend · Default Visibility)

Status: **decided (authoritative implementation standard, consolidated 2026-07-10)** · Supersedes `dag-layout-algorithm.md` + `dag-viewport.md`, absorbs the edge visual rules from `branch-collaboration.md`

> How the right-panel Viewport minimap draws the DAG: where each node goes, what
> each edge looks like, and what the user sees by default. **This document is the
> authoritative implementation standard** — write the layout code to match it, and
> when something breaks, check against it. For the data semantics (nodes, the two
> edges) see `session-dag.md`; this document only covers the drawing.
>
> Every rule comes with an example. The SVGs for the 7 base scenarios live in
> `dag-layout-spec.html`; the newly added scenarios 8–12 are authoritative in ASCII
> for now, and adding their spec.html figures is a to-do.

---

## 0. First, "what to draw": two granularities, only the conversation layer by default

A session graph has two kinds of node, an order of magnitude apart in count:

| Layer | Nodes | Question it answers | Magnitude |
|---|---|---|---|
| **Conversation layer** | ROOT, user, llm replies, spawn branch roots, merge | What shape the session has: how many turns, how many branches, who spawned whom | single digits ~ dozens |
| **Execution layer** | code (tool call) and its internal sub-calls | What one turn did internally | can reach dozens in a single turn |

**Default visibility rule: the Viewport lays out only the conversation layer.** If an
llm node has an execution subtree (code nodes hanging off it via `caller`), it is
collapsed into a `⚒N` count badge next to the node (N = direct + transitive
sub-calls). Click the badge and that turn's execution subtree expands and enters
layout; click again to collapse. Expansion state is remembered per node and cleared
when switching sessions.

```
Default (conversation layer):    Click ⚒9 to expand that turn:
◇ROOT                          ◇ROOT
├ ○你好                        ├ ○你好
│ └ △回复                      │ └ △回复
├ ○查天气                      ├ ○查天气
│ └ △回复 ⚒9                   │ └ △回复
                               │     ├ ■bash
                               │     ├ ■web_fetch
                               │     └ ■…(9 total)
```

Rationale: execution-layer information already has a better presentation in the chat
stream (each turn's execution-tree card, the Executions page). The Viewport's job is
to let you see the session structure at a glance; 50 tool squares laid out flat would
drown the 8 structural nodes — which is exactly what happened in the real 2026-07-10
weather session (66 nodes, 50+ of them code).

> The other two views — chat stream and call tree — are unaffected: the chat stream
> lays out top-level turns by seq with function nesting folded; the Executions /
> execution-tree card expands fully along caller. Same data, three projections.

---

## 1. A node's position = (column, row)

- **Column (horizontal) = lane start column + tier indent**
- **Row (vertical) = depth**

### lane — which branch it belongs to

**Count the branches and hand out column numbers 0, 1, 2… in order of appearance; no
gaps, no judging which is "the trunk."**

A branch = one conversation chain (user → llm → user → …). Three events produce a new
branch:

| Event | New branch's root | Attachment |
|---|---|---|
| retry / rewrite a turn | the forked-off user / llm node | shares predecessor with the replaced node |
| spawn (task / message_branch dispatch) | the `source=agent_spawn` user node | caller = the initiating node, predecessor empty |
| the new mainline from a merge | the merge node itself | lands in the base branch lane (see scenario 10), no new lane opened |

**Branches are packed by actual column occupancy**: the columns a branch occupies = from
its start column to the deepest column of its subtree; the next branch starts at the
previous branch's actually-occupied rightmost column +1, with no overlap.

### tier — how many columns to indent within a branch

**The conversation layer is fixed by role; the execution layer increases by caller
depth.** Two rules, each governing one layer, so they no longer conflict (the old docs
never adjudicated which one a spawn root counts by — now adjudicated: a spawn root is a
conversation-layer user, tier=1; its caller pointing at a deep node only determines
where the spawn edge is drawn from, not its own indent):

| Node | Layer | tier |
|---|---|---|
| ROOT | — | 0 |
| user (incl. spawn branch root, hand-back node) | conversation | 1 |
| llm reply, merge | conversation | 2 |
| code (tool / function call) | execution | 3 |
| a deeper call inside the execution layer | execution | caller's tier +1 |

### depth — which row

Top to bottom in order of occurrence (predecessor chain + seq). A branch that forks off
**starts on the same row** as the position it forked from; a spawn branch starts on the
**row after the initiating node** (it happens after the initiating node).

---

## 2. Three global layout rules (unchanged, inherited from the old layout docs)

**① Square grid**: `COL_W == ROW_H`, child nodes strictly at the parent's lower-right
corner (45°).

**② Strict alignment + compaction**: nodes land on grid intersections; **empty rows shift
up to fill, empty columns shift left to fill, no empty rows or columns are kept.** This
applies to every visibility change: execution subtree collapse/expand, branch folding,
visibility filtering — once collapsed, the rows and columns it occupied must be freed
immediately. **Corollary: any "placeholder box" violates this rule** — the running state
is expressed by the node's own stroke (see the legend), not by drawing a dashed
placeholder node.

**③ Branches don't overlap**: see the lane rule.

---

## 3. Edges: color = branch, line style = type (orthogonal, iron rule)

Each lane has one color (`dag/types.ts` `LANE_COLORS`). Any edge uses the lane color of
the branch it belongs to / points at; **never give a category of edge a fixed color.** Type
is conveyed only by line style:

| Edge type | Line style | Color | Default |
|---|---|---|---|
| same-branch parent→child | solid | this branch's color | shown |
| retry fork bridge | dashed `5 4` | this branch's color | shown |
| spawn edge (initiating node → branch root) | dash-dot `4 2 1 2` | child branch's color | shown |
| merge convergence (peer tip → merge node) | thick solid 2.4px | peer branch's color | shown |
| attach merge-back (source tip → embed position) | long dashes `4 4` | source branch's color | shown |
| inter-branch communication (send_to_branch) | dotted `1 5` | target branch's color | **shown only on hover** (numerous; always-on would smear) |

---

## 4. Node legend: shape = role, stroke = status

**Shape**: ◇ ROOT · ○ user · △ llm · ■ code · ◎ merge (double ring, the graph's unique
"convergence" shape).

**status mapping** (retiring the dashed placeholder box — status is drawn on the node
itself):

| status | Drawing |
|---|---|
| success | default stroke |
| running | same-shape dashed stroke + breathing-opacity animation |
| error | red stroke + `!` badge at the upper right |
| cancelled | whole node grayed 50% |

**Badges** (attached to the node, no grid cell of their own):

| Badge | Meaning |
|---|---|
| `⚒N` (right of an llm node) | a collapsed execution subtree, N sub-calls; click to expand |
| `×N` (right of a code node) | N isomorphic siblings produced by a loop, folded (pure display) |
| `↗` (upper right of a spawn root) | cross-session spawn: the caller lives in another session's graph; tooltip gives the source session. Within this session it hangs on ROOT |

---

## 5. Branch-name badge

- **Anchoring**: below the branch chain's **last conversation-layer node** (execution-layer
  nodes are not considered, so expanding/collapsing the execution layer doesn't move the
  badge).
- **Collision**: when two badges' grid positions overlap, the later one (by branch order)
  slides down one row until there's no collision.
- **Source**: active branches come from `list_branches` (bright, clickable to checkout);
  **merged branches'** names come from session meta `branches` (gray, read-only, not
  clickable) — merging doesn't erase the name.
- Styling follows the HEAD label (`--bg-hover` rounded background, 9px text, backing sized
  to the measured text width).

---

## 6. Scenarios (1–7 in spec.html, 8–12 newly added)

**Scenarios 1–7 (existing SVGs)**: single turn / multiple turns / retry / tool indent /
manual function / composite / collapse shift-left. The rules are unchanged; scenario 4
(tool indent) shows as a `⚒N` badge in the default view, and only becomes the original
indented squares once expanded.

### Scenario 8 · spawn branch (dispatch within this session)

The reply of the "check weather" turn calls task() and spawns a sub-agent:

```
col:  0    1    2    3    4
row0 ◇ROOT
row1 ├ ○你好
row2 │ └ △回复
row3 ├ ○查天气
row4 │ └ △回复 ⚒2 ─┄─╮        ← spawn edge (dash-dot) starts from the initiating node
row5 │               ○子代理prompt     ← spawn branch root: new lane, tier=1,
row6 │               └ △子代理回复 ⚒21    starts on the initiating node's row +1
```

Key points: the branch root's caller = the initiating node (`session-dag.md` §2.3), so the
dash-dot line is drawn precisely from "who spawned it" to "what got spawned"; the branch
root itself lays out as a conversation-layer user (tier=1, its own lane). If the sub-agent
spawns again (coordinator→worker, within the depth cap), the same rule recurses: the
worker branch's dash-dot line starts from the sub-agent's reply node.

### Scenario 9 · large execution subtree (default aggregation ↔ expansion)

See the section-0 example. When expanding a turn: that turn's code subtree enters layout
with the tier indent from scenario 4, rows and columns are re-packed on the spot per rule
②; once collapsed, rows and columns are reclaimed. **The two branches' expansion states are
independent of each other.**

### Scenario 10 · merge (multi-parent convergence)

Two branches merge, an equal merge produces a new tip:

```
col:  0    1    2    3    4
row0 ◇ROOT
row1 ├ ○user ┈┈┈┈┈┈ ○user'      ← retry fork (dashed bridge)
row2 │ └ △llm         └ △llm'
row3 │ ╔══════════════════╝      ← convergence line (thick solid, peer branch's color)
row4 ├ ◎merge                    ← double-ring shape, lands in base branch lane (ruling: no new lane)
```

- the merge node's `predecessor` = base tip; the peer is expressed via an attach pointer
  (data layer).
- **the attach pointer node is not drawn** (`display=runtime` filtered out); only the
  convergence line is drawn — an existing ruling from branch-collaboration.md, recorded
  here as spec.
- after the merge the peer branch no longer extends; its lane naturally narrows once the
  rows below it are vacated per rule ②.

### Scenario 11 · dispatch merge-back (spawn + attach)

The sub-branch finishes, the result is attached back to the main branch (the Spawned card
in the chat stream):

```
col:  0    1    2    3
row1 ├ ○查天气
row2 │ └ △回复 ─┄─╮
row3 │           ○子代理prompt
row4 │           └ △子代理tip
row5 ├ ⟨attach landing⟩ ⇠┄┄╯        ← attach merge-back long dashes: sub-branch tip → main-branch embed position
```

The attach pointer node itself is not drawn (same as scenario 10); the merge-back long
dashes are pulled from the sub-branch tip back to the position it embeds into on the main
branch. In the chat stream this data renders as a Spawned card (its display order moved up
to before that turn's reply — a display-layer reordering, the data order untouched, see
`ui/invariants.md` rule 9).

### Scenario 12 · hand-back node and switcher

The hand-back of a message_branch (the sub-branch's reply comes back to the initiator's
lane as a user node, `predecessor=the initiating point`): if the user also sent a message
of their own while waiting, the two share a predecessor and form a fork — **the hand-back
node participates in the `< N/M >` switcher** (it is a genuine continuation-alternative of
the initiator's conversation, `source=from_branch` does not isolate the way agent_spawn
does; for the isolation rule see `ui/invariants.md` rule 7).

---

## 7. Render pipeline (code map)

```
web/lib/runtime-bridge/dag/
  pipeline.ts        orchestration: passes → layout → edges → nodes → badges → visibility
  passes/            data transforms (merge runs, execution-subtree aggregation, collapse)
  layout/            lane / tier / depth (the implementation of section 1)
  render/edges.ts    the line-style table of section 3
  render/nodes.ts    the shapes + status strokes + badges of section 4
  render/badges.ts   the branch-name badge of section 5
  store/globals.ts   expansion state, lastGraph, signatures
```

The backend `openprogram/webui/graph_builder.py` produces the node array (including the
`branch_name` stamp, caller/predecessor), and `graph_layout/` does the lane/tier/depth
annotation. Verification tool: `python tools/dag_dump.py <session_id>` prints
lane/tier/depth + an ASCII grid.

## 8. Known gaps vs. the implementation (2026-07-10 inventory)

Item-by-item against this spec, in landing order:

| # | Gap | Spec item |
|---|---|---|
| 1 | Execution subtree laid out flat by default (no aggregation pass, no ⚒N badge) | §0 |
| 2 | Collapse leaves a placeholder dashed box that occupies a cell | rule ② corollary |
| 3 | running state drawn as a standalone dashed placeholder node | §4 status |
| 4 | badge anchored to the "lane's deepest visible node" (incl. execution layer), no collision slide | §5 |
| 5 | merge node has no dedicated shape, convergence line not colored by peer | scenario 10 |
| 6 | attach pointer still drawn as a square in the viewport | scenario 10/11 |
| 7 | cross-session spawn has no ↗ badge (silently hangs on ROOT) | §4 badges |
| 8 | spawn root tier computation not per the "conversation-layer user=1" ruling | §1 tier |
</content>
</invoke>
