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
| Perspective state | `CenterTab.dagView` (`apps/web/lib/state/center-tabs-store.ts`) — not persisted; a reload opens on the transcript |
| Controls | `apps/web/components/chat/view-controls.tsx` |
| Graph host | `apps/web/components/chat/dag-view.tsx` — renders `#historyPanel` + `.history-body`, the elements `pipeline.ts` and `render/visibility.ts` select |
| Perspective swap | `.center-pane-chat[data-center-view]` in `apps/web/app/styles/dag/view-host.css` |

Both surfaces stay mounted and swap by `display`: the renderer paints into the
host on every capture regardless of which perspective is showing, so unmounting
would blank the graph until the next one. The host does not re-flow on resize —
it is an infinite canvas, so a wider pane simply shows more of it (see below).

Clicking a node in the graph fills the right sidebar's Details / Context views;
those views stay in the sidebar because they read one selected node, not the
whole session.

### The canvas is infinite

There is no scroll box and no content-sized SVG. The SVG fills the pane, every
drawn thing lives inside one `<g>`, and that group carries a translate + scale
the user drives directly.

| Gesture | Effect |
|---|---|
| pinch, or ⌘/ctrl + wheel | zoom about the cursor, clamped to 25%–300% — the node under the pointer stays under the pointer |
| mouse wheel (discrete notches) | the same zoom, at wheel rate — a mouse has no pinch |
| trackpad two-finger scroll | pan, both axes — scrolling stays scrolling |
| drag on empty canvas | pan |
| drag starting on a node | the node's — click and double-click still work |

**Wheel triage** (`canvas.ts`): ctrl/⌘ + wheel zooms — browsers deliver a
trackpad pinch as a wheel event with `ctrlKey` set, and ⌘+wheel is the
explicit zoom chord — at a rate tuned for a pinch's small continuous deltas.
A mouse wheel zooms at wheel rate — a mouse has no pinch. macOS scroll
acceleration makes its `deltaY` fractional and variable, so the tell is the
legacy `wheelDeltaY`: Chromium/WebKit report a physical notch as a multiple
of 120 (trackpads carry arbitrary small values), with line-mode deltas as
the non-mac fallback. Everything else is a trackpad two-finger scroll and
pans, both axes — scrolling stays scrolling.

A box sized to its content decides two things it has no business deciding: how
big a graph may get before it needs scrollbars, and where the middle is. A wide
session got a horizontal scrollbar, a deep one got a vertical one, and reading
the whole shape meant scrolling two axes with no way to zoom out.

**The dot lattice is the coordinate system.** The pane's background paints a dot
per grid cell at the layout's own pitch (`COL_W`), transformed with the same pan
and zoom and offset so a dot's centre sits under every node anchor (the layout
pads its origin by `PAD_X` / `PAD_Y`; the lattice backs up half a tile from that
pad). A node visibly sits on a dot — and drifting off one is a bug anyone can
see without reading the layout code. The fit rounds its translation to whole
pixels for exactly this reason. The dot radius rides the zoom too (clamped
1–3px): fixed at 1.2px it vanishes into a zoomed-in tile.

**View state survives re-renders.** The graph repaints on every capture; moving
the camera each time would drag the view around while the user is reading. Pan
and zoom live in `store/globals` keyed by session, and only arriving at a
different session re-fits. Resizing the pane never re-fits — the user's angle on
the graph is theirs to keep.

**HUD.** At the composer's TOP-RIGHT — the right end of its env-chip row: a
fit button, a zoom cluster, and the legend popover, portaled into the
composer's `#dagHudSlot` (`dag-view.tsx`) so they ride the composer wherever
it sits and however tall it grows, rendered only while the DAG perspective is
showing. The zoom cluster is one pill holding − · readout · +: the buttons
step by exactly one wheel notch (`ZOOM_STEP`), and clicking the readout
resets to 100% — both anchored on the pane's centre, since a button has no
cursor to anchor on. The readout is written imperatively by `canvas.ts` on
every view change, because routing a gesture's every wheel event through
React state would repaint the tree sixty times a second.

The HUD draws no chrome of its own. The chips are listed in the composer's
env-pill rule (`composer.module.css`), so they are the same 24px filled pill
as the env chips beside them — same fill, inset ring, shadow, and hover — and
can never drift from them. The legend panel wears `MENU_PANEL`
(`components/chat/top-bar/menu-styles`), the one frame every popover menu in
the app shares; `styles/dag/hud.css` keeps only HUD-internal layout (the zoom
cluster's segments, the legend's upward anchoring and rows).

| Piece | Where |
|---|---|
| Pan / zoom / fit | `apps/web/lib/runtime-bridge/dag/canvas.ts` (`zoomStep` / `resetZoom` for the HUD buttons) |
| View state | `_viewTx` / `_viewTy` / `_viewScale` / `_viewSession` in `dag/store/globals.ts` |
| Surface + lattice | `.history-body` in `apps/web/app/styles/dag/canvas.css` |
| HUD | `DagHud` in `apps/web/components/chat/dag-view.tsx`; pill look from the env-pill rule in `composer.module.css`, legend frame from `MENU_PANEL`, internals in `styles/dag/hud.css` |

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

The canvas runs edge to edge under the composer — panning past it is one
gesture, and the fit centres the graph in the strip above it
(`canvas.ts::fitCanvas`), so nothing needs a reserved padding band.

### Branch switching lives in the graph

There is no branch strip above the canvas. Each branch's name is a button drawn
in the graph itself, anchored below the branch's last conversation-layer node
(`render/badges.ts`, §5): the active branch's tag is outlined and tinted in its
lane colour, and clicking any other tag checks that branch out. The graph
already draws the branch structure; a strip above it would repeat that
information and cut a line across the pane's floating view controls. Rename,
delete, merge and attach stay in the right sidebar's branch list, which has the
vertical room those flows need.

---

## 0. First, "what to draw": two granularities, only the conversation layer by default

A session graph has two kinds of node, an order of magnitude apart in count:

| Layer | Nodes | Question it answers | Magnitude |
|---|---|---|---|
| **Conversation layer** | ROOT, user, llm replies, spawn branch roots, merge, **manually-invoked top-level function nodes** (the user's explicit action — the code node behind a fn-form/run card) | What shape the session has: how many turns, how many branches, who spawned whom | single digits ~ dozens |
| **Execution layer** | code (tool call) and its internal sub-calls | What one turn did internally | can reach dozens in a single turn |

**Default visibility rule: the Viewport lays out only the conversation layer.**
Everything a turn *did* — every function call, every agent it spawned — is that
turn's **call thread** (§12): folded into a count on the node's shoulder by
default, opened by clicking the node into a column of real nodes beside it.

Two merges keep the chain at user-visible granularity — **one triangle = one
reply and everything the model did until the next user message**:

* a `task_followup` reply (the turn an agent's return triggers) is not a chain
  node. A function's return gets no new node when the model keeps talking, and
  an agent's return is the same event at a different scale — the reply merges
  into its **anchor**, found by climbing `predecessor` past every followup.
  A side effect worth naming: legacy data whose followup predecessors an old
  rollback bug scarred resolves to the same anchor, so the scar stops
  rendering as a phantom fork.
* a spawned agent's internal turns are not chain nodes either. The spawn head
  IS the agent (§12); everything in the agent's lane merges into it.

```
Default (conversation layer):    Click the reply to open its thread:
◇ROOT                          ◇ROOT
├ ○你好                        ├ ○你好
│ └ △回复                      │ └ △回复
├ ○查天气                      ├ ○查天气
│ └ △回复 ⁹                    │ └ △回复┄┐
                               │        ■ bash
                               │        ■ web_fetch
                               │        ■ sub-agent ⁵
                               │        ■ …(9 rows)
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

Both axes step in the same unit (`COL_W == ROW_H`, `dag/types.ts`), offset from
the origin by `PAD_X` / `PAD_Y` — the square lattice the canvas paints its dots
on.

### lane — which branch it belongs to

**Count the branches and hand out column numbers 0, 1, 2… in order of appearance; no
gaps, no judging which is "the trunk."**

A branch = one conversation chain (user → llm → user → …). Three events produce a new
branch:

| Event | New branch's root | Attachment |
|---|---|---|
| retry / rewrite a turn | the forked-off user / llm node | shares predecessor with the replaced node |
| spawn (agent / send_message dispatch) | the `source=agent_spawn` user node | caller = the initiating node, predecessor empty |
| the new mainline from a merge | the merge node itself | lands in the base branch lane (see scenario 8), no new lane opened |

**Branches are packed by actual column occupancy**: the columns a branch occupies = from
its start column to the deepest column of its visible CHAIN nodes; the next branch
starts at the previous branch's actually-occupied rightmost column +1, with no
overlap. A lane that begins with a fork root keeps **one extra gap column** from
the lane it forked off — the branch is a parallel version, and the two grid units
of air are what say so. Tiers are zeroed per lane (the backend hands a fork root
the tier of its old in-lane position; without zeroing a one-node branch arrives
columns adrift). **A fork lane mirrors the trunk internally**: the lane's
first column is an empty SPINE — the dashed bridge lands on it, its line
starts at the fork root's row and runs down, and every turn (the fork root
included) steps one column right and stubs off it, exactly as trunk turns
stub off ROOT's line. The branch starts as a line, not as a node. Only
capsules and superseded relics — lanes with no user turns of their own —
take the bridge straight to the glyph. Thread items
(§12) reserve no lane width: their column is chosen after the lanes are down,
in the first free column right of their anchor, and that column is then
reserved so a second open thread walks further right — two open threads
never share a cell.

### tier — how many columns to indent within a branch

**The conversation layer is fixed by role.** The execution layer does not indent
by caller depth any more — an open thread is one flat, time-ordered column (§12),
because "what did this turn do, in what order" is the question the graph answers;
per-call nesting is the transcript's and the Executions page's job.

| Node | Layer | column |
|---|---|---|
| ROOT | conversation | tier 0 |
| user | conversation | tier 1 |
| llm reply, merge | conversation | tier 2 |
| any thread item (call square, spawn head) | execution | its anchor's column +1; a nested open agent's thread +1 more |

### depth — which row

Chain rows are allocated by a **preorder walk of the structural parent tree**:
every visible chain node takes its own row, and a subtree pushes the siblings
below it down by however many rows it occupies. Rows are not "hops to root" —
that would stack all children of one parent on a single row.

One exception grows sideways instead of down: a fork root sits on the **same
row** as the chain sibling it runs parallel to (scene 3), and every branch off
one fork point shares that one row — the alternatives they are. Each branch
bridges to the branch immediately to its left, not back across everything to
the trunk, so the dashed bridge is a straight horizontal two grid units long.

Thread rows are allocated **recursively** (§12): an anchor's items run from its
next row (past any fork rows hanging off it) down one row per event; an open
spawn's own thread continues from its row, and its rows push the parent's later
items down. After a chain-anchor thread is seated, later conversation-layer
nodes (and already-placed thread rows below the insertion) shift down by the
occupied span — expansion is insertion, never overlay. Cross-session spawns land
as the target session's own conversation chain (lane 0), not a side branch
(scene 12).

---

## 2. Three global layout rules

**① Square grid**: `COL_W == ROW_H`, child nodes strictly at the parent's lower-right
corner (45°). The canvas's dot background paints the same lattice (see "The canvas
is infinite" above), so this is a property the eye checks, not a promise the code
makes.

**② Strict alignment + compaction**: nodes land on grid intersections; **empty rows shift
up to fill, empty columns shift left to fill, no empty rows or columns are kept.** This
applies to every visibility change: execution subtree collapse/expand, branch folding,
visibility filtering — once collapsed, the rows and columns it occupied must be freed
immediately. **Corollary: any "placeholder box" violates this rule** — the running state
is expressed by the node's own stroke (see the legend), not by drawing a dashed
placeholder node.

**③ Glyphs are cells; text is a caption.** A node occupies its grid point;
counts hang beside it in annotation grey, never inside a shape sized to its own
text. A glyph that grows with its label is a glyph every neighbour has to be
measured against — that negotiation is what §12's earlier pill got wrong.
Every glyph, the §9 compaction capsule included, is drawn on the reference
circle and occupies exactly one cell.
Names draw NOTHING on the canvas at all any more: a sub-agent's name lives in
the tooltip and inspector, and the only per-node text is the fold count on the
shoulder (§12) and the capsule's coverage count (§9).

---

## 3. Edges: color = branch, line style = type (orthogonal)

Each lane has one color (`dag/types.ts` `LANE_COLORS`). Any edge uses the lane color of
the branch it belongs to / points at; **never give a category of edge a fixed color.** Type
is conveyed only by line style:

| Edge type | Line style | Color | Default |
|---|---|---|---|
| same-branch parent→child | solid | this branch's color | shown |
| retry fork bridge (origin → the branch lane's spine top, shared row) | dashed `6 4` horizontal; an elbow only when rows diverged | branch's color | shown |
| call thread (anchor → its items, §12) | solid — the trunk pattern one level down: a vertical in the anchor's column, a horizontal stub per item (down first, then right, like every chain edge) | annotation grey | shown while open |
| merge convergence (peer tip → merge node) | thick solid 2.4px | peer branch's color | shown |
| attach merge-back (source tip → embed position) | long dashes `4 4` | source branch's color | shown when both ends are visible — an agent-internal tip merged into its triangle draws no line; the spawn head's position ON the thread is the return relationship |
| inter-branch communication (send_message) | dotted `1 5` | target branch's color | **shown only on hover** (numerous; always-on would smear) |

**Every line is drawn centre to centre**, and the glyphs' background fill paints
over the ends. Glyph edges sit at different distances per shape, so any fixed
stand-off eventually gaps against a sloped triangle side; a line that dies under
the glyph joins seamlessly for every shape.

---

## 4. Node legend: shape = role, stroke = status

**Shape**: ◇ ROOT · ○ user · △ llm · ■ code · ◉ merge (solid circle with a hole, the graph's unique
"convergence" shape) · ◎ compaction summary (a double circle: the reference circle with a thin inner ring — §9) ·
■ sub-agent spawn (a square like every call — dispatching an agent IS a
function call; it expands into the agent's own activity — §12).

**HEAD is a breathing glow on its own glyph**: a `drop-shadow` in the branch
colour that swells and settles on a slow cycle (2.4s), stamped on the shape
itself (`data-head`). The light hugs the glyph's outline at every zoom —
unlike a drawn halo ring, which reads as a second, blurrier node beside the
first — and unlike a solid fill, it leaves the shape vocabulary intact: HEAD
stays visibly a triangle/circle like its neighbours, just lit. Under
`prefers-reduced-motion` the pulse freezes to a steady glow. HEAD carries no
coverage mark of its own: it is the one node that cannot leave the context
window — the next request lands on it.

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
| fold count (upper-right shoulder, annotation grey) | the size of the node's folded call thread — §12. Digits glued to the glyph, no enclosing shape: anything shaped would read as a node. Open, it disappears — the calls are on screen and countable |
| `↗` (top-right corner) | marked on **both sides** of a cross-session spawn: `spawn_remote` on the first target-session user node and `spawn_out` on the initiating source-session node. The target projection places the external caller at ROOT; the source attach card stores the target session and branch head. A cross-session attach does not create an in-graph `attach_returns` edge because its endpoint belongs to another graph. The glyph currently indicates the cross-session relation only; clicking it does not navigate. **Cross-session only**: a same-session spawn has both ends in one graph and keeps the ordinary return edge, so it has no ↗ |

---

## 5. Branch-name badge

- **Anchoring**: **directly below the branch's deepest currently visible node**, one row
  down. In the default (folded) view that is the last conversation-layer node; when a
  call thread is open the badge follows the bottom-most thread item and moves back up
  on fold. Branch membership = lane (thread items share their anchor's lane).
- **No badge for a spawned agent's branch**: the agent is its triangle on the
  thread (§12); a branch pill for the same fact would be a second drawing of it.
  The climb from a branch row's head that passes a spawn root stops without
  placing anything.
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
| 1–7 | Base layout (single turn / multi-turn / retry / tool indent / manual function / composite / collapse shift-left) | Scenario 4's tool calls show as the shoulder count in the default view (scene 11); the thread squares appear only after opening |
| 8 | merge (multi-parent convergence) | ◉ solid circle with a hole, lands on the base branch lane, peer merge-in thick solid lines (peer lane color); attach pointer nodes are not drawn, only the lines |
| 9 | cross-branch messaging (send_message) | dotted `1 5`, target branch color, hidden by default / shown on hover; a from_branch user node lands at the target branch tail |
| 10 | spawn dispatch → return | the sub-agent's head is an item ON its caller's thread (§12): it sits in the sequence position the spawn actually happened at, between the calls before and after it. Its return needs no extra line — the followup reply it triggers merges into the same anchor (§0), so dispatch, work and return are one column read top to bottom (the chat stream still renders the Spawned card, display order moved ahead — see `ui/invariants.md` rule 9) |
| 11 | call-thread default aggregation | see §0/§12: folded to a shoulder count by default, click to open into layout, fold reclaims rows/cols per rule ②; open state is per-node independent and recursive |
| 12 | status & badge legend | see §4: status drawn on the node's own stroke, no placeholder boxes; both sides of a cross-session spawn carry the ↗ corner mark |
| 13 | badge anchoring · avoidance · collision · merged | see §5: anchor directly below the branch's last conversation-layer node, half-column left shift only when an edge crosses the anchor cell, collision shifts down one row, merging erases the badge (provenance moves into the merge node's tooltip) |

**Send-back nodes and the switcher (semantic note, no dedicated layout scene)**: a
send_message send-back (the child branch's answer returning to the initiator's lane
as a user node with `predecessor = the initiating node`) forms a fork whenever the user
also sent a message while waiting — **send-back nodes participate in the `< N/M >`
switcher** (they are genuine alternative continuations of the initiator's dialogue;
`source=from_branch` gets no agent_spawn-style isolation — see `ui/invariants.md`
rule 7).

**A sub-agent spawning again**: forbidden at the data layer at the default
spawn budget (`agent.max_spawn_depth=1` — only the main agent may agent(); a
spawned agent does the work itself, see `ui/invariants.md` rule 6, and a
larger budget allows further generations). The renderer recurses
anyway (§12 — a spawn on an agent's thread is a triangle like any other), so
historical multi-generation delegation chains remain drawable.

## 7. Render pipeline (code map)

```
apps/web/lib/runtime-bridge/dag/
  pipeline.ts        orchestration: passes → layout → edges → nodes → badges → canvas
  canvas.ts          the infinite canvas: pan / zoom / fit, and the dot lattice
                     that makes the grid visible
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
    fold-summaries.ts           fold a compaction capsule's covered range (§9)
    thread.ts                   the call-thread model (§0/§12): merge followup
                                and agent-internal turns into their anchors,
                                attribute every call and spawn to an anchor's
                                time-ordered thread, decide visibility from the
                                open set
  layout/geometry.ts the implementation of section 1. Packs the backend's
                     lane / tier / depth into lattice `(col, row)` positions for
                     CHAIN nodes (fork lanes one gap column out, fork roots on
                     their sibling's row), then places every open thread
                     recursively beside its anchor, shifts later chain rows by
                     the insertion, and reserves each thread column.
                     **tier and lane are NOT computed here** — the backend
                     computes them in `openprogram/webui/graph_layout/` and ships
                     them on the node; the front end only consumes the values.
  render/edges.ts    the line-style table of section 3
  render/nodes.ts    the shapes + status strokes + badges of section 4
  render/badges.ts   the branch-name badge of section 5
  store/globals.ts   expansion state, lastGraph, signatures
```

The backend `openprogram/webui/graph_builder.py` produces the node array (including the
`branch_name` stamp, caller/predecessor), and `graph_layout/` does the lane/tier/depth
annotation — **tier specifically in `graph_layout/tier.py`**. Verification tool: `python scripts/dag_dump.py <session_id>` prints
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
(`apps/web/lib/runtime-bridge/dag/index.ts`) is what the host calls to fetch and
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
fall out of `node_ids`, so the white fill lands on the summary and nowhere in
the range behind it. §9 is what the graph then does with that range.

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
- The summary node is drawn like any other conversation node in every respect
  the data cares about; only genuinely synthetic bridges are filtered
  (`graph_layout/filter.py`). Its capsule is a shape, not a role — §9.
- After compaction the covered prefix falls out of the set, so the fill lands
  on the summary and the kept tail.
- `compaction_finished` must refresh the context range
  (`chat-handlers.ts`); coverage is event-driven like everything else — the
  frontend never computes context membership itself.
- Context ids that have no drawn node (e.g. `display=runtime`
  task-followup rows) are silently ignored: they are context, but not
  graph.

## 9. Compaction reads as one capsule, not fourteen dimmed turns

Dimming a covered range says the right thing about each node and the wrong
thing about the session: fourteen faint circles still cost fourteen rows, and
the eye still has to walk them to reach the live conversation. The summary is
one node that stands for all of them, so the graph draws it that way.

**The capsule.** A summary node is a double circle on the trunk: the
reference circle with a thin concentric inner ring. Same footprint as every
other glyph, one grid slot — the ring is what says "a turn that holds more
than it shows". The ring follows the outer stroke's colour states (grey when
inert or superseded). It is still an ordinary `role=llm` chain member
(dag/overview.md §8): the shape is a shape, not a fourth role.

**The count.** A folded capsule wears the covered-node count as digits on its
right shoulder — the same fold vocabulary as a turn's call-thread count (§12),
and it disappears when the capsule opens, because then the stack is drawn.
No text caption: the glyph (double circle) says what it is, the count says
how much, the ghosts say it is open, and the tooltip/inspector carry the
details.

**The fold is per-branch.** A summary belongs to the branch whose context
carries it — the branch whose active chain contains the whole covered segment
(context/compaction.md §3). Only on that branch does the capsule fold: the
covered range is elided by default, clicking the capsule brings it back as
ghosts, clicking again folds it away (view-only state, never persisted; a
fresh session starts folded). Expansion is sticky across branch switches:
viewing a branch where the covered turns render raw marks the summary
expanded, so returning to the carrying branch finds the range open instead
of snapped shut — seen is seen. On any other branch — a fork from inside the
covered range, a sibling of the same era — those turns ARE the live context:
they render raw, in full branch colour, and the capsule stays on screen but
inert: its own colour, no fold affordance, no count, no white fill — the
node set is identical on every branch, only its reading changes. Switching
branches flips both readings; nothing about the stored graph changes.

**The ghosts.** An expanded range keeps its branch colour; "not part of the
next request" is said by the dashed incoming edge and by the missing white
fill (the white fill lands only on nodes in the context set), never by
draining the colour to grey — grey is reserved for dead history (archived
failure lines, superseded summaries), which no branch can ever carry again.
Readable, clickable, visibly not live: "did the summary actually capture
what I said" is answerable without the answer ever being confused for
context.

**Rolling summaries.** Compaction chains: a second compact feeds the first
summary's text back into the summariser and `extra_meta._last_summary_id`
moves to the replacement — the next request carries exactly one summary,
never a stack. The graph says the same thing: only the active summary gets
`covers_ids` (capsule + fold); a superseded summary keeps its row and its
capsule silhouette but arrives flagged `superseded_summary`, draws in ghost
grey, and folds nothing.

**Where the capsule sits.** One slot, identical in every state and on every
branch: the covered segment's end. Expanded, it follows its ghosts
(ghosts → capsule → tail); on a non-carrying branch it follows the same
turns in the raw (filled turns → hollow capsule → the rest); folded is the
same slot with the segment collapsed, which puts the capsule first on the
trunk. Position never changes when the user switches branches or toggles the
fold — only colour and folding do. All placements are view-only clone
rewrites in `fold-summaries.ts`; the stored row keeps its real `predecessor`
(the range's start — ROOT) in every state.

The white fill never lands on a covered node, in either state, because
`/context-range` does not list it. One fact, one source (§8).

### Where the coverage comes from

The persister writes `metadata.covers_ids` — the exact chain nodes the summary
replaces (context/persistence.py). Ids, not a seq interval: seq intervals span
sibling branches in a DAG, so a dead fork whose seqs fall inside
`[first_seq, last_seq]` would fold behind a capsule that never summarised it,
and the answer would change whenever HEAD moved. `webui/graph_builder.py`
passes the list through, adds the caller subtrees hanging off covered turns (a
covered turn folds with its calls), drops ids that no longer exist, and puts
the result on the summary row as `covers_ids`.

One field drives everything: the capsule shape, the fold, the pleat count, the
ghost marking, and the inspector's coverage row. The frontend does no seq
arithmetic and calls no second endpoint.

## 10. A failed turn stays visible as an archive, never as an alarm

A turn that ends in `status = error` is a terminal node; the retry forks off its
predecessor and the conversation continues on the new line (dag/overview.md).
The failed line is kept — that is the point of forking rather than rewinding —
but it can never re-enter context.

So once such a node is **off the HEAD chain**, it draws in the same grey as a
covered turn, and the inspector labels it `失败轮 · 已留档`. The two states look
alike because on the only axis the graph is about they *are* alike: on disk,
readable, and never in the next request.

The grey deliberately replaces the red `!` stroke §4 gives a live error. Red
means "this needs you now", and an archived line does not — the retry already
happened. The `!` glyph itself stays, so why the line ended is still legible.

Both halves of the test matter. `status` alone would grey the error you are
currently looking at, before you have retried it. Off-HEAD alone would grey
every sibling branch. The node has to be a failure *and* abandoned.

`status` is the store's own terminal marker, written by the turn machinery
([Execution cancellation](../execution/turn-cancellation.html) for the cancel case, which stays
`cancelled` and keeps its own 50% grey). The graph reads it; it never decides
it.

## 11. Hover, click, right-click, double-click

One surface per question. There used to be three info windows — a hover
tooltip, a second-stage dwell expansion of it, and a click-opened inspector
popover — which gave the click two jobs at once: open a window AND toggle the
node's thread, with the popover landing on top of the very expansion it had
just triggered.

**Hover → the brief card.** The quick cut: role (a spawn head titles itself
`子 agent · <name>` — this is where the name lives, §12), model/tokens, a
short content preview, folded call count. The token figure is
`llm.output_tokens` when the node carries a measurement and `chars/4` when it
does not; the card says which (`tokens` vs `tokens（估）`). Appears after a
short hover delay, gone when the cursor leaves.

**Click → the node's own action.** Fold or unfold its call thread (§12).
Nothing else — no window competes with the expansion. The right rail's
Details view still fills quietly for whenever the user opens it.

**Right-click → the SAME card expands in place.** Not a second window: the
one card element deepens where it stands (`tooltip.ts expandTooltip`) — every
field, longer previews, coverage state, context standing, id — and the verbs
join at its bottom: checkout to this branch · fork from this node · fork and
edit this message (user turns only) · copy node id · view raw JSON. One row
builder (`renderNodeInfo`) feeds both states, so they cannot disagree; the
coverage rows read off the DOM flags the node drawer already stamped
(`data-ghost`, `data-failed`, `.out-of-context`), so the card and the picture
beside it cannot disagree either. Expanded, the card turns interactive and
stays — mouse-off no longer dismisses it, hover cannot repaint it, and a
click anywhere else (or running a verb) collapses it. Raw JSON opens in the
inspector shell rather than a modal — a modal would take the graph away to
show you one node from it.

**Double-click a user turn → fork and edit.** The message text lands in the
composer with HEAD already back at its fork point. Other nodes keep the
checkout behaviour — there is nothing to edit on a reply or a tool result.

### Every action is an existing operation

Nothing here adds a verb to the protocol:

| Action | Route | Why that is the whole implementation |
|---|---|---|
| checkout | `POST /api/chat/checkout` | a pure HEAD move, exactly what the transcript's sibling navigator sends |
| fork from node | `POST /api/chat/checkout` | fork *is* checkout plus intent — a turn sent from a HEAD that already has children is by definition a sibling of them, which is how the transcript's "branch from here" button works too |
| fork and edit | `POST /api/chat/checkout` to the node's **predecessor**, then the text into the composer | the user edits and sends; that send is an ordinary send against the new HEAD, so it forks with no protocol change and no "send from node X" concept to keep alive. The predecessor is the fork point precisely because the edited message has to stand *beside* the original, not after it — the same shape `POST /api/chat/edit` produces |

The inspector and menu are built imperatively (`render/inspector.ts`) because
the graph is: they float over an SVG the renderer owns, keyed to node geometry,
outside React's tree.

**Legend.** A collapsible card names the shapes and the two greys, opened from
the canvas HUD beside the fit button (`components/chat/dag-view.tsx`). It starts
collapsed — the vocabulary is small and learnable, so the legend is for the
first few sessions rather than a permanent fixture on the canvas.

## 12. The call thread: what a turn did is one sequence, folded into one count

Everything a turn did — every function call, every agent it spawned — is one
time-ordered sequence of events, the turn's **thread**. Calls and spawns are
the same kind of event at different scales, so they share one line, one
ordering, and one fold. A session whose one reply made 41 calls and spawned two
agents is, by default, three chain nodes and the digits `43`; open, it is 43
real nodes in the order they happened.

**Folded is folded.** The only mark is the count on the node's upper-right
shoulder: digits in annotation grey, glued to the glyph. Not a badge shape, not
a pill, not a square on a line — anything with an outline reads as a node, and
a phantom node is exactly the misreading the fold must not invite. No thread
line, no items, nothing else.

**Open is open.** Clicking the node inserts the thread into the layout: a
solid annotation-grey line drops the anchor's own column and every event
hangs off it on a horizontal stub — down first, then right, the trunk
pattern one level down. Every event is a real node on that line — a square per call
in the anchor's lane colour — spawned agents included — one row
per event, top to bottom in call order. Later user / assistant nodes on the
figure move down by those inserted rows, so the thread vertical in the
anchor's column ends before the next triangle. Each open thread reserves its
column. Fold reclaims the rows (rule ②).

**The head IS the agent, and the spawn IS a call.** A spawn root draws as a
SQUARE — dispatching an agent is a function call, and the square is the call
vocabulary. The dispatch call node (`agent` / `send_message`) folds into it:
one spawn, one glyph (a dispatch that opened no spawn keeps its own square —
that failure is worth seeing). The agent's internal turns are not chain
nodes — they merge into it (§0), replies drawing as triangles and calls as
squares once opened — and its own thread sits one column further right,
opened by clicking the square. While the spawn square is on
screen (its owner's thread is open — the opt-in that keeps the default
canvas clean), a badge pill (the same pill as branch badges,
render/badges.ts) sits at its RIGHT as the square's name tag; clicking it
checks the agent chain's tip out as the active branch — taking over the
agent's conversation. Badges never cover a node: every visible glyph seeds the
badge collision boxes, so a badge that would land on one steps down a row. The model is recursive and so is the picture: every level reads
by the same two rules, count-on-shoulder folded, column-of-nodes open. A
nested open thread pushes the parent's later items down; a chain-anchor
thread likewise inserts later conversation-layer turns. Expansion is
insertion, never overlay.

**No captions.** The agent's name lives in the tooltip and the inspector
(which titles the node `子 agent · <name>` rather than the `user` its role
field claims); the canvas carries only the glyph and its count. The name on
the wire comes from the label the runner stamps (`spawned_from.label`), with
the recorded branch name as a fallback.

**No return line.** The agent's return triggers a followup reply, and that
reply merges into the same anchor whose thread carries the agent (§0) —
dispatch, work and return are one column read top to bottom. The old dashed
attach-return curve only draws when both of its ends are chain-visible, which
an agent-internal tip never is any more.

**View state.** `_threadOpen` in `store/globals.ts`, keyed by anchor id (chain
turn or spawn head — one vocabulary). Never persisted, reset on session
switch, exactly as in §9. A spawn head is visible only while every thread
above it is open; its items likewise — visibility is the whole ancestor
chain's, not the node's own flag.

## Appendix: Implementation Status

The whole spec is implemented. Where each part lives:

| Spec item | Implementation |
|---|---|
| Infinite canvas (pan / zoom / fit / dot lattice) | `dag/canvas.ts` + `.history-body` in `styles/dag/canvas.css`; view state in `dag/store/globals.ts`; HUD in `components/chat/dag-view.tsx` |
| §1 lane / tier / depth layout | `dag/layout/geometry.ts::computeGeometry` (tier-packed chain lanes with per-lane tier zeroing, preorder rows, scene-3 fork rows + gap column, recursive thread placement that inserts later chain rows and reserves thread columns); lattice, no-overlap, thread columns/rows and fork geometry all executed and asserted by `apps/web/scripts/check-dag-subagent.mjs` |
| §2 rule ③ glyphs are cells | no shape is sized from text, and no text draws on the canvas beyond the shoulder count and the capsule note |
| §4 HEAD breathing glow | `render/nodes.ts` stamps `data-head` + the branch colour as `color`; `dag-head-glow` keyframes in `styles/dag/nodes.css` (reduced-motion → steady glow); every glyph stays hollow (`shapes.ts`); HEAD pointing at a merged reply re-seats on its anchor (`pipeline.ts` via `threadModel.anchorOf`) |
| §0/§12 call-thread aggregation | `passes/thread.ts` (`buildThreadModel`: anchor merge, event attribution, recursive visibility); `render/nodes.ts` draws the shoulder count (`history-thread-count`); `_threadOpen` in `store/globals.ts` |
| Rule ② corollary (no placeholder box) | `shapes.ts`: no `square_outline`; task renders as a plain square |
| §4 status on the stroke | `graph_builder` emits status; `nodes.ts` draws it on the stroke (running dashed+breathing / error red+! / cancelled grayed) |
| §5 badge anchoring | `render/badges.ts`: anchor at last conversation-layer node, half-column left shift when a line crosses the anchor cell, measured-pixel-box collision slides down one row |
| Scene 8 merge shape and lines | `shapes.ts` `merge_dot` (◉); `edges.ts` merge-in line peer-colored 2.4px solid |
| Scenes 8/10 attach pointer | backend filters it (display=runtime) + `graph_builder` stamps the ref onto the embed host (`attach_returns`); `edges.ts` draws the long-dash return line |
| §4 cross-session ↗ | `graph_builder` stamps `spawn_remote` from target-root provenance and `spawn_out` from the source attach's target session; `nodes.ts` draws ↗ on both sides. Cross-session badge navigation is not implemented |
| §1 spawn root tier | `graph_layout`: tier=1 / same-row depth / new lane; `task_followup` without an attach pointer re-parents onto the receiving turn (`filter.py` fallback) |
| Composer shared by both perspectives | `styles/chat/center-pane.css` hides `#chatArea`, not `#chatView`; asserted by `apps/web/scripts/check-center-tabs.mjs` |
| In-graph branch tags (checkout buttons) | `render/badges.ts`; hover styles on `.history-branch-tag` in `styles/dag/badges.css` |
| §8 coverage query | `routes/tree.py::_coverage_nodes` fills `/context-range`'s `nodes`; tested in `tests/unit/context/test_context_range_coverage.py` |
| §8 aged / spilled drawing | `render/nodes.ts` (stroke-opacity + `▤`), fed by `_coverageSet` in `store/globals.ts` |
| §9 `covers_ids` on the wire | `webui/graph_builder.py` resolves `metadata.covers` to ids; tested in `tests/unit/dag/test_graph_builder_covers.py` |
| §9 capsule shape | `shapes.ts` `capsule` (keyed on `covers_ids`, tagged `data-shape` so `_applyShapeSize` leaves its geometry alone) |
| §9 fold + pleats + ghosts | `passes/fold-summaries.ts` (fold), `render/nodes.ts` (pleats, `已压缩 · N 轮` caption, ghost stroke), `render/edges.ts` (dashed ghost edge), `_summaryExpanded` in `store/globals.ts`; executed by `apps/web/scripts/check-dag-summary.mjs` |
| §10 archived failure | `render/nodes.ts::_isArchivedFailure` — `status=error` AND off the HEAD chain; grey overrides §4's red |
| §11 one card, two states / fork & edit | `dag/tooltip.ts`: `renderNodeInfo` feeds both states, `expandTooltip` deepens the card in place; `render/inspector.ts` builds only the verb list (+ raw JSON layer), wired in `render/interaction.ts`; the actions go through `POST /api/chat/checkout` |
| §11 legend | `DagLegend` in `components/chat/dag-view.tsx` (inside the canvas HUD), `.dag-legend` in `styles/dag/hud.css` |
| §12 call thread + agent spawn | `shapes.ts` (spawn → square), `passes/thread.ts` (model), `layout/geometry.ts` (recursive placement), `render/edges.ts` (dotted thread line, centre-to-centre chain edges, scene-3 bridge), `render/nodes.ts` (`data-thread*`, shoulder count), `render/interaction.ts` (`toggleThreadOpen`); executed by `apps/web/scripts/check-dag-subagent.mjs` |
| §12 the name on the wire | `task/runner.py::_update_attach_card` stamps `attach.label` from the task; `ws_actions/session.py::_annotate_spawn_origin` carries it to the spawn root as `spawned_from.label`; tested in `tests/unit/test_task_attach_integration.py` |
