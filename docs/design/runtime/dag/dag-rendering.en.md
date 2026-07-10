# DAG Rendering Spec (Layout · Edges · Legend · Default Visibility)

Status: **decided (authoritative implementation standard, consolidated 2026-07-10)** · Supersedes `dag-layout-algorithm.md` + `dag-viewport.md`, absorbs the edge visual rules from `branch-collaboration.md`

> How the right-panel Viewport minimap draws the DAG: where each node goes, what
> each edge looks like, and what the user sees by default. **This document is the
> authoritative implementation standard** — write the layout code to match it, and
> when something breaks, check against it. For the data semantics (nodes, the two
> edges) see `session-dag.md`; this document only covers the drawing.
>
> Every rule comes with an example. **The SVG scenario figures in
> `dag-layout-spec.html` are authoritative** (13 scenes: 1–7 base layout, 8 merge,
> 9 cross-branch messaging, 10 spawn dispatch & merge-back, 11 execution-subtree
> aggregation, 12 status & badge legend, 13 badge anchoring & collision). The ASCII
> figures in this file are a text-mode digest, equivalent to the html; on conflict
> the html wins.

---

## 0. First, "what to draw": two granularities, only the conversation layer by default

A session graph has two kinds of node, an order of magnitude apart in count:

| Layer | Nodes | Question it answers | Magnitude |
|---|---|---|---|
| **Conversation layer** | ROOT, user, llm replies, spawn branch roots, merge, **manually-invoked top-level function nodes** (the user's explicit action — the code node behind a fn-form/run card) | What shape the session has: how many turns, how many branches, who spawned whom | single digits ~ dozens |
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
| the new mainline from a merge | the merge node itself | lands in the base branch lane (see scenario 8), no new lane opened |

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
**same row as the spawn call node** (whichever row the spawn happens on, the dispatched branch starts there — spec.html scene 10).

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

**Shape**: ◇ ROOT · ○ user · △ llm · ■ code · ◉ merge (solid circle with a hole, the graph's unique
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
| `↗` (top-right corner) | marked on **both sides** of a cross-session spawn: the branch root in the target session (caller lives in another session's graph, hangs on ROOT here, tooltip "spawned from <source session>"); and the initiating node in the source session (tooltip "dispatched to <target session>" — otherwise the dispatch leaves no trace in its own graph). Click jumps to the peer session (implementation may come later). **Cross-session only**: a same-session spawn has both ends in the graph and the dash-dot edge already expresses the relationship (scene 10) — no ↗ there; the mark is a stand-in for the edge that cannot be drawn, not a generic spawn decoration |

---

## 5. Branch-name badge

- **Anchoring**: below the branch chain's **last conversation-layer node** (execution-layer
  nodes are not considered, so expanding/collapsing the execution layer doesn't move the
  badge).
- **Collision**: when two badges' grid positions overlap, the later one (by branch order)
- **Never on an edge**: when the anchor node has a descending edge (an expanded execution subtree, or the conversation continuing), the badge shifts half a column left to clear that vertical line.
  slides down one row until there's no collision.
- **Source**: badges come ONLY from `list_branches` — **active** branches (bright,
  clickable to checkout). **Merging erases the name** (git semantics): a merged-in
  branch no longer draws a badge; its name moves into the ◉ merge node's tooltip
  (like a merge commit message recording provenance). The name data in session meta
  is kept on disk.
- Styling follows the HEAD label (`--bg-hover` rounded background, 9px text, backing sized
  to the measured text width).

---

## 6. Scenarios (SVG authority in spec.html, 13 scenes)

| # | Scene | Key points |
|---|---|---|
| 1–7 | Base layout (single turn / multi-turn / retry / tool indent / manual function / composite / collapse shift-left) | Rules unchanged; scenario 4's tool indent shows as a ⚒N badge in the default view (scene 11), the indented squares appear only after expansion |
| 8 | merge (multi-parent convergence) | ◉ solid circle with a hole, lands on the base branch lane, peer merge-in thick solid lines (peer lane color); attach pointer nodes are not drawn, only the lines |
| 9 | cross-branch messaging (send_to_branch) | dotted `1 5`, target branch color, hidden by default / shown on hover; a from_branch user node lands at the target branch tail |
| 10 | spawn dispatch → attach merge-back | spawn edge dash-dot `4 2 1 2` (child branch color); the child branch's first node sits on the **same row** as the spawn node, own lane, tier=1; merge-back long dash `4 4` from the child tip back to its embed position on the main branch (the chat stream renders it as the Spawned card, display order moved ahead — see `ui/invariants.md` rule 9) |
| 11 | execution-subtree default aggregation | see §0: collapsed to a ⚒N badge by default, click to expand into layout, collapse reclaims rows/cols per rule ②; expansion state is per-branch independent |
| 12 | status & badge legend | see §4: status drawn on the node's own stroke, placeholder boxes abolished; both sides of a cross-session spawn carry the ↗ corner mark |
| 13 | badge anchoring & collision | see §5: anchor at the branch's last conversation-layer node, collision shifts down one row, merging erases the badge (provenance moves into the merge node's tooltip) |

**Send-back nodes and the switcher (semantic note, no dedicated layout scene)**: a
message_branch send-back (the child branch's answer returning to the initiator's lane
as a user node with `predecessor = the initiating node`) forms a fork whenever the user
also sent a message while waiting — **send-back nodes participate in the `< N/M >`
switcher** (they are genuine alternative continuations of the initiator's dialogue;
`source=from_branch` gets no agent_spawn-style isolation — see `ui/invariants.md`
rule 7).

**A sub-agent spawning again (coordinator→worker, within the depth cap)**: recurse per
scene 10 — the worker branch's dash-dot edge starts from the sub-agent's reply node and
hangs under the sub-agent's lane structure.

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
| 5 | merge node has no dedicated shape, convergence line not colored by peer | scene 8 |
| 6 | attach pointer still drawn as a square in the viewport | scenes 8/10 |
| 7 | cross-session spawn has no ↗ mark on either side (target silently hangs on ROOT, source leaves no trace) | §4 badges |
| 8 | spawn root tier computation not per the "conversation-layer user=1" ruling | §1 tier |
</content>
</invoke>
