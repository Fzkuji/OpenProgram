# DAG Rendering Spec (Layout · Edges · Legend · Default Visibility)

> How the session graph draws: where each node goes, what each edge looks like,
> and what the user sees by default. **This document is the authoritative
> implementation standard** — write the layout code to match it, and when
> something breaks, check against it. For the data semantics (nodes, the two
> edges) see `dag/overview.md`; this document only covers the drawing.
>
> Every rule comes with an example. **The SVG scenario figures in
> `dag-layout-spec.html` are authoritative** (13 scenes: 1–7 base layout, 8 merge,
> 9 cross-branch messaging, 10 spawn dispatch & merge-back, 11 execution-subtree
> aggregation, 12 status & badge legend, 13 badge anchoring & collision). The ASCII
> figures in this file are a text-mode digest, equivalent to the html; on conflict
> the html wins.

---

## Where the graph lives: a center perspective

The graph is one of the chat pane's two **perspectives**, not a side panel. Each
center tab is either on the conversation transcript or on the context graph, and
the pair of controls at the pane's top-right switches between them — a
perspective toggle plus a `…` menu of session actions, following Obsidian's
per-pane controls. The perspective is per tab, so parking one session on the
graph leaves the others on their transcripts.

Giving the graph the full column width is the point: a branchy session needs
lanes, tiers and branch badges that a 288px rail truncates.

| Piece | Where |
|---|---|
| Perspective state | `CenterTab.dagView` (`web/lib/state/center-tabs-store.ts`) — not persisted; a reload opens on the transcript |
| Controls | `web/components/chat/view-controls.tsx` |
| Graph host | `web/components/chat/dag-view.tsx` — renders `#historyPanel` + `.history-body`, the elements `pipeline.ts` and `render/visibility.ts` select |
| Perspective swap | `.center-pane-chat[data-center-view]` in `web/app/styles/chat.css` |

Both surfaces stay mounted and swap by `display`: the renderer paints into the
host on every capture regardless of which perspective is showing, so unmounting
would blank the graph until the next one. The host's `ResizeObserver`
(`_wirePanelResize`) is what re-flows the layout — a perspective switch, a
split-view drag and a window resize all reach it the same way.

Clicking a node in the graph fills the right sidebar's Details / Context views;
those views stay in the sidebar because they read one selected node, not the
whole session.

### The composer belongs to the pane, not to the transcript

**The perspective swap hides `#chatArea`, the transcript scroll box — never
`#chatView`.** The composer is a singleton portalled into `#composer-mount`
inside `#chatView` and anchored `bottom: 0` against it, so hiding that ancestor
would take the composer down with the transcript, and mounting a second one
would fork the draft, the run state and the model row into two copies of what
the user reads as one input box. Both perspectives therefore share the one
composer instance, and the graph replaces only the scroll area above it.

You can send a message from the graph, and the node it produces appears on the
next capture — the send path is the transcript's send path, unchanged, so
nothing about the graph perspective has to know it is showing.

The graph takes the transcript's place by **covering the pane**, not by taking
its slot in the column: `#chatView` keeps its `flex: 1` and its full height, so
the composer's `bottom: 0` still lands on the pane's bottom edge, and stacking
order does the rest — the graph sits under the composer's `z-index: 5`. Shrinking
`#chatView` instead would drag the input box up the pane.

`.dag-view` reserves the composer's strip as `padding-bottom`, the way the
transcript reserves it on `.chat-messages`, so the deepest nodes never sit
behind the input box.

### Branch strip

A single wrapping row of pills above the canvas, one per active branch
(`web/components/right-sidebar/branches`, `variant="chips"`). Each chip carries
the branch's lane colour as a dot and its name; the HEAD chip is outlined and
badged. Click a chip to check that branch out; hover reveals rename and delete.

The chip and the sidebar's list row are **the same component** under two
layouts, so checkout, rename and delete are one implementation and cannot drift
between the two surfaces. Only the box differs — a pill sized to its content
instead of a stretched row. Merge and attach stay with the list layout: picking
two branches and starring a base needs vertical room the strip does not have,
and the strip's question is the narrower "which branch am I on".

The graph already draws the branch structure, so the strip does not repeat it.
It is capped at three rows; past that it scrolls, leaving the canvas the bulk
of the pane.

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
drown the 8 structural nodes — a real weather session with 66 nodes, 50+ of them code,
looked exactly like that.

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
depth.** Two rules, each governing one layer, so they never conflict. A spawn root
counts by the conversation-layer rule: it is a conversation-layer user, tier=1, and its
caller pointing at a deep node only determines where the spawn edge is drawn from, not
its own indent.

| Node | Layer | tier |
|---|---|---|
| ROOT | — | 0 |
| user (incl. spawn branch root, hand-back node) | conversation | 1 |
| llm reply, merge | conversation | 2 |
| code (tool / function call) | execution | 3 |
| a deeper call inside the execution layer | execution | caller's tier +1 |

### depth — which row

Rows are allocated by a **preorder walk of the structural parent tree**: every visible
node takes its own row, and a subtree pushes the siblings below it down by however many
rows it occupies. Rows are not "hops to root" — that would stack all children of one
parent on a single row. Two exceptions keep their anchor's row because they grow
sideways, not down: a fork sibling sits on the **same row** as the sibling it rewrites
(scene 3), and a spawn branch root sits on the **same row as the spawn call node**
(scene 10). Cross-session spawns land as the target session's own conversation chain
(lane 0), not a side branch (scene 12).

---

## 2. Three global layout rules

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

## 3. Edges: color = branch, line style = type (orthogonal)

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

**status mapping** — status is drawn on the node itself, never as a separate dashed
placeholder box:

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
| `↗` (top-right corner) | marked on **both sides** of a cross-session spawn: the branch root in the target session (caller lives in another session's graph, hangs on ROOT here, tooltip "spawned from <source session>"); and the initiating node in the source session (tooltip "dispatched to <target session>" — otherwise the dispatch leaves no trace in its own graph). Click jumps to the peer session (implementation may come later). **Cross-session only**: a same-session spawn has both ends in the graph and the dash-dot edge already expresses the relationship (scene 10) — no ↗ there; the mark stands in for the edge that cannot be drawn, and is not a generic spawn decoration |

---

## 5. Branch-name badge

- **Anchoring**: **directly below the branch's deepest currently visible node**, one row
  down. In the default (folded) view that is the last conversation-layer node; when the
  execution subtree is expanded the badge follows the bottom-most expanded node and
  moves back up on collapse. Branch membership = lane (expanded execution nodes share
  their turn's lane).
- **Edge avoidance**: only when an edge crosses the anchor cell (the descending line of
  an expanded execution subtree, or the conversation continuing) does the badge shift
  half a column left — a badge never sits on an edge. Expanding/collapsing the execution
  layer only toggles this half-column shift; it never changes the anchor node.
- **Collision**: judged on **measured pixel boxes** (badge backing width = measured text
  width + padding, not grid cells). Short names almost never collide across the grid
  spacing; long names (branches auto-named from a message or task description) on
  same-row anchors do overlap — the later one (by branch order) slides down one row
  until there's no collision.
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
| 1–7 | Base layout (single turn / multi-turn / retry / tool indent / manual function / composite / collapse shift-left) | Scenario 4's tool indent shows as a ⚒N badge in the default view (scene 11); the indented squares appear only after expansion |
| 8 | merge (multi-parent convergence) | ◉ solid circle with a hole, lands on the base branch lane, peer merge-in thick solid lines (peer lane color); attach pointer nodes are not drawn, only the lines |
| 9 | cross-branch messaging (send_to_branch) | dotted `1 5`, target branch color, hidden by default / shown on hover; a from_branch user node lands at the target branch tail |
| 10 | spawn dispatch → attach merge-back | spawn edge dash-dot `4 2 1 2` (child branch color); the child branch's first node sits on the **same row** as the spawn node, own lane, tier=1; merge-back long dash `4 4` from the child tip back to its embed position on the main branch (the chat stream renders it as the Spawned card, display order moved ahead — see `ui/invariants.md` rule 9) |
| 11 | execution-subtree default aggregation | see §0: collapsed to a ⚒N badge by default, click to expand into layout, collapse reclaims rows/cols per rule ②; expansion state is per-branch independent |
| 12 | status & badge legend | see §4: status drawn on the node's own stroke, no placeholder boxes; both sides of a cross-session spawn carry the ↗ corner mark |
| 13 | badge anchoring · avoidance · collision · merged | see §5: anchor directly below the branch's last conversation-layer node, half-column left shift only when an edge crosses the anchor cell, collision shifts down one row, merging erases the badge (provenance moves into the merge node's tooltip) |

**Send-back nodes and the switcher (semantic note, no dedicated layout scene)**: a
message_branch send-back (the child branch's answer returning to the initiator's lane
as a user node with `predecessor = the initiating node`) forms a fork whenever the user
also sent a message while waiting — **send-back nodes participate in the `< N/M >`
switcher** (they are genuine alternative continuations of the initiator's dialogue;
`source=from_branch` gets no agent_spawn-style isolation — see `ui/invariants.md`
rule 7).

**A sub-agent spawning again**: forbidden at the data layer
(`MAX_TASK_DEPTH=1` — only the main agent may task(); a spawned agent always
does the work itself, see `ui/invariants.md` rule 6). The renderer still
recurses per scene 10 as a fallback, so historical multi-generation delegation
chains (the worker branch's dash-dot edge starting from the sub-agent's reply
node, hanging under its lane structure) remain drawable.

## 7. Render pipeline (code map)

```
web/lib/runtime-bridge/dag/
  pipeline.ts        orchestration: passes → layout → edges → nodes → badges → visibility
  passes/            data transforms, applied in order:
    merge-runs.ts               merge consecutive runs of the same node
    collapse-runtime-pairs.ts   fold a legacy display=runtime user/assistant
                                wrapper pair into a single row (pre-`caller`-edge
                                schema left the wrapper with no chat content of
                                its own, so it duplicated the column)
    demote-decoration-cards.ts  re-stamp LLM-triggered runtime cards so a reply
                                with both a card child and a follow-up user turn
                                isn't mistaken for a fork (which would split the
                                figure into two lanes)
    apply-collapse.ts           fold execution subtrees, emit the ⚒N badge
  layout/            lane / depth (the implementation of section 1).
                     **tier is NOT computed here** — the backend computes it in
                     `openprogram/webui/graph_layout/tier.py` and ships it on the
                     node; the front end only consumes the value.
  render/edges.ts    the line-style table of section 3
  render/nodes.ts    the shapes + status strokes + badges of section 4
  render/badges.ts   the branch-name badge of section 5
  store/globals.ts   expansion state, lastGraph, signatures
```

The backend `openprogram/webui/graph_builder.py` produces the node array (including the
`branch_name` stamp, caller/predecessor), and `graph_layout/` does the lane/tier/depth
annotation — **tier specifically in `graph_layout/tier.py`**. Verification tool: `python tools/dag_dump.py <session_id>` prints
lane/tier/depth + an ASCII grid.

## 8. The white fill means context coverage

A node's white fill marks it as **part of what the next LLM call will carry**.
There is no mode to choose and no switch to find: whenever the graph is
showing, it owns its pane. A lone session tab gets the chat shell plus this
graph; two session tabs split into two `PeerSessionPane`s where the shell — and
therefore the graph — is not rendered at all. So there is never a transcript
beside the graph, "which bubbles are on screen" has no reading to give, and
coverage is simply what the fill means.

The covered set is served by `GET /api/sessions/{id}/context-range`: the active
branch walked back from head, stopping at the most recent compaction summary.
Nodes outside it draw dimmed. `enterExclusiveCoverageMode`
(`web/lib/runtime-bridge/dag/index.ts`) is what the host calls to fetch and
apply it.

### Coverage data shape

The same response carries, per node, the two degradations the context pipeline
applies to content it is still carrying:

```json
{
  "session_id": "…",
  "node_ids": ["…"],
  "count": 12,
  "nodes": [
    { "node_id": "…", "in_context": true, "aged": false, "spilled": true }
  ]
}
```

| Field | Meaning | Backend source |
|---|---|---|
| `in_context` | the node is in the covered set — always true for a row, since the list IS that set | `get_branch` |
| `aged` | a code node old enough that its result renders as a one-line stub | `openprogram/context/render.py::_aged_code_ids`, boundary from `context/aging.py` |
| `spilled` | the result was written to `large_nodes/` and the render only cites it | `metadata.spilled`, written by `context/spill.py::spill_if_large` |

Both flags come from the functions the real render pass calls, not from a
parallel reimplementation — **the graph never derives context semantics
itself**; it asks the backend and draws the answer.

### Drawing the two degradations

| State | Drawing |
|---|---|
| in context | white fill (the baseline) |
| `aged` | stroke dimmed to 40% opacity — reads as "still here, but only the gist" |
| `spilled` | `▤` at the node's upper LEFT |

`aged` dims **`stroke-opacity`, never `opacity`**: the white fill is the
coverage signal, and fading the whole node would fade that too, collapsing two
independent facts into one. The `▤` mark takes the upper left because the upper
right is spoken for (`!` error, `↗` cross-session spawn) and the lower right
holds the fold badge; it is drawn as `<text>` so `_applyVisibility`'s
"first shape child" scan cannot mistake it for the node body.

Compaction covers a range of nodes with a summary; those covered nodes simply
fall out of `node_ids` and dim like anything else out of context. A dedicated
glyph for the summary node is a separate question — see §4's rule that status
and coverage live on the node's own stroke, never in a placeholder box.

Refreshing: coverage is re-fetched whenever `context_stats` or
`compaction_finished` arrives (`chat-handlers.ts`), so the fill tracks the real
context without the frontend ever recomputing it. Note that `aged` and
`spilled` are **not** part of the render signature, so applying new coverage
busts it (`setLastSignature(null)`) — otherwise the repaint silently no-ops and
the graph keeps painting the previous answer.

Compaction interaction:

- `insert_summary_node` clones nothing (dag/overview.md §8): the summary is an
  ordinary `role=llm` chain member carrying `metadata.covers`, and the kept
  tail keeps its own ids and predecessors. The branch ids therefore ARE the
  ids the graph draws, so `/context-range` returns them directly — no
  translation layer, no second id space.
- The summary node is drawn like any other conversation node; only genuinely
  synthetic bridges are filtered (`graph_layout/filter.py`).
- After compaction the covered prefix falls out of the set → it dims; the
  summary and the kept tail keep highlighting. This IS the compaction
  visualization — a separate summary-node glyph is rejected (per
  dag/overview.md: no 4th role). If a future need arises for an explicit
  "N turns compacted here" marker, it must be a badge on the first kept
  node, not a node.
- `compaction_finished` must refresh the context range
  (`chat-handlers.ts`); coverage is event-driven like everything else — the
  frontend never computes context membership itself.
- Context ids that have no drawn node (e.g. `display=runtime`
  task-followup rows) are silently ignored: they are context, but not
  graph.

## Appendix: Implementation Status

The whole spec is implemented. Where each part lives:

| Spec item | Implementation |
|---|---|
| §0 execution-subtree aggregation | `passes/apply-collapse.ts`: any node with execution sub-calls starts folded; `render/nodes.ts` draws ⚒N (spawn-root subtrees exempt) |
| Rule ② corollary (no placeholder box) | `shapes.ts`: no `square_outline`; task renders as a plain square |
| §4 status on the stroke | `graph_builder` emits status; `nodes.ts` draws it on the stroke (running dashed+breathing / error red+! / cancelled grayed) |
| §5 badge anchoring | `render/badges.ts`: anchor at last conversation-layer node, half-column left shift when a line crosses the anchor cell, measured-pixel-box collision slides down one row |
| Scene 8 merge shape and lines | `shapes.ts` `merge_dot` (◉); `edges.ts` merge-in line peer-colored 2.4px solid |
| Scenes 8/10 attach pointer | backend filters it (display=runtime) + `graph_builder` stamps the ref onto the embed host (`attach_returns`); `edges.ts` draws the long-dash return line |
| §4 cross-session ↗ | `graph_builder` stamps `spawn_remote` (target side); `nodes.ts` draws ↗ (source-side `spawn_out` rendering is ready, awaiting a data source that stamps it) |
| §1 spawn root tier | `graph_layout`: tier=1 / same-row depth / new lane; `task_followup` without an attach pointer re-parents onto the receiving turn (`filter.py` fallback) |
| Composer shared by both perspectives | `chat.css` hides `#chatArea`, not `#chatView`; asserted by `web/scripts/check-center-tabs.mjs` |
| Branch strip | `BranchesPanel variant="chips"` + `BranchItem chip`; `.branches-strip` / `.branch-chip` in `chat.css` / `right-dock.css` |
| §8 coverage query | `routes/tree.py::_coverage_nodes` fills `/context-range`'s `nodes`; tested in `tests/unit/test_context_range_coverage.py` |
| §8 aged / spilled drawing | `render/nodes.ts` (stroke-opacity + `▤`), fed by `_coverageSet` in `store/globals.ts` |
